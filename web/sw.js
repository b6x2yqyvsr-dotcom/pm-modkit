// 口蘑 Mod 工坊 · Service Worker
//
// 只缓存**界面外壳**（HTML/图标/manifest）—— 让手机装到主屏之后能秒开。
// 绝对不缓存 /api/*：那些是实时的数据包内容（动辄几百 MB），
// 缓存了既没意义又会把内存吃光。
const SHELL = "pm-modkit-shell-v1";
const SHELL_FILES = ["./", "./index.html", "./manifest.webmanifest",
                     "./icon-192.png", "./icon-512.png", "./icon-180.png"];

self.addEventListener("install", e => {
  e.waitUntil((async () => {
    const c = await caches.open(SHELL);
    // 单个失败不影响整体安装
    await Promise.all(SHELL_FILES.map(u => c.add(u).catch(() => {})));
    self.skipWaiting();
  })());
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== SHELL).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  // 接口一律走网络
  if (url.pathname.startsWith("/api/")) return;
  // 界面外壳：先给缓存，后台再更新
  e.respondWith((async () => {
    const c = await caches.open(SHELL);
    const hit = await c.match(e.request, { ignoreSearch: true });
    const net = fetch(e.request).then(r => {
      if (r && r.ok && url.origin === location.origin) c.put(e.request, r.clone());
      return r;
    }).catch(() => hit);
    return hit || net;
  })());
});
