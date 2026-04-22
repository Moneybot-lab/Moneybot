self.addEventListener('push', (event) => {
  if (!event || !event.data) {
    return;
  }
  let payload = {};
  try {
    payload = event.data.json();
  } catch (_err) {
    payload = { notification: { title: 'Moneybot Labs', body: event.data.text() } };
  }
  const notification = payload.notification || {};
  const title = notification.title || 'Moneybot Labs';
  const options = {
    body: notification.body || 'You have a new alert.',
    icon: '/static/moneybot-pro-logo.svg',
    data: payload.data || {},
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = '/';
  event.waitUntil(clients.openWindow(targetUrl));
});
