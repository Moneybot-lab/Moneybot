const TAB_SESSION_KEY = 'moneybot_tab_session_id';
const PUSH_TOKEN_STORAGE_KEY = 'moneybot_push_token';

function getTabSessionId() {
  return sessionStorage.getItem(TAB_SESSION_KEY) || '';
}

async function apiFetch(url, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  const tabSessionId = getTabSessionId();
  if (tabSessionId) {
    headers['X-Tab-Session-Id'] = tabSessionId;
  }
  const response = await fetch(url, Object.assign({}, options, { headers }));
  if (response.status === 401) {
    location.href = '/login';
    throw new Error('authentication required');
  }
  return response;
}

function firebaseBootstrap() {
  const bootstrap = window.__MONEYBOT_FIREBASE__;
  if (!bootstrap || !bootstrap.config || !bootstrap.vapidKey) {
    return null;
  }
  if (!window.firebase || typeof window.firebase.initializeApp !== 'function') {
    return null;
  }
  return bootstrap;
}

function status(message, danger = false) {
  const statusEl = document.getElementById('pushStatus');
  if (!statusEl) {
    return;
  }
  statusEl.textContent = message;
  statusEl.style.color = danger ? '#991b1b' : '#166534';
}

function triggerStatus(message, danger = false) {
  const statusEl = document.getElementById('triggerStatus');
  if (!statusEl) {
    return;
  }
  statusEl.textContent = message;
  statusEl.style.color = danger ? '#991b1b' : '#166534';
}

function browserPushSupportStatus() {
  const reasons = [];
  const isLocalhost = ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
  if (!window.isSecureContext && !isLocalhost) {
    reasons.push('Push requires HTTPS (or localhost for local development).');
  }
  if (!('Notification' in window)) {
    reasons.push('Notification API is unavailable in this browser/environment.');
  }
  if (!('serviceWorker' in navigator)) {
    reasons.push('Service workers are unavailable in this browser/environment.');
  }
  if (!('PushManager' in window)) {
    reasons.push('PushManager is unavailable in this browser/environment.');
  }
  return {
    supported: reasons.length === 0,
    reasons,
  };
}

async function listRegisteredTokens() {
  const response = await apiFetch('/api/notifications/fcm-tokens');
  const payload = await response.json();
  return Array.isArray(payload.items) ? payload.items : [];
}

async function registerPushToken() {
  const bootstrap = firebaseBootstrap();
  if (!bootstrap) {
    throw new Error('firebase not configured');
  }
  const support = browserPushSupportStatus();
  if (!support.supported) {
    throw new Error(`push not supported: ${support.reasons.join(' ')}`);
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    throw new Error('notification permission not granted');
  }

  const app = window.firebase.apps?.length
    ? window.firebase.app()
    : window.firebase.initializeApp(bootstrap.config);
  const messaging = window.firebase.messaging(app);
  const registration = await navigator.serviceWorker.register('/firebase-messaging-sw.js');
  const token = await messaging.getToken({
    vapidKey: bootstrap.vapidKey,
    serviceWorkerRegistration: registration,
  });
  if (!token) {
    throw new Error('no token returned by firebase');
  }

  const saveRes = await apiFetch('/api/notifications/fcm-token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, user_agent: navigator.userAgent || '' }),
  });
  if (!saveRes.ok) {
    const payload = await saveRes.json();
    throw new Error(payload.error || 'failed to save token');
  }
  localStorage.setItem(PUSH_TOKEN_STORAGE_KEY, token);
}

async function unregisterPushToken() {
  const savedToken = localStorage.getItem(PUSH_TOKEN_STORAGE_KEY) || '';
  if (!savedToken) {
    return;
  }

  const response = await apiFetch('/api/notifications/fcm-token', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: savedToken }),
  });
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || 'failed to delete token');
  }
  localStorage.removeItem(PUSH_TOKEN_STORAGE_KEY);
}

async function initializeToggle() {
  const toggle = document.getElementById('pushEnabledToggle');
  if (!toggle) {
    return;
  }

  const bootstrap = firebaseBootstrap();
  if (!bootstrap) {
    toggle.disabled = true;
    status('Firebase push is not configured yet. Add Firebase env vars first.', true);
    return;
  }

  const support = browserPushSupportStatus();
  if (!support.supported) {
    toggle.disabled = true;
    status(`Push unavailable: ${support.reasons.join(' ')}`, true);
    return;
  }

  const existingTokens = await listRegisteredTokens();
  toggle.checked = existingTokens.length > 0;
  status(toggle.checked ? 'Push notifications are enabled.' : 'Push notifications are disabled.');

  toggle.addEventListener('change', async () => {
    toggle.disabled = true;
    try {
      if (toggle.checked) {
        status('Enabling push notifications...');
        await registerPushToken();
        status('Push notifications enabled.');
      } else {
        status('Disabling push notifications...');
        await unregisterPushToken();
        status('Push notifications disabled.');
      }
    } catch (err) {
      toggle.checked = !toggle.checked;
      status(err.message || 'Unable to update notification preference.', true);
    } finally {
      toggle.disabled = false;
    }
  });
}

async function loadTriggerPreferences() {
  const response = await apiFetch('/api/notifications/triggers');
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || 'failed to load trigger settings');
  }
  const payload = await response.json();
  return payload.item || {};
}

async function saveTriggerPreferences(patch) {
  const response = await apiFetch('/api/notifications/triggers', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || 'failed to save trigger settings');
  }
  const payload = await response.json();
  return payload.item || {};
}

async function initializeTriggerToggles() {
  const fieldConfig = [
    { id: 'triggerPortfolioSell', field: 'portfolio_sell_advice_change', label: 'Portfolio SELL advice changes' },
    { id: 'triggerPortfolioBuy', field: 'portfolio_buy_advice_change', label: 'Portfolio BUY advice changes' },
    { id: 'triggerMomentum8', field: 'hot_momentum_score_crosses_8', label: 'Hot momentum score > 8' },
    { id: 'triggerWhaleAdded', field: 'whale_top_investor_added', label: 'Whale/top investor added' },
  ];
  const controls = fieldConfig
    .map((cfg) => ({ ...cfg, el: document.getElementById(cfg.id) }))
    .filter((cfg) => !!cfg.el);

  if (!controls.length) {
    return;
  }

  try {
    const current = await loadTriggerPreferences();
    controls.forEach(({ el, field }) => {
      el.checked = Boolean(current[field]);
    });
    triggerStatus('Trigger settings are up to date.');
  } catch (err) {
    controls.forEach(({ el }) => {
      el.disabled = true;
    });
    triggerStatus(err.message || 'Unable to load trigger settings.', true);
    return;
  }

  controls.forEach(({ el, field, label }) => {
    el.addEventListener('change', async () => {
      const previous = !el.checked;
      el.disabled = true;
      triggerStatus(`Saving ${label}...`);
      try {
        await saveTriggerPreferences({ [field]: el.checked });
        triggerStatus('Trigger settings saved.');
      } catch (err) {
        el.checked = previous;
        triggerStatus(err.message || 'Unable to save trigger settings.', true);
      } finally {
        el.disabled = false;
      }
    });
  });
}

initializeToggle();
initializeTriggerToggles();
