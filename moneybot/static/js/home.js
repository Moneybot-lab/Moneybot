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
                    ops: {
                      health: {
                        deterministic_quick_enabled: true,
                        deterministic_momentum_enabled: true,
                        model_loaded: true,
                        model_version: 'day1-logreg-v1',
                        decision_logging: { enabled: true, source_counts: { deterministic_model: 14, rule_based: 6 } },
                      },
                      summary: {
                        events_considered: 20,
                        source_counts: { deterministic_model: 14, rule_based: 6 },
                        endpoint_counts: { quick_ask: 12, hot_momentum_buys: 8 },
                        top_symbols: [{ symbol: 'AAPL', count: 7 }, { symbol: 'SOFI', count: 4 }, { symbol: 'NVDA', count: 3 }],
                        latest_event: { endpoint: 'quick_ask', symbol: 'AAPL', decision_source: 'deterministic_model' },
                      },
                    },
                    outcomes: {
                      summary_1d: { accuracy: 0.62, evaluated_rows: 13 },
                      summary_5d: { accuracy: 0.67, evaluated_rows: 12 },
                      rows: [
                        { symbol: 'AAPL', endpoint: 'quick_ask', decision_source: 'deterministic_model', action: 'BUY', model_version: 'day1-logreg-v1', return_1d: 0.021, return_5d: 0.048, outcome_1d: 'correct', outcome_5d: 'correct' },
                        { symbol: 'SOFI', endpoint: 'hot_momentum_buys', decision_source: 'rule_based', action: 'HOLD OFF FOR NOW', model_version: null, return_1d: -0.013, return_5d: -0.028, outcome_1d: 'correct', outcome_5d: 'correct' },
                      ],
                    },
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
                  function opsBadge(value, positive){
                    return `<span style="display:inline-block;padding:4px 10px;border-radius:999px;background:${positive ? '#166534' : '#7f1d1d'};color:#fefce8;font-weight:700;font-size:12px">${escapeHtml(value)}</span>`;
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

                  async function fetchOpsData(){
                    try {
                      const [healthRes, summaryRes] = await Promise.all([
                        fetch('/api/model-health'),
                        fetch('/api/decision-log-summary?limit=50'),
                      ]);
                      if(!healthRes.ok || !summaryRes.ok){
                        throw new Error('non-200');
                      }
                      const healthPayload = await healthRes.json();
                      const summaryPayload = await summaryRes.json();
                      return {
                        health: healthPayload.data || fallbackData.ops.health,
                        summary: summaryPayload.data || fallbackData.ops.summary,
                      };
                    } catch (err) {
                      return fallbackData.ops;
                    }
                  }

                  async function fetchOutcomesData(){
                    try {
                      const res = await fetch('/api/decision-outcomes?limit=20');
                      if(!res.ok) throw new Error('non-200');
                      const payload = await res.json();
                      return payload.data || fallbackData.outcomes;
                    } catch (err) {
                      return fallbackData.outcomes;
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

                  function renderOps(data){
                    const health = data.health || {};
                    const summary = data.summary || {};
                    const logging = health.decision_logging || {};
                    const sourceCounts = summary.source_counts || logging.source_counts || {};
                    const endpointCounts = summary.endpoint_counts || logging.endpoint_counts || {};
                    const deterministicCount = Number(sourceCounts.deterministic_model || 0);
                    const ruleCount = Number(sourceCounts.rule_based || 0);
                    const total = Number(summary.events_considered || 0);
                    const cards = [
                      {
                        label: 'Model status',
                        value: health.model_loaded ? opsBadge(`Loaded · ${health.model_version || 'unknown'}`, true) : opsBadge('Not loaded', false),
                        detail: `Quick ask ${health.deterministic_quick_enabled ? 'enabled' : 'disabled'} · Momentum ${health.deterministic_momentum_enabled ? 'enabled' : 'disabled'}`,
                      },
                      {
                        label: 'Decision logging',
                        value: logging.enabled ? opsBadge('Enabled', true) : opsBadge('Disabled', false),
                        detail: `Recent events: ${total || 0}`,
                      },
                      {
                        label: 'Decision split',
                        value: `<strong style="font-size:1.5rem;color:#14532d">${deterministicCount}</strong><span style="color:#4d7c0f"> deterministic</span>`,
                        detail: `${ruleCount} rule-based in recent summary`,
                      },
                      {
                        label: 'Most active endpoint',
                        value: `<strong style="font-size:1.2rem;color:#14532d">${escapeHtml(Object.entries(endpointCounts).sort((a,b)=>b[1]-a[1])[0]?.[0] || 'n/a')}</strong>`,
                        detail: `${Object.entries(endpointCounts).sort((a,b)=>b[1]-a[1])[0]?.[1] || 0} recent events`,
                      },
                    ];
                    document.getElementById('opsCards').innerHTML = cards.map(card => `<article style="background:#f7fee7;border:1px solid #d9f99d;border-radius:12px;padding:12px"><div style="font-size:12px;font-weight:800;letter-spacing:.06em;color:#4d7c0f;text-transform:uppercase;margin-bottom:8px">${card.label}</div><div>${card.value}</div><div style="margin-top:8px;color:#3f6212">${escapeHtml(card.detail)}</div></article>`).join('');

                    const topSymbols = Array.isArray(summary.top_symbols) ? summary.top_symbols : [];
                    document.getElementById('opsTopSymbols').innerHTML = `<div style="background:#f7fee7;border:1px solid #d9f99d;border-radius:12px;padding:12px"><div style="font-weight:800;color:#365314;margin-bottom:8px">Top recent symbols</div><div style="display:flex;gap:8px;flex-wrap:wrap">${topSymbols.length ? topSymbols.map(item => `<span style="display:inline-flex;gap:6px;align-items:center;background:#dcfce7;border-radius:999px;padding:6px 10px;color:#166534;font-weight:700">${escapeHtml(item.symbol)}<span style="color:#4d7c0f">×${escapeHtml(item.count)}</span></span>`).join('') : '<span style="color:#4d7c0f">No recent symbols yet.</span>'}</div></div>`;

                    const latest = summary.latest_event || {};
                    document.getElementById('opsLatestEvent').innerHTML = `<div style="background:#f7fee7;border:1px solid #d9f99d;border-radius:12px;padding:12px"><div style="font-weight:800;color:#365314;margin-bottom:8px">Latest logged decision</div><div style="color:#3f6212">${latest.symbol ? `${escapeHtml(latest.symbol)} via ${escapeHtml(latest.endpoint || 'unknown')} · ${escapeHtml(latest.decision_source || 'unknown')}` : 'No events logged yet.'}</div></div>`;
                  }

                  function formatPercent(v){
                    return typeof v === 'number' ? `${(v * 100).toFixed(2)}%` : 'n/a';
                  }

                  function outcomeBadge(outcome){
                    const value = String(outcome || 'skipped').toLowerCase();
                    const color = value === 'correct' ? '#166534' : (value === 'incorrect' ? '#991b1b' : '#475569');
                    return `<span style="display:inline-block;padding:4px 8px;border-radius:999px;background:${color};color:#f8fafc;font-weight:700;font-size:12px">${escapeHtml(value)}</span>`;
                  }

                  function renderOutcomes(data){
                    const summary1d = data.summary_1d || {};
                    const summary5d = data.summary_5d || {};
                    document.getElementById('outcomesSummary').innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px"><article style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:12px;padding:12px"><div style="font-size:12px;font-weight:800;letter-spacing:.06em;color:#166534;text-transform:uppercase;margin-bottom:6px">1D accuracy</div><div style="font-size:1.5rem;font-weight:800;color:#14532d">${summary1d.accuracy != null ? `${(summary1d.accuracy * 100).toFixed(1)}%` : 'n/a'}</div><div style="color:#166534">${summary1d.evaluated_rows || 0} evaluated rows</div></article><article style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:12px"><div style="font-size:12px;font-weight:800;letter-spacing:.06em;color:#1d4ed8;text-transform:uppercase;margin-bottom:6px">5D accuracy</div><div style="font-size:1.5rem;font-weight:800;color:#1e3a8a">${summary5d.accuracy != null ? `${(summary5d.accuracy * 100).toFixed(1)}%` : 'n/a'}</div><div style="color:#1d4ed8">${summary5d.evaluated_rows || 0} evaluated rows</div></article></div>`;

                    const rows = Array.isArray(data.rows) ? data.rows : [];
                    document.getElementById('outcomesTable').innerHTML = rows.length ? `<div style="overflow:auto"><table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Symbol</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Endpoint</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Source</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Action</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Model</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">1D Return</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">1D Outcome</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">5D Return</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">5D Outcome</th></tr></thead><tbody>${rows.map(row => `<tr><td style="padding:8px;border-bottom:1px solid #dcfce7">${tickerButton(row.symbol)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${escapeHtml(row.endpoint || 'n/a')}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${escapeHtml(row.decision_source || 'n/a')}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${escapeHtml(row.action || 'n/a')}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${escapeHtml(row.model_version || 'n/a')}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${formatPercent(row.return_1d)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${outcomeBadge(row.outcome_1d)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${formatPercent(row.return_5d)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${outcomeBadge(row.outcome_5d)}</td></tr>`).join('')}</tbody></table></div>` : `<div style="background:#f8fafc;border:1px dashed #cbd5e1;border-radius:12px;padding:16px;color:#475569">No evaluated decision outcomes yet. Run the Day 9 evaluator and let more logs accumulate.</div>`;
                  }

                  function setTabLoading(isLoading){
                    const loadingEl = document.getElementById('tabLoading');
                    if(!loadingEl) return;
                    loadingEl.style.display = isLoading ? 'flex' : 'none';
                  }

                  function setOpsLoading(isLoading){
                    const loadingEl = document.getElementById('opsLoading');
                    if(!loadingEl) return;
                    loadingEl.style.display = isLoading ? 'flex' : 'none';
                  }

                  function setOutcomesLoading(isLoading){
                    const loadingEl = document.getElementById('outcomesLoading');
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

                  async function refreshOps(){
                    setOpsLoading(true);
                    try {
                      renderOps(await fetchOpsData());
                    } finally {
                      setOpsLoading(false);
                    }
                  }

                  async function refreshOutcomes(){
                    setOutcomesLoading(true);
                    try {
                      renderOutcomes(await fetchOutcomesData());
                    } finally {
                      setOutcomesLoading(false);
                    }
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
                    await refreshOps();
                    await refreshOutcomes();
                  }

                  init();
