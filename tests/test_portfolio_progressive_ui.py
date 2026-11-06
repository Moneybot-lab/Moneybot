"""Execute the actual Python-decoded JS, including NDJSON escape sequences."""
import ast
import json
from pathlib import Path
import re
import subprocess

import pytest


def source():
    tree = ast.parse(Path("moneybot/app_factory.py").read_text())
    return next(node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                and isinstance(node.value, str) and "function selectPortfolioRows" in node.value)


def test_compact_advice_badge_remains_clickable_and_details_stay_in_modal():
    page = source()
    functions = page[page.index("function adviceBadge"):page.index("function openAdviceModal")]
    code = r"""
const assert = require('assert');
['BUY','HOLD','SELL'].forEach((action, idx) => {
  const html = adviceButton({advice:action,enrichment_state:'pending',ai_portfolio:{mode:'ai_enhanced'}},idx);
  assert(html.includes(`onclick="showAdvice(${idx})"`));
  assert.equal(html.replace(/<[^>]*>/g,'').trim(),action);
  assert(!html.includes('<small'));
  assert(!html.includes('AI pending'));
  assert(!html.includes('AI enhanced'));
});
"""
    subprocess.run(["node","-e",functions+"\n"+code],text=True,capture_output=True,check=True,timeout=10)
    # Detailed source, per-lot progress and advice reasons remain in the modal.
    assert "const mode = portfolioAdviceStatus(item);" in page
    assert "lot.advice_reason" in page


def test_rendered_inline_javascript_syntax():
    for script in re.findall(r"<script[^>]*>(.*?)</script>", source(), flags=re.S):
        subprocess.run(["node", "--check"], input=script, text=True, capture_output=True, check=True, timeout=10)


def run_js(scenario):
    text = source()
    functions = text[text.index("              async function load({"):text.index("              async function addItem(event)")]
    functions += text[text.index("              function selectPortfolioRows"):text.index("              function renderRows")]
    functions += text[text.index("              async function markSold"):text.index("              document.getElementById('tickerModal').addEventListener")]
    functions += "\n" + "\n".join(line for line in text.splitlines() if "window.addEventListener('pagehide'" in line or "window.addEventListener('pageshow'" in line)
    setup = r"""
const assert = require('assert');
let portfolioLoadGeneration=0, portfolioLoadController=null, portfolioViewId=null;
let currentPortfolioItems=[], currentAdviceContext=null, snapshots=0, patches=0, loading=true, liveStarts=0, modalCloses=0;
let portfolioEventSource=null, portfolioReconnectTimer=null, clearedTimer=null;
const window={events:{},addEventListener(name,fn){this.events[name]=fn;}};
const clearTimeout=timer=>{clearedTimer=timer;};
const elements = new Map();
const document={getElementById(id){if(!elements.has(id)) elements.set(id,{textContent:'',style:{},innerHTML:''}); return elements.get(id);}};
const outEl={textContent:''}, lifetimePanelEl={style:{display:'none'}};
const setLoading=value=>{loading=value;};
const startPortfolioLive=()=>{liveStarts++;};
const renderRows=rows=>{snapshots++; currentPortfolioItems=rows;};
const closeAdviceModal=()=>{modalCloses++;};
const adviceButton=(row,idx)=>{patches++; return `<button onclick="showAdvice(${idx})">${row.advice}</button>`;};
const amountColor=()=>'', formatMoney=amount=>`$${amount}`, location={href:''};
const displayValue=String, formatDate=String;
let prompt=()=>null, confirm=()=>false;
const initial={id:1,symbol:'SYNTH',shares:2,entry_price:100,current_price:120,history30:[100,120],advice:'HOLD',advice_reason:'HOLD initially',enrichment_state:'pending',portfolio_view_id:'view'};
const second={...initial,id:2,shares:3};
const snapshot={type:'snapshot',view_id:'view',enriched_items:[initial,second]};
const update={type:'advice',view_id:'view',item:{id:1,symbol:'SYNTH',portfolio_view_id:'view',advice:'SELL',advice_reason:'SELL accepted',base_advice:'SELL',enrichment_state:'completed',ai_portfolio:{mode:'ai_enhanced'},suitability:{changed:false}}};
let apiFetch;
"""
    completed = subprocess.run(["node", "-e", setup + functions + "\n(async()=>{" + scenario + "})().catch(e=>{console.error(e);process.exit(1)});"],
                               text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr


def test_split_chunks_initial_paint_precedes_ai_and_summary_preserves_live_prices():
    run_js(r"""
      let callCount=0;
      const encoder=new TextEncoder();
      apiFetch=async(url,options)=>{
        callCount++;
        if(url.includes('summary')) return new Promise(()=>{}); // slow summary cannot block rows/AI
        let index=0;
        const chunks=[JSON.stringify(snapshot).slice(0,15),JSON.stringify(snapshot).slice(15)+'\n',JSON.stringify(update)+'\n', '{"type":"complete"}\n'];
        return {ok:true,status:200,headers:{get:()=> 'application/x-ndjson'},body:{getReader:()=>({
          async read(){
            if(index===2){assert.equal(loading,false);assert.equal(snapshots,1);assert.equal(liveStarts,1);assert.equal(elements.get('currentMarketValue').textContent,'$600');currentPortfolioItems[0].current_price=130;}
            return index<chunks.length ? {done:false,value:encoder.encode(chunks[index++])} : {done:true};
          },async cancel(){},releaseLock(){}
        })}};
      };
      await load();
      assert.equal(snapshots,1);assert.equal(callCount,2);
      assert.equal(currentPortfolioItems[0].shares,5);
      assert.equal(currentPortfolioItems[0].current_price,130);
      assert.deepEqual(currentPortfolioItems[0].history30,[100,120]);
      assert.equal(currentPortfolioItems[0].advice,'SELL');
      assert.equal(currentPortfolioItems[0].lots[1].enrichment_state,'failed');
    """)


def test_wrong_view_duplicate_unknown_lot_and_old_generation_cannot_overwrite():
    run_js(r"""
      currentPortfolioItems=selectPortfolioRows(snapshot);portfolioViewId='view';
      applyPortfolioAdvice({...update,view_id:'another-account-view'},0);
      applyPortfolioAdvice({...update,item:{...update.item,id:999}},0);
      applyPortfolioAdvice(update,99);
      assert.equal(currentPortfolioItems[0].advice,'HOLD');
      currentAdviceContext={symbol:'SYNTH'};
      applyPortfolioAdvice(update,0);
      applyPortfolioAdvice({...update,item:{...update.item,advice:'BUY'}},0);
      assert.equal(currentPortfolioItems[0].advice,'SELL');assert.equal(patches,1);
      assert.equal(modalCloses,1);
      portfolioLoadController=new AbortController();const controller=portfolioLoadController;
      stopPortfolioEnrichment();assert(controller.signal.aborted);
      applyPortfolioAdvice({...update,item:{...update.item,id:2}},0);
      assert.equal(currentPortfolioItems[0].lots[1].advice,'HOLD');
    """)


def test_busy_uses_data_only_and_does_not_retry_ai():
    run_js(r"""
      const urls=[];
      apiFetch=async(url)=>{
        urls.push(url);
        if(url.endsWith('/stream')) return {status:429};
        if(url.includes('summary')) return new Promise(()=>{});
        return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>snapshot};
      };
      await load();
      assert.deepEqual(urls,['/api/user-watchlist/stream','/api/user-watchlist?defer_ai=1','/api/portfolio-summary']);
      assert.equal(loading,false);assert.equal(snapshots,1);
    """)


def test_interrupted_stream_keeps_snapshot_and_marks_pending_failed():
    run_js(r"""
      apiFetch=async(url)=>{
        if(url.includes('summary')) return new Promise(()=>{});
        let first=true;
        return {ok:true,status:200,headers:{get:()=> 'application/x-ndjson'},body:{getReader:()=>({
          async read(){if(first){first=false;return {done:false,value:new TextEncoder().encode(JSON.stringify(snapshot)+'\n')};} throw new Error('offline');},
          async cancel(){},releaseLock(){}
        })}};
      };
      await load();
      assert.equal(snapshots,1);assert.equal(currentPortfolioItems[0].enrichment_state,'failed');
      assert.equal(currentPortfolioItems[0].current_price,120);assert.equal(loading,false);
    """)


def test_old_summary_is_ignored_and_failed_summary_is_explicit():
    run_js(r"""
      let resolve;
      apiFetch=async()=>({ok:true,json:()=>new Promise(done=>{resolve=done;})});
      const pending=loadPortfolioSummary(0);
      await new Promise(done=>setImmediate(done));
      portfolioLoadGeneration=1;
      resolve({realized_gain_loss:999});await pending;
      assert(!elements.has('realizedPnl'));
      apiFetch=async()=>({ok:false,json:async()=>({})});
      await loadPortfolioSummary(1);
      assert.equal(elements.get('realizedPnl').textContent,'Unavailable — refresh to retry');
    """)


def test_navigation_aborts_ai_closes_feed_and_cancels_pending_refresh():
    run_js(r"""
      currentPortfolioItems=selectPortfolioRows(snapshot);portfolioViewId='view';
      portfolioLoadController=new AbortController();const controller=portfolioLoadController;
      let closed=false;portfolioEventSource={close(){closed=true;}};
      portfolioReconnectTimer=42;
      window.events.pagehide();
      assert(controller.signal.aborted);assert(closed);assert.equal(clearedTimer,42);assert.equal(portfolioReconnectTimer,null);
      assert.equal(currentPortfolioItems[0].enrichment_state,'failed');
      assert(currentPortfolioItems[0].lots.every(lot=>lot.enrichment_state==='failed'));
      let refresh=null;load=options=>{refresh=options;};
      window.events.pageshow({persisted:true});assert.deepEqual(refresh,{enrich:false});
    """)


@pytest.mark.parametrize("handler,answers", [
    ("markBought", [None]),
    ("markBought", ["110", None]),
    ("markSold", [None]),
    ("markSold", ["1", None]),
    ("markSold", ["1", "110", None]),
    ("markSold", ["1", "110", "1", None]),
    ("markSold", ["1", "110", "1", "2026-11-03"]),  # confirmation declined
    ("editRow", [None]),
    ("editRow", ["DELETE"]),  # confirmation declined
    ("editRow", ["UPDATE", None]),
])
@pytest.mark.parametrize("completed_first", [False, True])
def test_cancelled_mutation_finalizes_only_pending_advice(handler, answers, completed_first):
    run_js("const answers=" + json.dumps(answers) + "; const completedFirst=" + json.dumps(completed_first) + r""";
      const first={...initial,live_market:{source:'massive',is_stale:true},suitability:{changed:true,action:'HOLD'}};
      if(completedFirst){first.enrichment_state='completed';first.ai_portfolio={mode:'ai_enhanced'};}
      currentPortfolioItems=selectPortfolioRows({...snapshot,enriched_items:[first,second]});portfolioViewId='view';
      portfolioLoadController=new AbortController();const controller=portfolioLoadController;
      const expected=JSON.parse(JSON.stringify(currentPortfolioItems));
      expected.forEach(row=>{
        if(row.enrichment_state==='pending') row.enrichment_state='failed';
        row.lots.forEach(lot=>{if(lot.enrichment_state==='pending') lot.enrichment_state='failed';});
      });
      prompt=()=>{assert(answers.length);return answers.shift();};
      apiFetch=()=>{throw new Error('Cancelled dialog must not submit a mutation or reload');};
    """ + f"await {handler}(1);" + r"""
      assert.equal(answers.length,0);assert(controller.signal.aborted);
      assert.equal(portfolioLoadGeneration,1);assert.equal(portfolioViewId,null);
      assert.deepEqual(currentPortfolioItems,expected);
      assert(!portfolioAdviceStatus(currentPortfolioItems[0]).includes('AI pending'));
      assert.equal(elements.get('portfolio-advice-0').innerHTML,'<button onclick="showAdvice(0)">HOLD</button>');
      if(!completedFirst) assert.equal(currentPortfolioItems[0].enrichment_state,'failed');
      applyPortfolioAdvice(update,0); // A late result from the cancelled generation cannot restore advice.
      assert.deepEqual(currentPortfolioItems,expected);
    """)


@pytest.mark.parametrize("handler,answers,path,method", [
    ("markBought", ["110", "1"], "/api/user-watchlist/1/buy", "POST"),
    ("markSold", ["1", "110", "1", "2026-11-03"], "/api/user-watchlist/1/sell", "POST"),
    ("editRow", ["UPDATE", "SYNTH", "105", "2"], "/api/user-watchlist/1", "PATCH"),
    ("editRow", ["DELETE"], "/api/user-watchlist/1", "DELETE"),
])
def test_completed_mutation_still_submits_once_and_reloads(handler, answers, path, method):
    run_js("const answers=" + json.dumps(answers) + r""";
      currentPortfolioItems=selectPortfolioRows(snapshot);portfolioViewId='view';
      portfolioLoadController=new AbortController();const controller=portfolioLoadController;
      prompt=()=>answers.shift();confirm=()=>true;
      const calls=[];let reloads=0;
      apiFetch=async(url,options)=>{
        assert(controller.signal.aborted);
        assert(currentPortfolioItems[0].lots.every(lot=>lot.enrichment_state==='failed'));
        calls.push({url,method:options.method});
        return {ok:true,json:async()=>({})};
      };
      load=async()=>{reloads++;assert(!outEl.textContent.includes('AI enrichment stopped'));};
    """ + f"await {handler}(1);" + r"""
      assert.equal(reloads,1);assert.equal(answers.length,0);
    """ + "assert.deepEqual(calls," + json.dumps([{"url": path, "method": method}]) + ");")


def test_refresh_finalizes_old_pending_without_old_abort_overwriting_new_advice():
    run_js(r"""
      let streams=0,oldSignal;
      const encoder=new TextEncoder();
      apiFetch=async(url,options)=>{
        if(url.includes('summary')) return new Promise(()=>{});
        const isOld=streams++===0;
        let index=0;
        const freshSnapshot={...snapshot,view_id:'fresh',enriched_items:[{...initial,current_price:130,portfolio_view_id:'fresh'}]};
        const freshUpdate={...update,view_id:'fresh',item:{...update.item,portfolio_view_id:'fresh'}};
        const chunks=isOld ? [snapshot] : [freshSnapshot,freshUpdate,{type:'complete'}];
        if(isOld) oldSignal=options.signal;
        return {ok:true,status:200,headers:{get:()=> 'application/x-ndjson'},body:{getReader:()=>({
          async read(){
            if(index<chunks.length) return {done:false,value:encoder.encode(JSON.stringify(chunks[index++])+'\n')};
            if(!isOld) return {done:true};
            return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('cancelled'),{name:'AbortError'})),{once:true}));
          },async cancel(){},releaseLock(){}
        })}};
      };
      const oldLoad=load();
      await new Promise(done=>setImmediate(done));
      const oldRows=currentPortfolioItems;
      outEl.textContent='Refreshing portfolio';
      await load();await oldLoad;
      assert(oldSignal.aborted);assert(oldRows[0].lots.every(lot=>lot.enrichment_state==='failed'));
      assert.equal(streams,2);assert.equal(portfolioViewId,'fresh');assert.equal(loading,false);
      assert.equal(currentPortfolioItems[0].current_price,130);
      assert.equal(currentPortfolioItems[0].enrichment_state,'completed');assert.equal(currentPortfolioItems[0].advice,'SELL');
      assert.equal(outEl.textContent,'Refreshing portfolio');
    """)
