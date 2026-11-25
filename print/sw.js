/* CLUTCHPRINT ready alerts. Pushes carry no data: ask the site which order is ready, then notify. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", e => e.waitUntil((async () => {
  let title = "Your CLUTCHPRINT order is ready", body = "Show your token at the T FIX® CUSAT counter — Pipeline Road.";
  try {
    const sub = await self.registration.pushManager.getSubscription();
    if (sub) {
      const r = await fetch("/api/push.php?action=which", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ endpoint: sub.endpoint }), cache: "no-store" });
      const d = await r.json();
      const o = (d.orders || []).find(x => x.status === "ready") || (d.orders || [])[0];
      if (o) title = "#" + o.code + " is ready ✓";
    }
  } catch (err) { /* show the general message */ }
  await self.registration.showNotification(title, {
    body, icon: "/icon-192.png", badge: "/favicon-32.png", tag: "clutch-ready", renotify: true, data: { url: "/#upload-studio" }
  });
})()));

self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) if (new URL(w.url).origin === self.location.origin) { await w.focus(); return w.navigate(e.notification.data.url); }
    return self.clients.openWindow(e.notification.data.url);
  })());
});
