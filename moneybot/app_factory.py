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
    def home():
        return render_template_string(
            """
            <html>
              <body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc;max-width:1100px;margin:0 auto;color:#0f172a">
                <h1 style="margin-bottom:6px">MoneyBot Dashboard</h1>
                <p style="margin-top:0;color:#475569">Market pulse, curated ideas, and investor-inspired picks.</p>

                <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px">
                  <a href="/login" style="padding:8px 12px;background:#dbeafe;border-radius:8px;text-decoration:none">Login</a>
                  <a href="/signup" style="padding:8px 12px;background:#dbeafe;border-radius:8px;text-decoration:none">Sign up</a>
                  <a href="/watchlist" style="padding:8px 12px;background:#1e40af;color:#fff;border-radius:8px;text-decoration:none">My Watchlist</a>
                </div>

                <section style="margin-bottom:18px">
                  <h3 style="margin-bottom:8px">Market Indices</h3>
                  <div id="market-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px"></div>
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

                <p style="color:#64748b">Rule-based guidance; not financial advice.</p>

                <script>
                  const fallbackData = {
                    market: [
                      { name: 'Dow Jones', symbol: '^DJI', price: 39180.42, change_percent: 0.63, series: [38750,38840,38920,39015,39180] },
                      { name: 'S&P 500', symbol: '^GSPC', price: 5224.67, change_percent: 0.48, series: [5140,5168,5182,5201,5224] },
                      { name: 'Bitcoin', symbol: 'BTC-USD', price: 61225.11, change_percent: -1.07, series: [62980,62310,61920,61540,61225] }
                    ],
                    stable: [
                      { symbol: 'MSFT', company: 'Microsoft', price: 418.33, signal_score: 7.9 },
                      { symbol: 'JNJ', company: 'Johnson & Johnson', price: 154.62, signal_score: 7.55 },
                      { symbol: 'PG', company: 'Procter & Gamble', price: 168.44, signal_score: 7.2 }
                    ],
                    momentum: [
                      { symbol: 'NVDA', price: 902.11, score: 9.4, rationale: 'AI demand and earnings revisions remain strong.' },
                      { symbol: 'AMD', price: 182.42, score: 9.12, rationale: 'Chip momentum and improving gross margin profile.' }
                    ],
                    wells: [
                      { investor: 'Warren Buffett', top_stocks: ['AAPL', 'AXP', 'KO', 'OXY', 'BAC'] },
                      { investor: 'Cathie Wood', top_stocks: ['TSLA', 'ROKU', 'COIN', 'SQ', 'CRSP'] }
                    ]
                  };

                  function formatMoney(v){ return typeof v === 'number' ? '$' + v.toLocaleString(undefined,{maximumFractionDigits:2}) : 'n/a'; }

                  function sparkline(points, color){
                    if(!points || !points.length) return '';
                    const max = Math.max(...points);
                    const min = Math.min(...points);
                    const range = max - min || 1;
                    const width = 180;
                    const height = 44;
                    const step = points.length > 1 ? width / (points.length - 1) : width;
                    const coords = points.map((p,i)=>`${(i*step).toFixed(1)},${(height-((p-min)/range)*height).toFixed(1)}`).join(' ');
                    return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><polyline fill="none" stroke="${color}" stroke-width="2" points="${coords}"/></svg>`;
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

                  function renderMarket(items){
                    const grid = document.getElementById('market-grid');
                    grid.innerHTML = items.map(item => {
                      const up = (item.change_percent || 0) >= 0;
                      return `<article style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px">
                        <div style="font-weight:600">${item.name}</div>
                        <div style="font-size:13px;color:#64748b">${item.symbol}</div>
                        <div style="font-size:22px;margin:8px 0">${formatMoney(item.price)}</div>
                        <div style="font-size:14px;color:${up ? '#166534' : '#b91c1c'}">${up ? '+' : ''}${Number(item.change_percent || 0).toFixed(2)}%</div>
                        <div>${sparkline(item.series, up ? '#16a34a' : '#dc2626')}</div>
                      </article>`;
                    }).join('');
                  }

                  function renderStable(items){
                    document.getElementById('stable').innerHTML = `<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;border-bottom:1px solid #e2e8f0;padding:8px">Symbol</th><th style="text-align:left;border-bottom:1px solid #e2e8f0;padding:8px">Company</th><th style="text-align:left;border-bottom:1px solid #e2e8f0;padding:8px">Current Price</th><th style="text-align:left;border-bottom:1px solid #e2e8f0;padding:8px">Signal Score</th></tr></thead><tbody>${items.map(item=>`<tr><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.symbol}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.company}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${formatMoney(item.price)}</td><td style="padding:8px;border-bottom:1px solid #f1f5f9">${item.signal_score}</td></tr>`).join('')}</tbody></table>`;
                  }

                  function renderMomentum(items){
                    document.getElementById('momentum').innerHTML = `<ol style="margin:0;padding-left:18px">${items.slice(0,10).map(item=>`<li style="margin-bottom:10px"><strong>${item.symbol}</strong> · ${formatMoney(item.price)} · score ${item.score}<br/><span style="color:#475569">${item.rationale}</span></li>`).join('')}</ol>`;
                  }

                  function renderWells(items){
                    document.getElementById('wells').innerHTML = items.map(item=>`<article style="border:1px solid #e2e8f0;border-radius:10px;padding:10px;margin-bottom:10px"><div style="font-weight:600">${item.investor}</div><div style="color:#475569">Top 5: ${item.top_stocks.join(', ')}</div></article>`).join('');
                  }

                  function switchTab(tab){
                    document.querySelectorAll('.tab-panel').forEach(panel => panel.style.display = panel.id === tab ? 'block' : 'none');
                    document.querySelectorAll('.tab-btn').forEach(btn => btn.style.background = btn.dataset.tab === tab ? '#dbeafe' : '#fff');
                  }

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
                <thead><tr><th style="border:1px solid #e5e7eb;padding:8px">Symbol</th><th style="border:1px solid #e5e7eb;padding:8px">Entry</th><th style="border:1px solid #e5e7eb;padding:8px">Shares</th><th style="border:1px solid #e5e7eb;padding:8px">Action</th></tr></thead>
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
                rowsEl.innerHTML = (data.items||[]).map(i=>`<tr><td style="border:1px solid #e5e7eb;padding:8px">${i.symbol}</td><td style="border:1px solid #e5e7eb;padding:8px">${i.entry_price ?? 'n/a'}</td><td style="border:1px solid #e5e7eb;padding:8px">${i.shares ?? 'n/a'}</td><td style="border:1px solid #e5e7eb;padding:8px"><button onclick="del(${i.id})">Remove</button></td></tr>`).join('');
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
