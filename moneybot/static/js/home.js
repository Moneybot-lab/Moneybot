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
                        model_version: 'alpha-atlas-v1',
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
                        { symbol: 'AAPL', endpoint: 'quick_ask', decision_source: 'deterministic_model', action: 'BUY', model_version: 'alpha-atlas-v1', return_1d: 0.021, return_5d: 0.048, outcome_1d: 'correct', outcome_5d: 'correct' },
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
                  const TAB_SESSION_KEY = 'moneybot_tab_session_id';
                  let currentHomeUser = null;
                  function getTabSessionId(){
                    return sessionStorage.getItem(TAB_SESSION_KEY) || localStorage.getItem(TAB_SESSION_KEY) || '';
                  }
                  async function apiFetch(url, options = {}){
                    const headers = Object.assign({}, options.headers || {});
                    const tabSessionId = getTabSessionId();
                    if(tabSessionId){
                      headers['X-Tab-Session-Id'] = tabSessionId;
                    }
                    return fetch(url, Object.assign({}, options, { headers }));
                  }

                  function normalizeTickerInputValue(inputEl){
                    if(!inputEl) return '';
                    const normalized = String(inputEl.value || '').toUpperCase();
                    if(inputEl.value !== normalized){
                      inputEl.value = normalized;
                    }
                    return normalized.trim();
                  }

                  async function readJsonResponse(response){
                    try {
                      return await response.json();
                    } catch (_err) {
                      return {};
                    }
                  }
                  
                  function initialsFromName(name){
                    const tokens = String(name || '').trim().replaceAll('-', ' ').split(/\s+/).filter(Boolean);
                    return tokens.slice(0,2).map((part) => part[0]).join('').toUpperCase() || 'U';
                  }
                  function setAvatar(imageNode, initialsNode, profileImageUrl, name){
                    initialsNode.textContent = initialsFromName(name);
                    if(profileImageUrl){
                      imageNode.src = profileImageUrl;
                      imageNode.style.display = 'block';
                      initialsNode.style.display = 'none';
                    } else {
                      imageNode.style.display = 'none';
                      initialsNode.style.display = 'flex';
                    }
                  }
                  function renderAuthenticatedMenu(user){
                    const loginLink = document.getElementById('loginLink');
                    const signupLink = document.getElementById('signupLink');
                    const userMenuButton = document.getElementById('userMenuButton');
                    const profileCard = document.getElementById('menuProfileCard');
                    const profileName = user?.name || 'User';
                    const profileUsername = user?.username || 'user';
                    if(loginLink) loginLink.style.display = 'none';
                    if(signupLink) signupLink.style.display = 'none';
                    if(userMenuButton) userMenuButton.style.display = 'inline-flex';
                    if(profileCard) profileCard.style.display = 'block';
                    document.getElementById('userMenuName').textContent = profileName;
                    document.getElementById('menuProfileName').textContent = profileName;
                    document.getElementById('menuProfileUsername').textContent = '@' + profileUsername;
                    setAvatar(
                      document.getElementById('userMenuAvatarImage'),
                      document.getElementById('userMenuAvatarInitials'),
                      user?.profile_image_url,
                      profileName,
                    );
                    setAvatar(
                      document.getElementById('menuProfileImage'),
                      document.getElementById('menuProfileInitials'),
                      user?.profile_image_url,
                      profileName,
                    );
                  }
                  async function refreshCurrentUser(){
                    try {
                      const res = await apiFetch('/api/me');
                      if(!res.ok){
                        currentHomeUser = null;
                        return null;
                      }
                      const payload = await res.json();
                      const user = payload.user || null;
                      if(user){
                        renderAuthenticatedMenu(user);
                      }
                      currentHomeUser = user;
                      return user;
                    } catch (err) {
                      currentHomeUser = null;
                      return null;
                    }
                  }
                  async function logoutFromMenu(){
                    await apiFetch('/api/auth/logout', { method: 'POST' });
                    sessionStorage.removeItem(TAB_SESSION_KEY);
                    localStorage.removeItem(TAB_SESSION_KEY);
                    location.href = '/';
                  }
                  const marketChartInstances = {};
                  function destroyMarketCharts(){ Object.values(marketChartInstances).forEach(c => c.destroy()); Object.keys(marketChartInstances).forEach(k => delete marketChartInstances[k]); }
                  function destroyQuickTrendChart(){
                    const graphEl = document.getElementById('quickTrendGraph');
                    if(graphEl){
                      graphEl.innerHTML = '';
                    }
                  }
                  function trendPath(points, width, height){
                    if(!points.length) return '';
                    const min = Math.min(...points);
                    const max = Math.max(...points);
                    const span = Math.max(max - min, 1e-9);
                    return points.map((value, idx) => {
                      const x = points.length === 1 ? 0 : (idx / (points.length - 1)) * width;
                      const y = height - (((value - min) / span) * height);
                      return `${idx === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
                    }).join(' ');
                  }
                  function renderQuickTrend(symbol, series){
                    const labelEl = document.getElementById('quickTrendLabel');
                    const graphEl = document.getElementById('quickTrendGraph');
                    if(!labelEl || !graphEl){
                      return;
                    }

                    const values = Array.isArray(series) ? series.filter((v) => Number.isFinite(Number(v))).map((v) => Number(v)) : [];
                    if(values.length < 2){
                      destroyQuickTrendChart();
                      labelEl.textContent = '';
                      return;
                    }

                    const latest = values[values.length - 1];
                    const min = Math.min(...values);
                    const max = Math.max(...values);
                    const span = max - min;
                    const position = span <= 0 ? 0.5 : (latest - min) / span;
                    const trendLabel = position >= 0.8 ? 'Near peak' : (position <= 0.2 ? 'Near dip' : 'Mid-range');
                    const isPositive = values[values.length - 1] >= values[0];

                    labelEl.textContent = `${escapeHtml(symbol)} · ${trendLabel} (${values.length} sessions)`;
                    labelEl.style.color = trendLabel === 'Near peak' ? '#86efac' : (trendLabel === 'Near dip' ? '#fca5a5' : '#bbf7d0');
                    destroyQuickTrendChart();
                    const stroke = isPositive ? '#22c55e' : '#dc2626';
                    const fill = isPositive ? 'rgba(34,197,94,.16)' : 'rgba(239,68,68,.14)';
                    const width = 400;
                    const height = 90;
                    const linePath = trendPath(values, width, height - 8);
                    if(!linePath){
                      return;
                    }
                    const areaPath = `${linePath} L ${width} ${height} L 0 ${height} Z`;
                    graphEl.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" width="100%" height="100%" aria-label="${escapeHtml(symbol)} 30 day trend graph">
                      <path d="${areaPath}" fill="${fill}"></path>
                      <path d="${linePath}" fill="none" stroke="${stroke}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"></path>
                    </svg>`;
                  }

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


                  function profileAdjustmentExplanation(data, baseRecommendation, recommendation){
                    const personalization = (data && typeof data.personalization === 'object' && data.personalization) ? data.personalization : {};
                    const rules = Array.isArray(personalization.applied_rules) ? personalization.applied_rules : [];
                    const reasonSet = new Set();
                    rules.forEach((rule) => {
                      const details = (rule && typeof rule.details === 'object' && rule.details) ? rule.details : {};
                      if(Array.isArray(details.reasons)){
                        details.reasons.forEach((reason) => { if(reason) reasonSet.add(String(reason)); });
                      }
                      if(rule && rule.message) reasonSet.add(String(rule.message));
                    });
                    const reasons = Array.from(reasonSet).slice(0, 3);
                    const reasonText = reasons.length ? reasons.join('; ') : 'your risk, horizon, or suitability settings require a stronger setup before changing this to a buy';
                    return `Profile adjusted ${escapeHtml(baseRecommendation)} → ${escapeHtml(recommendation)}. The market signal still matters, but your investor profile made the final Quick Ask more cautious because ${escapeHtml(reasonText)}. In plain English: the signal did not fit your selected risk tolerance, time horizon, or suitability guardrails strongly enough for the original action. <a href="/settings" style="color:#fde68a;font-weight:800">Review profile</a>`;
                  }

                  async function quickAsk(){
                    const inputEl = document.getElementById('quickSymbol');
                    const symbol = normalizeTickerInputValue(inputEl);
                    const outEl = document.getElementById('quickOut');
                    const adviceEl = document.getElementById('quickAdvice');
                    const loadingEl = document.getElementById('quickLoading');
                    const quickAskBtn = document.getElementById('quickAskBtn');
                    const profileNoteEl = document.getElementById('quickProfileNote');
                    inputEl.blur();
                    if(profileNoteEl){ profileNoteEl.style.display = 'none'; profileNoteEl.innerHTML = ''; }
                    if(!symbol){ outEl.textContent='Please enter a ticker symbol.'; return; }

                    loadingEl.style.display = 'flex';
                    outEl.style.display = 'none';
                    quickAskBtn.disabled = true;
                    quickAskBtn.style.opacity = '.75';
                    quickAskBtn.style.cursor = 'wait';
                    adviceEl.innerHTML = `<strong style="display:block;color:#bbf7d0;margin-bottom:4px">AI key points</strong><span style="color:#86efac">Analyzing ${escapeHtml(symbol)}...</span>`;

                    try {
                      const res = await fetch('/api/quick-ask?symbol=' + encodeURIComponent(symbol));
                      const payload = await readJsonResponse(res);
                      if(!res.ok){
                        outEl.textContent = payload.error || 'Unable to analyze this ticker.';
                        adviceEl.innerHTML = `<strong style="display:block;color:#bbf7d0;margin-bottom:4px">AI key points</strong><span style="color:#fca5a5">Unable to load assistant notes right now.</span>`;
                        return;
                      }
                      const data = payload.data || {};
                      const baseRecommendation = String(data.recommendation || 'HOLD OFF FOR NOW').toUpperCase();
                      const recommendation = String(data.personalized_recommendation || baseRecommendation).toUpperCase();
                      const profileChanged = recommendation !== baseRecommendation;
                      outEl.innerHTML = `${quickRecommendationBadge(recommendation)} <span style="margin-left:8px">· <span id="quickLivePrice">${formatMoney(data.current_price)}</span> · ${data.rationale || 'Signal generated from current indicators.'}</span>`;
                      if(profileNoteEl && profileChanged){
                        profileNoteEl.innerHTML = profileAdjustmentExplanation(data, baseRecommendation, recommendation);
                        profileNoteEl.style.display = 'block';
                      }
                      renderQuickTrend(symbol, data.history30 || []);

                      const ai = data.ai || {};
                      const narrative = profileChanged
                        ? `Final Quick Ask call is ${recommendation}. Base Alpha Atlas signal was ${baseRecommendation} before profile suitability adjusted it, so use the left-side badge as the final action.`
                        : (ai.narrative || data.rationale || 'No AI narrative available.');
                      const riskNotes = Array.isArray(ai.risk_notes) ? ai.risk_notes : [];
                      const nextChecks = Array.isArray(ai.next_checks) ? ai.next_checks : [];
                      const topRisk = riskNotes[0] || 'Keep strict risk controls and position sizing.';
                      const topCheck = nextChecks[0] || 'Recheck momentum and volume before changing size.';
                      adviceEl.innerHTML = `
                        <strong style="display:block;color:#bbf7d0;margin-bottom:6px">${escapeHtml(symbol)} · ${((ai.mode==='ai_enhanced'||data.ai_mode==='ai_enhanced') ? 'AI Enhanced' : 'AI Fallback')}</strong>
                        <ul style="margin:0;padding-left:18px;display:grid;gap:4px;color:#dcfce7">
                          <li>${escapeHtml(narrative)}</li>
                          <li><strong>Risk:</strong> ${escapeHtml(topRisk)}</li>
                          <li><strong>Next:</strong> ${escapeHtml(topCheck)}</li>
                        </ul>`;
                    } catch (err) {
                      outEl.textContent = 'Unable to analyze this ticker.';
                      adviceEl.innerHTML = `<strong style="display:block;color:#bbf7d0;margin-bottom:4px">AI key points</strong><span style="color:#fca5a5">Network issue while loading assistant notes.</span>`;
                      renderQuickTrend(symbol, []);
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
                  function setMenuState(isOpen){
                    const sidebar = document.getElementById('homeMenuSidebar');
                    const overlay = document.getElementById('homeMenuOverlay');
                    const toggleBtn = document.getElementById('menuToggleBtn');
                    if(!sidebar || !overlay || !toggleBtn) return;
                    sidebar.style.transform = isOpen ? 'translateX(0)' : 'translateX(105%)';
                    sidebar.setAttribute('aria-hidden', String(!isOpen));
                    overlay.style.display = isOpen ? 'block' : 'none';
                    overlay.setAttribute('aria-hidden', String(!isOpen));
                    toggleBtn.setAttribute('aria-expanded', String(isOpen));
                  }
                  function openMenu(){ setMenuState(true); }
                  function closeMenu(){ setMenuState(false); }
                  function toggleMenu(){
                    const sidebar = document.getElementById('homeMenuSidebar');
                    const isOpen = sidebar?.getAttribute('aria-hidden') === 'false';
                    setMenuState(!isOpen);
                  }
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
                      const summary = data.summary || 'No summary available.';
                      const news = Array.isArray(data.latest_news) ? data.latest_news : [];
                      if(!news.length){
                        summaryEl.innerHTML = `<div>${escapeHtml(summary)}</div>`;
                        return;
                      }
                      const newsHtml = news.slice(0, 5).map((item) => {
                        const title = escapeHtml(item?.title || 'Untitled');
                        const publisher = escapeHtml(item?.publisher || 'Source');
                        const link = item?.link ? String(item.link) : '';
                        const publishedAt = item?.published_at ? ` · ${escapeHtml(item.published_at)}` : '';
                        const headline = link
                          ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer" style="color:#166534;font-weight:700">${title}</a>`
                          : `<span style="color:#166534;font-weight:700">${title}</span>`;
                        return `<li style="margin-bottom:6px">${headline}<div style="font-size:12px;color:#3f6212">${publisher}${publishedAt}</div></li>`;
                      }).join('');
                      summaryEl.innerHTML = `<div style="margin-bottom:10px">${escapeHtml(summary)}</div><div><strong>Recent news</strong><ul style="padding-left:18px;margin:8px 0 0 0">${newsHtml}</ul></div>`;
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
                    document.getElementById('stable').innerHTML = `<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Score</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Transparency</th></tr></thead><tbody>${items.map(item=>`<tr><td style="padding:8px;border-bottom:1px solid #dcfce7">${tickerButton(item.symbol)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${formatMoney(item.price)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.signal_score}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.transparency || ''}</td></tr>`).join('')}</tbody></table><p style="margin:10px 0 0 0;color:#166534;font-size:12px;font-weight:700">Click on advice badges to see why.</p>`;
                  }

                  function renderMomentum(items){
                    document.getElementById('momentum').innerHTML = `<table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Score</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Source</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Transparency</th></tr></thead><tbody>${items.map(item=>`<tr><td style="padding:8px;border-bottom:1px solid #dcfce7">${tickerButton(item.symbol)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${formatMoney(item.price)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.score}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.decision_source || 'rule_based'}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${item.rationale}</td></tr>`).join('')}</tbody></table><p style="margin:10px 0 0 0;color:#166534;font-size:12px;font-weight:700">Click on advice badges to see why.</p>`;
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
                    const calibrationReport = health.calibration_report || {};
                    const calibrationReportExists = Boolean(health.calibration_report_exists);
                    const rawBrierScore = typeof calibrationReport.brier_score_raw === 'number' ? calibrationReport.brier_score_raw : (typeof calibrationReport.brier_score === 'number' ? calibrationReport.brier_score : null);
                    const calibratedBrierScore = typeof calibrationReport.calibrated_brier_score === 'number' ? calibrationReport.calibrated_brier_score : null;
                    const effectiveBrierScore = typeof calibrationReport.effective_brier_score === 'number' ? calibrationReport.effective_brier_score : (calibratedBrierScore ?? rawBrierScore);
                    const calibrationRows = Number(calibrationReport.rows || 0);
                    const hasCalibrationPayload = calibrationReportExists || Object.keys(calibrationReport).length > 0;
                    const calibrationStatus = !hasCalibrationPayload
                      ? 'No report'
                      : (effectiveBrierScore == null ? 'Pending maturity' : (effectiveBrierScore <= 0.26 ? 'Gate-ready' : 'Drifting'));
                    const trainingFresh = health.training_fresh;
                    const trainingAgeHours = typeof health.training_age_hours === 'number' ? health.training_age_hours : null;
                    const trainingMaxAgeHours = Number(health.training_max_age_hours || 36);
                    const trainingRecordedAt = String(health.training_recorded_at_utc || '');
                    const trainingStatus = trainingFresh === false ? 'STALE' : (trainingFresh === true ? 'Fresh' : 'Unknown');
                    const cards = [
                      {
                        label: 'Training freshness',
                        value: opsBadge(trainingStatus, trainingFresh === true),
                        detail: trainingFresh === false
                          ? `Model training is stale (${trainingAgeHours ?? 'n/a'}h old, max ${trainingMaxAgeHours}h). Check Render cron jobs now.`
                          : (trainingFresh === true
                            ? `Last training recorded ${trainingAgeHours ?? 'n/a'}h ago.`
                            : `No usable training timestamp found${trainingRecordedAt ? ` (${trainingRecordedAt})` : ''}.`),
                        tone: trainingFresh === false ? 'danger' : 'normal',
                      },
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
                      {
                        label: 'Calibration',
                        value: opsBadge(calibrationStatus, calibrationStatus === 'Gate-ready' || calibrationStatus === 'Pending maturity'),
                        detail: !hasCalibrationPayload
                          ? 'Run day13_calibration_report.py to populate diagnostics.'
                          : (effectiveBrierScore == null
                            ? `Report loaded · ${calibrationRows} mature rows available for scoring`
                            : `Effective Brier ${effectiveBrierScore.toFixed(4)}${rawBrierScore != null && rawBrierScore !== effectiveBrierScore ? ` (raw ${rawBrierScore.toFixed(4)})` : ''} · rows ${calibrationRows}`),
                        tone: 'normal',
                      },
                    ];
                    document.getElementById('opsCards').innerHTML = cards.map((card) => {
                      const isDanger = card.tone === 'danger';
                      const bg = isDanger ? '#fff1f2' : '#f7fee7';
                      const border = isDanger ? '#fda4af' : '#d9f99d';
                      const labelColor = isDanger ? '#9f1239' : '#4d7c0f';
                      const detailColor = isDanger ? '#881337' : '#3f6212';
                      return `<article style="background:${bg};border:1px solid ${border};border-radius:12px;padding:12px"><div style="font-size:12px;font-weight:800;letter-spacing:.06em;color:${labelColor};text-transform:uppercase;margin-bottom:8px">${card.label}</div><div>${card.value}</div><div style="margin-top:8px;color:${detailColor}">${escapeHtml(card.detail)}</div></article>`;
                    }).join('');

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


                  const CLEARVIEW_STORAGE_KEY = 'moneybot_clearview_symbols';
                  let clearviewSymbolsCache = null;
                  let clearviewSaveChain = Promise.resolve();
                  async function loadClearviewSymbols(){
                    if(Array.isArray(clearviewSymbolsCache)) return [...clearviewSymbolsCache];
                    try {
                      const res = await fetch('/api/clearview-symbols');
                      if(res.ok){
                        const payload = await res.json();
                        const symbols = Array.isArray(payload.symbols) ? payload.symbols : [];
                        if(symbols.length){
                          clearviewSymbolsCache = symbols.slice(0,20);
                          return [...clearviewSymbolsCache];
                        }
                      }
                    } catch (_err) {}
                    try {
                      let raw = localStorage.getItem(key);
                      if(!raw){
                        const legacy = localStorage.getItem('moneybot_clearview_symbols');
                        if(legacy){
                          raw = legacy;
                          localStorage.setItem(key, legacy);
                        }
                      }
                      const parsed = JSON.parse(raw || '[]');
                      if(!Array.isArray(parsed)) return ['NVDA','TSLA'];
                      const normalized = parsed.map((v)=>String(v||'').trim().toUpperCase()).filter(Boolean);
                      clearviewSymbolsCache = Array.from(new Set(normalized)).slice(0,20);
                      return [...clearviewSymbolsCache];
                    } catch (err) {
                      clearviewSymbolsCache = ['NVDA','TSLA'];
                      return [...clearviewSymbolsCache];
                    }
                  }
                  async function saveClearviewSymbols(symbols){
                    clearviewSymbolsCache = Array.from(new Set((symbols || []).map((s)=>String(s||'').trim().toUpperCase()).filter(Boolean))).slice(0,20);
                    localStorage.setItem(CLEARVIEW_STORAGE_KEY, JSON.stringify(clearviewSymbolsCache));
                    clearviewSaveChain = clearviewSaveChain.then(async () => {
                      try {
                        await fetch('/api/clearview-symbols', {
                          method:'PUT',
                          headers:{'Content-Type':'application/json'},
                          body: JSON.stringify({ symbols: clearviewSymbolsCache }),
                        });
                      } catch (_err) {}
                    });
                    await clearviewSaveChain;
                  }
                  async function addClearviewTicker(){
                    const el = document.getElementById('clearviewInput');
                    const value = normalizeTickerInputValue(el);
                    if(!value) return;
                    const symbols = await loadClearviewSymbols();
                    if(!symbols.includes(value)) symbols.push(value);
                    await saveClearviewSymbols(symbols);
                    if(el) el.value = '';
                    await refreshClearview();
                  }
                  async function removeClearviewTicker(symbol){
                    const symbols = await loadClearviewSymbols();
                    await saveClearviewSymbols(symbols.filter((s)=>s!==symbol));
                    await refreshClearview();
                  }

                  async function refreshClearview(){
                    setTabLoading(true);
                    try { renderClearview(await fetchClearviewItems()); } finally { setTabLoading(false); }
                  }

                  async function fetchClearviewItems(){
                    const symbols = await loadClearviewSymbols();
                    const results = await Promise.all(symbols.map(async(symbol)=>{
                      try {
                        const res = await fetch('/api/quick-ask?symbol=' + encodeURIComponent(symbol));
                        const payload = await res.json();
                        const d = payload.data || {};
                        const rec = String(d.recommendation || 'HOLD OFF FOR NOW').toUpperCase();
                        const derivedScore = Number.isFinite(Number(d.score)) ? Number(d.score) : (Number.isFinite(Number(d.signal_score)) ? Number(d.signal_score) : (Number.isFinite(Number(d.probability_up)) ? Number(d.probability_up) * 10 : 0));
                        const buyThreshold = Number.isFinite(Number(d.decision_threshold)) ? Number(d.decision_threshold) * 10 : 6;
                        const clearviewAdvice = (rec === 'BUY' || rec === 'STRONG BUY') && derivedScore >= buyThreshold ? 'BUY' : 'HOLD OFF';
                        return {symbol, current_price:d.current_price, score:derivedScore, history30:d.history30 || [], recommendation: clearviewAdvice, rationale:((d.ai && d.ai.narrative) || d.rationale || 'The AI sees improving momentum and risk setup; wait if volume weakens.')};
                      } catch (err) {
                        return {symbol, current_price:null, score:0, history30:[], recommendation:'HOLD OFF', rationale:'Unable to load live signal right now.'};
                      }
                    }));
                    return results;
                  }
                  function trendMiniGraph(series){
                    const values = Array.isArray(series) ? series.filter((v)=>Number.isFinite(Number(v))).map(Number) : [];
                    if(values.length < 2) return '<span style="color:#64748b">n/a</span>';
                    const width = 100, height = 26;
                    const path = trendPath(values, width, height);
                    const up = values[values.length - 1] >= values[0];
                    return `<svg viewBox="0 0 ${width} ${height}" width="110" height="28" preserveAspectRatio="none"><path d="${path}" fill="none" stroke="${up ? '#16a34a':'#dc2626'}" stroke-width="2" stroke-linecap="round"/></svg>`;
                  }
                  function renderClearview(items){
                    document.getElementById('clearview').innerHTML = `
                    <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap">
                      <input id="clearviewInput" placeholder="Add ticker (e.g. AMD)" autocapitalize="characters" style="text-transform:uppercase;padding:8px 10px;border:1px solid #86efac;border-radius:8px;min-width:210px" />
                      <button onclick="addClearviewTicker()" style="padding:8px 12px;border:none;background:#166534;color:#ecfdf5;border-radius:8px;font-weight:700">Add</button>
                      <span style="font-size:12px;color:#166534;align-self:center">Model: alpha-atlas-v1</span>
                    </div>
                    <table style="width:100%;border-collapse:collapse"><thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Ticker</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Price</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Score</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Trend</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Advice</th><th style="text-align:left;padding:8px;border-bottom:1px solid #d1fae5">Remove</th></tr></thead><tbody>${items.map(item=>`<tr><td style="padding:8px;border-bottom:1px solid #dcfce7">${tickerButton(item.symbol)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${formatMoney(item.current_price)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${Number(item.score||0).toFixed(1)}</td><td style="padding:8px;border-bottom:1px solid #dcfce7">${trendMiniGraph(item.history30 || [])}</td><td style="padding:8px;border-bottom:1px solid #dcfce7"><button onclick="showAdviceReason('${item.symbol}','${encodeURIComponent(item.rationale || 'Signal generated from current indicators.')}')" style="border:none;background:${item.recommendation==='BUY'?'#166534':'#b91c1c'};color:#f8fafc;padding:6px 10px;border-radius:999px;font-weight:800;cursor:pointer">${escapeHtml(item.recommendation || 'HOLD OFF')}</button></td><td style="padding:8px;border-bottom:1px solid #dcfce7"><button onclick="removeClearviewTicker('${item.symbol}')" style="border:none;background:#fee2e2;color:#991b1b;border-radius:8px;padding:6px 10px;cursor:pointer">Remove</button></td></tr>`).join('')}</tbody></table><p style="margin:10px 0 0 0;color:#166534;font-size:12px;font-weight:700">Click on advice badges to see why.</p>`;
                  }

                  document.addEventListener('input', (event) => {
                    const target = event.target;
                    if(target instanceof HTMLElement && target.id === 'clearviewInput'){
                      normalizeTickerInputValue(target);
                    }
                  });

                  document.addEventListener('keydown', (event) => {
                    const input = document.getElementById('clearviewInput');
                    if(!input || document.activeElement !== input) return;
                    if(event.key === 'Enter'){
                      event.preventDefault();
                      addClearviewTicker();
                    }
                  });

                  function showAdviceReason(symbol, encodedRationale){
                    document.getElementById('homeModalTitle').textContent = `${symbol} · Advice details`;
                    document.getElementById('homeModalSummary').textContent = decodeURIComponent(encodedRationale || '');
                    openHomeModal();
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
                      } else if(tab === 'clearview'){
                        await refreshClearview();
                      }
                    } finally {
                      setTabLoading(false);
                    }
                  }

                  function switchTab(tab){
                    if(tab === 'clearview' && !currentHomeUser){
                      location.href = '/login';
                      return;
                    }
                    document.querySelectorAll('.tab-panel').forEach(panel => panel.style.display = panel.id === tab ? 'block' : 'none');
                    document.querySelectorAll('.tab-btn').forEach(btn => btn.style.background = btn.dataset.tab === tab ? '#bbf7d0' : '#f0fdf4');
                    refreshTab(tab);
                  }

                  async function refreshOps(){
                    if(!document.getElementById('opsCards')) return;
                    setOpsLoading(true);
                    try {
                      renderOps(await fetchOpsData());
                    } finally {
                      setOpsLoading(false);
                    }
                  }

                  async function refreshOutcomes(){
                    if(!document.getElementById('outcomesTable')) return;
                    setOutcomesLoading(true);
                    try {
                      renderOutcomes(await fetchOutcomesData());
                    } finally {
                      setOutcomesLoading(false);
                    }
                  }

                  document.getElementById('quickSymbol').addEventListener('input', (event) => normalizeTickerInputValue(event.target));
                  document.getElementById('quickSymbol').addEventListener('keydown', (event) => { if(event.key==='Enter'){event.preventDefault();quickAsk();} });
                  document.getElementById('quickSymbol').addEventListener('focus', (event) => {
                    if(event.target.value){ event.target.value = ''; }
                  });
                  document.getElementById('homeTickerModal').addEventListener('click', (event) => { if(event.target.id==='homeTickerModal'){ closeHomeModal(); }});
                  document.addEventListener('click', (event) => {
                    const target = event.target;
                    if(!(target instanceof HTMLElement)) return;
                    if(!target.classList.contains('advice-reason-btn')) return;
                    showAdviceReason(target.dataset.symbol || '', target.dataset.rationale || '');
                  });
                  document.getElementById('menuToggleBtn').addEventListener('click', toggleMenu);
                  document.getElementById('userMenuButton').addEventListener('click', openMenu);
                  document.getElementById('menuCloseBtn').addEventListener('click', closeMenu);
                  document.getElementById('homeMenuOverlay').addEventListener('click', closeMenu);
                  document.getElementById('menuLogoutBtn').addEventListener('click', logoutFromMenu);
                  document.addEventListener('keydown', (event) => {
                    if(event.key === 'Escape'){
                      closeHomeModal();
                      closeMenu();
                    }
                  });

                  async function init(){
                    const backtestHeading = Array.from(document.querySelectorAll('h1,h2,h3,h4')).find((el) => (el.textContent || '').trim() === 'AI Backtest Results (Wireframe)');
                    if (backtestHeading) {
                      const backtestSection = backtestHeading.closest('section');
                      if (backtestSection) {
                        backtestSection.remove();
                      }
                    }
                    setMenuState(false);
                    await refreshCurrentUser();
                    const clearviewBtn = document.querySelector('.tab-btn[data-tab="clearview"]');
                    if(clearviewBtn){ clearviewBtn.style.display = 'inline-block'; }
                    const market = await fetchWithFallback('/api/market-overview', 'market');
                    renderMarket(market);
                    await refreshTab('stable');
                  }

                  init();
