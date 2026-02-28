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
                    <a href="/watchlist" style="padding:8px 12px;background:#1e40af;color:#fff;border-radius:999px;text-decoration:none;font-weight:700">My Watchlist</a>
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

                <div id="tickerModal" style="display:none;position:fixed;inset:0;background:rgba(15,23,42,0.55);z-index:1000;align-items:center;justify-content:center;padding:20px">
                  <div style="background:#fff;border-radius:12px;max-width:500px;width:100%;padding:16px;border:1px solid #cbd5e1;box-shadow:0 14px 32px rgba(15,23,42,.2)">
                    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
                      <h4 id="modalTitle" style="margin:0">Company Details</h4>
                      <button onclick="closeTickerModal()" style="border:none;background:#e2e8f0;border-radius:8px;padding:6px 10px;cursor:pointer">Close</button>
                    </div>
                    <div id="modalBody" style="margin-top:10px;color:#334155"></div>
                  </div>
                </div>

                <p style="color:#64748b">Rule-based guidance; not financial advice.</p>

                <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
                <script>
                  const fallbackData = {
                    market: [
                      { name: 'Dow Jones', symbol: '^DJI', price: 39180.42, change_percent: 0.63, series: [38750,38840,38920,39015,39180] },
                      { name: 'S&P 500', symbol: '^GSPC', price: 5224.67, change_percent: 0.48, series: [5140,5168,5182,5201,5224] },
                      { name: 'Nasdaq', symbol: '^IXIC', price: 16480.93, change_percent: 0.74, series: [16120,16210,16300,16395,16480] },
                      { name: 'Gold', symbol: 'GC=F', price: 2334.55, change_percent: -0.21, series: [2350.2,2346.1,2341.8,2338.9,2334.55] },
                      { name: 'Bitcoin', symbol: 'BTC-USD', price: 61225.11, change_percent: -1.07, series: [62980,62310,61920,61540,61225] }
                    ],
                    stable: [
                      { symbol: 'MSFT', company: 'Microsoft', price: 418.33, signal_score: 7.9, transparency: 'Large-cap software leader with resilient cash flow.', details: { company: 'Microsoft', sector: 'Technology', stability_note: 'Large-cap software leader with resilient cash flow.' } },
                      { symbol: 'JNJ', company: 'Johnson & Johnson', price: 154.62, signal_score: 7.55, transparency: 'Defensive healthcare profile with diversified products.', details: { company: 'Johnson & Johnson', sector: 'Healthcare', stability_note: 'Defensive healthcare profile with diversified products.' } },
                      { symbol: 'PG', company: 'Procter & Gamble', price: 168.44, signal_score: 7.2, transparency: 'Staples demand and pricing power support consistency.', details: { company: 'Procter & Gamble', sector: 'Consumer Defensive', stability_note: 'Staples demand and pricing power support consistency.' } }
                    ],
                    momentum: [
                      { symbol: 'SOFI', price: 9.84, score: 9.4, rationale: 'Strong member growth and improving profitability trend.' },
                      { symbol: 'PLUG', price: 3.72, score: 9.12, rationale: 'Hydrogen adoption narrative and high-volume breakout watch.' },
                      { symbol: 'LCID', price: 2.98, score: 8.86, rationale: 'EV momentum setup with volatility-driven upside potential.' },
                      { symbol: 'NIO', price: 4.31, score: 8.58, rationale: 'Delivery trend stabilization and speculative rebound interest.' },
                      { symbol: 'RIOT', price: 11.42, score: 8.30, rationale: 'Bitcoin-linked momentum with expanding trading volume.' }
                    ],
                    wells: [
                      { investor: 'Warren Buffett', stocks: [{ ticker: 'AAPL', price: 191.22, performance: 1.42 }, { ticker: 'AXP', price: 227.13, performance: 0.81 }, { ticker: 'KO', price: 60.18, performance: 0.33 }, { ticker: 'OXY', price: 62.55, performance: -0.48 }, { ticker: 'BAC', price: 37.44, performance: 0.57 }] },
                      { investor: 'Cathie Wood', stocks: [{ ticker: 'TSLA', price: 178.44, performance: 2.38 }, { ticker: 'ROKU', price: 59.61, performance: 1.02 }, { ticker: 'COIN', price: 223.72, performance: -1.12 }, { ticker: 'SQ', price: 73.16, performance: 0.91 }, { ticker: 'CRSP', price: 61.2, performance: -0.33 }] }
                    ]
                  };

                  function formatMoney(v){ return typeof v === 'number' ? '$' + v.toLocaleString(undefined,{maximumFractionDigits:2}) : 'n/a'; }

                  const marketChartInstances = {};

                  function destroyMarketCharts(){
                    Object.values(marketChartInstances).forEach(chart => chart.destroy());
                    Object.keys(marketChartInstances).forEach(key => delete marketChartInstances[key]);
                  }

                  async function fetchWithFallback(url, key){
                    try {
                      const res = await fetch(url);
                      if (!res.ok) throw new Error('non-200');
                      const data = await res.json();
                      return data.items || fallbackData[key];
                    } catch (err) {
                      return fallbackData[key];
                    }
                  }

                  async function quickAsk(){
                    const symbol = (document.getElementById('quickSymbol').value || '').trim().toUpperCase();
                    const outEl = document.getElementById('quickOut');
                    if(!symbol){
                      outEl.textContent = 'Please enter a ticker symbol.';
                      return;
                    }
                    outEl.textContent = 'Analyzing...';
                    try {
                      const res = await fetch('/api/quick-ask?symbol=' + encodeURIComponent(symbol));
                      const payload = await res.json();
                      if(!res.ok){
                        outEl.textContent = payload.error || 'Unable to analyze this ticker right now.';
                        return;
                      }
                      const data = payload.data || {};
                      const isBuy = data.recommendation === 'BUY';
                      outEl.innerHTML = `<div style="margin-top:6px;padding:10px;border:1px solid #334155;border-radius:10px;background:#111827">
                        <div style="font-weight:700;color:${isBuy ? '#22c55e' : '#f87171'}">${data.recommendation}</div>
                        <div style="margin-top:2px">Price: ${formatMoney(data.current_price)} · Daily: ${(data.change_percent ?? 0).toFixed ? data.change_percent.toFixed(2) : data.change_percent}%</div>
                        <div style="margin-top:4px;color:#cbd5e1">${data.rationale}</div>
                      </div>`;
                    } catch (err) {
                      outEl.textContent = 'Request failed. Try again in a moment.';
                    }
                  }

                  function renderMarket(items){
                    const grid = document.getElementById('market-charts');
                    destroyMarketCharts();
                    grid.innerHTML = items.map((item, idx) => {
                      const up = (item.change_percent || 0) >= 0;
                      return `<article style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px">
                        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
                          <div>
                            <div style="font-weight:700">${item.name}</div>
                            <div style="font-size:12px;color:#64748b">${item.symbol}</div>
                          </div>
                          <div style="text-align:right">
                            <div style="font-size:18px">${formatMoney(item.price)}</div>
                            <div style="font-size:13px;color:${up ? '#166534' : '#b91c1c'}">${up ? '+' : ''}${Number(item.change_percent || 0).toFixed(2)}%</div>
                          </div>
                        </div>
                        <div style="margin-top:8px;height:120px"><canvas id="market-chart-${idx}"></canvas></div>
                      </article>`;
                    }).join('');

                    if (!window.Chart) return;
                    items.forEach((item, idx) => {
                      const up = (item.change_percent || 0) >= 0;
                      const ctx = document.getElementById(`market-chart-${idx}`);
                      if(!ctx) return;
                      marketChartInstances[idx] = new Chart(ctx, {
                        type: 'line',
                        data: {
                          labels: (item.series || []).map((_, i) => `${i + 1}`),
                          datasets: [{
                            data: item.series || [],
                            borderColor: up ? '#16a34a' : '#dc2626',
                            borderWidth: 2,
                            pointRadius: 0,
                            tension: 0.32,
                            fill: true,
                            backgroundColor: up ? 'rgba(22,163,74,0.14)' : 'rgba(220,38,38,0.12)',
                          }],
                        },
                        options: {
                          responsive: true,
                          maintainAspectRatio: false,
                          plugins: { legend: { display: false }, tooltip: { enabled: true, backgroundColor: '#0f172a', titleColor: '#fff', bodyColor: '#e2e8f0' } },
                          scales: {
                            x: { display: false, grid: { display: false } },
                            y: { display: false, grid: { color: '#eef2ff' } },
                          },
                        },
                      });
                    });
                  }

                  function showTickerDetails(item){
                    const modal = document.getElementById('tickerModal');
                    document.getElementById('modalTitle').textContent = `${item.symbol || item.ticker || 'Ticker'} · ${item.company || 'Company'}`;
                    const details = item.details || {};
                    document.getElementById('modalBody').innerHTML = `
                      <div><strong>Company:</strong> ${details.company || item.company || 'n/a'}</div>
                      <div><strong>Sector:</strong> ${details.sector || 'n/a'}</div>
                      <div><strong>Current Price:</strong> ${formatMoney(item.price)}</div>
                      <div><strong>${item.details && item.details.sector === 'Investor pick' ? 'Performance' : 'Score'}:</strong> ${item.signal_score ?? 'n/a'}</div>
                      <div style="margin-top:6px"><strong>Stability note:</strong> ${details.stability_note || item.transparency || 'n/a'}</div>
                    `;
                    modal.style.display = 'flex';
                  }

                  function closeTickerModal(){
                    document.getElementById('tickerModal').style.display = 'none';
                  }

                  function renderStable(items){
                    document.getElementById('stable').innerHTML = `<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;border-bottom:1px solid #e2e8f0;padding:8px">Ticker</th><th style="text-align:left;border-bottom:1px solid #e2e8f0;padding:8px">Price</th><th style="text-align:left;border-bottom:1px solid #e2e8f0;padding:8px">Score</th><th style="text-align:left;border-bottom:1px solid #e2e8f0;padding:8px">Transparency</th></tr></thead><tbody>${items.map((item, idx)=>`<tr><td style="padding:8px;border-bottom:1px solid #f1f5f9"><button type="button" data-idx="${idx}" class="ticker-detail-btn" style="border:none;background:none;color:#1d4ed8;font-weight:700;font-size:16px;line-height:1.2;cursor:pointer;padding:0">${item.symbol}</button></td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${formatMoney(item.price)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.signal_score}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.transparency || 'Stable profile with lower volatility characteristics.'}</td></tr>`).join('')}</tbody></table>`;
                    document.querySelectorAll('.ticker-detail-btn').forEach(btn => {
                      btn.addEventListener('click', () => {
                        const idx = Number(btn.dataset.idx || 0);
                        showTickerDetails(items[idx]);
                      });
                    });
                  }

                  function renderMomentum(items){
                    document.getElementById('momentum').innerHTML = `<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Score</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Transparency</th></tr></thead><tbody>${items.slice(0,10).map((item, idx)=>`<tr><td style="padding:8px;border-bottom:1px solid #f1f5f9"><button type="button" data-idx="${idx}" class="momentum-ticker-btn" style="border:none;background:none;color:#1d4ed8;font-weight:700;font-size:16px;line-height:1.2;cursor:pointer;padding:0">${item.symbol}</button></td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${formatMoney(item.price)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.score}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.rationale}</td></tr>`).join('')}</tbody></table>`;
                    document.querySelectorAll('.momentum-ticker-btn').forEach(btn => {
                      btn.addEventListener('click', () => {
                        const idx = Number(btn.dataset.idx || 0);
                        const item = items[idx] || {};
                        showTickerDetails({
                          symbol: item.symbol,
                          company: item.symbol,
                          price: item.price,
                          signal_score: item.score,
                          transparency: item.rationale,
                          details: item.details || { company: item.symbol, sector: 'Momentum', stability_note: item.rationale },
                        });
                      });
                    });
                  }

                  function renderWells(items){
                    document.getElementById('wells').innerHTML = items.map((item, investorIdx)=>`<article style="border:1px solid #e2e8f0;border-radius:10px;padding:10px;margin-bottom:10px"><div style="font-weight:700;margin-bottom:8px">${item.investor}</div><table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #e2e8f0">Performance</th></tr></thead><tbody>${(item.stocks || []).map((stock, stockIdx)=>`<tr><td style="padding:8px;border-bottom:1px solid #f1f5f9"><button type="button" data-investor="${investorIdx}" data-stock="${stockIdx}" class="wells-ticker-btn" style="border:none;background:none;color:#1d4ed8;font-weight:700;font-size:16px;line-height:1.2;cursor:pointer;padding:0">${stock.ticker}</button></td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${formatMoney(stock.price)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9;color:${(stock.performance || 0) >= 0 ? '#166534' : '#b91c1c'}">${Number(stock.performance || 0).toFixed(2)}%</td></tr>`).join('')}</tbody></table></article>`).join('');
                    document.querySelectorAll('.wells-ticker-btn').forEach(btn => {
                      btn.addEventListener('click', () => {
                        const investorIdx = Number(btn.dataset.investor || 0);
                        const stockIdx = Number(btn.dataset.stock || 0);
                        const investor = items[investorIdx] || {};
                        const stock = (investor.stocks || [])[stockIdx] || {};
                        showTickerDetails({
                          symbol: stock.ticker,
                          company: stock.ticker,
                          price: stock.price,
                          signal_score: stock.performance,
                          transparency: `Top holding from ${investor.investor || 'investor'} list.`,
                          details: { company: stock.ticker, sector: 'Investor pick', stability_note: `Selected by ${investor.investor || 'top investor'}` },
                        });
                      });
                    });
                  }

                  function switchTab(tab){
                    document.querySelectorAll('.tab-panel').forEach(panel => panel.style.display = panel.id === tab ? 'block' : 'none');
                    document.querySelectorAll('.tab-btn').forEach(btn => btn.style.background = btn.dataset.tab === tab ? '#dbeafe' : '#fff');
                  }

                  document.getElementById('tickerModal').addEventListener('click', (event) => {
                    if (event.target.id === 'tickerModal') closeTickerModal();
                  });

                  document.getElementById('quickSymbol').addEventListener('keydown', (event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault();
                      quickAsk();
                    }
                  });

                  async function init(){
                    const [market, stable, momentum, wells] = await Promise.all([
                      fetchWithFallback('/api/market-overview', 'market'),
                      fetchWithFallback('/api/stable-watchlist', 'stable'),
                      fetchWithFallback('/api/hot-momentum-buys', 'momentum'),
                      fetchWithFallback('/api/wells-picks', 'wells'),
                    ]);
                    renderMarket(market);
                    renderStable(stable);
                    renderMomentum(momentum);
                    renderWells(wells);
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
              <pre id="out"></pre>
              <script>
              const emailEl = document.getElementById('email');
              const passwordEl = document.getElementById('password');
              const outEl = document.getElementById('out');
              document.getElementById('loginForm').addEventListener('submit', go);

              async function go(event){
                if (event) event.preventDefault();
                const res = await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:emailEl.value,password:passwordEl.value})});
                const data = await res.json();
                outEl.textContent = JSON.stringify(data,null,2);
                if(res.ok) location.href='/watchlist';
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
              <pre id="out"></pre>
              <script>
              const emailEl = document.getElementById('email');
              const passwordEl = document.getElementById('password');
              const outEl = document.getElementById('out');
              document.getElementById('signupForm').addEventListener('submit', go);

              async function go(event){
                if (event) event.preventDefault();
                const res = await fetch('/api/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:emailEl.value,password:passwordEl.value})});
                const data = await res.json();
                outEl.textContent = JSON.stringify(data,null,2);
                if(res.ok) location.href='/watchlist';
              }
              </script>
            </body></html>
            """
        )

    @app.get("/watchlist")
    @app.get("/watchlist/")
    def watchlist_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc;max-width:960px;margin:0 auto">
              <h2>User Watchlist</h2>
              <p><a href="/">Home</a> · <button onclick="logout()">Logout</button></p>
              <form id="addForm">
                <input id="symbol" placeholder="AAPL" required />
                <input id="buy_price" type="number" step="0.01" placeholder="buy price"/>
                <input id="shares" type="number" step="0.0001" placeholder="shares"/>
                <button type="submit">Add</button>
              </form>
              <pre id="out"></pre>
              <table style="width:100%;background:#fff;border-collapse:collapse">
                <thead><tr><th style="border:1px solid #e5e7eb;padding:8px">Symbol</th><th style="border:1px solid #e5e7eb;padding:8px">Entry</th><th style="border:1px solid #e5e7eb;padding:8px">Shares</th><th style="border:1px solid #e5e7eb;padding:8px">Current Price</th><th style="border:1px solid #e5e7eb;padding:8px">Performance</th><th style="border:1px solid #e5e7eb;padding:8px">Advice</th><th style="border:1px solid #e5e7eb;padding:8px">Score</th><th style="border:1px solid #e5e7eb;padding:8px">Why</th><th style="border:1px solid #e5e7eb;padding:8px">Action</th></tr></thead>
                <tbody id="rows"></tbody>
              </table>
              <script>
              const rowsEl = document.getElementById('rows');
              const outEl = document.getElementById('out');
              const symbolEl = document.getElementById('symbol');
              const buyPriceEl = document.getElementById('buy_price');
              const sharesEl = document.getElementById('shares');
              document.getElementById('addForm').addEventListener('submit', addItem);

              async function logout(){ await fetch('/api/auth/logout',{method:'POST'}); location.href='/'; }
              async function load(){
                const res = await fetch('/api/user-watchlist');
                const data = await res.json();
                if(!res.ok){
                  if (res.status === 401) { location.href='/login'; return; }
                  rowsEl.innerHTML = '<pre>'+JSON.stringify(data,null,2)+'</pre>';
                  return;
                }
                const formatMoney = (v) => typeof v === 'number' ? `$${v.toLocaleString(undefined,{maximumFractionDigits:2})}` : 'n/a';
                rowsEl.innerHTML = (data.items||[]).map(i=>{
                  const perfPct = typeof i.performance === 'number' ? `${i.performance.toFixed(2)}%` : 'n/a';
                  const perfAbs = typeof i.performance_amount === 'number' ? `${i.performance_amount >= 0 ? '+' : '-'}${formatMoney(Math.abs(i.performance_amount))}` : 'n/a';
                  const perfColor = typeof i.performance === 'number' ? (i.performance >= 0 ? '#166534' : '#b91c1c') : '#334155';
                  return `<tr>
                    <td style="border:1px solid #e5e7eb;padding:8px">${i.symbol}</td>
                    <td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(i.entry_price)}</td>
                    <td style="border:1px solid #e5e7eb;padding:8px">${i.shares ?? 'n/a'}</td>
                    <td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(i.current_price)}</td>
                    <td style="border:1px solid #e5e7eb;padding:8px;color:${perfColor}">${perfAbs} (${perfPct})</td>
                    <td style="border:1px solid #e5e7eb;padding:8px">${i.advice ?? 'HOLD'}</td>
                    <td style="border:1px solid #e5e7eb;padding:8px">${typeof i.score === 'number' ? i.score.toFixed(2) : 'n/a'}</td>
                    <td style="border:1px solid #e5e7eb;padding:8px;max-width:280px">${i.why ?? 'n/a'}</td>
                    <td style="border:1px solid #e5e7eb;padding:8px"><button onclick="del(${i.id})">Remove</button></td>
                  </tr>`;
                }).join('');
              }
              async function addItem(event){
                if (event) event.preventDefault();
                const res = await fetch('/api/user-watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:symbolEl.value,buy_price:buyPriceEl.value||null,shares:sharesEl.value||null})});
                const data = await res.json();
                outEl.textContent = JSON.stringify(data,null,2);
                if (res.ok) {
                  symbolEl.value='';
                  await load();
                }
              }
              async function del(id){ await fetch('/api/user-watchlist/'+id,{method:'DELETE'}); await load(); }
              load();
              </script>
            </body></html>
            """
        )

    return app
