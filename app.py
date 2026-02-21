from flask import Flask, request, jsonify
import yfinance as yf

app = Flask(__name__)

@app.route('/', methods=['GET'])
def home():
    return '''
<html>
<head>
    <style>
        body { font-family: Arial; text-align: center; padding: 50px; background: #f0f4f8; }
        h1 { color: #2c3e50; }
        select, input { width: 300px; padding: 12px; font-size: 16px; border: 2px solid #3498db; border-radius: 8px; margin: 10px 0; }
        button { padding: 12px 30px; font-size: 16px; background: #3498db; color: white; border: none; border-radius: 8px; cursor: pointer; }
        button:hover { background: #2980b9; }
        #response { margin-top: 20px; font-size: 18px; font-weight: bold; }
        #loading { display: none; color: #e67e22; margin-top: 15px; font-style: italic; }
        .dropdowns { display: flex; justify-content: center; gap: 30px; align-items: center; }
        .risk { color: #e74c3c; font-weight: bold; font-size: 14px; background: #fadbd8; padding: 5px 10px; border-radius: 5px; display: none; }
    </style>
</head>
<body>
<h1>MoneyBot</h1>
<p>Type any ticker or pick from below</p>
<input id="custom" placeholder="Enter ticker: e.g. APLD" autofocus>
<button onclick="ask()">Get Advice</button>
<div class="dropdowns">
    <select id="topTicker" onchange="fillCustom()">
        <option value="">Top stocks</option>
        <option value="TSLA">Tesla (TSLA)</option>
        <option value="NVDA">NVIDIA (NVDA)</option>
        <option value="AAPL">Apple (AAPL)</option>
    </select>
    <select id="lowTicker" onchange="fillCustom()">
        <option value="">Low-priced gem</option>
        <option value="DSWL">Deswell (DSWL)</option>
        <option value="APLD">Applied Digital (APLD)</option>
        <span id="riskBadge" class="risk">High Risk!</span>
    </select>
</div>
<div id="loading">Thinking...</div>
<div id="response"></div>
<script>
function fillCustom() {
    const top = document.getElementById('topTicker').value;
    const low = document.getElementById('lowTicker').value;
    const custom = document.getElementById('custom');
    if (top || low) custom.value = top || low;
    document.getElementById('riskBadge').style.display = low ? 'inline' : 'none';
}
async function ask() {
    const loading = document.getElementById('loading');
    const response = document.getElementById('response');
    let ticker = document.getElementById('custom').value.trim().toUpperCase();
    if (!ticker) { response.innerText = "Enter a ticker!"; return; }
    response.innerText = '';
    loading.style.display = 'block';
    try {
        const res = await fetch('/advice', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: ticker})});
        const data = await res.json();
        response.innerHTML = data.tip;
    } catch (e) {
        response.innerText = "Oops—couldn't fetch data.";
    }
    loading.style.display = 'none';
}
document.getElementById('custom').addEventListener('keypress', e => { if (e.key === 'Enter') ask(); });
</script>
</body>
</html>
'''

@app.route('/advice', methods=['POST'])
def advice():
    ticker = request.json.get('text', '').strip().upper() or 'TSLA'
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        price = info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')
        change = info.get('regularMarketChangePercent', 0)
        if price is None:
            raise ValueError("No price data")
        if change > 1:
            tip = f"<span style='color:#27ae60;'>Buy—strong momentum!</span><br>Price: ${price:.2f}. Up {change:.1f}% today."
        elif change < -3:
            tip = f"<span style='color:#e74c3c;'>Sell—weakening fast</span><br>Price: ${price:.2f}. Down {abs(change):.1f}% today."
        else:
            tip = f"<span style='color:#f39c12;'>Hold—steady</span><br>Price: ${price:.2f}. Change {change:+.1f}% today."
    except Exception as e:
        tip = f"Couldn't load '{ticker}'—try TSLA."
    return jsonify({"tip": tip})
    tip = f"Couldn't load '{ticker}'—Yahoo's acting up. Try TSLA."
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)