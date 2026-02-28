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
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc;max-width:960px;margin:0 auto">
              <h1 style="margin-bottom:6px">MoneyBot</h1>
              <p style="margin-top:0;color:#475569">Hybrid stock assistant with watchlist tracking, signals, and API-backed auth.</p>
              <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px">
                <a href="/login" style="padding:8px 12px;background:#dbeafe;border-radius:8px;text-decoration:none">Login</a>
                <a href="/signup" style="padding:8px 12px;background:#dbeafe;border-radius:8px;text-decoration:none">Sign up</a>
                <a href="/watchlist" style="padding:8px 12px;background:#1e40af;color:#fff;border-radius:8px;text-decoration:none">My Watchlist</a>
              </div>
              <div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px">
                <h3 style="margin-top:0">Quick Quote</h3>
                <input id="symbol" placeholder="AAPL" style="padding:8px;border:1px solid #cbd5e1;border-radius:8px"/>
                <button onclick="lookup()" style="padding:8px 12px;border:none;background:#1e40af;color:#fff;border-radius:8px">Lookup</button>
                <div id="out" style="background:#0f172a;color:#e2e8f0;padding:10px;border-radius:8px;min-height:56px">Enter a ticker to see a quick signal.</div>
              </div>
              <p style="color:#64748b">Rule-based guidance; not financial advice.</p>
              <script>
              async function lookup(){
                const symbol = (document.getElementById('symbol').value || '').trim().toUpperCase();
                if(!symbol) return;
                const res = await fetch('/api/signal?symbol=' + encodeURIComponent(symbol));
                const data = await res.json();
                const signal = data.data || {};
                const quote = signal.quote || {};
                const price = typeof quote.price === 'number' ? `$${quote.price.toFixed(2)}` : 'n/a';
                const action = signal.action || 'HOLD';
                const reason = (signal.rationale && signal.rationale[0]) || 'Signal generated from latest indicators.';
                document.getElementById('out').textContent = `${action} · ${price} · ${reason}`;
              }
              </script>
            </body></html>
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
                if(res.ok){ outEl.textContent='Login successful. Redirecting...'; location.href='/watchlist'; }
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
                if(res.ok){ outEl.textContent='Account created. Redirecting...'; location.href='/watchlist'; }
                else { outEl.textContent = data.error || 'Sign-up failed. Please try again.'; }
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
              <div id="out" style="margin:10px 0;color:#334155"></div>
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
                  rowsEl.innerHTML = '<tr><td colspan="4" style="padding:8px;color:#b91c1c">Unable to load watchlist right now.</td></tr>';
                  outEl.textContent = data.error || 'Please try again in a moment.';
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
                if (res.ok) {
                  outEl.textContent = 'Watchlist item added.';
                  symbolEl.value='';
                  await load();
                } else {
                  outEl.textContent = data.error || 'Unable to add item.';
                }
              }
              async function del(id){ await fetch('/api/user-watchlist/'+id,{method:'DELETE'}); await load(); }
              load();
              </script>
            </body></html>
            """
        )

    return app
