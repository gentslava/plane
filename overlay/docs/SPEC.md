# Спецификация форка Plane (gentslava/plane)

> Статус: v2 · База: `eyriehq/plane-plus` (ветка `main`, Plane 1.3.1) · Лицензия: AGPL-3.0

## 1. Цель
Вести **собственный дистрибутив Plane «Plus»** на базе `eyriehq/plane-plus`, который:
1. из коробки даёт **agent-first опыт** (App Rail, Wiki, AI-ассистент, first-class epics, API-key auth) — это база eyriehq;
2. **добавляет наше** (учёт времени, PWA, нативная мобильная авторизация);
3. собирается **готовыми Docker-образами** через CI (не на сервере);
4. **обновляется** из eyriehq (свежий Plus) и наблюдает за upstream Plane.

## 2. Зачем (контекст и смена базы)
- **Агенты/Hermes/MCP.** Развёрнут Hermes-агент и Plane MCP — нужен agent-first слой.
- **Почему база eyriehq, а не upstream.** Опыт Plane Plus (App Rail, Wiki-как-приложение, AI,
  epics) — это **EE/коммерческий слой Plane** (`@/plane-web/`), в upstream Community отключён
  (`apps/web/ce/.../app-rail/provider.tsx` → `isEnabled=false`, в CE App Rail = 1 пункт «Projects»).
  eyriehq переоткрыл эти фичи в своём AGPL-форке. Точечно портировать их на upstream — большой и
  бесконечный труд, поэтому **берём eyriehq как базу**.
- **Цена.** База eyriehq ≈ Plane 1.3.1 — отстаёт от upstream/preview по «свежести». Свежие фиксы
  Plane приходят к нам через eyriehq (они мержат upstream к себе). Приемлемо: 1.3.1 — стабильный релиз.
- **Учёт времени.** Фича `time-tracking` есть у `shbvn` (старая база 0.19 — переписать).
- **Мобильное приложение.** Нативное app Plane — Commercial (роут `/m/auth` в Community нет).
  Хотим: PWA (MVP) → реверс `/m/auth` (полноценное app).

## 3. Архитектурные принципы
1. **База eyriehq + overlay-изоляция.** Наш код — в `overlay/` (см. `overlay/README.md`); точки
   подключения в файлах базы метим `// OVERLAY: <feature>`. Это держит merge с `eyriehq/main` чистым.
2. **Multi-remote sync.** `origin`(наш) / `eyriehq`(база, merge) / `upstream`(makeplane, наблюдение) /
   `shbvn`(time-tracking, переписать). Процедуры — `SYNC.md`.
3. **CI-сборка.** GitHub Actions собирают 6 образов (`web, space, admin, live, api, proxy`) на раннерах
   GitHub из ветки `plus` → push в `ghcr.io/gentslava/plane-*:plus`. Сервер сборкой не нагружается.
4. **Развёртывание — Dokploy.** Образы (private) из GHCR через registry-credential. Прокси Plane —
   **Caddy**: за Traefik `SITE_ADDRESS=:80`, `CERT_ACME_CA` НЕпустой, `proxy` как `expose: ["80"]`,
   домен Traefik → `proxy:80`, named volumes.

## 4. Состав дистрибутива
| Фича | Источник | Статус |
|------|----------|--------|
| App Rail (нав-рейл с приложениями) | eyriehq (база) | ✅ из коробки |
| Wiki (как приложение) | eyriehq (база) | ✅ из коробки |
| AI / Vaults (+ AI-ассистент в редакторе) | eyriehq (база) | ✅ из коробки |
| First-class epics | eyriehq (база) | ✅ из коробки |
| API-key auth | eyriehq (база) | ✅ из коробки |
| Time-tracking | shbvn | ⏳ переписать в `overlay/features/time-tracking/` |
| PWA (manifest/SW/иконки) | своё | ⏳ Фаза 2 |
| Нативная мобильная авторизация `/m/auth` | своё (реверс) | ⏳ Фаза 4 |
| Org-chart, custom workflows | shbvn | опционально |

## 5. Нефункциональные требования
- **Лицензия:** AGPL-3.0 (как eyriehq и upstream), репозиторий публичный.
- **Синхронизируемость:** конфликты при `merge eyriehq/main` — минимальны (дисциплина overlay).
- **Платформа образов:** `linux/amd64` (сервер пользователя).
- **Совместимость с Dokploy:** raw docker-compose, домен через Traefik, образы private + registry-cred.
- **Версионирование:** канал `plus` → тег `:plus` (плавающий); релизные теги `v*` → `:version` + `:latest`.

## 6. Инфраструктура
- Репозиторий: `github.com/gentslava/plane`, рабочая ветка `plus`.
- Образы: `ghcr.io/gentslava/plane-{web,space,admin,live,api,proxy}:plus` (private).
- Деплой-домен: `myplane.gentslava.ru` (Dokploy проект **MyPlane**).
- Секреты деплоя — в Dokploy Environment.

## 7. Решённые вопросы
- ✅ База — `eyriehq/plane-plus` (`main`), не upstream (нужен опыт Plus, см. §2).
- ✅ Деплой-домен — `myplane.gentslava.ru`.
- ✅ Из shbvn берём только time-tracking (org-chart/workflows — опционально).
- ⏳ Риск edition-check в нативном приложении (Фаза 4) — проверить первым делом Фазы 4.
