from __future__ import annotations

import logging
import os

from flask import Flask, render_template_string
from flask_cors import CORS

from .api import api_bp
from .extensions import db, migrate
from .services.market_data import MarketDataService


def create_app() -> Flask:
    secret = os.environ.get("MONEYBOT_SECRET_KEY")
    if not secret:
        raise RuntimeError("MONEYBOT_SECRET_KEY must be set")

    database_url = os.environ.get("DATABASE_URL", "sqlite:///moneybot.db")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app = Flask(__name__)
    app.url_map.strict_slashes = False
    app.config.update(
        SECRET_KEY=secret,
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DATA_PROVIDER=os.environ.get("DATA_PROVIDER", "yfinance"),
    )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    CORS(app)
    db.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401

    app.register_blueprint(api_bp)
    app.extensions["market_data_service"] = MarketDataService()

    with app.app_context():
        db.create_all()

    @app.get("/")
    @app.get("/index.html")
    @app.get("/home")
    def home():
        return render_template_string(
            """
            <html>
              <body style="font-family:Inter,Segoe UI,system-ui,sans-serif;padding:24px;background:linear-gradient(180deg,#f8fafc,#eef2ff);max-width:1120px;margin:0 auto;color:#0f172a">
                <header style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:14px">
                  <div style="flex:1;min-width:280px">
                    <img src="/static/moneybot-pro-logo.svg" alt="MoneyBot Pro logo" style="display:block;width:100%;max-width:860px;height:auto"/>
                  </div>
                  <div style="display:flex;gap:10px;flex-wrap:wrap">
                    <a href="/login" style="padding:8px 12px;background:#dbeafe;border-radius:999px;text-decoration:none;font-weight:600">Login</a>
                    <a href="/signup" style="padding:8px 12px;background:#dbeafe;border-radius:999px;text-decoration:none;font-weight:600">Sign up</a>
                    <a href="/portfolio" style="padding:8px 12px;background:#1e40af;color:#fff;border-radius:999px;text-decoration:none;font-weight:700">User Portfolio</a>
                  </div>
                </header>

                <section style="background:#0f172a;color:#e2e8f0;border-radius:14px;padding:16px;margin-bottom:18px;box-shadow:0 10px 24px rgba(2,6,23,.18)">
                  <h3 style="margin:0 0 10px 0;color:#f8fafc">Quick Ask · Buy or Sell Now</h3>
                  <div style="display:flex;gap:8px;flex-wrap:wrap">
                    <input id="quickSymbol" placeholder="Ticker (e.g. AAPL)" style="padding:10px 12px;border:1px solid #334155;border-radius:10px;min-width:210px;background:#111827;color:#f8fafc"/>
                    <button onclick="quickAsk()" style="padding:10px 14px;border:none;background:#2563eb;color:#fff;border-radius:10px;font-weight:700">Analyze</button>
                  </div>
                  <div id="quickOut" style="margin-top:10px;color:#cbd5e1">Type a ticker to get an instant BUY/SELL call.</div>
                </section>

                <section style="margin-bottom:18px">
                  <h3 style="margin-bottom:8px">Market Indices</h3>
                  <div id="market-charts" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px"></div>
                </section>

                <section style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px">
                  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
                    <button class="tab-btn" data-tab="stable" onclick="switchTab('stable')" style="padding:8px 12px;border:1px solid #cbd5e1;background:#dbeafe;border-radius:8px">Stable Watchlist</button>
                    <button class="tab-btn" data-tab="momentum" onclick="switchTab('momentum')" style="padding:8px 12px;border:1px solid #cbd5e1;background:#fff;border-radius:8px">Hot Momentum Buys</button>
                    <button class="tab-btn" data-tab="wells" onclick="switchTab('wells')" style="padding:8px 12px;border:1px solid #cbd5e1;background:#fff;border-radius:8px">Wells of Wall Street</button>
                  </div>
                  <div id="stable" class="tab-panel"></div>
                  <div id="momentum" class="tab-panel" style="display:none"></div>
                  <div id="wells" class="tab-panel" style="display:none"></div>
                </section>

                <div id="homeTickerModal" style="display:none;position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:50;align-items:center;justify-content:center;padding:14px">
                  <div style="background:#fff;border-radius:12px;max-width:680px;width:100%;max-height:80vh;overflow:auto;padding:14px">
                    <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
                      <h3 id="homeModalTitle" style="margin:0">Company Details</h3>
                      <button onclick="closeHomeModal()" style="border:none;background:#e2e8f0;border-radius:8px;padding:6px 10px">Close</button>
                    </div>
                    <p id="homeModalSummary" style="color:#334155"></p>
                  </div>
                </div>

                <p style="color:#64748b">Rule-based guidance; not financial advice.</p>

                <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
                <script>
                  const fallbackData = {
                    market: [
                      { name: 'Dow', symbol: '^DJI', price: 39210.4, change_percent: 0.52, series: [38800,38940,39020,39105,39210] },
                      { name: 'S&P 500', symbol: '^GSPC', price: 5245.1, change_percent: 0.44, series: [5188,5204,5218,5231,5245] },
                      { name: 'Nasdaq', symbol: '^IXIC', price: 16592.3, change_percent: 0.71, series: [16280,16355,16430,16501,16592] },
                      { name: 'Gold', symbol: 'GC=F', price: 2340.8, change_percent: -0.18, series: [2356,2351,2348,2344,2340] },
                      { name: 'Bitcoin', symbol: 'BTC-USD', price: 61110.2, change_percent: -0.93, series: [62400,62020,61680,61390,61110] },
                    ],
                    stable: [{ symbol: 'MSFT', company: 'Microsoft', price: 418.2, signal_score: 7.9, transparency: 'Strong balance sheet and recurring revenue.' }],
                    momentum: [{ symbol: 'SOFI', price: 9.84, score: 9.4, rationale: 'Member growth trend and improving margins.' }],
                    wells: [{ investor: 'Warren Buffett', stocks: [{ ticker: 'AAPL', price: 191.2, performance: 1.42 }] }],
                  };

                  function formatMoney(v){ return typeof v === 'number' ? '$' + v.toLocaleString(undefined,{maximumFractionDigits:2}) : 'n/a'; }
                  const marketChartInstances = {};
                  function destroyMarketCharts(){ Object.values(marketChartInstances).forEach(c => c.destroy()); Object.keys(marketChartInstances).forEach(k => delete marketChartInstances[k]); }

                  async function fetchWithFallback(url, key){
                    try {
                      const res = await fetch(url);
                      if(!res.ok) throw new Error('non-200');
                      const data = await res.json();
                      return data.items || fallbackData[key];
                    } catch (err) {
                      return fallbackData[key];
                    }
                  }

                  async function quickAsk(){
                    const symbol = (document.getElementById('quickSymbol').value || '').trim().toUpperCase();
                    const outEl = document.getElementById('quickOut');
                    if(!symbol){ outEl.textContent='Please enter a ticker symbol.'; return; }
                    const res = await fetch('/api/quick-ask?symbol=' + encodeURIComponent(symbol));
                    const payload = await res.json();
                    if(!res.ok){ outEl.textContent = payload.error || 'Unable to analyze this ticker.'; return; }
                    const data = payload.data || {};
                    outEl.textContent = `${data.recommendation || 'HOLD'} · ${formatMoney(data.current_price)} · ${data.rationale || 'Signal generated from current indicators.'}`;
                  }

                  function tickerButton(symbol){
                    return `<button onclick="showCompanyDetails('${symbol}')" style="border:none;background:none;color:#1d4ed8;font-weight:700;cursor:pointer;font-size:14px;padding:0">${symbol}</button>`;
                  }
                  function openHomeModal(){ document.getElementById('homeTickerModal').style.display='flex'; }
                  function closeHomeModal(){ document.getElementById('homeTickerModal').style.display='none'; }
                  async function showCompanyDetails(symbol){
                    const titleEl = document.getElementById('homeModalTitle');
                    const summaryEl = document.getElementById('homeModalSummary');
                    titleEl.textContent = `${symbol} · Loading...`;
                    summaryEl.textContent = 'Fetching company profile...';
                    openHomeModal();
                    try {
                      const res = await fetch('/api/company-details?symbol=' + encodeURIComponent(symbol));
                      const payload = await res.json();
                      if(!res.ok){
                        titleEl.textContent = symbol;
                        summaryEl.textContent = payload.error || 'Unable to load company details.';
                        return;
                      }
                      const data = payload.data || {};
                      titleEl.textContent = `${data.company_name || symbol} (${symbol})`;
                      summaryEl.textContent = data.summary || 'No summary available.';
                    } catch (err) {
                      titleEl.textContent = symbol;
                      summaryEl.textContent = 'Unable to load company details right now.';
                    }
                  }

                  function renderMarket(items){
                    const grid = document.getElementById('market-charts');
                    destroyMarketCharts();
                    grid.innerHTML = items.map((item, idx) => {
                      const up = (item.change_percent || 0) >= 0;
                      return `<article style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px">
                        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
                          <div><div style="font-weight:700">${item.name}</div><div style="font-size:12px;color:#64748b">${item.symbol}</div></div>
                          <div style="text-align:right"><div style="font-size:18px">${formatMoney(item.price)}</div><div style="font-size:13px;color:${up ? '#166534' : '#b91c1c'}">${up ? '+' : ''}${Number(item.change_percent || 0).toFixed(2)}%</div></div>
                        </div>
                        <div style="margin-top:8px;height:120px"><canvas id="market-chart-${idx}"></canvas></div>
                      </article>`;
                    }).join('');
                    if(!window.Chart) return;
                    items.forEach((item, idx)=>{
                      const up = (item.change_percent || 0) >= 0;
                      const ctx = document.getElementById(`market-chart-${idx}`);
                      if(!ctx) return;
                      marketChartInstances[idx] = new Chart(ctx, {
                        type:'line',
                        data:{labels:(item.series||[]).map((_,i)=>`${i+1}`),datasets:[{data:item.series||[],borderColor:up?'#16a34a':'#dc2626',borderWidth:2,pointRadius:0,tension:.32,fill:true,backgroundColor:up?'rgba(22,163,74,.14)':'rgba(220,38,38,.12)'}]},
                        options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{enabled:true}},scales:{x:{display:false},y:{display:false}}}
                      });
                    });
                  }

                  function renderStable(items){
                    document.getElementById('stable').innerHTML = `<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Score</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Transparency</th></tr></thead><tbody>${items.map(item=>`<tr><td style="padding:8px;border-bottom:1px solid #f1f5f9">${tickerButton(item.symbol)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${formatMoney(item.price)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.signal_score}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.transparency || ''}</td></tr>`).join('')}</tbody></table>`;
                  }

                  function renderMomentum(items){
                    document.getElementById('momentum').innerHTML = `<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Score</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Transparency</th></tr></thead><tbody>${items.map(item=>`<tr><td style="padding:8px;border-bottom:1px solid #f1f5f9">${tickerButton(item.symbol)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${formatMoney(item.price)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.score}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.rationale}</td></tr>`).join('')}</tbody></table>`;
                  }

                  function renderWells(items){
                    document.getElementById('wells').innerHTML = items.map(item=>`<article style="border:1px solid #e2e8f0;border-radius:10px;padding:10px;margin-bottom:10px"><div style="font-weight:700;margin-bottom:8px">${item.investor}</div><table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Performance</th></tr></thead><tbody>${(item.stocks||[]).map(stock=>`<tr><td style="padding:8px;border-bottom:1px solid #f1f5f9">${tickerButton(stock.ticker)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${formatMoney(stock.price)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${Number(stock.performance||0).toFixed(2)}%</td></tr>`).join('')}</tbody></table></article>`).join('');
                  }

                  function switchTab(tab){
                    document.querySelectorAll('.tab-panel').forEach(panel => panel.style.display = panel.id === tab ? 'block' : 'none');
                    document.querySelectorAll('.tab-btn').forEach(btn => btn.style.background = btn.dataset.tab === tab ? '#dbeafe' : '#fff');
                  }

                  document.getElementById('quickSymbol').addEventListener('keydown', (event) => { if(event.key==='Enter'){event.preventDefault();quickAsk();} });
                  document.getElementById('homeTickerModal').addEventListener('click', (event) => { if(event.target.id==='homeTickerModal'){ closeHomeModal(); }});

                  async function init(){
                    const [market, stable, momentum, wells] = await Promise.all([
                      fetchWithFallback('/api/market-overview', 'market'),
                      fetchWithFallback('/api/stable-watchlist', 'stable'),
                      fetchWithFallback('/api/hot-momentum-buys', 'momentum'),
                      fetchWithFallback('/api/wells-picks', 'wells'),
                    ]);
                    renderMarket(market); renderStable(stable); renderMomentum(momentum); renderWells(wells);
                  }

                  init();
                </script>
              </body>
            </html>
            """
        )

    @app.get("/login")
    @app.get("/login/")
    def login_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc;max-width:720px;margin:0 auto">
              <h2>Login</h2>
              <p><a href="/">Home</a> · <a href="/signup">Create account</a></p>
              <form id="loginForm">
                <input id="email" placeholder="email" required />
                <input id="password" type="password" placeholder="password" required />
                <button type="submit">Login</button>
              </form>
              <div id="out" style="margin-top:10px;color:#334155"></div>
              <script>
              const emailEl = document.getElementById('email');
              const passwordEl = document.getElementById('password');
              const outEl = document.getElementById('out');
              document.getElementById('loginForm').addEventListener('submit', go);

              async function go(event){
                if (event) event.preventDefault();
                const res = await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:emailEl.value,password:passwordEl.value})});
                const data = await res.json();
                if(res.ok){ outEl.textContent='Login successful. Redirecting...'; location.href='/portfolio'; }
                else { outEl.textContent = data.error || 'Login failed. Please verify your credentials.'; }
              }
              </script>
            </body></html>
            """
        )

    @app.get("/signup")
    @app.get("/signup/")
    def signup_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc;max-width:720px;margin:0 auto">
              <h2>Sign Up</h2>
              <p><a href="/">Home</a> · <a href="/login">Login</a></p>
              <form id="signupForm">
                <input id="email" placeholder="email" required />
                <input id="password" type="password" placeholder="password" required />
                <button type="submit">Create</button>
              </form>
              <div id="out" style="margin-top:10px;color:#334155"></div>
              <script>
              const emailEl = document.getElementById('email');
              const passwordEl = document.getElementById('password');
              const outEl = document.getElementById('out');
              document.getElementById('signupForm').addEventListener('submit', go);

              async function go(event){
                if (event) event.preventDefault();
                const res = await fetch('/api/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:emailEl.value,password:passwordEl.value})});
                const data = await res.json();
                if(res.ok){ outEl.textContent='Account created. Redirecting...'; location.href='/portfolio'; }
                else { outEl.textContent = data.error || 'Sign-up failed. Please try again.'; }
              }
              </script>
            </body></html>
            """
        )

    @app.get("/portfolio")
    @app.get("/portfolio/")
    def portfolio_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc;max-width:1100px;margin:0 auto">
              <h2>User Portfolio</h2>
              <p><a href="/">Home</a> · <button onclick="logout()">Logout</button></p>
              <form id="addForm">
                <input id="symbol" placeholder="AAPL" required />
                <input id="buy_price" type="number" step="0.01" placeholder="buy price"/>
                <input id="shares" type="number" step="0.0001" placeholder="shares"/>
                <button type="submit">Add</button>
              </form>
              <div id="out" style="margin:10px 0;color:#334155"></div>
              <div style="overflow-x:auto"><table style="width:100%;background:#fff;border-collapse:collapse;min-width:980px">
                <thead><tr><th style="border:1px solid #e5e7eb;padding:8px">Symbol</th><th style="border:1px solid #e5e7eb;padding:8px">Entry</th><th style="border:1px solid #e5e7eb;padding:8px">Shares</th><th style="border:1px solid #e5e7eb;padding:8px">Acquired</th><th style="border:1px solid #e5e7eb;padding:8px">Current Price</th><th style="border:1px solid #e5e7eb;padding:8px">Performance</th><th style="border:1px solid #e5e7eb;padding:8px">Trend</th><th style="border:1px solid #e5e7eb;padding:8px">Score</th><th style="border:1px solid #e5e7eb;padding:8px">Sentiment</th><th style="border:1px solid #e5e7eb;padding:8px">Advice</th><th style="border:1px solid #e5e7eb;padding:8px">Action</th></tr></thead>
                <tbody id="rows"></tbody>
              </table></div>
              <div id="tickerModal" style="display:none;position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:50;align-items:center;justify-content:center;padding:14px">
                <div style="background:#fff;border-radius:12px;max-width:680px;width:100%;max-height:80vh;overflow:auto;padding:14px">
                  <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
                    <h3 id="modalTitle" style="margin:0">Company Details</h3>
                    <button onclick="closeModal()" style="border:none;background:#e2e8f0;border-radius:8px;padding:6px 10px">Close</button>
                  </div>
                  <p id="modalSummary" style="color:#334155"></p>
                  <div id="modalNews" style="display:grid;gap:8px"></div>
                </div>
              </div>
              <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
              <script>
              const rowsEl = document.getElementById('rows');
              const outEl = document.getElementById('out');
              const symbolEl = document.getElementById('symbol');
              const buyPriceEl = document.getElementById('buy_price');
              const sharesEl = document.getElementById('shares');
              document.getElementById('addForm').addEventListener('submit', addItem);

              async function logout(){ await fetch('/api/auth/logout',{method:'POST'}); location.href='/'; }
              function displayValue(value){
                return (value === null || value === undefined || value === '') ? 'n/a' : value;
              }
              function formatMoney(v){
                return (typeof v === 'number' && isFinite(v)) ? ('$' + v.toLocaleString(undefined,{maximumFractionDigits:2})) : 'n/a';
              }
              function formatDate(value){
                if(!value) return 'n/a';
                const d = new Date(value);
                return Number.isNaN(d.getTime()) ? 'n/a' : d.toLocaleDateString();
              }

              function sentimentBadge(value){
                const sentiment = String(value || 'Neutral').toLowerCase();
                if(sentiment === 'bullish' || sentiment === 'positive') return '<span style="color:#166534;font-weight:700;white-space:nowrap">▇ Bullish</span>';
                if(sentiment === 'bearish' || sentiment === 'negative') return '<span style="color:#b91c1c;font-weight:700;white-space:nowrap">▇ Bearish</span>';
                return '<span style="color:#475569;font-weight:600;white-space:nowrap">▇ Neutral</span>';
              }
              function adviceBadge(value){
                const advice = String(value || 'HOLD').toUpperCase();
                const color = advice === 'BUY' ? '#166534' : (advice === 'SELL' ? '#b91c1c' : '#475569');
                return `<span style="display:inline-block;padding:4px 8px;border-radius:999px;background:${color};color:#fff;font-weight:700;font-size:12px">${advice}</span>`;
              }
              function performanceCell(amount, pct){
                if(typeof amount !== 'number' || typeof pct !== 'number') return '<span style="color:#64748b">n/a</span>';
                const up = amount >= 0;
                const color = up ? '#166534' : '#b91c1c';
                const sign = up ? '+' : '';
                return `<div style="color:${color};font-weight:700">${sign}${formatMoney(amount)}</div><div style="color:${color};font-size:12px">(${sign}${pct.toFixed(2)}%)</div>`;
              }
              function renderTrend(divId, series){
                if(!window.Plotly) return;
                if(!Array.isArray(series) || series.length < 2){
                  const el = document.getElementById(divId); if(el) el.innerHTML='<span style="color:#94a3b8">No trend data</span>'; return;
                }
                const up = series[series.length-1] >= series[0];
                Plotly.newPlot(divId,[{y:series,mode:'lines',type:'scatter',line:{color:up?'#16a34a':'#dc2626',width:2},hoverinfo:'skip'}],{margin:{l:2,r:2,t:2,b:2},height:30,width:100,showlegend:false,xaxis:{visible:false,fixedrange:true},yaxis:{visible:false,fixedrange:true},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)'},{displayModeBar:false,responsive:true,staticPlot:true});
              }


              function tickerButton(symbol){
                return `<button onclick="showCompanyDetails('${symbol}')" style="border:none;background:none;color:#1d4ed8;font-weight:700;cursor:pointer;font-size:15px;padding:0">${symbol}</button>`;
              }
              function openModal(){ document.getElementById('tickerModal').style.display='flex'; }
              function closeModal(){ document.getElementById('tickerModal').style.display='none'; }
              async function showCompanyDetails(symbol){
                const titleEl = document.getElementById('modalTitle');
                const summaryEl = document.getElementById('modalSummary');
                const newsEl = document.getElementById('modalNews');
                titleEl.textContent = `${symbol} · Loading...`;
                summaryEl.textContent = 'Fetching company profile...';
                newsEl.innerHTML = '';
                openModal();
                try {
                  const res = await fetch('/api/company-details?symbol=' + encodeURIComponent(symbol));
                  const payload = await res.json();
                  if(!res.ok){
                    titleEl.textContent = symbol;
                    summaryEl.textContent = payload.error || 'Unable to load company details.';
                    return;
                  }
                  const data = payload.data || {};
                  titleEl.textContent = `${data.company_name || symbol} (${symbol})`;
                  summaryEl.textContent = data.summary || 'No summary available.';
                  const news = data.latest_news || [];
                  newsEl.innerHTML = news.length ? news.map(n => `<a href="${n.link || '#'}" target="_blank" rel="noopener" style="display:block;padding:8px;border:1px solid #e2e8f0;border-radius:8px;text-decoration:none;color:#0f172a"><div style="font-weight:600">${n.title || 'Untitled'}</div><div style="font-size:12px;color:#64748b">${n.publisher || ''}</div></a>`).join('') : '<div style="color:#64748b">No recent news available.</div>';
                } catch (err) {
                  titleEl.textContent = symbol;
                  summaryEl.textContent = 'Unable to load company details right now.';
                }
              }

              function renderRows(items){
                if(!items || !items.length){
                  rowsEl.innerHTML = '<tr><td colspan="11" style="padding:8px;color:#64748b">No watchlist entries yet.</td></tr>';
                  return;
                }
                rowsEl.innerHTML = items.map((i,idx)=>`<tr><td style="border:1px solid #e5e7eb;padding:8px;font-size:15px">${tickerButton(i.symbol)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(i.entry_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(i.shares)}</td><td style="border:1px solid #e5e7eb;padding:8px;white-space:nowrap">${formatDate(i.created_at)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(i.current_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${performanceCell(i.performance_amount, i.performance_percent)}</td><td style="border:1px solid #e5e7eb;padding:8px"><div id="trend-${idx}" style="width:100px;height:30px"></div></td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(i.score)}</td><td style="border:1px solid #e5e7eb;padding:8px">${sentimentBadge(i.sentiment)}</td><td style="border:1px solid #e5e7eb;padding:8px">${adviceBadge(i.advice)}</td><td style="border:1px solid #e5e7eb;padding:8px"><button onclick="del(${i.id})">Remove</button></td></tr>`).join('');
                items.forEach((item, idx)=> renderTrend(`trend-${idx}`, item.history30 || []));
              }

              async function load(){
                const res = await fetch('/api/user-watchlist');
                const data = await res.json();
                if(!res.ok){
                  if (res.status === 401) { location.href='/login'; return; }
                  rowsEl.innerHTML = '<tr><td colspan="11" style="padding:8px;color:#b91c1c">Unable to load watchlist right now.</td></tr>';
                  outEl.textContent = data.error || 'Please try again in a moment.';
                  return;
                }
                renderRows(data.enriched_items || data.items || []);
              }
              async function addItem(event){
                if (event) event.preventDefault();
                const payload = { symbol:(symbolEl.value || '').trim().toUpperCase(), buy_price:buyPriceEl.value||null, shares:sharesEl.value||null };
                const res = await fetch('/api/user-watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                const data = await res.json();
                if (res.ok) {
                  outEl.textContent = 'Watchlist item added.';
                  symbolEl.value=''; buyPriceEl.value=''; sharesEl.value='';
                  await load();
                } else {
                  outEl.textContent = data.error || 'Unable to add item.';
                }
              }
              async function del(id){ await fetch('/api/user-watchlist/'+id,{method:'DELETE'}); await load(); }
              document.getElementById('tickerModal').addEventListener('click', (event) => { if(event.target.id==='tickerModal'){ closeModal(); }});
              load();
              </script>
            </body></html>
            """
        )

    return app
