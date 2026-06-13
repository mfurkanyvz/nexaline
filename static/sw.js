const CACHE_NAME = "nexaline-pwa-v62-segment-20260613";
const APP_SHELL = [
  "/manifest.webmanifest",
  "/static/vendor/socket.io.min.js",
  "/static/nexaline-mark-3d.png",
  "/static/icons/icon-192-3d.png",
  "/static/icons/icon-512-3d.png",
  "/static/icons/maskable-512-3d.png",
  "/static/icons/apple-touch-icon-3d.png"
];
const NETWORK_FIRST_PATHS = new Set([
  "/",
  "/client.html",
  "/static/client.html",
  "/static/admin.html",
  "/static/theme.css",
  "/static/service-worker.js",
  "/sw.js"
]);

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("push", event => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = { title: "NexaLine", message: event.data ? event.data.text() : "Yeni bildirim" };
  }

  const type = data.type || "message";
  const targetUrl = data.url || (data.chatId ? `/chat/${encodeURIComponent(data.chatId)}` : "/");
  const options = {
    body: data.message || data.body || "Yeni bildirim",
    icon: data.icon || "/static/icons/icon-192-3d.png",
    badge: data.badge || "/static/icons/icon-192-3d.png",
    tag: data.tag || `${type}-${data.chatId || Date.now()}`,
    renotify: type === "call.audio" || type === "call.video",
    requireInteraction: type === "call.audio" || type === "call.video",
    data: {
      url: targetUrl,
      type,
      chatId: data.chatId || null,
      callKind: data.callKind || null
    },
    actions: [{ action: "open", title: "Goruntule" }]
  };

  event.waitUntil(self.registration.showNotification(data.title || "NexaLine", options));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(clientList => {
      const targetUrl = event.notification.data?.url || "/";
      const target = clientList.find(client => client.url.includes(targetUrl) && "focus" in client) || clientList.find(client => "focus" in client);
      if (target) {
        return target.focus();
      }
      return clients.openWindow(targetUrl);
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

self.addEventListener("message", event => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
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
    url.pathname.startsWith("/qr-login/") ||
    url.pathname.startsWith("/points/") ||
    url.pathname.startsWith("/nearby/") ||
    url.pathname.startsWith("/vault/") ||
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
      fetch(request, { cache: "no-store" })
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
