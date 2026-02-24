import logging
import os
import secrets

from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('MONEYBOT_SECRET_KEY', secrets.token_hex(32))
CORS(app)

logging.basicConfig(level=logging.INFO)

QUOTE_FALLBACK = {"price": "DATA_MISSING", "change_percent": "DATA_MISSING"}

LONG_TERM_WATCHLIST = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("GOOGL", "Alphabet"),
    ("AMZN", "Amazon"),
    ("JNJ", "Johnson & Johnson"),
    ("PG", "Procter & Gamble"),
    ("KO", "Coca-Cola"),
    ("V", "Visa"),
]

HOT_WATCHLIST_CANDIDATES = [
    ("SOFI", "SoFi Technologies"),
    ("RKLB", "Rocket Lab"),
    ("PLTR", "Palantir"),
    ("SNAP", "Snap"),
    ("F", "Ford"),
    ("PFE", "Pfizer"),
    ("NIO", "NIO"),
    ("LCID", "Lucid Group"),
    ("HOOD", "Robinhood"),
    ("RIVN", "Rivian"),
    ("ACHR", "Archer Aviation"),
    ("JOBY", "Joby Aviation"),
]

DEFAULT_HOT_WATCHLIST = HOT_WATCHLIST_CANDIDATES[:8]

POSITIVE_WORDS = {
    "beat", "beats", "surge", "surges", "growth", "upgrade", "upgrades", "strong",
    "bullish", "record", "profit", "profits", "outperform", "expands", "expansion",
    "partnership", "launch", "wins", "demand", "momentum", "innovation",
}

NEGATIVE_WORDS = {
    "miss", "misses", "downgrade", "downgrades", "weak", "weakness", "lawsuit",
    "probe", "decline", "falls", "drop", "drops", "bearish", "loss", "losses",
    "cuts", "cut", "slowdown", "risk", "warning", "volatile", "regulatory",
}


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_valid_symbol(symbol):
    return bool(symbol) and all(ch.isalnum() or ch in {'.', '-', '^'} for ch in symbol)


def get_quote_data(symbol):
    ticker = yf.Ticker(symbol)
    info = {}

    try:
        info = ticker.info or {}
    except Exception as error:
        logging.warning("Ticker info unavailable for %s: %s", symbol, error)

    price = (
        info.get('regularMarketPrice')
        or info.get('currentPrice')
        or info.get('regularMarketPreviousClose')
        or info.get('previousClose')
    )
    previous_close = info.get('regularMarketPreviousClose') or info.get('previousClose')
    change_percent = info.get('regularMarketChangePercent')

    if price is None or previous_close is None or change_percent is None:
        try:
            history = ticker.history(period='5d', interval='1d')
            if not history.empty:
                latest_close = history['Close'].iloc[-1]
                prev_close = history['Close'].iloc[-2] if len(history.index) > 1 else None
                if price is None:
                    price = latest_close
                if previous_close is None and prev_close is not None:
                    previous_close = prev_close
                if change_percent is None and prev_close not in (None, 0):
                    change_percent = ((latest_close - prev_close) / prev_close) * 100
        except Exception as error:
            logging.warning("Price history unavailable for %s: %s", symbol, error)

    if change_percent is None and price is not None and previous_close not in (None, 0):
        change_percent = ((price - previous_close) / previous_close) * 100

    return {
        "price": _to_float(price) if price is not None else "DATA_MISSING",
        "change_percent": _to_float(change_percent) if change_percent is not None else "DATA_MISSING",
    }


def _compute_technical_indicators(symbol):
    ticker = yf.Ticker(symbol)
    try:
        history = ticker.history(period='6mo', interval='1d')
    except Exception as error:
        logging.warning("Technical history unavailable for %s: %s", symbol, error)
        history = None

    if history is None or history.empty or 'Close' not in history.columns:
        return {
            "rsi": None,
            "macd": None,
            "macd_signal": None,
            "macd_histogram": None,
            "trend": "unknown",
        }

    close = history['Close'].dropna()
    if len(close) < 35:
        return {
            "rsi": None,
            "macd": None,
            "macd_signal": None,
            "macd_histogram": None,
            "trend": "insufficient_data",
        }

    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    avg_gain = gains.rolling(14).mean()
    avg_loss = losses.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal

    latest_hist = _to_float(macd_hist.iloc[-1])
    trend = "bullish" if latest_hist is not None and latest_hist > 0 else "bearish"

    return {
        "rsi": _to_float(rsi.iloc[-1]),
        "macd": _to_float(macd_line.iloc[-1]),
        "macd_signal": _to_float(macd_signal.iloc[-1]),
        "macd_histogram": latest_hist,
        "trend": trend,
    }


def _compute_news_sentiment(symbol):
    ticker = yf.Ticker(symbol)
    try:
        items = ticker.news or []
    except Exception as error:
        logging.warning("News unavailable for %s: %s", symbol, error)
        items = []

    if not items:
        return {
            "score": 0.5,
            "label": "neutral",
            "headlines": [],
            "explanation": "No recent headlines were available, so sentiment defaults to neutral.",
        }

    pos_hits = 0
    neg_hits = 0
    headlines = []

    for item in items[:8]:
        title = (item.get('title') or '').strip()
        summary = (item.get('summary') or '').strip()
        text = f"{title} {summary}".lower()
        words = {word.strip(".,:;!?()[]'\"") for word in text.split()}

        pos = len(words & POSITIVE_WORDS)
        neg = len(words & NEGATIVE_WORDS)
        pos_hits += pos
        neg_hits += neg

        if title:
            headlines.append(title)

    denominator = max(pos_hits + neg_hits, 1)
    raw_score = (pos_hits - neg_hits) / denominator
    score = (raw_score + 1) / 2

    if score >= 0.67:
        label = "positive"
    elif score <= 0.33:
        label = "negative"
    else:
        label = "neutral"

    return {
        "score": round(score, 3),
        "label": label,
        "headlines": headlines[:3],
        "explanation": (
            f"Sentiment derived from {min(len(items), 8)} recent headlines using a financial"
            " keyword lexicon for positive vs. negative signals."
        ),
    }




def _build_hot_watchlist(max_price=50.0, limit=8):
    selected = []
    for ticker, company in HOT_WATCHLIST_CANDIDATES:
        quote = get_quote_data(ticker)
        price = quote.get("price")
        if isinstance(price, (int, float)) and price <= max_price:
            selected.append((ticker, company))
        elif price == "DATA_MISSING":
            # Keep unknown-priced trending candidates available when the data provider is unavailable.
            selected.append((ticker, company))

        if len(selected) >= limit:
            break

    return selected or DEFAULT_HOT_WATCHLIST[:limit]

def _hybrid_signal_engine(symbol):
    technical = _compute_technical_indicators(symbol)
    sentiment = _compute_news_sentiment(symbol)

    rsi = technical.get("rsi")
    macd_hist = technical.get("macd_histogram")
    sentiment_score = sentiment.get("score", 0.5)

    technical_score = 0.0
    reasons = []

    if rsi is not None:
        if rsi <= 35:
            technical_score += 0.45
            reasons.append(f"RSI {rsi:.1f} is low, indicating oversold conditions.")
        elif rsi >= 70:
            technical_score -= 0.45
            reasons.append(f"RSI {rsi:.1f} is high, indicating overbought risk.")
        else:
            reasons.append(f"RSI {rsi:.1f} is neutral.")
    else:
        reasons.append("RSI unavailable due to limited price history.")

    if macd_hist is not None:
        if macd_hist > 0:
            technical_score += 0.25
            reasons.append("MACD is above signal (bullish momentum).")
        else:
            technical_score -= 0.25
            reasons.append("MACD is below signal (bearish momentum).")
    else:
        reasons.append("MACD unavailable due to limited price history.")

    sentiment_score_centered = sentiment_score - 0.5
    technical_weight = 0.6
    sentiment_weight = 0.4
    hybrid_score = (technical_score * technical_weight) + ((sentiment_score_centered * 2) * sentiment_weight)

    if sentiment_score >= 0.7 and (rsi is not None and rsi <= 40) and (macd_hist is not None and macd_hist > 0):
        action = "BUY"
        reasons.append("Rule trigger: strong positive sentiment + supportive RSI/MACD.")
    elif sentiment_score <= 0.35 and (rsi is not None and rsi >= 60) and (macd_hist is not None and macd_hist < 0):
        action = "SELL"
        reasons.append("Rule trigger: negative sentiment + overbought/weak momentum.")
    elif hybrid_score >= 0.18:
        action = "BUY"
        reasons.append("Hybrid score is sufficiently positive.")
    elif hybrid_score <= -0.18:
        action = "SELL"
        reasons.append("Hybrid score is sufficiently negative.")
    else:
        action = "HOLD"
        reasons.append("Signals are mixed; hold until clearer direction appears.")

    confidence = min(0.95, max(0.5, abs(hybrid_score) + 0.5))

    rule_engine = {
        "name": "rule_based_strategy_engine",
        "buy_threshold": {
            "sentiment_min": 0.7,
            "rsi_max": 40,
            "macd_histogram_min": 0,
        },
        "sell_threshold": {
            "sentiment_max": 0.35,
            "rsi_min": 60,
            "macd_histogram_max": 0,
        },
        "hybrid_score_buy_min": 0.18,
        "hybrid_score_sell_max": -0.18,
        "hybrid_weights": {"technical": technical_weight, "sentiment": sentiment_weight},
    }

    return {
        "symbol": symbol,
        "action": action,
        "confidence": round(confidence, 3),
        "hybrid_score": round(hybrid_score, 3),
        "model": "hybrid_model",
        "signal_engine": "technical_plus_sentiment",
        "technical": technical,
        "sentiment": sentiment,
        "rule_engine": rule_engine,
        "rationale": reasons,
    }


@app.route('/', methods=['GET'])
def home():
    return '''
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body{font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#eef3ff;color:#1f2937}
    .wrap{max-width:1000px;margin:0 auto;padding:32px}
    h1{font-size:3rem;margin:0;color:#1a237e}
    .sub{font-size:1.1rem;color:#455a64;margin:8px 0 24px}
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
    .card{background:#fff;border-radius:16px;padding:20px;box-shadow:0 10px 24px rgba(0,0,0,.08)}
    a.btn{display:inline-block;background:#1e40af;color:#fff;padding:10px 14px;border-radius:10px;text-decoration:none;margin-top:8px}
    .ask{margin-top:26px;background:#fff;border-radius:16px;padding:20px;box-shadow:0 10px 24px rgba(0,0,0,.08)}
    input{padding:12px;border-radius:10px;border:1px solid #cbd5e1;width:220px}
    button{padding:12px 14px;border:none;border-radius:10px;background:#1e40af;color:#fff;cursor:pointer}
    #out{margin-top:10px;line-height:1.6}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>MoneyBot Pro</h1>
    <p class="sub">Hybrid stock advisor using technical indicators + headline sentiment with transparent rule-based recommendations.</p>

    <div class="cards">
      <div class="card">
        <h3>🏛️ Stable Long-Term Watchlist</h3>
        <p>Quality names for longer horizons and steadier fundamentals.</p>
        <a class="btn" href="/watchlist">Open long-term watchlist</a>
      </div>
      <div class="card">
        <h3>⚡ Hot Momentum Watchlist</h3>
        <p>Short-term opportunities with dynamic Buy/Hold/Sell actions.</p>
        <a class="btn" href="/hot-watchlist">Open hot watchlist</a>
      </div>
    </div>

    <div class="ask">
      <h3>Quick Signal Lookup</h3>
      <input id="sym" placeholder="AAPL, NVDA, TSLA" />
      <button onclick="lookup()">Analyze</button>
      <div id="out"></div>
    </div>
  </div>

<script>
async function lookup(){
  const el = document.getElementById('out');
  const sym = (document.getElementById('sym').value || '').trim().toUpperCase();
  if(!sym){ el.textContent = 'Enter a symbol.'; return; }
  el.textContent = 'Analyzing...';
  try {{
    const res = await fetch('/signal?symbol=' + encodeURIComponent(sym));
    const data = await res.json();
    if(!res.ok){ el.textContent = data.error || 'Error'; return; }
    el.innerHTML = `<b>${data.symbol}</b> → <b>${data.action}</b> (confidence ${(data.confidence*100).toFixed(0)}%)<br>${data.rationale.join('<br>')}`;
  } catch(err){
    el.textContent = 'Could not fetch signal.';
  }
}
</script>
</body>
</html>
'''


@app.route('/quote', methods=['GET'])
def quote():
    symbol = request.args.get('symbol', '').strip().upper()
    if not _is_valid_symbol(symbol):
        return jsonify(QUOTE_FALLBACK), 400

    try:
        return jsonify(get_quote_data(symbol))
    except Exception as error:
        logging.error("Quote error for %s: %s", symbol, error)
        return jsonify(QUOTE_FALLBACK), 500


@app.route('/signal', methods=['GET'])
def signal():
    symbol = request.args.get('symbol', '').strip().upper()
    if not _is_valid_symbol(symbol):
        return jsonify({'error': 'invalid symbol format'}), 400

    try:
        quote_data = get_quote_data(symbol)
        signal_data = _hybrid_signal_engine(symbol)
        signal_data['quote'] = quote_data
        signal_data['quote_data_available'] = quote_data.get('price') != 'DATA_MISSING' and quote_data.get('change_percent') != 'DATA_MISSING'
        return jsonify(signal_data)
    except Exception as error:
        logging.error("Signal engine error for %s: %s", symbol, error)
        return jsonify({'error': 'signal unavailable'}), 500


@app.route('/watchlist', methods=['GET'])
def watchlist():
    rows = ''.join(
        f"<tr><td>{ticker}</td><td>{company}</td><td id='{ticker.lower()}_price'>...</td><td id='{ticker.lower()}_chg'>...</td></tr>"
        for ticker, company in LONG_TERM_WATCHLIST
    )

    symbols = [ticker for ticker, _ in LONG_TERM_WATCHLIST]
    symbol_array = ','.join([f'"{s}"' for s in symbols])

    return f'''
<html><head>
<style>
body{{font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:28px;background:#f8fafc}}
.table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border:1px solid #e5e7eb;padding:12px;text-align:left}}
th{{background:#1e3a8a;color:#fff}} a{{color:#1d4ed8;text-decoration:none}}
</style>
</head><body>
<h1>🏛️ Long-Term Stable Watchlist</h1>
<p>Designed for durable businesses and compounding potential.</p>
<table class="table">
<tr><th>Ticker</th><th>Company</th><th>Price</th><th>Daily Change %</th></tr>
{rows}
</table>
<p><a href="/">← Back</a></p>
<script>
const symbols=[{symbol_array}];
function renderQuote(idPrefix, quote) {{
  const priceEl = document.getElementById(idPrefix + '_price');
  const chgEl = document.getElementById(idPrefix + '_chg');
  const price = quote?.price;
  const change = quote?.change_percent;
  priceEl.innerText = (price === 'DATA_MISSING' || price === undefined) ? 'DATA MISSING' : '$' + Number(price).toFixed(2);
  chgEl.innerText = (change === 'DATA_MISSING' || change === undefined) ? 'DATA MISSING' : Number(change).toFixed(2) + '%';
}}

async function refreshLongTerm(symbol){{
  try {{
    const res = await fetch('/quote?symbol=' + encodeURIComponent(symbol));
    const data = await res.json();
    renderQuote(symbol.toLowerCase(), data);
  }} catch(err) {{
    renderQuote(symbol.toLowerCase(), {{price: 'DATA_MISSING', change_percent: 'DATA_MISSING'}});
  }}
}}

symbols.forEach(refreshLongTerm);
setInterval(()=>symbols.forEach(refreshLongTerm), 60000);
</script>
</body></html>
'''


@app.route('/hot-watchlist', methods=['GET'])
def hot_watchlist():
    hot_watchlist_items = _build_hot_watchlist(max_price=50.0, limit=8)

    rows = ''.join(
        f"<tr><td>{ticker}</td><td>{company}</td><td id='{ticker.lower()}_price'>...</td><td id='{ticker.lower()}_chg'>...</td><td id='{ticker.lower()}_act'>...</td><td id='{ticker.lower()}_why'>...</td></tr>"
        for ticker, company in hot_watchlist_items
    )

    symbols = [ticker for ticker, _ in hot_watchlist_items]
    symbol_array = ','.join([f'"{s}"' for s in symbols])

    return f'''
<html><head>
<style>
body{{font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:28px;background:#f8fafc}}
.table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border:1px solid #e5e7eb;padding:12px;text-align:left;vertical-align:top}}
th{{background:#7c2d12;color:#fff}}
.buy{{color:#166534;font-weight:700}} .hold{{color:#92400e;font-weight:700}} .sell{{color:#991b1b;font-weight:700}}
a{{color:#1d4ed8;text-decoration:none}}
</style>
</head><body>
<h1>⚡ Hot Momentum Watchlist</h1>
<p>Short-term signal engine focused on lower-priced (under $50) trending stocks with technical indicators + sentiment analysis.</p>
<table class="table">
<tr><th>Ticker</th><th>Company</th><th>Price</th><th>Daily Change %</th><th>Signal Engine Action</th><th>Why (Transparency)</th></tr>
{rows}
</table>
<p><a href="/">← Back</a></p>
<script>
const symbols=[{symbol_array}];
function klass(action){{
  if(action==='BUY') return 'buy';
  if(action==='SELL') return 'sell';
  return 'hold';
}}
async function refresh(s){{
  try {{
    const signalRes = await fetch('/signal?symbol='+encodeURIComponent(s));
    const data = await signalRes.json();
    const price = data.quote?.price;
    const chg = data.quote?.change_percent;
    const priceText = (price === 'DATA_MISSING' || price === undefined) ? 'DATA MISSING' : '$' + Number(price).toFixed(2);
    const changeText = (chg === 'DATA_MISSING' || chg === undefined) ? 'DATA MISSING' : Number(chg).toFixed(2) + '%';
    document.getElementById(s.toLowerCase()+'_price').innerText = priceText;
    document.getElementById(s.toLowerCase()+'_chg').innerText = changeText;

    const actionEl = document.getElementById(s.toLowerCase()+'_act');
    actionEl.innerText = data.action || 'HOLD';
    actionEl.className = klass(data.action || 'HOLD');

    const topHeadline = data.sentiment?.headlines?.[0] || 'No major headline available.';
    const firstReason = data.rationale?.[0] || 'Signals mixed.';
    const modelName = data.model || 'hybrid_model';
    const weights = 'Weights(technical=0.60, sentiment=0.40)';
    const dataFlag = (data.quote_data_available === false) ? ' Quote/change data missing from live API.' : '';
    document.getElementById(s.toLowerCase()+'_why').innerText = '[' + modelName + '] ' + firstReason + ' ' + weights + '. Headline: ' + topHeadline + dataFlag;
  }} catch(err) {{
    document.getElementById(s.toLowerCase()+'_price').innerText = 'DATA MISSING';
    document.getElementById(s.toLowerCase()+'_chg').innerText = 'DATA MISSING';
    document.getElementById(s.toLowerCase()+'_act').innerText = 'HOLD';
    document.getElementById(s.toLowerCase()+'_why').innerText = 'Live signal or quote data missing.';
  }}
}}
symbols.forEach(refresh);
setInterval(()=>symbols.forEach(refresh), 60000);
</script>
</body></html>
'''


if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=5000, debug=True)
    except Exception as error:
        logging.error("App failed to start: %s", error)
        raise
