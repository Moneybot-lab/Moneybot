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

function paintToggle() {
  const toggle = document.getElementById('pushEnabledToggle');
  const slider = document.getElementById('pushEnabledSlider');
  const knob = document.getElementById('pushEnabledKnob');
  if (!toggle || !slider || !knob) {
    return;
  }
  slider.style.background = toggle.checked ? '#16a34a' : '#bbf7d0';
  slider.style.boxShadow = toggle.disabled
    ? 'inset 0 0 0 1px #a3a3a3'
    : `inset 0 0 0 1px ${toggle.checked ? '#15803d' : '#86efac'}`;
  slider.style.opacity = toggle.disabled ? '0.6' : '1';
  knob.style.transform = toggle.checked ? 'translateX(24px)' : 'translateX(0)';
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
    paintToggle();
    return;
  }

  const support = browserPushSupportStatus();
  if (!support.supported) {
    toggle.disabled = true;
    status(`Push unavailable: ${support.reasons.join(' ')}`, true);
    paintToggle();
    return;
  }

  const existingTokens = await listRegisteredTokens();
  toggle.checked = existingTokens.length > 0;
  paintToggle();
  status(toggle.checked ? 'Push notifications are enabled.' : 'Push notifications are disabled.');

  toggle.addEventListener('change', async () => {
    toggle.disabled = true;
    paintToggle();
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
      paintToggle();
    }
  });
}

initializeToggle();
