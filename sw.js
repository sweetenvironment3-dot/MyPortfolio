/* MyPortfolio service worker
   - Pages: network first, fall back to the last cached copy when offline
   - /static files, /manifest.json and /icon-*.png (all served from the project root): served from cache, refreshed in the background
   - Google Fonts are cached separately so text keeps its typeface offline
   - Never touches /api/*, POSTs or other sites
   Bump CACHE_VERSION when you want every device to drop its old cache. */
var CACHE_VERSION = "myportfolio-v5";
var FONT_CACHE = "myportfolio-fonts";

var PRECACHE = [
  "/",
  "/static/css/style.css?v=3",
  "/static/js/main.js?v=3",
  "/static/images/profile.jpg",
  "/static/images/profile.jpg?v=2",
  "/static/images/avatar.jpg",
  "/manifest.json",
  "/icon-192x192.png",
  "/icon-512x512.png",
  "/icon-maskable-192x192.png",
  "/icon-maskable-512x512.png"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_VERSION).then(function (cache) {
      return Promise.all(PRECACHE.map(function (url) {
        return cache.add(url).catch(function () {});
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys
        .filter(function (k) { return k !== CACHE_VERSION && k !== FONT_CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;

  var url = new URL(req.url);

  // Google Fonts: keep a copy so the typography still works offline.
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    event.respondWith(
      caches.open(FONT_CACHE).then(function (cache) {
        return cache.match(req).then(function (hit) {
          var network = fetch(req).then(function (res) {
            cache.put(req, res.clone());
            return res;
          }).catch(function () { return hit; });
          return hit || network;
        });
      })
    );
    return;
  }

  if (url.origin !== self.location.origin) return;
  if (url.pathname.indexOf("/api/") === 0) return;
  if (url.pathname === "/sw.js") return;

  // Page navigations: network first, cached copy as the offline fallback.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE_VERSION).then(function (c) { c.put(req, copy); });
        return res;
      }).catch(function () {
        return caches.match(req).then(function (hit) {
          return hit || caches.match("/");
        });
      })
    );
    return;
  }

  // Static files: cache first, update in the background.
  if (url.pathname.indexOf("/static/") === 0 || url.pathname === "/manifest.json" ||
      /^\/icon-[a-z0-9-]+\.png$/.test(url.pathname)) {
    event.respondWith(
      caches.match(req).then(function (hit) {
        var network = fetch(req).then(function (res) {
          if (res && res.ok) {
            var copy = res.clone();
            caches.open(CACHE_VERSION).then(function (c) { c.put(req, copy); });
          }
          return res;
        }).catch(function () { return hit; });
        return hit || network;
      })
    );
  }
});
