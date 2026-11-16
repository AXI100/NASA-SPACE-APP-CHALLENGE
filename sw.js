const CACHE = 'agro-mesh-shell-27';
// Refresh the release shell as a unit on installation.
const ASSETS = ['/', '/styles.css?v=27','/app.js?v=27','/i18n.js?v=27','/offline_rag.js','/vendor/leaflet.js','/vendor/leaflet.css'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS.map(url=>new Request(url,{cache:'reload'})))).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('agro-mesh-shell-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  if (event.request.mode === 'navigate' || ASSETS.includes(url.pathname+url.search)) {
    event.respondWith(caches.match(event.request.mode==='navigate' ? '/' : url.pathname+url.search).then(res => res || fetch(event.request)));
  }
});
