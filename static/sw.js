/* Service worker: halaman bisa dibuka offline dengan data terakhir.
   - Halaman utama: jaringan dulu (cache bila offline). Ikon, manifest, library: cache dulu, diperbarui di latar belakang.
   - File data (data/*.json): jaringan dulu; bila gagal, pakai salinan terakhir (ditandai X-Offline). */
const VERSION = "v2";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png",
               "vendor/lightweight-charts.standalone.production.js"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open("shell-" + VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => !k.endsWith(VERSION)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/data/")) {
    const key = url.origin + url.pathname;  // abaikan ?t= penghindar cache
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) { const copy = r.clone(); caches.open("data-" + VERSION).then(c => c.put(key, copy)); }
      return r;
    }).catch(() => caches.open("data-" + VERSION).then(c => c.match(key)).then(m => {
      if (!m) return new Response(JSON.stringify({error: "offline"}), {status: 503, headers: {"Content-Type": "application/json"}});
      return m.blob().then(b => new Response(b, {status: 200, headers: {"Content-Type": "application/json", "X-Offline": "1"}}));
    })));
    return;
  }
  if (e.request.mode === "navigate" || url.pathname.endsWith(".html") || url.pathname.endsWith("/")) {
    // halaman utama: jaringan dulu agar pembaruan tampilan langsung terlihat
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) { const copy = r.clone(); caches.open("shell-" + VERSION).then(c => c.put(e.request, copy)); }
      return r;
    }).catch(() => caches.match(e.request).then(m => m || caches.match("index.html"))));
    return;
  }
  e.respondWith(caches.open("shell-" + VERSION).then(c => c.match(e.request).then(hit => {
    const net = fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
    return hit || net;
  })));
});
