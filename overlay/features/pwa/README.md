# Feature: PWA (Progressive Web App)

**Фаза 2.** Цель — устанавливаемое на телефон приложение (standalone-режим) с базовой
офлайн-оболочкой. Plane API-зависим, поэтому полный офлайн не делаем — фокус на
устанавливаемости, standalone-запуске без адресной строки и аккуратном офлайн-shell.

## Подход

**Overlay-изолированный SW** (вариант B), без `vite-plugin-pwa`: свой минимальный service
worker + ручная регистрация. Так чисто мержится с базой eyriehq и нет риска интеграции
плагина с React Router v7.

## Что уже было в базе eyriehq (используем)

- `apps/web/public/manifest.json` — `display: standalone`, иконки `/icons/icon-{192,348,512}.png`, `start_url:/`.
- `apps/web/app/root.tsx` — Apple/Android PWA-meta (`apple-mobile-web-app-capable`, `mobile-web-app-capable`, apple-touch-icons).
- Иконки в `apps/web/public/icons/`.

## Точки подключения (метка `// OVERLAY: pwa`)

| Файл                    | Что сделано                                                                                                                                                                      |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/web/public/sw.js` | Заменён мёртвый workbox-SW на свой: navigation network-first → офлайн-shell `/`; статика stale-while-revalidate; `/api,/auth,/uploads,/live,/spaces,/god-mode` — всегда из сети. |
| `apps/web/app/root.tsx` | (1) убран дубль `rel="manifest"` (`/site.webmanifest.json`), оставлен `/manifest.json`; (2) добавлена регистрация `/sw.js` (inline-скрипт после `<Scripts/>`).                   |
| удалены                 | `apps/web/public/workbox-*.js(.map)`, `sw.js.map` — мёртвые артефакты.                                                                                                           |

## Обновление SW

Поднять `VERSION` в `apps/web/public/sw.js` — старые кэши чистятся на `activate`,
`skipWaiting` + `clients.claim` активируют новую версию сразу.

## Проверка (критерий Фазы 2)

1. Открыть `https://<deploy-domain>/` на телефоне (HTTPS обязателен — есть через Traefik).
2. Меню браузера → «Добавить на главный экран» / «Установить приложение».
3. Запустить с экрана → открывается **standalone** (без адресной строки).
4. Базовый офлайн: после первого визита оболочка открывается без сети (данные требуют API).

## Известные ограничения / дальше

- Полноценный офлайн данных невозможен (нужен бэкенд) — вне scope MVP.
- (Опц.) web-push, кастомная офлайн-страница, precache хешированных ассетов — позже.
