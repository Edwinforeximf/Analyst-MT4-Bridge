const KEY='gap-static-v1';
self.addEventListener('install',e=>{e.waitUntil(caches.open(KEY).then(c=>c.addAll(['/static/icon-192.png','/static/icon-512.png'])));self.skipWaiting()});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim())});
// Do NOT cache personalized API, login, reports, or dynamic HTML.
self.addEventListener('fetch',e=>{});
