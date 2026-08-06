// service-worker.js
// Necesario para que el navegador considere la app "instalable" (requisito de las PWA).
// Guarda en caché los archivos de la interfaz para que abra rápido y funcione
// aunque haya poca señal; los datos (API) siempre se piden en vivo al servidor.
const CACHE_NAME = 'enlace-escolar-demo-v1';
const ARCHIVOS_BASE = [
  './index.html',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ARCHIVOS_BASE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((nombres) =>
      Promise.all(nombres.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  // Las llamadas a la API siempre van a la red (nunca a caché, son datos en vivo).
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
