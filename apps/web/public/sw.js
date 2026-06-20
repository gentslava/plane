// OVERLAY: pwa — service worker дистрибутива Plus (gentslava/plane).
//
// Plane — API-зависимое приложение, поэтому цель SW здесь: устанавливаемость,
// быстрый повторный запуск и аккуратная офлайн-оболочка (не полный офлайн).
// Стратегии:
//   - навигация (SPA-оболочка): network-first → офлайн отдаём закэшированный "/"
//   - статика (assets/шрифты/иконки): stale-while-revalidate
//   - API / auth / uploads / live / spaces / god-mode: всегда из сети (не кэшируем)
//
// Версию кэша поднимать при изменении стратегии — старые кэши чистятся на activate.

const VERSION = "v1";
const SHELL_CACHE = `plane-shell-${VERSION}`;
const ASSET_CACHE = `plane-assets-${VERSION}`;
const SHELL_URL = "/";

// Префиксы, которые НИКОГДА не кэшируем (динамика/бэкенд).
const BYPASS_PREFIXES = ["/api", "/auth", "/uploads", "/live", "/spaces", "/god-mode"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.add(SHELL_URL))
      .catch(() => undefined)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== SHELL_CACHE && k !== ASSET_CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // только свой origin
  if (BYPASS_PREFIXES.some((p) => url.pathname === p || url.pathname.startsWith(p + "/"))) return;

  // Навигация — network-first, офлайн → закэшированная оболочка "/"
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(SHELL_URL, copy));
          return response;
        })
        .catch(() => caches.match(SHELL_URL).then((cached) => cached || Response.error()))
    );
    return;
  }

  // Статика — stale-while-revalidate
  event.respondWith(
    caches.open(ASSET_CACHE).then((cache) =>
      cache.match(request).then((cached) => {
        const network = fetch(request)
          .then((response) => {
            if (response && response.status === 200 && response.type === "basic") {
              cache.put(request, response.clone());
            }
            return response;
          })
          .catch(() => cached);
        return cached || network;
      })
    )
  );
});
