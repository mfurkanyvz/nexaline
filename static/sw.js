const CACHE_NAME = "nexaline-pwa-v16";
const APP_SHELL = [
  "/",
  "/client.html",
  "/manifest.webmanifest",
  "/static/client.html",
  "/static/admin.html",
  "/static/vendor/socket.io.min.js",
  "/static/nexaline-mark.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/maskable-512.png",
  "/static/icons/apple-touch-icon.png"
];
const NETWORK_FIRST_PATHS = new Set([
  "/",
  "/client.html",
  "/static/client.html",
  "/static/admin.html",
  "/sw.js"
]);

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(clientList => {
      const target = clientList.find(client => "focus" in client);
      if (target) {
        return target.focus();
      }
      return clients.openWindow("/");
    })
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }

  if (
    url.pathname.startsWith("/socket.io/") ||
    url.pathname.startsWith("/admin/") ||
    url.pathname.startsWith("/ai/") ||
    url.pathname.startsWith("/bootstrap/") ||
    url.pathname.startsWith("/chat/") ||
    url.pathname.startsWith("/downloads/") ||
    ["/health", "/login", "/register", "/rtc-config"].includes(url.pathname)
  ) {
    event.respondWith(fetch(request));
    return;
  }

  if (request.mode === "navigate" || NETWORK_FIRST_PATHS.has(url.pathname)) {
    event.respondWith(
      fetch(request)
        .then(response => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request).then(cached => cached || caches.match("/").then(root => root || caches.match("/client.html"))))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(cached => cached || fetch(request).then(response => {
      const copy = response.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
      return response;
    }))
  );
});
