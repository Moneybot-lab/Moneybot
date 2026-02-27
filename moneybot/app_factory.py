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

    @app.get("/")
    @app.get("")
    def home():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc">
              <h1>MoneyBot</h1>
              <p>Rule-based guidance; not financial advice.</p>
              <p><a href="/login">Login</a> · <a href="/signup">Sign up</a> · <a href="/watchlist">My Watchlist</a></p>
            </body></html>
            """
        )

    @app.get("/login")
    @app.get("/login/")
    def login_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc">
              <h2>Login</h2>
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
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc">
              <h2>Sign Up</h2>
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
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f8fafc">
              <h2>User Watchlist</h2>
              <p><button onclick="logout()">Logout</button></p>
              <form id="addForm">
                <input id="symbol" placeholder="AAPL" required />
                <input id="buy_price" type="number" step="0.01" placeholder="buy price"/>
                <input id="shares" type="number" step="0.0001" placeholder="shares"/>
                <button type="submit">Add</button>
              </form>
              <pre id="out"></pre>
              <div id="rows"></div>
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
                rowsEl.innerHTML = (data.items||[]).map(i=>`<div>${i.symbol} | entry: ${i.entry_price ?? 'n/a'} | shares: ${i.shares ?? 'n/a'} <button onclick="del(${i.id})">x</button></div>`).join('');
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
