const DEFAULT_NOTIFICATION_ICON = "/static/icons/nexaline-icon-192-v2.png";
const DEFAULT_NOTIFICATION_BADGE = "/static/icons/nexaline-icon-192-v2.png";

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
    icon: data.icon || DEFAULT_NOTIFICATION_ICON,
    badge: data.badge || DEFAULT_NOTIFICATION_BADGE,
    tag: data.tag || `${type}-${data.chatId || Date.now()}`,
    renotify: type === "call.audio" || type === "call.video" || type === "ai.task.call",
    requireInteraction: type === "call.audio" || type === "call.video" || type === "ai.task.call",
    data: {
      url: targetUrl,
      type,
      chatId: data.chatId || null,
      callKind: data.callKind || null
    },
    actions: [{ action: "open", title: type === "ai.task.call" ? "Yanıtla" : "Görüntüle" }]
  };

  event.waitUntil(self.registration.showNotification(data.title || "NexaLine", options));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const targetUrl = new URL(event.notification.data?.url || "/", self.location.origin).href;

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(clientList => {
      const existing = clientList.find(client => client.url.includes(targetUrl) && "focus" in client);
      if (existing) {
        return existing.focus();
      }
      return clients.openWindow(targetUrl);
    })
  );
});
