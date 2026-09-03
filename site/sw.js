/* Cache the shell so the meter opens offline -- the data is local anyway.
 * Bump CACHE when any shell file changes; old caches are dropped on activate. */
var CACHE = "water-meter-v1";
var SHELL = [
  "./", "./index.html", "./style.css", "./app.js",
  "./water-model.js", "./constants.json", "./manifest.webmanifest", "./icon.svg",
];

self.addEventListener("install", function (ev) {
  ev.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () {
    return self.skipWaiting();
  }));
});

self.addEventListener("activate", function (ev) {
  ev.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

/* Network first, so a redeploy is picked up as soon as the device is online,
 * falling back to cache when it is not. */
self.addEventListener("fetch", function (ev) {
  if (ev.request.method !== "GET") return;
  ev.respondWith(
    fetch(ev.request).then(function (res) {
      var copy = res.clone();
      caches.open(CACHE).then(function (c) { c.put(ev.request, copy); });
      return res;
    }).catch(function () {
      return caches.match(ev.request).then(function (hit) {
        return hit || caches.match("./index.html");
      });
    })
  );
});
