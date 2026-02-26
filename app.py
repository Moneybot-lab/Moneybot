import json
import logging
import os
import secrets
import sqlite3
from functools import wraps

from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for
from flask_cors import CORS
import yfinance as yf
import werkzeug.security as wz_security
from werkzeug.security import check_password_hash, generate_password_hash
from trade_signal import analyze_ticker, SignalResult
import time

QUOTE_CACHE = dict()          

def get_cached_quote(symbol):
    now = time.time()
    if symbol in QUOTE_CACHE and now - QUOTE_CACHE  < CACHE_TTL:
        return QUOTE_CACHE ['data']
    # If stale or missing → fetch fresh
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        QUOTE_CACHE = {'data': info, 'timestamp': now}
        return info
    except Exception as e:
        logging.warning(f"Cache miss for {symbol}: {e}")
        return None  # or fallback dict

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('MONEYBOT_SECRET_KEY', secrets.token_hex(32))
CORS(app)
logging.basicConfig(level=logging.INFO)

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'moneybot.db')
DB_PATH = os.environ.get('MONEYBOT_DB_PATH', DEFAULT_DB_PATH)


def _ensure_db_parent_dir():
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
QUOTE_FALLBACK = {
    'price': 'DATA_MISSING',
    'change_percent': 'DATA_MISSING',
    'live_data_available': False,
    'quote_source': 'yfinance',
}

LONG_TERM_CANDIDATES = [
    ('MSFT', 'Microsoft'), ('AAPL', 'Apple'), ('GOOGL', 'Alphabet'), ('AMZN', 'Amazon'),
    ('META', 'Meta Platforms'), ('BRK-B', 'Berkshire Hathaway'), ('JNJ', 'Johnson & Johnson'),
    ('PG', 'Procter & Gamble'), ('KO', 'Coca-Cola'), ('V', 'Visa'),
    ('MA', 'Mastercard'), ('COST', 'Costco'), ('PEP', 'PepsiCo'), ('HD', 'Home Depot'),
    ('ABBV', 'AbbVie'), ('ADBE', 'Adobe'), ('CRM', 'Salesforce'), ('UNH', 'UnitedHealth Group'),
]

HOT_WATCHLIST_CANDIDATES = [
    ('SOFI', 'SoFi Technologies'),
    ('RKLB', 'Rocket Lab'),
    ('PLTR', 'Palantir'),
    ('SNAP', 'Snap'),
    ('F', 'Ford'),
    ('PFE', 'Pfizer'),
    ('NIO', 'NIO'),
    ('LCID', 'Lucid Group'),
    ('HOOD', 'Robinhood'),
    ('RIVN', 'Rivian'),
    ('ACHR', 'Archer Aviation'),
    ('JOBY', 'Joby Aviation'),
]
DEFAULT_HOT_WATCHLIST = HOT_WATCHLIST_CANDIDATES[:8]

MARKET_OVERVIEW_SYMBOLS = [
    ('^DJI', 'Dow'),
    ('^IXIC', 'Nasdaq'),
    ('^GSPC', 'S&P 500'),
    ('GC=F', 'Gold'),
    ('BTC-USD', 'Bitcoin'),
]

POSITIVE_WORDS = {
    'beat', 'beats', 'surge', 'surges', 'growth', 'upgrade', 'upgrades', 'strong',
    'bullish', 'record', 'profit', 'profits', 'outperform', 'expands', 'expansion',
    'partnership', 'launch', 'wins', 'demand', 'momentum', 'innovation',
}

NEGATIVE_WORDS = {
    'miss', 'misses', 'downgrade', 'downgrades', 'weak', 'weakness', 'lawsuit',
    'probe', 'decline', 'falls', 'drop', 'drops', 'bearish', 'loss', 'losses',
    'cuts', 'cut', 'slowdown', 'risk', 'warning', 'volatile', 'regulatory',
}


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_valid_symbol(symbol):
    return bool(symbol) and all(ch.isalnum() or ch in {'.', '-', '^'} for ch in symbol)


def _db_conn():
    _ensure_db_parent_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _db_conn() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS user_watchlist (
                user_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                buy_price REAL,
                shares REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, ticker),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            '''
        )

        columns = {row['name'] for row in conn.execute('PRAGMA table_info(user_watchlist)').fetchall()}
        if 'buy_price' not in columns:
            conn.execute('ALTER TABLE user_watchlist ADD COLUMN buy_price REAL')
        if 'shares' not in columns:
            conn.execute('ALTER TABLE user_watchlist ADD COLUMN shares REAL')


def _current_user_id():
    user_id = session.get('user_id')
    if isinstance(user_id, int):
        return user_id
    return None


def _current_user_email():
    return session.get('user_email')


def _login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not _current_user_id():
            return redirect(url_for('login', next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def _add_user(email, password):
    password_hash = wz_security.generate_password_hash(password)
    try:
        with _db_conn() as conn:
            conn.execute('INSERT INTO users(email, password_hash) VALUES(?, ?)', (email.lower().strip(), password_hash))
        return True, None
    except sqlite3.IntegrityError:
        return False, 'An account with that email already exists.'


def _verify_user(email, password):
    with _db_conn() as conn:
        row = conn.execute('SELECT id, email, password_hash FROM users WHERE email = ?', (email.lower().strip(),)).fetchone()

    if not row:
        return None
    if not wz_security.check_password_hash(row['password_hash'], password):
        return None
    return {'id': row['id'], 'email': row['email']}



def _get_user_tickers(user_id):
    with _db_conn() as conn:
        rows = conn.execute(
            'SELECT ticker FROM user_watchlist WHERE user_id = ? ORDER BY created_at DESC, ticker ASC',
            (user_id,),
        ).fetchall()
    return [row['ticker'] for row in rows]


def _get_user_positions(user_id):
    with _db_conn() as conn:
        rows = conn.execute(
            'SELECT ticker, buy_price, shares FROM user_watchlist WHERE user_id = ? ORDER BY created_at DESC, ticker ASC',
            (user_id,),
        ).fetchall()
    return {row['ticker']: {'buy_price': row['buy_price'], 'shares': row['shares']} for row in rows}


def _add_user_ticker(user_id, ticker, buy_price=None, shares=None):
    try:
        with _db_conn() as conn:
            conn.execute(
                'INSERT INTO user_watchlist(user_id, ticker, buy_price, shares) VALUES(?, ?, ?, ?)',
                (user_id, ticker.upper(), buy_price, shares),
            )
        return True, None
    except sqlite3.IntegrityError:
        return False, f'{ticker.upper()} is already in your watchlist.'


def _remove_user_ticker(user_id, ticker):
    with _db_conn() as conn:
        conn.execute('DELETE FROM user_watchlist WHERE user_id = ? AND ticker = ?', (user_id, ticker.upper()))


def _update_position(user_id, ticker, buy_price, shares):
    with _db_conn() as conn:
        conn.execute(
            'UPDATE user_watchlist SET buy_price = ?, shares = ? WHERE user_id = ? AND ticker = ?',
            (buy_price, shares, user_id, ticker.upper()),
        )


def get_quote_data(symbol):
    ticker = yf.Ticker(symbol)
    info = {}

    try:
        info = ticker.info or {}
    except Exception as error:
        logging.warning('Ticker info unavailable for %s: %s', symbol, error)

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
            if history is not None and not history.empty:
                latest_close = history['Close'].iloc[-1]
                prev_close = history['Close'].iloc[-2] if len(history.index) > 1 else None
                if price is None:
                    price = latest_close
                if previous_close is None and prev_close is not None:
                    previous_close = prev_close
                if change_percent is None and prev_close not in (None, 0):
                    change_percent = ((latest_close - prev_close) / prev_close) * 100
        except Exception as error:
            logging.warning('Price history unavailable for %s: %s', symbol, error)

    if change_percent is None and price is not None and previous_close not in (None, 0):
        change_percent = ((price - previous_close) / previous_close) * 100

    price_value = _to_float(price) if price is not None else 'DATA_MISSING'
    change_value = _to_float(change_percent) if change_percent is not None else 'DATA_MISSING'

    return {
        'price': price_value,
        'change_percent': change_value,
        'live_data_available': price_value != 'DATA_MISSING' and change_value != 'DATA_MISSING',
        'quote_source': 'yfinance',
    }


def _compute_technical_indicators(symbol):
    ticker = yf.Ticker(symbol)
    try:
        history = ticker.history(period='6mo', interval='1d')
    except Exception as error:
        logging.warning('Technical history unavailable for %s: %s', symbol, error)
        history = None

    if history is None or history.empty or 'Close' not in history.columns:
        return {'rsi': None, 'macd': None, 'macd_signal': None, 'macd_histogram': None, 'trend': 'unknown'}

    close = history['Close'].dropna()
    if len(close) < 35:
        return {'rsi': None, 'macd': None, 'macd_signal': None, 'macd_histogram': None, 'trend': 'insufficient_data'}

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
    trend = 'bullish' if latest_hist is not None and latest_hist > 0 else 'bearish'

    return {
        'rsi': _to_float(rsi.iloc[-1]),
        'macd': _to_float(macd_line.iloc[-1]),
        'macd_signal': _to_float(macd_signal.iloc[-1]),
        'macd_histogram': latest_hist,
        'trend': trend,
    }


def _compute_news_sentiment(symbol):
    ticker = yf.Ticker(symbol)
    try:
        items = ticker.news or []
    except Exception as error:
        logging.warning('News unavailable for %s: %s', symbol, error)
        items = []

    if not items:
        return {
            'score': 0.5,
            'label': 'neutral',
            'headlines': [],
            'explanation': 'No recent headlines were available, so sentiment defaults to neutral.',
        }

    pos_hits = 0
    neg_hits = 0
    headlines = []

    for item in items[:8]:
        title = (item.get('title') or '').strip()
        summary = (item.get('summary') or '').strip()
        text = f'{title} {summary}'.lower()
        words = {word.strip(".,:;!?()[]'\"") for word in text.split()}

        pos_hits += len(words & POSITIVE_WORDS)
        neg_hits += len(words & NEGATIVE_WORDS)

        if title:
            headlines.append(title)

    denominator = max(pos_hits + neg_hits, 1)
    raw_score = (pos_hits - neg_hits) / denominator
    score = (raw_score + 1) / 2

    if score >= 0.67:
        label = 'positive'
    elif score <= 0.33:
        label = 'negative'
    else:
        label = 'neutral'

    return {
        'score': round(score, 3),
        'label': label,
        'headlines': headlines[:3],
        'explanation': f'Sentiment derived from {min(len(items), 8)} recent headlines using a financial keyword lexicon.',
    }


def _build_hot_watchlist(max_price=50.0, limit=8):
    selected = []
    for ticker, company in HOT_WATCHLIST_CANDIDATES:
        quote = get_quote_data(ticker)
        price = quote.get('price')
        if isinstance(price, (int, float)) and price <= max_price:
            selected.append((ticker, company))
        elif price == 'DATA_MISSING':
            selected.append((ticker, company))
        if len(selected) >= limit:
            break

    return selected or DEFAULT_HOT_WATCHLIST[:limit]


def _filter_buy_candidates(candidates, limit=10, max_price=None):
    picks = []
    for ticker, company in candidates:
        quote = get_quote_data(ticker)
        price = quote.get('price')
        if max_price is not None and not (isinstance(price, (int, float)) and price <= max_price):
            continue

        try:
            signal = _hybrid_signal_engine(ticker)
            action = (signal.get('action') or '').upper()
            score = _to_float(signal.get('hybrid_score')) or 0.0
        except Exception as error:
            logging.warning('Signal filter failed for %s: %s', ticker, error)
            continue

        if _is_buy_recommendation(action):
            picks.append((ticker, company, score))

    picks.sort(key=lambda row: row[2], reverse=True)
    return [(ticker, company) for ticker, company, _ in picks[:limit]]


WHALE_INVESTOR_PICKS = {
    'Warren Buffett': [('AAPL', 'Apple'), ('BAC', 'Bank of America'), ('KO', 'Coca-Cola'), ('AXP', 'American Express'), ('OXY', 'Occidental Petroleum')],
    'Bill Ackman': [('GOOGL', 'Alphabet'), ('CMG', 'Chipotle'), ('HLT', 'Hilton'), ('QSR', 'Restaurant Brands'), ('LOW', "Lowe's")],
    'Ray Dalio': [('SPY', 'SPDR S&P 500 ETF'), ('IVV', 'iShares Core S&P 500 ETF'), ('GLD', 'SPDR Gold Shares'), ('VWO', 'Vanguard FTSE Emerging Markets'), ('FXI', 'iShares China Large-Cap ETF')],
}


def _is_buy_recommendation(action):
    action_text = (action or '').upper()
    return 'BUY' in action_text


def _hybrid_signal_engine(symbol):
    result: SignalResult = analyze_ticker(symbol)  # Pulls from trade_signal.py

    action = result.verdict.upper()  # 'BUY' → 'BUY', 'HOLD' → 'HOLD', etc.
    confidence = min(1.0, result.score / 12.0)  # normalize score 0-12 to 0-1

    reasons = list(result.reasons or ['No reasons available.'])
    if _is_buy_recommendation(action):
        reasons.append('Buy signal targets promising dips with recovery potential when momentum/fundamentals align.')
    elif 'SELL' in action:
        reasons.append('Sell signal prioritizes exiting before potential drops when downside risk signals rise.')

    return {
        'symbol': symbol,
        'action': action,
        'confidence': round(confidence, 3),
        'hybrid_score': round(result.score, 1),
        'model': 'quant_trade_signal',
        'signal_engine': 'macd_rsi_volume_fundamentals_sentiment',
        'technical': {
            'rsi': result.rsi,
            'macd_histogram': result.macd_hist
        },
        'sentiment': {'score': None, 'label': 'n/a'},  # old news still here if you want
        'rationale': reasons,
    }

def _top_tabs(active_tab):
    tabs = [
        ('home', 'Home', '/'),
        ('stable', 'Stable Watchlist', '/watchlist'),
        ('hot', 'Hot Momentum Buys', '/hot-watchlist'),
        ('whales', 'Whales of Wall Street', '/whales'),
        ('user', 'User Watchlist', '/user-watchlist'),
    ]

    links = []
    for key, label, href in tabs:
        cls = 'tab active' if key == active_tab else 'tab'
        links.append(f'<a class="{cls}" href="{href}">{label}</a>')

    auth_link = '<a class="tab" href="/logout">Logout</a>' if _current_user_id() else '<a class="tab" href="/login">Login</a>'
    return '<nav class="toolbar">' + ''.join(links) + auth_link + '</nav>'




def _get_market_snapshot(symbol, label):
    quote = get_quote_data(symbol)
    return {
        'symbol': symbol,
        'label': label,
        'price': quote.get('price', 'DATA_MISSING'),
        'change_percent': quote.get('change_percent', 'DATA_MISSING'),
        'live_data_available': bool(quote.get('live_data_available')),
        'quote_source': quote.get('quote_source', 'yfinance'),
    }


def get_market_overview():
    return [_get_market_snapshot(symbol, label) for symbol, label in MARKET_OVERVIEW_SYMBOLS]


def _render_watchlist_page(title, subtitle, symbols, include_action=False, user_page=False, active_tab='home', user_positions=None):
    symbols_json = json.dumps(symbols)
    user_positions = user_positions or {}
    management = ''
    if user_page:
        management = '''
        <form method="post" style="margin: 14px 0 18px; display:flex; gap:10px; flex-wrap:wrap;align-items:center">
          <input name="ticker" placeholder="Add ticker e.g. AMD" style="padding:10px;border:1px solid #cbd5e1;border-radius:8px" required />
          <input name="buy_price" type="number" step="0.01" min="0" placeholder="Buy price (optional)" style="padding:10px;border:1px solid #cbd5e1;border-radius:8px" />
          <input name="shares" type="number" step="0.0001" min="0" placeholder="Shares (optional)" style="padding:10px;border:1px solid #cbd5e1;border-radius:8px" />
          <button type="submit" style="padding:10px 14px;border:none;border-radius:8px;background:#1e40af;color:#fff">Add Ticker</button>
        </form>
        '''

    remove_button = ''
    user_extra_headers = ''
    if user_page:
        remove_button = '<th>Remove</th>'
        user_extra_headers = '<th>Buy Price</th><th>Shares</th><th>Performance</th><th>Total Gain/Loss</th>'

    rows = []
    for symbol in symbols:
        position = user_positions.get(symbol, {})
        buy_price = position.get('buy_price')
        shares = position.get('shares')
        buy_price_value = '' if buy_price is None else f"{float(buy_price):.2f}"
        shares_value = '' if shares is None else f"{float(shares):.4f}"
        remove_col = ''
        if user_page:
            remove_col = (
                f"<td><form method='post' action='/user-watchlist/remove' style='margin:0'>"
                f"<input type='hidden' name='ticker' value='{symbol}'/>"
                "<button style='border:none;background:#fee2e2;color:#991b1b;padding:6px 9px;border-radius:6px;cursor:pointer'>Remove</button>"
                "</form></td>"
            )
        buy_price_col = ''
        shares_col = ''
        perf_col = ''
        total_col = ''
        if user_page:
            buy_price_col = (
                f"<td><form method='post' action='/user-watchlist/position' style='margin:0;display:flex;gap:6px;align-items:center'>"
                f"<input type='hidden' name='ticker' value='{symbol}'/>"
                f"<input id='{symbol.lower()}_buyprice' name='buy_price' data-buy-price='{buy_price_value}' value='{buy_price_value}' type='number' step='0.01' min='0' placeholder='n/a' style='width:90px;padding:6px;border:1px solid #cbd5e1;border-radius:6px'/>"
                f"<input id='{symbol.lower()}_shares' name='shares' data-shares='{shares_value}' value='{shares_value}' type='number' step='0.0001' min='0' placeholder='n/a' style='width:90px;padding:6px;border:1px solid #cbd5e1;border-radius:6px'/>"
                "<button style='border:none;background:#dbeafe;color:#1e3a8a;padding:6px 8px;border-radius:6px;cursor:pointer'>Save</button>"
                "</form></td>"
            )
            shares_col = f"<td id='{symbol.lower()}_shares_display'>{shares_value or 'n/a'}</td>"
            perf_col = f"<td id='{symbol.lower()}_perf'>n/a</td>"
            total_col = f"<td id='{symbol.lower()}_total'>n/a</td>"

        ticker_cell = (
            f"<a href='#' class='ticker-link' data-symbol='{symbol}' "
            "style='font-weight:700;color:#1d4ed8;text-decoration:none'>"
            f"{symbol}</a>"
        )
        if include_action:
            rows.append(
                f"<tr><td>{ticker_cell}</td><td id='{symbol.lower()}_price'>...</td><td id='{symbol.lower()}_chg'>...</td>{buy_price_col}{shares_col}{perf_col}{total_col}"
                f"<td id='{symbol.lower()}_score'>...</td><td id='{symbol.lower()}_why'>...</td>{remove_col}</tr>"
            )
        else:
            rows.append(
                f"<tr><td>{ticker_cell}</td><td id='{symbol.lower()}_price'>...</td><td id='{symbol.lower()}_chg'>...</td>{buy_price_col}{shares_col}{perf_col}{total_col}{remove_col}</tr>"
            )

    headers = '<tr><th>Ticker</th><th>Current Price</th><th>Daily Change %</th>' + user_extra_headers
    if include_action:
        headers += '<th>Score</th><th>Why (Transparency)</th>'
    headers += remove_button + '</tr>'

    script = '''
<script>
const rawSymbols = __SYMBOLS__;
const symbols = (rawSymbols || []).map(item => Array.isArray(item) ? item[0] : item).filter(Boolean);
const includeAction = __INCLUDE_ACTION__;
function formatQuote(quote){
  const p = quote?.price;
  const c = quote?.change_percent;
  return {
    price: (p === 'DATA_MISSING' || p === undefined) ? 'DATA MISSING' : '$' + Number(p).toFixed(2),
    change: (c === 'DATA_MISSING' || c === undefined) ? 'DATA MISSING' : Number(c).toFixed(2) + '%'
  }
}

async function refreshRow(symbol, includeAction){
  const key = symbol.toLowerCase();
  try {
    if(includeAction){
      const res = await fetch('/signal?symbol=' + encodeURIComponent(symbol));
      const data = await res.json();
      const fq = formatQuote(data.quote || {});
      document.getElementById(key + '_price').innerText = fq.price;
      document.getElementById(key + '_chg').innerText = fq.change;

      const buyInput = document.getElementById(key + '_buyprice');
      const sharesInput = document.getElementById(key + '_shares');
      const perfEl = document.getElementById(key + '_perf');
      const totalEl = document.getElementById(key + '_total');
      if (buyInput && perfEl) {
        const buyPrice = Number(buyInput.value || buyInput.dataset.buyPrice || NaN);
        const livePrice = Number(data?.quote?.price);
        const shares = Number(sharesInput?.value || sharesInput?.dataset?.shares || NaN);
        if (!Number.isNaN(buyPrice) && buyPrice > 0 && Number.isFinite(livePrice)) {
          const diff = livePrice - buyPrice;
          const pct = (diff / buyPrice) * 100;
          const sign = diff >= 0 ? '+' : '';
          perfEl.innerText = `${sign}$${diff.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
          perfEl.style.color = diff >= 0 ? '#166534' : '#991b1b';
          if (totalEl && !Number.isNaN(shares) && shares > 0) {
            const total = diff * shares;
            const signTotal = total >= 0 ? '+' : '';
            totalEl.innerText = `${signTotal}$${total.toFixed(2)}`;
            totalEl.style.color = total >= 0 ? '#166534' : '#991b1b';
          } else if (totalEl) {
            totalEl.innerText = 'n/a';
            totalEl.style.color = '#334155';
          }
        } else {
          perfEl.innerText = 'n/a';
          perfEl.style.color = '#334155';
          if (totalEl) { totalEl.innerText = 'n/a'; totalEl.style.color = '#334155'; }
        }
      }

      const scoreEl = document.getElementById(key + '_score');
      if (scoreEl) scoreEl.innerText = data.hybrid_score ?? 'n/a';

      const reasons = (data.rationale || []).slice(0, 2);
      const reasonsText = reasons.length ? reasons.join(' | ') : 'Signals mixed.';
      const rsi = data.technical?.rsi ?? 'n/a';
      const macd = data.technical?.macd_histogram ?? 'n/a';
      const source = data.data_provider || 'yfinance';
      const dataFlag = data.quote_data_available ? `Live price/change from ${source}.` : `Quote/change data missing from live API (${source}).`;
      document.getElementById(key + '_why').innerText = `${reasonsText}. RSI=${rsi}, MACD_hist=${macd}, Score=${data.hybrid_score ?? 'n/a'}. ${dataFlag}`;
    } else {
      const res = await fetch('/quote?symbol=' + encodeURIComponent(symbol));
      const data = await res.json();
      const fq = formatQuote(data);
      document.getElementById(key + '_price').innerText = fq.price;
      document.getElementById(key + '_chg').innerText = fq.change;

      const buyInput = document.getElementById(key + '_buyprice');
      const sharesInput = document.getElementById(key + '_shares');
      const perfEl = document.getElementById(key + '_perf');
      const totalEl = document.getElementById(key + '_total');
      if (buyInput && perfEl) {
        const buyPrice = Number(buyInput.value || buyInput.dataset.buyPrice || NaN);
        const livePrice = Number(data?.price);
        const shares = Number(sharesInput?.value || sharesInput?.dataset?.shares || NaN);
        if (!Number.isNaN(buyPrice) && buyPrice > 0 && Number.isFinite(livePrice)) {
          const diff = livePrice - buyPrice;
          const pct = (diff / buyPrice) * 100;
          const sign = diff >= 0 ? '+' : '';
          perfEl.innerText = `${sign}$${diff.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
          perfEl.style.color = diff >= 0 ? '#166534' : '#991b1b';
          if (totalEl && !Number.isNaN(shares) && shares > 0) {
            const total = diff * shares;
            const signTotal = total >= 0 ? '+' : '';
            totalEl.innerText = `${signTotal}$${total.toFixed(2)}`;
            totalEl.style.color = total >= 0 ? '#166534' : '#991b1b';
          } else if (totalEl) {
            totalEl.innerText = 'n/a';
            totalEl.style.color = '#334155';
          }
        } else {
          perfEl.innerText = 'n/a';
          perfEl.style.color = '#334155';
          if (totalEl) { totalEl.innerText = 'n/a'; totalEl.style.color = '#334155'; }
        }
      }
    }
  } catch (err) {
    document.getElementById(key + '_price').innerText = 'DATA MISSING';
    document.getElementById(key + '_chg').innerText = 'DATA MISSING';
    if(includeAction){
      const scoreEl = document.getElementById(key + '_score');
      if (scoreEl) scoreEl.innerText = 'n/a';
      document.getElementById(key + '_why').innerText = 'Live signal or quote data missing.';
    }
  }
}

let index = 0;
let inflight = 0;
const MAX_INFLIGHT = 2;
const REQUEST_GAP_MS = 350;

function pumpRows() {
  while (inflight < MAX_INFLIGHT && index < symbols.length) {
    const symbol = symbols[index++];
    inflight++;
    refreshRow(symbol, includeAction)
      .catch(() => {})
      .finally(() => {
        inflight--;
        if (index < symbols.length || inflight > 0) {
          setTimeout(pumpRows, REQUEST_GAP_MS);
        }
      });
  }
}

function closeCompanyModal(){
  const modal = document.getElementById('companyModal');
  if (modal) modal.style.display = 'none';
}

async function openCompanyModal(symbol){
  const modal = document.getElementById('companyModal');
  const title = document.getElementById('companyModalTitle');
  const body = document.getElementById('companyModalBody');
  if (!modal || !title || !body) return;

  modal.style.display = 'flex';
  title.innerText = symbol;
  body.innerText = 'Loading company profile...';

  try {
    const res = await fetch('/company-profile?symbol=' + encodeURIComponent(symbol));
    const data = await res.json();
    if (!res.ok) {
      body.innerText = data.error || 'Unable to load company info.';
      return;
    }
    title.innerText = `${data.symbol} — ${data.name || 'Company Name Unavailable'}`;
    body.innerText = data.description || 'No description available for this company.';
  } catch (err) {
    body.innerText = 'Unable to load company info right now.';
  }
}

document.addEventListener('click', (event) => {
  const link = event.target.closest('.ticker-link');
  if (!link) return;
  event.preventDefault();
  openCompanyModal(link.dataset.symbol || '');
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeCompanyModal();
});

pumpRows();  // kick it off
</script>
'''.replace('__SYMBOLS__', symbols_json).replace('__INCLUDE_ACTION__', 'true' if include_action else 'false')

    return f'''
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body{{font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:28px;background:#f8fafc}}
    .table{{border-collapse:collapse;width:100%;background:#fff}}
    th,td{{border:1px solid #e5e7eb;padding:12px;text-align:left;vertical-align:top}}
    th{{background:#1e3a8a;color:#fff}}
    .buy{{color:#166534;font-weight:700}} .hold{{color:#92400e;font-weight:700}} .sell{{color:#991b1b;font-weight:700}}
    a{{color:#1d4ed8;text-decoration:none}}
    .toolbar{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;padding:8px;background:#dbeafe;border-radius:12px}} .tab{{display:inline-block;padding:8px 12px;border-radius:8px;text-decoration:none;color:#1e3a8a;font-weight:600}} .tab:hover{{background:#bfdbfe}} .tab.active{{background:#1e40af;color:#fff}}
    .modal{{position:fixed;inset:0;background:rgba(15,23,42,.5);display:none;align-items:center;justify-content:center;padding:20px;z-index:1000}}
    .modal-card{{background:#fff;max-width:560px;width:100%;border-radius:12px;padding:16px 18px;box-shadow:0 18px 40px rgba(2,6,23,.25)}}
    .modal-title{{margin:0 0 8px;color:#1e3a8a;font-size:1.05rem}}
    .modal-close{{border:none;background:#e2e8f0;color:#0f172a;padding:8px 10px;border-radius:8px;cursor:pointer;font-weight:700}}
  </style>
</head>
<body>
  {_top_tabs(active_tab)}
  <h1>{title}</h1>
  <p>{subtitle}</p>
  <p style='margin-top:0;color:#475569'>Score is the model's composite conviction (0-12): higher scores indicate stronger buy setups.</p>
  {management}
  <table class="table">
    {headers}
    {''.join(rows)}
  </table>
  <p><a href="/">← Back</a></p>
  <div id="companyModal" class="modal" onclick="if(event.target.id==='companyModal') closeCompanyModal()">
    <div class="modal-card">
      <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
        <h3 id="companyModalTitle" class="modal-title">Company</h3>
        <button class="modal-close" onclick="closeCompanyModal()">Close</button>
      </div>
      <p id="companyModalBody" style="margin:0;color:#334155;line-height:1.5">Select a ticker to view details.</p>
    </div>
  </div>
  {script}
</body>
</html>
'''


@app.route('/', methods=['GET'])
def home():
    return render_template_string(
        '''
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body{font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#eef3ff;color:#1f2937}
    .wrap{max-width:1000px;margin:0 auto;padding:32px}
    h1{font-size:3rem;margin:0;color:#1a237e}
    .sub{font-size:1.1rem;color:#455a64;margin:8px 0 24px}
    .ask{margin-top:26px;background:#fff;border-radius:16px;padding:20px;box-shadow:0 10px 24px rgba(0,0,0,.08)}
    .markets{margin-top:26px;background:#fff;border-radius:16px;padding:20px;box-shadow:0 10px 24px rgba(0,0,0,.08)}
    .markets-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
    .range-tabs{display:flex;gap:8px;flex-wrap:wrap}
    .range-tab{border:none;background:#dbeafe;color:#1e3a8a;padding:8px 12px;border-radius:999px;font-weight:700;cursor:pointer}
    .range-tab.active{background:#1e40af;color:#fff}
    .market-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:12px}
    .mcard{border:1px solid #dbeafe;border-radius:12px;padding:12px;background:#f8fbff;display:flex;flex-direction:column;gap:6px}
    .mname{font-weight:700;color:#1e3a8a}
    .mprice{font-size:1.1rem;margin-top:4px}
    .mchg.up{color:#166534;font-weight:700}
    .mchg.down{color:#991b1b;font-weight:700}
    .mchg.flat{color:#475569;font-weight:700}
    .chart-wrap{height:58px;position:relative}
    .chart-missing{font-size:0.78rem;color:#64748b;padding-top:8px}
    input{padding:12px;border-radius:10px;border:1px solid #cbd5e1;width:220px}
    button{padding:12px 14px;border:none;border-radius:10px;background:#1e40af;color:#fff;cursor:pointer}
    #out{margin-top:10px;line-height:1.6}
    .toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;padding:8px;background:#dbeafe;border-radius:12px} .tab{display:inline-block;padding:8px 12px;border-radius:8px;text-decoration:none;color:#1e3a8a;font-weight:600} .tab:hover{background:#bfdbfe} .tab.active{background:#1e40af;color:#fff}
  </style>
</head>
<body>
  <div class="wrap">
    {{ toolbar|safe }}
    <div style="margin-bottom:8px">{% if not user_id %}<a href="/signup">Sign up</a>{% endif %} {% if user_id %}<span style="color:#475569">Logged in as {{ user_email }}</span>{% endif %}</div>
    <h1>MoneyBot Pro</h1>
    <p class="sub">Hybrid stock advisor with technical indicators, sentiment analysis, and transparent rule-based buy/hold/sell signals.</p>

    <div class="markets">
      <div class="markets-head">
        <div>
          <h3 style="margin:0">Markets</h3>
          <p style="margin:6px 0 0">Major indices and key assets with real-time level, daily % change, and trend sparkline.</p>
        </div>
        <div class="range-tabs" role="tablist" aria-label="Market history range">
          <button class="range-tab active" data-range="1mo" onclick="setMarketRange('1mo')">1M</button>
          <button class="range-tab" data-range="3mo" onclick="setMarketRange('3mo')">3M</button>
          <button class="range-tab" data-range="1y" onclick="setMarketRange('1y')">1Y</button>
        </div>
      </div>
      <div id="marketGrid" class="market-grid"></div>
    </div>

    <div class="ask">
      <h3>Quick Signal Lookup</h3>
      <input id="sym" placeholder="AAPL, SOFI, PLTR" onkeydown="if(event.key==='Enter'){lookup();}" />
      <button onclick="lookup()">Analyze</button>
      <div id="out"></div>
    </div>
  </div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
let currentRange = '1mo';
let marketItems = [];
const marketCharts = {};

function safeId(symbol){
  return symbol.replace(/[^a-zA-Z0-9]/g, '_');
}

function updateRangeButtons(){
  document.querySelectorAll('.range-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.range === currentRange);
  });
}

function setMarketRange(rangeValue){
  currentRange = rangeValue;
  updateRangeButtons();
  refreshAllMarketCharts();
}

function makeMarketCard(item){
  const id = safeId(item.symbol);
  const card = document.createElement('div');
  card.className = 'mcard';
  card.innerHTML = `<div class="mname">${item.label}</div><div class="mprice" id="mp_${id}">DATA MISSING</div><div class="mchg flat" id="mc_${id}">DATA MISSING</div><div class="chart-wrap"><canvas id="chart_${id}"></canvas><div class="chart-missing" id="md_${id}" style="display:none">DATA MISSING</div></div>`;
  return card;
}

function applyMarketQuote(item){
  const id = safeId(item.symbol);
  const p = item.price;
  const c = item.change_percent;
  const priceText = (p === 'DATA_MISSING' || p === undefined) ? 'DATA MISSING' : Number(p).toLocaleString(undefined, {maximumFractionDigits: 2});
  const changeText = (c === 'DATA_MISSING' || c === undefined) ? 'DATA MISSING' : `${Number(c).toFixed(2)}%`;
  let cls = 'flat';
  if (typeof c === 'number') cls = c > 0 ? 'up' : (c < 0 ? 'down' : 'flat');
  const pe = document.getElementById('mp_' + id);
  const ce = document.getElementById('mc_' + id);
  if (pe) pe.innerText = priceText;
  if (ce) { ce.innerText = changeText; ce.className = 'mchg ' + cls; }
}

async function fetchMarketHistory(symbol, rangeValue){
  try {
    const res = await fetch(`/market-history?symbol=${encodeURIComponent(symbol)}&range=${encodeURIComponent(rangeValue)}`);
    const data = await res.json();
    return data.points || [];
  } catch (err){
    return [];
  }
}

function renderMarketChart(symbol, points){
  const id = safeId(symbol);
  const canvas = document.getElementById('chart_' + id);
  const missingEl = document.getElementById('md_' + id);
  if (!canvas || !missingEl) return;

  if (marketCharts[id]) {
    marketCharts[id].destroy();
    delete marketCharts[id];
  }

  if (!points || points.length === 0) {
    canvas.style.display = 'none';
    missingEl.style.display = 'block';
    return;
  }

  canvas.style.display = 'block';
  missingEl.style.display = 'none';
  const labels = points.map(p => p.t);
  const values = points.map(p => p.v);
  const color = values.length > 1 && values[values.length - 1] >= values[0] ? '#16a34a' : '#dc2626';

  marketCharts[id] = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: { labels, datasets: [{ data: values, borderColor: color, borderWidth: 2, fill: false, tension: 0.3, pointRadius: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } }, animation: false }
  });
}

async function refreshAllMarketCharts(){
  await Promise.all(marketItems.map(async item => {
    const points = await fetchMarketHistory(item.symbol, currentRange);
    renderMarketChart(item.symbol, points);
  }));
}

function renderMarkets(items){
  const grid = document.getElementById('marketGrid');
  grid.innerHTML = '';
  marketItems = items || [];
  marketItems.forEach(item => {
    grid.appendChild(makeMarketCard(item));
    applyMarketQuote(item);
  });
}

async function loadMarkets(){
  try {
    const res = await fetch('/market-overview');
    const data = await res.json();
    renderMarkets(data.markets || []);
  } catch (err) {
    renderMarkets([]);
  }
  await refreshAllMarketCharts();
}

async function lookup(){
  const el = document.getElementById('out');
  const sym = (document.getElementById('sym').value || '').trim().toUpperCase();
  if(!sym){ el.textContent = 'Enter a symbol.'; return; }
  el.textContent = 'Analyzing...';
  try {
    const res = await fetch('/signal?symbol=' + encodeURIComponent(sym));
    const data = await res.json();
    if(!res.ok){ el.textContent = data.error || 'Error'; return; }
    el.innerHTML = `<b>${data.symbol}</b> → <b>${data.action}</b> (confidence ${(data.confidence*100).toFixed(0)}%)<br>RSI: ${data.technical?.rsi ?? 'n/a'} | Sentiment: ${data.sentiment?.score ?? 'n/a'} (${data.sentiment?.label ?? 'n/a'})<br>${(data.rationale || []).slice(0,3).join('<br>')}`;
  } catch(err){
    el.textContent = 'Could not fetch signal.';
  }
}

updateRangeButtons();
loadMarkets();
setInterval(loadMarkets, 60000);
</script>
</body>
</html>
''',
        user_id=_current_user_id(),
        user_email=_current_user_email(),
        toolbar=_top_tabs('home'),
    )


@app.route('/market-overview', methods=['GET'])
def market_overview():
    try:
        return jsonify({'markets': get_market_overview(), 'source': 'yfinance'})
    except Exception as error:
        logging.error('Market overview error: %s', error)
        fallback = []
        for symbol, label in MARKET_OVERVIEW_SYMBOLS:
            fallback.append({
                'symbol': symbol,
                'label': label,
                'price': 'DATA_MISSING',
                'change_percent': 'DATA_MISSING',
                'live_data_available': False,
                'quote_source': 'yfinance',
            })
        return jsonify({'markets': fallback, 'source': 'yfinance'}), 500


@app.route('/market-history', methods=['GET'])
def market_history():
    symbol = (request.args.get('symbol') or '').strip()
    range_value = (request.args.get('range') or '1mo').strip().lower()

    allowed_ranges = {'1mo': '1mo', '3mo': '3mo', '1y': '1y'}
    if range_value not in allowed_ranges:
        return jsonify({'symbol': symbol, 'range': range_value, 'points': [], 'error': 'invalid range'}), 400

    allowed_symbols = {sym for sym, _ in MARKET_OVERVIEW_SYMBOLS}
    if symbol not in allowed_symbols:
        return jsonify({'symbol': symbol, 'range': range_value, 'points': [], 'error': 'unsupported symbol'}), 400

    try:
        history = yf.Ticker(symbol).history(period=allowed_ranges[range_value], interval='1d')
        points = []
        if history is not None and not history.empty and 'Close' in history.columns:
            closes = history['Close'].dropna()
            for ts, value in closes.items():
                date_label = ts.strftime('%Y-%m-%d') if hasattr(ts, 'strftime') else str(ts)
                fv = _to_float(value)
                if fv is not None:
                    points.append({'t': date_label, 'v': fv})
        return jsonify({'symbol': symbol, 'range': range_value, 'points': points})
    except Exception as error:
        logging.warning('Market history unavailable for %s (%s): %s', symbol, range_value, error)
        return jsonify({'symbol': symbol, 'range': range_value, 'points': [], 'error': 'history unavailable'})


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if not email or '@' not in email:
            error = 'Enter a valid email.'
        elif len(password) < 8:
            error = 'Password must be at least 8 characters.'
        else:
            created, err = _add_user(email, password)
            if created:
                user = _verify_user(email, password)
                session['user_id'] = user['id']
                session['user_email'] = user['email']
                return redirect(url_for('user_watchlist'))
            error = err

    return render_template_string(
        '''
<html><body style="font-family:Inter;padding:30px;background:#f8fafc">
  <h1>Create account</h1>
  {% if error %}<p style="color:#b91c1c">{{ error }}</p>{% endif %}
  <form method="post" style="display:grid;gap:10px;max-width:380px">
    <input name="email" type="email" placeholder="Email" required style="padding:10px" />
    <input name="password" type="password" placeholder="Password" required style="padding:10px" />
    <button style="padding:10px;background:#1e40af;color:#fff;border:none;border-radius:8px">Sign up</button>
  </form>
  <p><a href="/login">Already have an account? Login</a> · <a href="/">Home</a></p>
</body></html>
''',
        error=error,
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    next_page = request.args.get('next') or request.form.get('next') or url_for('user_watchlist')
    if not next_page.startswith('/') or next_page.startswith('//'):
        next_page = url_for('user_watchlist')

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = _verify_user(email, password)
        if user:
            session['user_id'] = user['id']
            session['user_email'] = user['email']
            return redirect(next_page)
        error = 'Invalid email or password.'

    return render_template_string(
        '''
<html><body style="font-family:Inter;padding:30px;background:#f8fafc">
  <h1>Login</h1>
  {% if error %}<p style="color:#b91c1c">{{ error }}</p>{% endif %}
  <form method="post" style="display:grid;gap:10px;max-width:380px">
    <input type="hidden" name="next" value="{{ next_page }}" />
    <input name="email" type="email" placeholder="Email" required style="padding:10px" />
    <input name="password" type="password" placeholder="Password" required style="padding:10px" />
    <button style="padding:10px;background:#1e40af;color:#fff;border:none;border-radius:8px">Login</button>
  </form>
  <p><a href="/signup">Create account</a> · <a href="/">Home</a></p>
</body></html>
''',
        error=error,
        next_page=next_page,
    )


@app.route('/logout', methods=['GET'])
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/quote', methods=['GET'])
def quote():
    symbol = request.args.get('symbol', '').strip().upper()
    if not _is_valid_symbol(symbol):
        return jsonify(QUOTE_FALLBACK), 400

    try:
        return jsonify(get_quote_data(symbol))
    except Exception as error:
        logging.error('Quote error for %s: %s', symbol, error)
        return jsonify(QUOTE_FALLBACK), 500


@app.route('/company-profile', methods=['GET'])
def company_profile():
    symbol = request.args.get('symbol', '').strip().upper()
    if not _is_valid_symbol(symbol):
        return jsonify({'error': 'invalid symbol format'}), 400

    try:
        info = yf.Ticker(symbol).info or {}
        return jsonify({
            'symbol': symbol,
            'name': info.get('longName') or info.get('shortName') or symbol,
            'description': info.get('longBusinessSummary') or 'No company description available.',
        })
    except Exception as error:
        logging.warning('Company profile unavailable for %s: %s', symbol, error)
        return jsonify({
            'symbol': symbol,
            'name': symbol,
            'description': 'No company description available right now.',
        })


@app.route('/signal', methods=['GET'])
def signal():
    symbol = request.args.get('symbol', '').strip().upper()
    if not _is_valid_symbol(symbol):
        return jsonify({'error': 'invalid symbol format'}), 400

    try:
        quote_data = get_quote_data(symbol)
        signal_data = _hybrid_signal_engine(symbol)
        signal_data['quote'] = quote_data
        signal_data['quote_data_available'] = bool(quote_data.get('live_data_available'))
        signal_data['data_provider'] = quote_data.get('quote_source', 'yfinance')
        return jsonify(signal_data)
    except Exception as error:
        logging.error('Signal engine error for %s: %s', symbol, error)
        return jsonify({'error': 'signal unavailable'}), 500


@app.route('/watchlist', methods=['GET'])
def watchlist():
    picks = _filter_buy_candidates(LONG_TERM_CANDIDATES, limit=10, max_price=None)
    symbols = [ticker for ticker, _ in picks]
    return _render_watchlist_page(
        title='🏛️ Long-Term Stable Watchlist',
        subtitle='Top 10 long-term buy stocks based on model score and fundamentals.',
        symbols=symbols,
        include_action=True,
        active_tab='stable',
    )


@app.route('/hot-watchlist', methods=['GET'])
def hot_watchlist():
    picks = _filter_buy_candidates(HOT_WATCHLIST_CANDIDATES, limit=10, max_price=50.0)
    symbols = [ticker for ticker, _ in picks]
    return _render_watchlist_page(
        title='⚡ Hot Momentum Buys',
        subtitle='Top 10 momentum buys under $50, ranked by model score.',
        symbols=symbols,
        include_action=True,
        active_tab='hot',
    )


@app.route('/whales', methods=['GET'])
def whales_watchlist():
    sections = []
    for investor, picks in WHALE_INVESTOR_PICKS.items():
        rows = ''.join([f"<li><b>{ticker}</b> — {company}</li>" for ticker, company in picks[:5]])
        sections.append(f"<div style='background:#fff;border:1px solid #dbeafe;border-radius:12px;padding:14px'><h3 style='margin:0 0 8px'>{investor}</h3><ul style='margin:0;padding-left:18px;line-height:1.7'>{rows}</ul></div>")

    return f'''
<html><head><meta name="viewport" content="width=device-width, initial-scale=1" />
<style>body{{font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:28px;background:#f8fafc}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}} .toolbar{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;padding:8px;background:#dbeafe;border-radius:12px}} .tab{{display:inline-block;padding:8px 12px;border-radius:8px;text-decoration:none;color:#1e3a8a;font-weight:600}} .tab:hover{{background:#bfdbfe}} .tab.active{{background:#1e40af;color:#fff}} a{{color:#1d4ed8;text-decoration:none}}</style></head>
<body>{_top_tabs('whales')}<h1>🐋 Whales of Wall Street</h1><p>Top investors and their widely tracked top 5 picks.</p><div class='grid'>{''.join(sections)}</div><p><a href='/'>← Back</a></p></body></html>
'''


@app.route('/user-watchlist', methods=['GET', 'POST'])
@_login_required
def user_watchlist():
    error = None
    user_id = _current_user_id()

    if request.method == 'POST':
        ticker = request.form.get('ticker', '').strip().upper()
        buy_price_raw = request.form.get('buy_price', '').strip()
        shares_raw = request.form.get('shares', '').strip()
        buy_price = _to_float(buy_price_raw) if buy_price_raw else None
        shares = _to_float(shares_raw) if shares_raw else None
        if not _is_valid_symbol(ticker):
            error = 'Invalid ticker symbol format.'
        elif buy_price is not None and buy_price <= 0:
            error = 'Buy price must be greater than zero.'
        elif shares is not None and shares <= 0:
            error = 'Shares must be greater than zero.'
        else:
            ok, err = _add_user_ticker(user_id, ticker, buy_price, shares)
            if not ok:
                error = err

    symbols = _get_user_tickers(user_id)
    positions = _get_user_positions(user_id)
    page = _render_watchlist_page(
        title='👤 User Watchlist',
        subtitle='Track your buy price, live performance, and keep full buy/hold/sell signal transparency.',
        symbols=symbols,
        include_action=True,
        user_page=True,
        active_tab='user',
        user_positions=positions,
    )

    if error:
        page = page.replace('</p>\n  <table', f'</p><p style="color:#b91c1c">{error}</p>\n  <table')
    return page


@app.route('/user-watchlist/position', methods=['POST'])
@_login_required
def update_user_position():
    ticker = request.form.get('ticker', '').strip().upper()
    buy_price_raw = request.form.get('buy_price', '').strip()
    shares_raw = request.form.get('shares', '').strip()
    if _is_valid_symbol(ticker):
        buy_price = _to_float(buy_price_raw) if buy_price_raw else None
        shares = _to_float(shares_raw) if shares_raw else None
        if (buy_price is None or buy_price > 0) and (shares is None or shares > 0):
            _update_position(_current_user_id(), ticker, buy_price, shares)
    return redirect(url_for('user_watchlist'))


@app.route('/user-watchlist/remove', methods=['POST'])
@_login_required
def remove_user_watchlist_ticker():
    ticker = request.form.get('ticker', '').strip().upper()
    if _is_valid_symbol(ticker):
        _remove_user_ticker(_current_user_id(), ticker)
    return redirect(url_for('user_watchlist'))


try:
    _init_db()
except Exception as error:
    logging.error('Database initialization failed at %s: %s', DB_PATH, error)
    raise

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=5000, debug=True)
    except Exception as error:
        logging.error('App failed to start: %s', error)
        raise
