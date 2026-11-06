"""Execute actual page JavaScript; price quality must count each position once."""
import ast
import json
from pathlib import Path
import subprocess
import pytest


def source():
    tree=ast.parse(Path('moneybot/app_factory.py').read_text())
    page=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Constant)
        and isinstance(n.value,str) and 'function selectPortfolioRows' in n.value)
    return (page[page.index('function validPortfolioPrice('):page.index('function livePriceCell(')]
        +page[page.index('function updatePortfolioLiveStatus('):page.index('function applyPortfolioLiveQuotes(')])


def test_degraded_quote_is_not_dropped_from_portfolio_summary():
    # Original independent failing assertion, with portable page-source lookup.
    code=r'''
const assert=require('node:assert/strict');
const portfolioBrowserState='connected';
const portfolioProviderStatus=()=> 'Provider connected';
let displayed;
const setPortfolioLiveStatus=(text,mode)=>{displayed={text,mode}};
const currentPortfolioItems=[{current_price:100,live_market:{price:100,source_mode:'websocket',
 admission_status:'DEGRADED',is_stale:false,is_degraded:true,quality_flags:[],age_ms:0,
 event_timestamp:new Date().toISOString(),market_session:'regular'}}];
assert.equal(portfolioPriceQuality(currentPortfolioItems[0]).key,'degraded');
updatePortfolioLiveStatus();
console.log(JSON.stringify(displayed));
assert(!displayed.text.includes('No portfolio prices'),'Retained degraded value must be counted');
assert(displayed.text.includes('1 degraded'),'Degraded position must appear in quality counts');
'''
    result=subprocess.run(['node','-e',source()+'\n'+code],text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('kinds,expected,mode',[
    (['degraded'],'Prices: 1 degraded','stale'),
    (['degraded','degraded'],'Prices: 2 degraded','stale'),
    (['fresh','degraded'],'Mixed quality · Prices: 1 fresh, 1 degraded','stale'),
    (['degraded','stale'],'Mixed quality · Prices: 1 stale, 1 degraded','stale'),
    (['unavailable','unknown'],'Mixed quality · Prices: 1 unavailable, 1 status unknown','stale'),
    ([], 'No portfolio prices','idle'),
    (['fallback'],'Prices: 1 degraded','stale'),
    (['stale'],'Prices: 1 stale','stale'),
    (['unknown'],'Prices: 1 status unknown','idle'),
    (['fresh'],'Prices: 1 fresh','live'),
    (['fresh','stale','degraded','unavailable','unknown'],
     'Mixed quality · Prices: 1 fresh, 1 stale, 1 degraded, 1 unavailable, 1 status unknown','stale'),
])
def test_primary_quality_summary(kinds,expected,mode):
    code=r'''
const assert=require('node:assert/strict');
const kinds=KINDS;
const currentPortfolioItems=kinds.map(kind=>({current_price:100,shares:2,entry_price:50,
 history30:[90,100],performance_amount:100,advice:'HOLD',live_market:{price:100,
 source_mode:kind==='fallback'?'rest':'websocket',source:'massive',
 admission_status:kind==='degraded'?'DEGRADED':kind==='unknown'?'UNKNOWN':'LIVE',
 is_stale:kind==='stale',is_degraded:['fallback','degraded','stale'].includes(kind),
 quality_flags:kind==='unavailable'?['entry_price_fallback']:[],age_ms:0,
 event_timestamp:new Date().toISOString(),market_session:'regular'}}));
const before=JSON.stringify(currentPortfolioItems);
const portfolioBrowserState='connected',portfolioProviderStatus=()=> 'Provider connected';
let displayed;
const setPortfolioLiveStatus=(text,mode)=>{displayed={text,mode}};
updatePortfolioLiveStatus();
assert.equal(displayed.text,'Browser price feed connected · Provider connected · '+EXPECTED);
assert.equal(displayed.mode,MODE);
assert(!/NaN|undefined/.test(displayed.text));
assert.equal((displayed.text.match(/\d+ degraded/g)||[]).length,kinds.some(k=>['degraded','fallback'].includes(k))?1:0);
const counted=[...displayed.text.matchAll(/(\d+) (?:fresh|stale|degraded|unavailable|status unknown)/g)]
 .reduce((sum,m)=>sum+Number(m[1]),0);
assert.equal(counted,kinds.length);
assert.equal(JSON.stringify(currentPortfolioItems),before);
if(kinds.includes('fallback')) assert(!portfolioPriceQuality(currentPortfolioItems[0]).label.includes('Live'));
'''.replace('KINDS',json.dumps(kinds)).replace('EXPECTED',json.dumps(expected)).replace('MODE',json.dumps(mode))
    result=subprocess.run(['node','-e',source()+'\n'+code],text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_unrecognized_category_fails_to_counted_unknown():
    code=r'''
const assert=require('node:assert/strict');
const currentPortfolioItems=[{current_price:100}],portfolioBrowserState='connected';
const portfolioProviderStatus=()=> 'Provider connected';
portfolioPriceQuality=()=>({key:'future_quality'});
let displayed;
const setPortfolioLiveStatus=(text,mode)=>{displayed={text,mode}};
updatePortfolioLiveStatus();
assert(displayed.text.endsWith('Prices: 1 status unknown'));
assert.equal(displayed.mode,'idle');
assert(!/NaN|undefined/.test(displayed.text));
'''
    result=subprocess.run(['node','-e',source()+'\n'+code],text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_degraded_admission_uses_actual_warning_banner_and_retains_price():
    from test_portfolio_ui_offline_investigation import _ui, _item, _quote
    quote={**_quote(), 'source_mode':'websocket', 'admission_status':'DEGRADED',
           'is_degraded':True}
    result=_ui([_item(live=quote)], 'updatePortfolioLiveStatus(); capture();')
    assert result['status'].endswith('Prices: 1 degraded')
    assert 'No portfolio prices' not in result['status']
    assert result['background']=='#fef3c7'
    assert 'WebSocket · Live' not in result['cells'][0]
    assert result['items'][0]['current_price']==quote['price']
