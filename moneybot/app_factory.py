from __future__ import annotations

import importlib.util
import logging
import os

from flask import Flask, render_template_string
from flask_cors import CORS
from .api import api_bp
from .extensions import db, migrate
from .services.ai_advisor import AIAdvisorService
from .services.decision_log import DecisionLogger
from .services.deterministic_advisor import DeterministicQuickAdvisor
from .services.market_data import MarketDataService


def _resolve_database_url() -> str:
    # Prefer explicit DATABASE_URL, but support common provider aliases used on hosted platforms.
    raw_database_url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_INTERNAL_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("POSTGRESQL_URL")
    )
    database_url = (raw_database_url or "").strip() or "sqlite:///moneybot.db"

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    # Fail fast on hosted deployments so we do not silently deploy with non-persistent auth/portfolio storage.
    is_hosted = os.environ.get("RENDER") == "true" or os.environ.get("FLASK_ENV") == "production"

    # Pick an installed PostgreSQL DBAPI when the URL does not pin one.
    if database_url.startswith("postgresql://") and "+" not in database_url.split("://", 1)[0]:
        has_psycopg = importlib.util.find_spec("psycopg") is not None
        has_psycopg2 = importlib.util.find_spec("psycopg2") is not None
        if has_psycopg:
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        elif not has_psycopg2:
            msg = (
                "DATABASE_URL points to PostgreSQL but no PostgreSQL driver is installed. "
                "Install psycopg[binary] or psycopg2-binary in the build command."
            )
            if is_hosted:
                raise RuntimeError(msg)
            logging.error(
                "%s Falling back to local SQLite for local/dev only; data will not persist.",
                msg,
            )
            database_url = "sqlite:///moneybot.db"

    if database_url.startswith("sqlite") and is_hosted:
        raise RuntimeError(
            "No persistent PostgreSQL database is configured for production. "
            "Set DATABASE_URL (or POSTGRES_INTERNAL_URL/POSTGRES_URL) and ensure a PostgreSQL driver is installed."
        )

    if " " in database_url or "://" not in database_url:
        raise RuntimeError(
            "DATABASE_URL is not a valid database URL. "
            "Set DATABASE_URL to a valid value such as "
            "postgresql://user:password@host:5432/dbname."
        )

    return database_url


def create_app() -> Flask:
    secret = os.environ.get("MONEYBOT_SECRET_KEY")
    if not secret:
        logging.warning(
            "MONEYBOT_SECRET_KEY is not set. Using an insecure fallback key; set MONEYBOT_SECRET_KEY in production."
        )
        secret = "moneybot-insecure-fallback-key"

    database_url = _resolve_database_url()

    app = Flask(__name__)
    app.url_map.strict_slashes = False
    app.config.update(
        SECRET_KEY=secret,
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DATA_PROVIDER=os.environ.get("DATA_PROVIDER", "yfinance"),
        PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", ""),
        SMTP_HOST=os.environ.get("SMTP_HOST", ""),
        SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
        SMTP_USER=os.environ.get("SMTP_USER", ""),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD", ""),
        SMTP_USE_TLS=(os.environ.get("SMTP_USE_TLS", "true").lower() == "true"),
        SMTP_USE_SSL=(os.environ.get("SMTP_USE_SSL", "false").lower() == "true"),
        PASSWORD_RESET_FROM_EMAIL=os.environ.get("PASSWORD_RESET_FROM_EMAIL", os.environ.get("SMTP_USER", "")),
        PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS=int(os.environ.get("PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS", "3600")),
        AI_ENABLED=(os.environ.get("AI_ENABLED", "false").lower() == "true"),
        AI_PROVIDER=os.environ.get("AI_PROVIDER", "openai"),
        AI_MODEL=os.environ.get("AI_MODEL", "gpt-5-mini"),
        AI_API_KEY=os.environ.get("AI_API_KEY", ""),
        AI_TIMEOUT_SECONDS=float(os.environ.get("AI_TIMEOUT_SECONDS", "6.0")),
        AI_FAILURE_COOLDOWN_SECONDS=int(os.environ.get("AI_FAILURE_COOLDOWN_SECONDS", "120")),
        AI_RESPONSE_CACHE_TTL_SECONDS=int(os.environ.get("AI_RESPONSE_CACHE_TTL_SECONDS", "300")),
        DETERMINISTIC_QUICK_ENABLED=(os.environ.get("DETERMINISTIC_QUICK_ENABLED", "true").lower() == "true"),
        DETERMINISTIC_MODEL_PATH=os.environ.get("DETERMINISTIC_MODEL_PATH", "data/day1_baseline_model.json"),
        DETERMINISTIC_MOMENTUM_ENABLED=(os.environ.get("DETERMINISTIC_MOMENTUM_ENABLED", "true").lower() == "true"),
        DECISION_LOGGING_ENABLED=(os.environ.get("DECISION_LOGGING_ENABLED", "true").lower() == "true"),
        DECISION_LOG_PATH=os.environ.get("DECISION_LOG_PATH", "data/decision_events.jsonl"),
    )

    app.extensions["ai_advisor_service"] = AIAdvisorService(
        enabled=app.config["AI_ENABLED"],
        provider=app.config["AI_PROVIDER"],
        model=app.config["AI_MODEL"],
        api_key=app.config["AI_API_KEY"],
        timeout_s=app.config["AI_TIMEOUT_SECONDS"],
        failure_cooldown_s=app.config["AI_FAILURE_COOLDOWN_SECONDS"],
        cache_ttl_s=app.config["AI_RESPONSE_CACHE_TTL_SECONDS"],
    )
    app.extensions["deterministic_quick_advisor"] = DeterministicQuickAdvisor(
        enabled=app.config["DETERMINISTIC_QUICK_ENABLED"],
        artifact_path=app.config["DETERMINISTIC_MODEL_PATH"],
    )
    app.extensions["decision_logger"] = DecisionLogger(
        enabled=app.config["DECISION_LOGGING_ENABLED"],
        output_path=app.config["DECISION_LOG_PATH"],
    )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    CORS(app)
    db.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401

    app.register_blueprint(api_bp)
    app.extensions["market_data_service"] = MarketDataService(
        deterministic_quick_advisor=app.extensions["deterministic_quick_advisor"],
        deterministic_momentum_enabled=app.config["DETERMINISTIC_MOMENTUM_ENABLED"],
    )

    with app.app_context():
        db.create_all()

    @app.get("/")
    @app.get("/index.html")
    @app.get("/home")
    def home():
        return render_template_string(
            """
            <html>
              <body style="font-family:Inter,Segoe UI,system-ui,sans-serif;padding:24px;background:linear-gradient(180deg,#f7fee7,#ecfdf3);max-width:1120px;margin:0 auto;color:#0f172a">
                <header style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:14px">
                  <div style="flex:1;min-width:280px">
                    <img src="/static/moneybot-pro-logo.svg" alt="MoneyBot Pro logo" style="display:block;width:100%;max-width:860px;height:auto"/>
                  </div>
                  <div style="display:flex;gap:10px;flex-wrap:wrap">
                    <a href="/login" style="padding:8px 12px;background:#dcfce7;color:#000;border-radius:999px;text-decoration:none;font-weight:600">Login</a>
                    <a href="/signup" style="padding:8px 12px;background:#dcfce7;color:#000;border-radius:999px;text-decoration:none;font-weight:600">Sign up</a>
                    <a href="/portfolio" style="padding:8px 12px;background:#166534;color:#f0fdf4;border-radius:999px;text-decoration:none;font-weight:700">User Portfolio</a>
                  </div>
                </header>

                <section style="background:#000;color:#d1fae5;border-radius:14px;padding:16px;margin-bottom:18px;box-shadow:0 10px 24px rgba(2,6,23,.18)">
                  <h3 style="margin:0 0 10px 0;color:#f0fdf4">Quick Ask · What should I do now?</h3>
                  <div style="display:grid;grid-template-columns:minmax(300px,430px) minmax(300px,1fr);gap:12px;align-items:start">
                    <div>
                      <div style="display:flex;gap:8px;flex-wrap:wrap">
                        <input id="quickSymbol" placeholder="Ticker (e.g. AAPL)" style="padding:10px 12px;border:1px solid #166534;border-radius:10px;min-width:210px;background:#000;color:#f7fee7;font-size:1.15rem;font-weight:700;letter-spacing:.01em"/>
                        <button id="quickAskBtn" onclick="quickAsk()" style="padding:10px 16px;border:none;background:#16a34a;color:#f0fdf4;border-radius:10px;font-weight:700;font-size:1.08rem">Analyze</button>
                      </div>
                      <div id="quickOut" style="margin-top:10px;color:#bbf7d0">Type a ticker to get an instant STRONG BUY / BUY / HOLD OFF FOR NOW call.</div>
                      <div id="quickLoading" style="display:none;align-items:center;gap:8px;margin-top:10px;color:#86efac;font-weight:600">
                        <span style="display:inline-block;width:14px;height:14px;border:2px solid #86efac;border-top-color:#22c55e;border-radius:9999px;animation:spin .7s linear infinite"></span>
                        Analyzing signal...
                      </div>
                    </div>
                    <div id="quickAdvice" style="min-height:72px;background:#052e16;border:1px solid #14532d;border-radius:10px;padding:10px 12px;color:#dcfce7">
                      <strong style="display:block;color:#bbf7d0;margin-bottom:4px">AI key points</strong>
                      <span style="color:#86efac">Advice will appear here after you analyze a ticker.</span>
                    </div>
                  </div>
                </section>

                <section style="margin-bottom:18px">
                  <h3 style="margin-bottom:8px">Market Indices</h3>
                  <div id="market-charts" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px"></div>
                </section>

                <section style="background:#f7fee7;border:1px solid #bbf7d0;border-radius:12px;padding:14px;margin-bottom:12px">
                  <h3 style="margin:0 0 8px 0">Buyer's Guide</h3>
                  <p style="margin:0 0 6px 0;color:#166534"><strong>Stable Watchlist:</strong> Lower risk, long-term stocks.</p>
                  <p style="margin:0 0 6px 0;color:#166534"><strong>Hot Momentum:</strong> Higher risk, low-price stocks with growth potential.</p>
                  <p style="margin:0;color:#166534"><strong>Whales of Wall Street:</strong> See and follow top investors' picks.</p>
                </section>

                <section style="background:#f0fdf4;border:1px solid #d1fae5;border-radius:12px;padding:16px">
                  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
                    <button class="tab-btn" data-tab="stable" onclick="switchTab('stable')" style="padding:9px 14px;border:1px solid #bbf7d0;background:#dcfce7;border-radius:8px;font-size:1.06rem;font-weight:700">Stable Watchlist</button>
                    <button class="tab-btn" data-tab="momentum" onclick="switchTab('momentum')" style="padding:9px 14px;border:1px solid #bbf7d0;background:#f0fdf4;border-radius:8px;font-size:1.06rem;font-weight:700">Hot Momentum Buys</button>
                    <button class="tab-btn" data-tab="wells" onclick="switchTab('wells')" style="padding:9px 14px;border:1px solid #bbf7d0;background:#f0fdf4;border-radius:8px;font-size:1.06rem;font-weight:700">Whales of Wall Street</button>
                  </div>
                  <div id="tabLoading" style="display:none;align-items:center;gap:10px;margin-bottom:10px;color:#166534;font-weight:600">
                    <span style="display:inline-block;width:16px;height:16px;border:2px solid #86efac;border-top-color:#16a34a;border-radius:9999px;animation:spin .7s linear infinite"></span>
                    Loading table...
                  </div>
                  <div id="stable" class="tab-panel"></div>
                  <div id="momentum" class="tab-panel" style="display:none"></div>
                  <div id="wells" class="tab-panel" style="display:none"></div>
                </section>

                <div id="homeTickerModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50;align-items:center;justify-content:center;padding:14px">
                  <div style="background:#f0fdf4;border-radius:12px;max-width:680px;width:100%;max-height:80vh;overflow:auto;padding:14px">
                    <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
                      <h3 id="homeModalTitle" style="margin:0">Company Details</h3>
                      <button onclick="closeHomeModal()" style="border:none;background:#d1fae5;border-radius:8px;padding:6px 10px">Close</button>
                    </div>
                    <p id="homeModalSummary" style="color:#166534"></p>
                  </div>
                </div>

                <p style="color:#3f6212">Rule-based guidance; not financial advice.</p>

                <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
                <style>@keyframes spin { to { transform: rotate(360deg); } }</style>
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
                  function escapeHtml(value){
                    return String(value || '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch] || ch));
                  }
                  function quickRecommendationBadge(recommendation){
                    const rec = String(recommendation || 'HOLD OFF FOR NOW').toUpperCase();
                    const color = rec === 'STRONG BUY' ? '#22c55e' : (rec === 'BUY' ? '#166534' : '#dc2626');
                    return `<span style="display:inline-block;padding:4px 10px;border-radius:999px;background:${color};color:#f0fdf4;font-weight:800;font-size:12px;letter-spacing:.02em">${rec}</span>`;
                  }
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
                    const inputEl = document.getElementById('quickSymbol');
                    const symbol = (inputEl.value || '').trim().toUpperCase();
                    const outEl = document.getElementById('quickOut');
                    const adviceEl = document.getElementById('quickAdvice');
                    const loadingEl = document.getElementById('quickLoading');
                    const quickAskBtn = document.getElementById('quickAskBtn');
                    inputEl.blur();
                    if(!symbol){ outEl.textContent='Please enter a ticker symbol.'; return; }

                    loadingEl.style.display = 'flex';
                    outEl.style.display = 'none';
                    quickAskBtn.disabled = true;
                    quickAskBtn.style.opacity = '.75';
                    quickAskBtn.style.cursor = 'wait';
                    adviceEl.innerHTML = `<strong style="display:block;color:#bbf7d0;margin-bottom:4px">AI key points</strong><span style="color:#86efac">Analyzing ${escapeHtml(symbol)}...</span>`;

                    try {
                      const res = await fetch('/api/quick-ask?symbol=' + encodeURIComponent(symbol));
                      const payload = await res.json();
                      if(!res.ok){
                        outEl.textContent = payload.error || 'Unable to analyze this ticker.';
                        adviceEl.innerHTML = `<strong style="display:block;color:#bbf7d0;margin-bottom:4px">AI key points</strong><span style="color:#fca5a5">Unable to load assistant notes right now.</span>`;
                        return;
                      }
                      const data = payload.data || {};
                      const recommendation = String(data.recommendation || 'HOLD OFF FOR NOW').toUpperCase();
                      outEl.innerHTML = `${quickRecommendationBadge(recommendation)} <span style="margin-left:8px">· ${formatMoney(data.current_price)} · ${data.rationale || 'Signal generated from current indicators.'}</span>`;

                      const ai = data.ai || {};
                      const narrative = ai.narrative || data.rationale || 'No AI narrative available.';
                      const riskNotes = Array.isArray(ai.risk_notes) ? ai.risk_notes : [];
                      const nextChecks = Array.isArray(ai.next_checks) ? ai.next_checks : [];
                      const topRisk = riskNotes[0] || 'Keep strict risk controls and position sizing.';
                      const topCheck = nextChecks[0] || 'Recheck momentum and volume before changing size.';
                      adviceEl.innerHTML = `
                        <strong style="display:block;color:#bbf7d0;margin-bottom:6px">${escapeHtml(symbol)} · ${escapeHtml((ai.mode || data.ai_mode || 'rule_based').replaceAll('_',' '))}</strong>
                        <ul style="margin:0;padding-left:18px;display:grid;gap:4px;color:#dcfce7">
                          <li>${escapeHtml(narrative)}</li>
                          <li><strong>Risk:</strong> ${escapeHtml(topRisk)}</li>
                          <li><strong>Next:</strong> ${escapeHtml(topCheck)}</li>
                        </ul>`;
                    } catch (err) {
                      outEl.textContent = 'Unable to analyze this ticker.';
                      adviceEl.innerHTML = `<strong style="display:block;color:#bbf7d0;margin-bottom:4px">AI key points</strong><span style="color:#fca5a5">Network issue while loading assistant notes.</span>`;
                    } finally {
                      loadingEl.style.display = 'none';
                      outEl.style.display = 'block';
                      quickAskBtn.disabled = false;
                      quickAskBtn.style.opacity = '1';
                      quickAskBtn.style.cursor = 'pointer';
                    }
                  }

                  function tickerButton(symbol){
                    return `<button onclick="showCompanyDetails('${symbol}')" style="border:none;background:none;color:#15803d;font-weight:700;cursor:pointer;font-size:14px;padding:0">${symbol}</button>`;
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
                        const err = String(payload.error || '');
                        summaryEl.textContent = err === 'authentication required' ? 'Please log in to view company details.' : (payload.error || 'Unable to load company details.');
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
                      return `<article style="background:#000;border:1px solid #166534;border-radius:12px;padding:12px">
                        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
                          <div><div style="font-weight:700;color:#f0fdf4">${item.name}</div><div style="font-size:12px;color:#d1fae5">${item.symbol}</div></div>
                          <div style="text-align:right"><div style="font-size:18px;color:#f0fdf4">${formatMoney(item.price)}</div><div style="font-size:13px;color:${up ? '#22c55e' : '#dc2626'}">${up ? '+' : ''}${Number(item.change_percent || 0).toFixed(2)}%</div></div>
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
                        data:{labels:(item.series||[]).map((_,i)=>`${i+1}`),datasets:[{data:item.series||[],borderColor:up?'#16a34a':'#dc2626',borderWidth:2,pointRadius:0,tension:.32,fill:true,backgroundColor:up?'rgba(22,163,74,.18)':'rgba(220,38,38,.16)'}]},
                        options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{enabled:true}},scales:{x:{display:false},y:{display:false}}}
                      });
                    });
                  }

                  function renderStable(items){
                    document.getElementById('stable').innerHTML = `<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Score</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Transparency</th></tr></thead><tbody>${items.map(item=>`<tr><td style="padding:8px;border-bottom:1px solid #dcfce7">${tickerButton(item.symbol)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${formatMoney(item.price)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.signal_score}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.transparency || ''}</td></tr>`).join('')}</tbody></table>`;
                  }

                  function renderMomentum(items){
                    document.getElementById('momentum').innerHTML = `<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Score</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Source</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Transparency</th></tr></thead><tbody>${items.map(item=>`<tr><td style="padding:8px;border-bottom:1px solid #dcfce7">${tickerButton(item.symbol)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${formatMoney(item.price)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.score}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.decision_source || 'rule_based'}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.rationale}</td></tr>`).join('')}</tbody></table>`;
                  }

                  function renderWells(items){
                    document.getElementById('wells').innerHTML = items.map(item=>`<article style="border:1px solid #d1fae5;border-radius:10px;padding:10px;margin-bottom:10px"><div style="font-weight:700;margin-bottom:8px">${item.investor}</div><table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Performance</th></tr></thead><tbody>${(item.stocks||[]).map(stock=>`<tr><td style="padding:8px;border-bottom:1px solid #dcfce7">${tickerButton(stock.ticker)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${formatMoney(stock.price)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${Number(stock.performance||0).toFixed(2)}%</td></tr>`).join('')}</tbody></table></article>`).join('');
                  }

                  function setTabLoading(isLoading){
                    const loadingEl = document.getElementById('tabLoading');
                    if(!loadingEl) return;
                    loadingEl.style.display = isLoading ? 'flex' : 'none';
                  }

                  async function refreshTab(tab){
                    setTabLoading(true);
                    try {
                      if(tab === 'stable'){
                        const stable = await fetchWithFallback('/api/stable-watchlist', 'stable');
                        renderStable(stable);
                      } else if(tab === 'momentum'){
                        const momentum = await fetchWithFallback('/api/hot-momentum-buys', 'momentum');
                        renderMomentum(momentum);
                      } else if(tab === 'wells'){
                        const wells = await fetchWithFallback('/api/wells-picks', 'wells');
                        renderWells(wells);
                      }
                    } finally {
                      setTabLoading(false);
                    }
                  }

                  function switchTab(tab){
                    document.querySelectorAll('.tab-panel').forEach(panel => panel.style.display = panel.id === tab ? 'block' : 'none');
                    document.querySelectorAll('.tab-btn').forEach(btn => btn.style.background = btn.dataset.tab === tab ? '#bbf7d0' : '#f0fdf4');
                    refreshTab(tab);
                  }

                  document.getElementById('quickSymbol').addEventListener('keydown', (event) => { if(event.key==='Enter'){event.preventDefault();quickAsk();} });
                  document.getElementById('quickSymbol').addEventListener('focus', (event) => {
                    if(event.target.value){ event.target.value = ''; }
                  });
                  document.getElementById('homeTickerModal').addEventListener('click', (event) => { if(event.target.id==='homeTickerModal'){ closeHomeModal(); }});

                  async function init(){
                    const market = await fetchWithFallback('/api/market-overview', 'market');
                    renderMarket(market);
                    await refreshTab('stable');
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
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;display:flex;align-items:center;justify-content:center;background:#f7fee7;padding:24px;box-sizing:border-box">
              <div style="width:100%;max-width:520px;background:#f0fdf4;padding:34px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08)">
                <h2 style="font-size:2.2rem;margin:0 0 18px;text-align:center">Login</h2>
                <p style="display:flex;justify-content:center;gap:10px;margin:0 0 18px">
                  <a href="/" style="text-decoration:none;background:#dcfce7;color:#14532d;padding:10px 16px;border-radius:999px;font-size:1.05rem;font-weight:600">Home</a>
                  <a href="/signup" style="text-decoration:none;background:#d1fae5;color:#0f172a;padding:10px 16px;border-radius:999px;font-size:1.05rem;font-weight:600">Create account</a>
                </p>
                <form id="loginForm" style="display:flex;flex-direction:column;gap:12px">
                  <input id="email" placeholder="email" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="password" type="password" placeholder="password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <button type="button" onclick="forgotPassword()" style="align-self:flex-start;border:none;background:none;color:#15803d;padding:0 2px;font-size:0.95rem;font-weight:600;cursor:pointer;text-decoration:underline">Forgot Password?</button>
                  <button type="submit" style="font-size:1.08rem;padding:12px;border:none;border-radius:10px;background:#16a34a;color:#f0fdf4;font-weight:700;cursor:pointer">Login</button>
                </form>
                <div id="out" style="margin-top:12px;color:#166534;text-align:center;font-size:1.02rem"></div>
              </div>
              <script>
              const emailEl = document.getElementById('email');
              const passwordEl = document.getElementById('password');
              const outEl = document.getElementById('out');
              const TAB_SESSION_KEY = 'moneybot_tab_session_id';
              function getOrCreateTabSessionId(){
                let tabSessionId = sessionStorage.getItem(TAB_SESSION_KEY);
                if(!tabSessionId){
                  tabSessionId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
                  sessionStorage.setItem(TAB_SESSION_KEY, tabSessionId);
                }
                return tabSessionId;
              }
              document.getElementById('loginForm').addEventListener('submit', go);

              async function go(event){
                if (event) event.preventDefault();
                const res = await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:emailEl.value,password:passwordEl.value,tab_session_id:getOrCreateTabSessionId()})});
                const data = await res.json();
                if(res.ok){ outEl.textContent='Login successful. Redirecting...'; location.href='/portfolio'; }
                else { outEl.textContent = data.error || 'Login failed. Please verify your credentials.'; }
              }

              async function forgotPassword(){
                const email = (emailEl.value || '').trim();
                if(!email){
                  outEl.textContent = 'Enter your email first, then click Forgot Password.';
                  return;
                }
                const res = await fetch('/api/auth/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
                const data = await res.json();
                if (data && data.email_delivery_configured === false) {
                  outEl.textContent = 'Password recovery email service is not configured yet. Please contact support or try again later.';
                  return;
                }
                outEl.textContent = data.message || data.error || 'Unable to start password recovery right now.';
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
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;display:flex;align-items:center;justify-content:center;background:#f7fee7;padding:24px;box-sizing:border-box">
              <div style="width:100%;max-width:520px;background:#f0fdf4;padding:34px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08)">
                <h2 style="font-size:2.2rem;margin:0 0 18px;text-align:center">Sign Up</h2>
                <p style="display:flex;justify-content:center;gap:10px;margin:0 0 18px">
                  <a href="/" style="text-decoration:none;background:#dcfce7;color:#14532d;padding:10px 16px;border-radius:999px;font-size:1.05rem;font-weight:600">Home</a>
                  <a href="/login" style="text-decoration:none;background:#d1fae5;color:#0f172a;padding:10px 16px;border-radius:999px;font-size:1.05rem;font-weight:600">Login</a>
                </p>
                <form id="signupForm" style="display:flex;flex-direction:column;gap:12px">
                  <input id="email" placeholder="email" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="password" type="password" placeholder="password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="confirmPassword" type="password" placeholder="confirm password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <button type="submit" style="font-size:1.08rem;padding:12px;border:none;border-radius:10px;background:#16a34a;color:#f0fdf4;font-weight:700;cursor:pointer">Create</button>
                </form>
                <div id="out" style="margin-top:12px;color:#166534;text-align:center;font-size:1.02rem"></div>
              </div>
              <script>
              const emailEl = document.getElementById('email');
              const passwordEl = document.getElementById('password');
              const confirmPasswordEl = document.getElementById('confirmPassword');
              const outEl = document.getElementById('out');
              const TAB_SESSION_KEY = 'moneybot_tab_session_id';
              function getOrCreateTabSessionId(){
                let tabSessionId = sessionStorage.getItem(TAB_SESSION_KEY);
                if(!tabSessionId){
                  tabSessionId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
                  sessionStorage.setItem(TAB_SESSION_KEY, tabSessionId);
                }
                return tabSessionId;
              }
              document.getElementById('signupForm').addEventListener('submit', go);

              async function go(event){
                if (event) event.preventDefault();
                if(passwordEl.value !== confirmPasswordEl.value){
                  outEl.textContent = 'Passwords do not match.';
                  return;
                }
                const res = await fetch('/api/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:emailEl.value,password:passwordEl.value,password_confirmation:confirmPasswordEl.value,tab_session_id:getOrCreateTabSessionId()})});
                const data = await res.json();
                if(res.ok){ outEl.textContent='Account created. Redirecting...'; location.href='/portfolio'; }
                else { outEl.textContent = data.error || 'Sign-up failed. Please try again.'; }
              }
              </script>
            </body></html>
            """
        )



    @app.get("/reset-password")
    @app.get("/reset-password/")
    def reset_password_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;min-height:100vh;margin:0;display:flex;align-items:center;justify-content:center;background:#f7fee7;padding:24px;box-sizing:border-box">
              <div style="width:100%;max-width:520px;background:#f0fdf4;padding:34px;border-radius:14px;box-shadow:0 10px 28px rgba(15,23,42,.08)">
                <h2 style="font-size:2rem;margin:0 0 18px;text-align:center">Reset Password</h2>
                <form id="resetForm" style="display:flex;flex-direction:column;gap:12px">
                  <input id="password" type="password" placeholder="new password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <input id="confirmPassword" type="password" placeholder="confirm new password" required style="font-size:1.08rem;padding:12px;border:1px solid #bbf7d0;border-radius:10px" />
                  <button type="submit" style="font-size:1.08rem;padding:12px;border:none;border-radius:10px;background:#16a34a;color:#f0fdf4;font-weight:700;cursor:pointer">Update Password</button>
                </form>
                <div id="out" style="margin-top:12px;color:#166534;text-align:center;font-size:1.02rem"></div>
                <p style="margin-top:14px;text-align:center"><a href="/login" style="color:#15803d;font-weight:600">Back to login</a></p>
              </div>
              <script>
                const passwordEl = document.getElementById('password');
                const confirmPasswordEl = document.getElementById('confirmPassword');
                const outEl = document.getElementById('out');
                const params = new URLSearchParams(window.location.search);
                const token = params.get('token') || '';
                document.getElementById('resetForm').addEventListener('submit', async (event) => {
                  event.preventDefault();
                  if(!token){ outEl.textContent='Reset link is invalid.'; return; }
                  if(passwordEl.value !== confirmPasswordEl.value){ outEl.textContent='Passwords do not match.'; return; }
                  const res = await fetch('/api/auth/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token, password:passwordEl.value})});
                  const data = await res.json();
                  if(res.ok){ outEl.textContent='Password updated. Redirecting to login...'; setTimeout(()=>{ location.href='/login'; }, 900); }
                  else { outEl.textContent = data.error || 'Unable to reset password.'; }
                });
              </script>
            </body></html>
            """
        )

    @app.get("/portfolio")
    @app.get("/portfolio/")
    def portfolio_page():
        return render_template_string(
            """
            <html><body style="font-family:Inter,sans-serif;padding:24px;background:#f7fee7;max-width:1100px;margin:0 auto">
              <h2>User Portfolio</h2>
              <p style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <a href="/" style="text-decoration:none;background:#dcfce7;color:#14532d;padding:12px 18px;border-radius:999px;font-size:1.08rem;font-weight:700">Home</a>
                <button onclick="logout()" style="border:none;background:#166534;color:#f0fdf4;padding:12px 18px;border-radius:999px;font-size:1.08rem;font-weight:700;cursor:pointer">Logout</button>
              </p>
              <form id="addForm">
                <input id="symbol" placeholder="AAPL" required />
                <input id="buy_price" type="number" step="0.01" placeholder="buy price"/>
                <input id="shares" type="number" step="0.0001" placeholder="shares"/>
                <button type="submit" style="border:none;background:#16a34a;color:#f0fdf4;padding:9px 14px;border-radius:8px;font-weight:700;cursor:pointer">Add</button>
              </form>
              <div id="out" style="margin:10px 0;color:#166534"></div>
              <div id="loadingState" style="display:none;align-items:center;gap:10px;margin:12px 0;color:#14532d;font-weight:600">
                <span style="width:16px;height:16px;border:2px solid #86efac;border-top-color:#16a34a;border-radius:999px;display:inline-block;animation:spin .8s linear infinite"></span>
                Loading latest portfolio stock data...
              </div>
              <button id="toggleLifetimeBtn" onclick="toggleLifetime()" style="border:none;background:#14532d;color:#f0fdf4;padding:9px 14px;border-radius:8px;font-weight:700;cursor:pointer;margin-bottom:10px">Show Lifetime Gains/Losses</button>
              <div id="lifetimePanel" style="display:none;background:#ecfccb;border:1px solid #d9f99d;border-radius:10px;padding:12px;margin-bottom:12px">
                <div style="font-weight:700;margin-bottom:8px">Lifetime Realized Gains/Losses: <span id="lifetimeTotal">$0.00</span></div>
                <div style="overflow-x:auto"><table style="width:100%;background:#f0fdf4;border-collapse:collapse;min-width:640px">
                  <thead><tr><th style="border:1px solid #e5e7eb;padding:8px">Sold At</th><th style="border:1px solid #e5e7eb;padding:8px">Symbol</th><th style="border:1px solid #e5e7eb;padding:8px">Entry</th><th style="border:1px solid #e5e7eb;padding:8px">Sold Price</th><th style="border:1px solid #e5e7eb;padding:8px">Shares Sold</th><th style="border:1px solid #e5e7eb;padding:8px">Realized</th></tr></thead>
                  <tbody id="soldRows"><tr><td colspan="6" style="padding:8px;color:#3f6212">No sold trades yet.</td></tr></tbody>
                </table></div>
              </div>
              <div style="overflow-x:auto"><table style="width:100%;background:#f0fdf4;border-collapse:collapse;min-width:980px">
                <thead><tr><th style="border:1px solid #e5e7eb;padding:8px">Symbol</th><th style="border:1px solid #e5e7eb;padding:8px">Entry</th><th style="border:1px solid #e5e7eb;padding:8px">Shares</th><th style="border:1px solid #e5e7eb;padding:8px">Current Price</th><th style="border:1px solid #e5e7eb;padding:8px">Today's Gain/Loss</th><th style="border:1px solid #e5e7eb;padding:8px">Performance</th><th style="border:1px solid #e5e7eb;padding:8px">Trend</th><th style="border:1px solid #e5e7eb;padding:8px">Score</th><th style="border:1px solid #e5e7eb;padding:8px">Sentiment</th><th style="border:1px solid #e5e7eb;padding:8px">Advice</th><th style="border:1px solid #e5e7eb;padding:8px">Action</th></tr></thead>
                <tbody id="rows"></tbody>
              </table></div>
              <div id="tickerModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50;align-items:center;justify-content:center;padding:14px">
                <div style="background:#f0fdf4;border-radius:12px;max-width:680px;width:100%;max-height:80vh;overflow:auto;padding:14px">
                  <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
                    <h3 id="modalTitle" style="margin:0">Company Details</h3>
                    <button onclick="closeModal()" style="border:none;background:#d1fae5;border-radius:8px;padding:6px 10px">Close</button>
                  </div>
                  <p id="modalSummary" style="color:#166534"></p>
                  <div id="modalNews" style="display:grid;gap:8px"></div>
                </div>
              </div>
              <div id="adviceModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:51;align-items:center;justify-content:center;padding:14px">
                <div style="background:#f0fdf4;border-radius:12px;max-width:520px;width:100%;padding:14px">
                  <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
                    <h3 id="adviceTitle" style="margin:0">Advice Reasoning</h3>
                    <button onclick="closeAdviceModal()" style="border:none;background:#d1fae5;border-radius:8px;padding:6px 10px">Close</button>
                  </div>
                  <div id="adviceReason" style="color:#dcfce7;margin-top:10px;background:#14532d;border:1px solid #166534;border-radius:10px;padding:10px">
                    <strong style="display:block;color:#bbf7d0;margin-bottom:6px">AI key points</strong>
                    <ul style="margin:0;padding-left:18px;display:grid;gap:4px">
                      <li style="color:#dcfce7">No reasoning available.</li>
                    </ul>
                  </div>
                  <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                    <button id="plainEnglishBtn" onclick="explainAdviceInPlainEnglish()" style="border:none;background:#16a34a;color:#f0fdf4;padding:7px 10px;border-radius:8px;font-weight:700;cursor:pointer">Explain this recommendation in plain English</button>
                    <span id="plainEnglishLoading" style="display:none;color:#3f6212;font-size:13px">Explaining...</span>
                  </div>
                  <p id="plainEnglishExplanation" style="display:none;color:#14532d;margin-top:10px;background:#ecfccb;border:1px solid #bef264;border-radius:8px;padding:8px"></p>
                  <div style="margin-top:12px">
                    <div style="font-size:12px;color:#3f6212;font-weight:700;letter-spacing:.02em;text-transform:uppercase">Latest Headlines</div>
                    <div id="adviceHeadlines" style="display:grid;gap:8px;margin-top:8px"></div>
                  </div>
                </div>
              </div>
              <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
              <script>
              const styleTag = document.createElement('style');
              styleTag.textContent = '@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }';
              document.head.appendChild(styleTag);

              const TAB_SESSION_KEY = 'moneybot_tab_session_id';
              function getTabSessionId(){
                return sessionStorage.getItem(TAB_SESSION_KEY) || '';
              }
              async function apiFetch(url, options = {}){
                const tabSessionId = getTabSessionId();
                if(!tabSessionId){
                  location.href = '/login';
                  throw new Error('missing tab session');
                }
                const headers = Object.assign({}, options.headers || {}, {'X-Tab-Session-Id': tabSessionId});
                const response = await fetch(url, Object.assign({}, options, {headers}));
                if(response.status === 401){
                  sessionStorage.removeItem(TAB_SESSION_KEY);
                  location.href = '/login';
                }
                return response;
              }

              const rowsEl = document.getElementById('rows');
              const soldRowsEl = document.getElementById('soldRows');
              const lifetimePanelEl = document.getElementById('lifetimePanel');
              const lifetimeTotalEl = document.getElementById('lifetimeTotal');
              const toggleLifetimeBtnEl = document.getElementById('toggleLifetimeBtn');
              const outEl = document.getElementById('out');
              const loadingStateEl = document.getElementById('loadingState');
              const symbolEl = document.getElementById('symbol');
              const buyPriceEl = document.getElementById('buy_price');
              const sharesEl = document.getElementById('shares');
              let currentPortfolioItems = [];
              let currentAdviceContext = null;
              document.getElementById('addForm').addEventListener('submit', addItem);

              async function logout(){ await apiFetch('/api/auth/logout',{method:'POST'}); sessionStorage.removeItem(TAB_SESSION_KEY); location.href='/'; }
              function setLoading(isLoading){ loadingStateEl.style.display = isLoading ? 'flex' : 'none'; }
              function displayValue(value){
                return (value === null || value === undefined || value === '') ? 'n/a' : value;
              }
              function formatMoney(v){
                return (typeof v === 'number' && isFinite(v)) ? ('$' + v.toLocaleString(undefined,{maximumFractionDigits:2})) : 'n/a';
              }
              function sentimentBadge(value){
                const sentiment = String(value || 'Neutral').toLowerCase();
                if(sentiment === 'bullish' || sentiment === 'positive') return '<span style="color:#166534;font-weight:700;white-space:nowrap">▇ Bullish</span>';
                if(sentiment === 'bearish' || sentiment === 'negative') return '<span style="color:#4d7c0f;font-weight:700;white-space:nowrap">▇ Bearish</span>';
                return '<span style="color:#3f3f46;font-weight:600;white-space:nowrap">▇ Neutral</span>';
              }
              function adviceBadge(value){
                const advice = String(value || 'HOLD').toUpperCase();
                const color = advice === 'BUY' ? '#166534' : (advice === 'SELL' ? '#4d7c0f' : '#3f3f46');
                return `<span style="display:inline-block;padding:4px 8px;border-radius:999px;background:${color};color:#f0fdf4;font-weight:700;font-size:12px">${advice}</span>`;
              }
              function adviceButton(item, idx){
                return `<button onclick="showAdvice(${idx})" title="Click to see why this advice was generated" style="border:none;background:none;padding:0;cursor:pointer">${adviceBadge(item.advice)}</button>`;
              }
              function escapeHtml(value){
                return String(value || '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch] || ch));
              }
              function openAdviceModal(){ document.getElementById('adviceModal').style.display='flex'; }
              function closeAdviceModal(){ document.getElementById('adviceModal').style.display='none'; }
              async function showAdvice(idx){
                const item = currentPortfolioItems[idx] || {};
                const symbol = item.symbol || '';
                const advice = String(item.advice || 'HOLD').toUpperCase();
                const reason = item.advice_reason || 'Rule-based recommendation from technical momentum and sentiment checks.';
                const aiPortfolio = (item && typeof item.ai_portfolio === 'object' && item.ai_portfolio) ? item.ai_portfolio : {};
                const mode = String(aiPortfolio.mode || 'rule_based').replaceAll('_', ' ');
                const riskNotes = Array.isArray(aiPortfolio.risk_notes) ? aiPortfolio.risk_notes : [];
                const nextChecks = Array.isArray(aiPortfolio.next_checks) ? aiPortfolio.next_checks : [];
                const topRisk = riskNotes[0] || 'Keep strict risk controls and position sizing.';
                const topCheck = nextChecks[0] || 'Recheck trend and sentiment before changing your position size.';
                currentAdviceContext = { symbol, advice, reason };
                document.getElementById('adviceTitle').textContent = `${symbol} · ${advice} rationale`;
                document.getElementById('adviceReason').innerHTML = `
                  <strong style="display:block;color:#bbf7d0;margin-bottom:6px">${escapeHtml(symbol)} · ${escapeHtml(mode)}</strong>
                  <ul style="margin:0;padding-left:18px;display:grid;gap:4px;color:#dcfce7">
                    <li>${escapeHtml(reason)}</li>
                    <li><strong>Risk:</strong> ${escapeHtml(topRisk)}</li>
                    <li><strong>Next:</strong> ${escapeHtml(topCheck)}</li>
                  </ul>`;
                const plainEnglishEl = document.getElementById('plainEnglishExplanation');
                plainEnglishEl.style.display = 'block';
                plainEnglishEl.textContent = buildPlainEnglishExplanation(advice, reason);
                const headlinesEl = document.getElementById('adviceHeadlines');
                headlinesEl.innerHTML = '<div style="color:#3f6212">Loading latest headlines...</div>';
                openAdviceModal();
                if(!symbol){
                  headlinesEl.innerHTML = '<div style="color:#3f6212">No recent headlines available.</div>';
                  return;
                }
                try {
                  const res = await apiFetch('/api/company-details?symbol=' + encodeURIComponent(symbol));
                  const payload = await res.json();
                  if(!res.ok){
                    if (res.status === 401) { location.href='/login'; return; }
                    headlinesEl.innerHTML = '<div style="color:#3f6212">No recent headlines available.</div>';
                    return;
                  }
                  const news = (payload.data && payload.data.latest_news) || [];
                  headlinesEl.innerHTML = news.length ? news.map(n => `<a href="${n.link || '#'}" target="_blank" rel="noopener" style="display:block;padding:8px;border:1px solid #d1fae5;border-radius:8px;text-decoration:none;color:#0f172a"><div style="font-weight:600">${n.title || 'Story'}</div><div style="font-size:12px;color:#3f6212">${n.publisher || 'Source unavailable'}</div></a>`).join('') : '<div style="color:#3f6212">No recent headlines available.</div>';
                } catch (err) {
                  headlinesEl.innerHTML = '<div style="color:#3f6212">Unable to load headlines right now.</div>';
                }
              }
              function performanceCell(amount, pct){
                if(typeof amount !== 'number' || typeof pct !== 'number') return '<span style="color:#3f6212">n/a</span>';
                const up = amount >= 0;
                const color = up ? '#166534' : '#dc2626';
                const sign = up ? '+' : '';
                return `<div style="color:${color};font-weight:700">${sign}${formatMoney(amount)}</div><div style="color:${color};font-size:12px">(${sign}${pct.toFixed(2)}%)</div>`;
              }
              function amountCell(amount){
                if(typeof amount !== 'number') return '<span style="color:#3f6212">n/a</span>';
                const up = amount >= 0;
                const color = up ? '#166534' : '#dc2626';
                const sign = up ? '+' : '';
                return `<div style="color:${color};font-weight:700">${sign}${formatMoney(amount)}</div>`;
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
                return `<button onclick="showCompanyDetails('${symbol}')" style="border:none;background:none;color:#15803d;font-weight:700;cursor:pointer;font-size:15px;padding:0">${symbol}</button>`;
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
                  const res = await apiFetch('/api/company-details?symbol=' + encodeURIComponent(symbol));
                  const payload = await res.json();
                  if(!res.ok){
                    if (res.status === 401) { location.href='/login'; return; }
                    titleEl.textContent = symbol;
                    const err = String(payload.error || '');
                    summaryEl.textContent = err === 'authentication required' ? 'Please log in to view company details.' : (payload.error || 'Unable to load company details.');
                    return;
                  }
                  const data = payload.data || {};
                  titleEl.textContent = `${data.company_name || symbol} (${symbol})`;
                  summaryEl.textContent = data.summary || 'No summary available.';
                  const news = data.latest_news || [];
                  newsEl.innerHTML = news.length ? news.map(n => `<a href="${n.link || '#'}" target="_blank" rel="noopener" style="display:block;padding:8px;border:1px solid #d1fae5;border-radius:8px;text-decoration:none;color:#0f172a"><div style="font-weight:600">${n.title || 'Story'}</div><div style="font-size:12px;color:#3f6212">${n.publisher || 'Source unavailable'}</div></a>`).join('') : '<div style="color:#3f6212">No recent news available.</div>';
                } catch (err) {
                  titleEl.textContent = symbol;
                  summaryEl.textContent = 'Unable to load company details right now.';
                }
              }

              function humanizeReason(reason){
                const text = String(reason || 'signals are mixed').trim();
                return text
                  .replace(/MACD/gi, 'trend momentum')
                  .replace(/RSI/gi, 'price pressure')
                  .replace(/hist/gi, 'trend strength')
                  .replace(/\bpts\b/gi, 'points')
                  .replace(/bullish/gi, 'positive')
                  .replace(/bearish/gi, 'negative');
              }

              function buildPlainEnglishExplanation(advice, reason){
                const rec = String(advice || 'HOLD').toUpperCase();
                const friendlyReason = humanizeReason(reason).toLowerCase();
                let action = 'There is no clear edge right now, so holding is safer';
                if(rec === 'STRONG BUY') action = 'This looks like a strong buying setup';
                else if(rec === 'BUY') action = 'This looks reasonable to buy';
                else if(rec === 'SELL') action = 'This looks like a good time to trim or sell';
                else if(rec === 'HOLD OFF FOR NOW') action = 'It is better to wait instead of buying right now';
                return `${action}. The system saw ${friendlyReason}. This is guidance only, not financial advice.`;
              }

              function explainAdviceInPlainEnglish(){
                const loadingEl = document.getElementById('plainEnglishLoading');
                const explanationEl = document.getElementById('plainEnglishExplanation');
                if(!currentAdviceContext){
                  explanationEl.style.display = 'block';
                  explanationEl.textContent = 'Open an advice card first.';
                  return;
                }
                loadingEl.style.display = 'inline';
                explanationEl.style.display = 'block';
                explanationEl.textContent = buildPlainEnglishExplanation(currentAdviceContext.advice, currentAdviceContext.reason);
                loadingEl.style.display = 'none';
              }

              function renderRows(items){
                if(!items || !items.length){
                  rowsEl.innerHTML = '<tr><td colspan="11" style="padding:8px;color:#3f6212">No watchlist entries yet.</td></tr>';
                  currentPortfolioItems = [];
                  return;
                }
                currentPortfolioItems = items;
                const totalValue = items.reduce((sum, item) => {
                  const price = typeof item.current_price === 'number' ? item.current_price : 0;
                  const shares = typeof item.shares === 'number' ? item.shares : 1;
                  return sum + (price * shares);
                }, 0);
                const totalTodayChange = items.reduce((sum, item) => sum + (typeof item.today_change_amount === 'number' ? item.today_change_amount : 0), 0);
                const totalPerformance = items.reduce((sum, item) => sum + (typeof item.performance_amount === 'number' ? item.performance_amount : 0), 0);

                rowsEl.innerHTML = items.map((i,idx)=>`<tr><td style="border:1px solid #e5e7eb;padding:8px;font-size:15px">${tickerButton(i.symbol)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(i.entry_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(i.shares)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(i.current_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${performanceCell(i.today_change_amount, i.today_change_percent)}</td><td style="border:1px solid #e5e7eb;padding:8px">${performanceCell(i.performance_amount, i.performance_percent)}</td><td style="border:1px solid #e5e7eb;padding:8px"><div id="trend-${idx}" style="width:100px;height:30px"></div></td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(i.score)}</td><td style="border:1px solid #e5e7eb;padding:8px">${sentimentBadge(i.sentiment)}</td><td style="border:1px solid #e5e7eb;padding:8px">${adviceButton(i, idx)}</td><td style="border:1px solid #e5e7eb;padding:8px"><div style="display:flex;gap:6px;flex-wrap:wrap"><button onclick="markSold(${i.id})" style="border:none;background:#15803d;color:#f0fdf4;padding:6px 10px;border-radius:8px;font-weight:600;cursor:pointer">Sold</button><button onclick="del(${i.id})" style="border:none;background:#65a30d;color:#f0fdf4;padding:6px 10px;border-radius:8px;font-weight:600;cursor:pointer">Remove</button></div></td></tr>`).join('')
                + `<tr style="background:#f7fee7;font-weight:700"><td style="border:1px solid #e5e7eb;padding:8px">Totals</td><td style="border:1px solid #e5e7eb;padding:8px"></td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(totalValue)}</td><td style="border:1px solid #e5e7eb;padding:8px"></td><td style="border:1px solid #e5e7eb;padding:8px">${amountCell(totalTodayChange)}</td><td style="border:1px solid #e5e7eb;padding:8px">${amountCell(totalPerformance)}</td><td style="border:1px solid #e5e7eb;padding:8px"></td><td style="border:1px solid #e5e7eb;padding:8px"></td><td style="border:1px solid #e5e7eb;padding:8px"></td><td style="border:1px solid #e5e7eb;padding:8px;color:#3f3f46;font-size:12px">Click advice badges to see why.</td><td style="border:1px solid #e5e7eb;padding:8px"></td></tr>`;
                items.forEach((item, idx)=> renderTrend(`trend-${idx}`, item.history30 || []));
              }

              async function load(){
                setLoading(true);
                try {
                  const res = await apiFetch('/api/user-watchlist');
                  const data = await res.json();
                  if(!res.ok){
                    if (res.status === 401) { location.href='/login'; return; }
                    rowsEl.innerHTML = '<tr><td colspan="11" style="padding:8px;color:#4d7c0f">Unable to load watchlist right now.</td></tr>';
                    outEl.textContent = data.error || 'Please try again in a moment.';
                    return;
                  }
                  renderRows(data.enriched_items || data.items || []);
                  if (lifetimePanelEl.style.display !== 'none') {
                    await loadSoldTrades();
                  }
                } finally {
                  setLoading(false);
                }
              }
              async function addItem(event){
                if (event) event.preventDefault();
                const payload = { symbol:(symbolEl.value || '').trim().toUpperCase(), buy_price:buyPriceEl.value||null, shares:sharesEl.value||null };
                const res = await apiFetch('/api/user-watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                const data = await res.json();
                if (res.ok) {
                  outEl.textContent = 'Watchlist item added.';
                  symbolEl.value=''; buyPriceEl.value=''; sharesEl.value='';
                  await load();
                } else {
                  outEl.textContent = data.error || 'Unable to add item.';
                }
              }


              function amountColor(v){
                if (typeof v !== 'number') return '#3f3f46';
                if (v > 0) return '#166534';
                if (v < 0) return '#b91c1c';
                return '#3f3f46';
              }

              function formatDate(iso){
                if(!iso) return 'n/a';
                const d = new Date(iso);
                return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
              }

              function renderSoldTrades(items, totalRealized){
                lifetimeTotalEl.textContent = formatMoney(totalRealized || 0);
                lifetimeTotalEl.style.color = amountColor(totalRealized);
                if(!items || !items.length){
                  soldRowsEl.innerHTML = '<tr><td colspan="6" style="padding:8px;color:#3f6212">No sold trades yet.</td></tr>';
                  return;
                }
                soldRowsEl.innerHTML = items.map((item)=>`<tr><td style="border:1px solid #e5e7eb;padding:8px">${formatDate(item.sold_at)}</td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(item.symbol)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(item.entry_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${formatMoney(item.sold_price)}</td><td style="border:1px solid #e5e7eb;padding:8px">${displayValue(item.shares_sold)}</td><td style="border:1px solid #e5e7eb;padding:8px;color:${amountColor(item.realized_amount)}">${formatMoney(item.realized_amount)}</td></tr>`).join('');
              }

              async function loadSoldTrades(){
                const res = await apiFetch('/api/sold-trades');
                const data = await res.json();
                if(!res.ok){
                  if (res.status === 401) { location.href='/login'; return; }
                  outEl.textContent = data.error || 'Unable to load sold trades.';
                  return;
                }
                renderSoldTrades(data.items || [], data.total_realized || 0);
              }

              function toggleLifetime(){
                const isOpen = lifetimePanelEl.style.display !== 'none';
                if(isOpen){
                  lifetimePanelEl.style.display = 'none';
                  toggleLifetimeBtnEl.textContent = 'Show Lifetime Gains/Losses';
                } else {
                  lifetimePanelEl.style.display = 'block';
                  toggleLifetimeBtnEl.textContent = 'Hide Lifetime Gains/Losses';
                  loadSoldTrades();
                }
              }

              async function markSold(id){
                const item = currentPortfolioItems.find((entry)=> entry.id === id);
                if(!item){
                  outEl.textContent = 'Unable to find portfolio item.';
                  return;
                }
                const soldPriceRaw = prompt(`What price did you sell ${item.symbol} at?`);
                if(soldPriceRaw === null) return;
                const soldPrice = Number(soldPriceRaw);
                if(!Number.isFinite(soldPrice) || soldPrice <= 0){
                  outEl.textContent = 'Sold price must be a positive number.';
                  return;
                }

                const sharesRaw = prompt(`How many shares of ${item.symbol} did you sell? (Current: ${displayValue(item.shares)})`);
                if(sharesRaw === null) return;
                const sharesSold = Number(sharesRaw);
                if(!Number.isFinite(sharesSold) || sharesSold <= 0){
                  outEl.textContent = 'Shares sold must be a positive number.';
                  return;
                }

                const res = await apiFetch('/api/user-watchlist/' + id + '/sell', {
                  method:'POST',
                  headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({ sold_price:soldPrice, shares_sold:sharesSold })
                });
                const data = await res.json();
                if(!res.ok){
                  outEl.textContent = data.error || 'Unable to record sold trade.';
                  return;
                }
                const realized = data.sold_trade && typeof data.sold_trade.realized_amount === 'number' ? data.sold_trade.realized_amount : 0;
                outEl.textContent = `Sold trade recorded (${formatMoney(realized)} realized).`;
                await load();
                if (lifetimePanelEl.style.display !== 'none') {
                  await loadSoldTrades();
                }
              }

              async function del(id){ await apiFetch('/api/user-watchlist/'+id,{method:'DELETE'}); await load(); }
              document.getElementById('tickerModal').addEventListener('click', (event) => { if(event.target.id==='tickerModal'){ closeModal(); }});
              document.getElementById('adviceModal').addEventListener('click', (event) => { if(event.target.id==='adviceModal'){ closeAdviceModal(); }});
              load();
              </script>
            </body></html>
            """
        )

    return app
