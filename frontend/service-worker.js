// Enlace Escolar SW + Push
const CACHE_NAME = 'enlace-escolar-v20260814-tutorenv';

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE_NAME).then((c) => c.addAll(['./index.html', './manifest.json']).catch(() => {})));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const isNav = req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html');
  if (isNav || url.pathname.endsWith('index.html') || url.pathname.endsWith('/')) {
    e.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => caches.match(req).then((r) => r || caches.match('./index.html')))
    );
    return;
  }
  e.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
      return res;
    }).catch(() => cached))
  );
});

self.addEventListener('push', (event) => {
  let data = { title: 'Enlace Escolar', body: 'Tienes un mensaje nuevo', data: {} };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (e) {
    try { data.body = event.data.text(); } catch (_) {}
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'Enlace Escolar', {
      body: data.body || 'Tienes un mensaje nuevo',
      icon: './icons/icon-192.png',
      badge: './icons/icon-192.png',
      data: data.data || {},
      vibrate: [120, 60, 120],
      renotify: true,
      tag: 'enlace-escolar-msg'
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if (c.url && 'focus' in c) return c.focus();
      }
      if (clients.openWindow) return clients.openWindow('./index.html');
    })
  );
});
