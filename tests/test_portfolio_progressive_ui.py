"""Execute the actual Python-decoded JS, including NDJSON escape sequences."""
import ast
import json
from pathlib import Path
import re
import subprocess


def source():
    tree = ast.parse(Path("moneybot/app_factory.py").read_text())
    return next(node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                and isinstance(node.value, str) and "function selectPortfolioRows" in node.value)


def test_rendered_inline_javascript_syntax():
    for script in re.findall(r"<script[^>]*>(.*?)</script>", source(), flags=re.S):
        subprocess.run(["node", "--check"], input=script, text=True, capture_output=True, check=True, timeout=10)


def run_js(scenario):
    text = source()
    functions = text[text.index("              async function load({"):text.index("              async function addItem(event)")]
    functions += text[text.index("              function selectPortfolioRows"):text.index("              function renderRows")]
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
const adviceButton=(row)=>{patches++; return row.advice;};
const amountColor=()=>'', formatMoney=amount=>`$${amount}`, location={href:''};
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
      portfolioLoadController=new AbortController();const controller=portfolioLoadController;
      let closed=false;portfolioEventSource={close(){closed=true;}};
      portfolioReconnectTimer=42;
      window.events.pagehide();
      assert(controller.signal.aborted);assert(closed);assert.equal(clearedTimer,42);assert.equal(portfolioReconnectTimer,null);
      let refresh=null;load=options=>{refresh=options;};
      window.events.pageshow({persisted:true});assert.deepEqual(refresh,{enrich:false});
    """)
