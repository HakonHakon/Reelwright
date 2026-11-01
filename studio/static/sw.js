"use strict";
const CACHE = "reelwright-mobile-v2";
const SHELL = ["/static/offline.html", "/static/studio.css", "/static/studio.js", "/static/timeline.js", "/static/episode.js", "/static/app-install.js", "/static/manifest.webmanifest", "/static/icons/icon-192.png", "/static/icons/icon-512.png", "/static/icons/apple-touch-icon.png"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith("reelwright-") && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const request = event.request, url = new URL(request.url);
  // Project data, media and all writes always go to the server, never to an offline queue/cache.
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/") || url.pathname.startsWith("/media/")) return;
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/static/offline.html"))); return;
  }
  if (!url.pathname.startsWith("/static/")) return;
  event.respondWith(fetch(request).then(response => {
    if (response.ok) { const copy = response.clone(); event.waitUntil(caches.open(CACHE).then(cache => cache.put(request, copy))); }
    return response;
  }).catch(() => caches.match(request)));
});
