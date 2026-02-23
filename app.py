from flask import Flask, request, jsonify
import yfinance as yf
from flask_cors import CORS
import logging

app = Flask(__name__)

CORS(app)

logging.basicConfig(level=logging.INFO)
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
            const res = await fetch('/advice', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: ticker})
            });
            const data = await res.json();
            response.innerHTML = data.tip;
        } catch (e) {
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
const ids = ["aapl","axp","bac","ko","cvx","amzn","dbx","spot","googl","tsla","nvda","aapl2","msft","googl2","pltr","uthr","mu","vrsn","kto","amzn2"];
ids.forEach(id => {
    const t = id.replace(/[0-9]/g,'').toUpperCase();
    fetch(`https://query1.finance.yahoo.com/v7/finance/quote?symbols=${t}`)
    .then(r => r.json())
    .then(data => {
        const q = data.quoteResponse.result[0] || {};
        const price = q.regularMarketPrice?.toFixed(2) || q.regularMarketPreviousClose?.toFixed(2) || "N/A";
        document.getElementById(id).innerText = `$${price}`;
    })
    .catch(() => document.getElementById(id).innerText = "N/A");
});
</script>
<p style="font-style:italic; color:#555;">Live prices during market hours—weekends show N/A or last close.</p>
    <p><a href="/">← Back to MoneyBot</a></p>
    </body>
    </html>
    '''
import logging
import requests
from flask import Flask, request, jsonify
import yfinance as yf
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)

NEWS_API_KEY = "d6dnp5pr01qm89pka11gd6dnp5pr01qm89pka120"

@app.route('/advice', methods=['POST'])
def advice():
    ticker = request.json.get('text', '').strip().upper() or 'TSLA'
    try:
        from functools import lru_cache

        @lru_cache(maxsize=128)
        def get_price(ticker):
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            return hist

        hist = get_price(ticker)
        
            if hist.empty:
               raise ValueError("No price data")

            price = float(hist['Close'].iloc[-1])
            change = float(hist['Close'].pct_change().iloc[-1] * 100)

        if price is None:
            raise ValueError("No price data")

        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        news_url = (
            f"https://finnhub.io/api/v1/company-news"
            f"?symbol={ticker}&from={yesterday}&to={today}&token={"d6dnp5pr01qm89pka11gd6dnp5pr01qm89pka120"}"
        )
        news_response = requests.get(news_url, timeout=5)
        news_data = news_response.json()
        articles = news_data if isinstance(news_data, list) else []

        positive_keywords = ['gain', 'rise', 'strong', 'beat', 'growth']
        negative_keywords = ['loss', 'drop', 'fall', 'miss', 'decline']

        sentiment_score = 0
        for article in articles:
           title = article.get('headline', '').lower()
           if any(word in title for word in positive_keywords):
               sentiment_score += sum(word in title for word in positive_keywords)
           elif any(word in title for word in negative_keywords):
               sentiment_score -= sum(word in title for word in negative_keywords)

        if change > 1 and sentiment_score > 0:
            tip = f"<span style='color:#27ae60;'>Buy—strong momentum + positive news!</span><br>Price: ${price:.2f}. Up {change:.1f}% today."
        elif change < -3 or sentiment_score < 0:
            tip = f"<span style='color:#e74c3c;'>Sell—negative momentum + bad news</span><br>Price: ${price:.2f}. Down {abs(change):.1f}% today."
        else:
            tip = f"<span style='color:#f39c12;'>Hold—steady or mixed signals</span><br>Price: ${price:.2f}."

    except Exception as e:
        logging.error(f"Error fetching {ticker}: {e}")
        tip = f"❌ Couldn't load {ticker}. Check the symbol."
    return jsonify({"tip": tip})
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
        fetch(`https://query1.finance.yahoo.com/v7/finance/quote?symbols=${t}`)
        .then(r => r.json())
        .then(d => {
            const q = d.quoteResponse.result[0];
            const price = q.regularMarketPrice?.toFixed(2) || "N/A";
            const ch = q.regularMarketChangePercent?.toFixed(2) || "N/A";
            document.getElementById(t.toLowerCase() + '_price').innerText = `$${price}`;
            document.getElementById(t.toLowerCase() + '_change').innerText = ch + '%';
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
