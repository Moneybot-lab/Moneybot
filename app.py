import logging

from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)

CORS(app)

logging.basicConfig(level=logging.INFO)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_percent(value):
    numeric_value = _to_float(value)
    if numeric_value is None:
        return None
    return numeric_value * 100


def _is_valid_symbol(symbol):
    return bool(symbol) and all(ch.isalnum() or ch in {'.', '-', '^'} for ch in symbol)

def get_quote_data(symbol):
    ticker = yf.Ticker(symbol)
    info = {}

    try:
        info = ticker.info or {}
    except Exception as e:
        logging.warning(f"Ticker info unavailable for {symbol}: {e}")

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
            history = ticker.history(period='2d', interval='1d')
            if not history.empty:
                latest_close = history['Close'].iloc[-1]
                prev_close = history['Close'].iloc[-2] if len(history.index) > 1 else None

                if price is None:
                    price = latest_close
                if previous_close is None and prev_close is not None:
                    previous_close = prev_close
                if change_percent is None and prev_close not in (None, 0):
                    change_percent = ((latest_close - prev_close) / prev_close) * 100
        except Exception as e:
            logging.warning(f"Price history unavailable for {symbol}: {e}")

    if change_percent is None and price is not None and previous_close not in (None, 0):
        change_percent = ((price - previous_close) / previous_close) * 100

    return {
        "price": _to_float(price) if price is not None else "N/A",
        "change_percent": _to_float(change_percent) if change_percent is not None else "N/A"
    }


def get_long_term_investor_analysis(symbol):
    ticker = yf.Ticker(symbol)

    try:
        info = ticker.info or {}
    except Exception as e:
        logging.warning(f"Long-term info unavailable for {symbol}: {e}")
        info = {}

    try:
        history = ticker.history(period='5y', interval='1mo')
    except Exception as e:
        logging.warning(f"Long-term history unavailable for {symbol}: {e}")
        history = None

    growth_1y = None
    growth_3y = None
    growth_5y = None

    if history is not None and not history.empty and 'Close' in history.columns:
        closes = history['Close'].dropna()
        if not closes.empty:
            latest = closes.iloc[-1]

            def pct_change(months_back):
                if len(closes.index) <= months_back:
                    return None
                start_price = closes.iloc[-(months_back + 1)]
                if start_price in (None, 0):
                    return None
                return ((latest - start_price) / start_price) * 100

            growth_1y = pct_change(12)
            growth_3y = pct_change(36)
            growth_5y = pct_change(60)

    roe = _to_percent(info.get('returnOnEquity'))
    profit_margin = _to_percent(info.get('profitMargins'))
    operating_margin = _to_percent(info.get('operatingMargins'))
    debt_to_equity = _to_float(info.get('debtToEquity'))
    current_ratio = _to_float(info.get('currentRatio'))
    free_cashflow = _to_float(info.get('freeCashflow'))
    operating_cashflow = _to_float(info.get('operatingCashflow'))
    revenue_growth = _to_percent(info.get('revenueGrowth'))
    earnings_growth = _to_percent(info.get('earningsGrowth'))
    beta = _to_float(info.get('beta'))

    risk_points = 0

    if debt_to_equity is not None and debt_to_equity > 150:
        risk_points += 2
    elif debt_to_equity is not None and debt_to_equity > 80:
        risk_points += 1

    if current_ratio is not None and current_ratio < 1:
        risk_points += 1

    if free_cashflow is not None and free_cashflow < 0:
        risk_points += 2

    if operating_margin is not None and operating_margin < 0:
        risk_points += 2
    elif operating_margin is not None and operating_margin < 8:
        risk_points += 1

    if beta is not None and beta > 1.5:
        risk_points += 1

    if growth_3y is not None and growth_3y < 0:
        risk_points += 1

    risk_level = 'low' if risk_points <= 1 else 'moderate' if risk_points <= 3 else 'high'

    return {
        'ticker': symbol,
        'long_term_growth': {
            'revenue_growth_pct': revenue_growth,
            'earnings_growth_pct': earnings_growth,

    return {
        "price": float(price) if price is not None else "N/A",
        "change_percent": float(change_percent) if change_percent is not None else "N/A"
    }

def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_long_term_investor_analysis(symbol):
    ticker = yf.Ticker(symbol)

    try:
        info = ticker.info or {}
    except Exception as e:
        logging.warning(f"Long-term info unavailable for {symbol}: {e}")
        info = {}

    try:
        history = ticker.history(period='5y', interval='1mo')
    except Exception as e:
        logging.warning(f"Long-term history unavailable for {symbol}: {e}")
        history = None

    growth_1y = None
    growth_3y = None
    growth_5y = None

    if history is not None and not history.empty and 'Close' in history.columns:
        closes = history['Close'].dropna()
        if not closes.empty:
            latest = closes.iloc[-1]

            def pct_change(months_back):
                if len(closes.index) <= months_back:
                    return None
                start_price = closes.iloc[-(months_back + 1)]
                if start_price in (None, 0):
                    return None
                return ((latest - start_price) / start_price) * 100

            growth_1y = pct_change(12)
            growth_3y = pct_change(36)
            growth_5y = pct_change(60)

    roe = _to_float(info.get('returnOnEquity'))
    profit_margin = _to_float(info.get('profitMargins'))
    operating_margin = _to_float(info.get('operatingMargins'))
    debt_to_equity = _to_float(info.get('debtToEquity'))
    current_ratio = _to_float(info.get('currentRatio'))
    free_cashflow = _to_float(info.get('freeCashflow'))
    operating_cashflow = _to_float(info.get('operatingCashflow'))
    revenue_growth = _to_float(info.get('revenueGrowth'))
    earnings_growth = _to_float(info.get('earningsGrowth'))
    beta = _to_float(info.get('beta'))

    risk_points = 0

    if debt_to_equity is not None and debt_to_equity > 150:
        risk_points += 2
    elif debt_to_equity is not None and debt_to_equity > 80:
        risk_points += 1

    if current_ratio is not None and current_ratio < 1:
        risk_points += 1

    if free_cashflow is not None and free_cashflow < 0:
        risk_points += 2

    if operating_margin is not None and operating_margin < 0:
        risk_points += 2
    elif operating_margin is not None and operating_margin < 0.08:
        risk_points += 1

    if beta is not None and beta > 1.5:
        risk_points += 1

    if growth_3y is not None and growth_3y < 0:
        risk_points += 1

    risk_level = 'low' if risk_points <= 1 else 'moderate' if risk_points <= 3 else 'high'

    return {
        'ticker': symbol,
        'long_term_growth': {
            'revenue_growth': revenue_growth,
            'earnings_growth': earnings_growth,
            'price_growth_1y_pct': growth_1y,
            'price_growth_3y_pct': growth_3y,
            'price_growth_5y_pct': growth_5y,
        },
        'financial_health': {
            'return_on_equity_pct': roe,
            'profit_margin_pct': profit_margin,
            'operating_margin_pct': operating_margin,
            'return_on_equity': roe,
            'profit_margin': profit_margin,
            'operating_margin': operating_margin,
            'debt_to_equity': debt_to_equity,
            'current_ratio': current_ratio,
            'free_cash_flow': free_cashflow,
            'operating_cash_flow': operating_cashflow,
        },
        'risk_assessment': {
            'risk_level': risk_level,
            'risk_score': risk_points,
            'summary': (
                f"{symbol} is assessed as {risk_level} risk for long-term investors "
                f"based on leverage, profitability, cash flow, and volatility metrics."
            ),
        },
    }

@app.route('/', methods=['GET'])
def home():
    return '''
<html>
<head>
    <style>
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #e0f7fa, #f5f5f5); 
            color: #333;
            margin: 0;
            padding: 0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            padding: 40px 20px;
            flex: 1;
        }
        h1 {
            text-align: center;
            color: #1a237e;
            font-size: 3.5em;
            margin-bottom: 20px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.1);
        }
        .tagline {
            text-align: center;
            color: #455a64;
            font-size: 1.3em;
            margin-bottom: 30px;
        }
        .input-group {
            display: flex;
            justify-content: center;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        input, select {
            padding: 14px 20px;
            font-size: 1.1em;
            border: 2px solid #1e88e5;
            border-radius: 12px;
            outline: none;
            transition: border 0.3s;
            min-width: 250px;
        }
        input:focus, select:focus {
            border-color: #0d47a1;
            box-shadow: 0 0 0 3px rgba(29, 68, 125, 0.2);
        }
        button {
            padding: 14px 30px;
            font-size: 1.1em;
            background: #1e88e5;
            color: white;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            transition: background 0.3s, transform 0.1s;
        }
        button:hover {
            background: #1565c0;
            transform: translateY(-2px);
        }
        #response {
            margin-top: 30px;
            padding: 20px;
            background: white;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            font-size: 1.4em;
            text-align: center;
            min-height: 100px;
            transition: opacity 0.5s;
        }
        #loading {
            display: none;
            color: #1e88e5;
            font-style: italic;
            margin: 20px 0;
        }
        .links {
            text-align: center;
            margin-top: 40px;
        }
        .links a {
            color: #1e88e5;
            text-decoration: none;
            margin: 0 15px;
            font-size: 1.2em;
        }
        .links a:hover {
            text-decoration: underline;
        }
        footer {
            text-align: center;
            padding: 20px;
            color: #78909c;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>MoneyBot</h1>
        <p class="tagline">Your AI stock advisor—real-time advice, no fluff.</p>
        
        <div class="input-group">
            <input id="custom" placeholder="TSLA, AAPL, NVDA..." autofocus>
            <button onclick="ask()">Ask</button>
        </div>
        
        <div id="loading">Thinking...</div>
        <div id="response"></div>
        
        <div class="links">
            <a href="/whales">📊 Whales Tracker</a>
            <a href="/watchlist">🔥 Watchlist</a>
        </div>
    </div>
    
    <footer>Powered by yfinance · Built by Troy · 2026</footer>
    
<script>
async function ask() {
    const loading = document.getElementById('loading');
    const response = document.getElementById('response');
    let ticker = document.getElementById('custom').value.trim().toUpperCase();
    if (!ticker) {
        response.innerText = "Enter a ticker!";
        return;
    }
    response.innerText = '';
    loading.style.display = 'block';

    try {
        const res = await fetch('/advice?text=' + encodeURIComponent(ticker));
        if (!res.ok) throw new Error('Bad response');
        const data = await res.json();
        response.innerHTML = data.tip;
    } catch (e) {
        console.log('Fetch error:', e);
        response.innerText = "Couldn't fetch—try again.";
    }
    loading.style.display = 'none';
}

document.getElementById('custom').addEventListener('keypress', e => {
    if (e.key === 'Enter') ask();
});
</script>
</body>
</html>
'''

@app.route('/whales', methods=['GET'])
def whales():
    return '''
    <html>
    <head>
    <style>
        body{font-family:Arial;padding:30px;background:#f8f9fa}
        h1{margin:0 0 20px;color:#2c3e50}
        h2{margin:40px 0 10px}
        table{border-collapse:collapse;width:100%;margin-bottom:30px}
        th,td{border:1px solid #ddd;padding:12px;text-align:left}
        th{background:#3498db;color:white}
        .note{background:#fff3cd;padding:15px;border-radius:8px;margin:20px 0}
    </style>
    </head>
    <body>
    <h1>💰 Wall Street Whales Tracker</h1>
    <p>Latest 13F filings + live prices (Yahoo Finance)</p>

    <h2>Warren Buffett (Berkshire Hathaway)</h2>
    <table><tr><th>Ticker</th><th>Company</th><th>Live Price</th></tr>
    <tr><td>AAPL</td><td>Apple</td><td id="aapl"></td></tr>
    <tr><td>AXP</td><td>American Express</td><td id="axp"></td></tr>
    <tr><td>BAC</td><td>Bank of America</td><td id="bac"></td></tr>
    <tr><td>KO</td><td>Coca-Cola</td><td id="ko"></td></tr>
    <tr><td>CVX</td><td>Chevron</td><td id="cvx"></td></tr></table>

    <h2>George Soros</h2>
    <table><tr><th>Ticker</th><th>Company</th><th>Live Price</th></tr>
    <tr><td>AMZN</td><td>Amazon</td><td id="amzn"></td></tr>
    <tr><td>DBX</td><td>Dropbox</td><td id="dbx"></td></tr>
    <tr><td>SPOT</td><td>Spotify</td><td id="spot"></td></tr>
    <tr><td>GOOGL</td><td>Alphabet</td><td id="googl"></td></tr>
    <tr><td>TSLA</td><td>Tesla</td><td id="tsla"></td></tr></table>

    <h2>Ken Griffin (Citadel)</h2>
    <table><tr><th>Ticker</th><th>Company</th><th>Live Price</th></tr>
    <tr><td>NVDA</td><td>Nvidia</td><td id="nvda"></td></tr>
    <tr><td>AMZN</td><td>Amazon</td><td id="amzn2"></td></tr>
    <tr><td>AAPL</td><td>Apple</td><td id="aapl2"></td></tr>
    <tr><td>MSFT</td><td>Microsoft</td><td id="msft"></td></tr>
    <tr><td>GOOGL</td><td>Alphabet</td><td id="googl2"></td></tr></table>

    <h2>Jim Simons (Renaissance Technologies)</h2>
    <table><tr><th>Ticker</th><th>Company</th><th>Live Price</th></tr>
    <tr><td>PLTR</td><td>Palantir</td><td id="pltr"></td></tr>
    <tr><td>UTHR</td><td>United Therapeutics</td><td id="uthr"></td></tr>
    <tr><td>MU</td><td>Micron</td><td id="mu"></td></tr>
    <tr><td>VRSN</td><td>Verisign</td><td id="vrsn"></td></tr>
    <tr><td>K.TO</td><td>Kinross Gold</td><td id="kto"></td></tr></table>

    <div class="note">Jim Simons' fund is quant-heavy—no public "favorites," but these are top recent holds.</div>

    <script>
const symbolsById = {
    aapl: "AAPL",
    axp: "AXP",
    bac: "BAC",
    ko: "KO",
    cvx: "CVX",
    amzn: "AMZN",
    dbx: "DBX",
    spot: "SPOT",
    googl: "GOOGL",
    tsla: "TSLA",
    nvda: "NVDA",
    amzn2: "AMZN",
    aapl2: "AAPL",
    msft: "MSFT",
    googl2: "GOOGL",
    pltr: "PLTR",
    uthr: "UTHR",
    mu: "MU",
    vrsn: "VRSN",
    kto: "K.TO"
};

Object.entries(symbolsById).forEach(([id, symbol]) => {
    fetch(`/quote?symbol=${encodeURIComponent(symbol)}`)
    .then(r => r.json())
    .then(data => {
        const price = data.price ?? "N/A";
        document.getElementById(id).innerText = price === "N/A" ? "N/A" : `$${Number(price).toFixed(2)}`;
    })
    .catch(() => document.getElementById(id).innerText = "N/A");
});
</script>
<p style="font-style:italic; color:#555;">Live prices during market hours—weekends show N/A or last close.</p>
    <p><a href="/">← Back to MoneyBot</a></p>
    </body>
    </html>
    '''

QUOTE_FALLBACK = {"price": "N/A", "change_percent": "N/A"}


@app.route('/quote', methods=['GET'])
def quote():
    symbol = request.args.get('symbol', '').strip().upper()
    if not _is_valid_symbol(symbol):
        return jsonify(QUOTE_FALLBACK), 400

    try:
        quote_data = get_quote_data(symbol)
        return jsonify(quote_data)
    except Exception as e:
        logging.error(f"Quote error for {symbol}: {e}")
        return jsonify(QUOTE_FALLBACK), 500


@app.route('/advice', methods=['GET'])
def advice():
    ticker = request.args.get('text', '').strip().upper() or 'TSLA'
    if not _is_valid_symbol(ticker):
        return jsonify({"tip": "Invalid ticker symbol."})

    tip = "Data unavailable right now—try another ticker."

    try:
        quote_data = get_quote_data(ticker)
        price = quote_data.get("price", "N/A")
        change = quote_data.get("change_percent", "N/A")

        if price != "N/A" and change != "N/A":
            if change > 1:
                tip = f"<span style='color:#27ae60;'>Buy—strong!</span><br>Price: ${price:.2f}. Up {change:.1f}%."
            elif change < -3:
                tip = f"<span style='color:#e74c3c;'>Sell—weak!</span><br>Price: ${price:.2f}. Down {abs(change):.1f}%."
            else:
                tip = f"<span style='color:#f39c12;'>Hold—steady</span><br>Price: ${price:.2f}. Change {change:+.1f}%."
        elif price != "N/A":
            tip = f"<span style='color:#f39c12;'>Hold—steady</span><br>Price: ${price:.2f}."
        else:
            tip = f"No quote data available for {ticker}."
    except Exception as e:
        logging.error(f"Error: {e}")

    return jsonify({"tip": tip})


@app.route('/long-term-analysis', methods=['GET'])
def long_term_analysis():
    symbol = request.args.get('symbol', '').strip().upper()
    if not symbol:
        return jsonify({'error': 'symbol is required'}), 400
    if not _is_valid_symbol(symbol):
        return jsonify({'error': 'invalid symbol format'}), 400

    try:
        return jsonify(get_long_term_investor_analysis(symbol))
    except Exception as e:
        logging.error(f"Long-term analysis error for {symbol}: {e}")
        return jsonify({'error': 'analysis unavailable'}), 500


@app.route('/watchlist', methods=['GET', 'POST'])
def watchlist():
    return '''
    <html>
    <head><style>body{font-family:Arial;padding:30px;background:#f8f9fa} h1{margin:0 0 20px;color:#1a237e} table{border-collapse:collapse;width:100%} th,td{border:1px solid #ddd;padding:12px;text-align:left} th{background:#3498db;color:white} a{color:#1e88e5;text-decoration:none} a:hover{text-decoration:underline}</style></head>
    <body>
    <h1>🔥 Hot Stocks Watchlist</h1>
    <p>Trending US stocks right now (Yahoo Finance) · Click for advice</p>
    <table>
        <tr><th>Ticker</th><th>Company</th><th>Price</th><th>Change %</th></tr>
        <tr><td>TSLA</td><td>Tesla</td><td id="tsla_price"></td><td id="tsla_change"></td></tr>
        <tr><td>NVDA</td><td>Nvidia</td><td id="nvda_price"></td><td id="nvda_change"></td></tr>
        <tr><td>AAPL</td><td>Apple</td><td id="aapl_price"></td><td id="aapl_change"></td></tr>
        <tr><td>AMZN</td><td>Amazon</td><td id="amzn_price"></td><td id="amzn_change"></td></tr>
        <tr><td>MSFT</td><td>Microsoft</td><td id="msft_price"></td><td id="msft_change"></td></tr>
        <tr><td>GOOGL</td><td>Alphabet</td><td id="googl_price"></td><td id="googl_change"></td></tr>
        <tr><td>META</td><td>Meta</td><td id="meta_price"></td><td id="meta_change"></td></tr>
        <tr><td>AMD</td><td>AMD</td><td id="amd_price"></td><td id="amd_change"></td></tr>
        <tr><td>PLTR</td><td>Palantir</td><td id="pltr_price"></td><td id="pltr_change"></td></tr>
        <tr><td>SMCI</td><td>Super Micro</td><td id="smci_price"></td><td id="smci_change"></td></tr>
    </table>

    <script>
    const stocks = ["TSLA","NVDA","AAPL","AMZN","MSFT","GOOGL","META","AMD","PLTR","SMCI"];
    stocks.forEach(t => {
        fetch(`/quote?symbol=${encodeURIComponent(t)}`)
        .then(r => r.json())
        .then(d => {
            const price = d.price === "N/A" ? "N/A" : `$${Number(d.price).toFixed(2)}`;
            const ch = d.change_percent === "N/A" ? "N/A" : `${Number(d.change_percent).toFixed(2)}%`;
            document.getElementById(t.toLowerCase() + '_price').innerText = price;
            document.getElementById(t.toLowerCase() + '_change').innerText = ch;
        })
        .catch(() => {
            document.getElementById(t.toLowerCase() + '_price').innerText = "N/A";
            document.getElementById(t.toLowerCase() + '_change').innerText = "N/A";
        });
    });
    </script>
    <p><a href="/">← Back to MoneyBot</a> | Market closed—showing last close. Live Monday!</p>
    </body>
    </html>
    '''
if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=5000, debug=True)
    except Exception as e:
        logging.error(f"App failed to start: {e}")
        raise
