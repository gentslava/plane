# Дорожная карта реализации форка

> Спецификация — [`SPEC.md`](SPEC.md). Процедуры синхронизации — [`../../SYNC.md`](../../SYNC.md).
> Чекбоксы отмечать по мере выполнения.

---

## ✅ Фаза 0 — Каркас форка (СДЕЛАНО)

- [x] Форк `makeplane/plane` → `gentslava/plane`, remotes `origin / upstream / eyriehq / shbvn`.
- [x] Ветки-зеркала upstream: `master` (релизы), `preview` (dev).
- [x] `overlay/` (features/docs/ci) + `SYNC.md`.

---

## ✅ Фаза 1 — CI: свои образы в GHCR + Dokploy (СДЕЛАНО)

- [x] `.github/workflows/build-images.yml`: matrix 6 образов (`web, space, admin, live, api, proxy`),
      `docker/build-push-action`, `linux/amd64`, кэш `type=gha`, push в `ghcr.io/gentslava/plane-*`.
      Каналы тегов: `master`/тег `v*` → `:<version>`+`:latest`; любая ветка → `:<branch>` (`plus` → `:plus`).
- [x] Образы **private**, Dokploy тянет через registry-credential `GHCR` (PAT `read:packages`).
- [x] `overlay/ci/docker-compose.dokploy.yml` — зеркало community-compose, образы `ghcr.io/gentslava/plane-*`.
- [x] Развёрнут baseline в Dokploy (проект **MyPlane**, домен `myplane.gentslava.ru` → `proxy:80`, Traefik+LE).

**Важное про прокси:** в Plane 1.3.x прокси — **Caddy** (`apps/proxy/Caddyfile.ce`), не nginx. За Traefik:
`SITE_ADDRESS=:80`, `CERT_ACME_CA` **непустой** (иначе Caddy падает), `proxy` как `expose: ["80"]`.

---

## ✅ ПОВОРОТ: база форка → `eyriehq/plane-plus` (СДЕЛАНО)

Сначала baseline собрали из чистого upstream — но в upstream Community нет опыта Plane Plus:
App Rail / Wiki-как-приложение / AI / first-class epics — это **EE/коммерческий слой** Plane
(`@/plane-web/`), в CE отключён (`apps/web/ce/.../app-rail/provider.tsx` → `isEnabled=false`).

**Решение:** базой дистрибутива сделана `eyriehq/plane-plus` (`main`, Plane 1.3.1) — там эти фичи
уже реализованы. Заведена ветка **`plus`** = `eyriehq/main` + наш overlay/CI.

- [x] `git checkout -b plus eyriehq/main`, push в `origin/plus`.
- [x] Перенесён наш `build-images.yml` (канал `:plus`) + `overlay/` + `SYNC.md`.
- [x] Собраны `ghcr.io/gentslava/plane-*:plus` (App Rail/Wiki/AI/epics/api-key из коробки).
- [ ] Развернуть `:plus` на `myplane.gentslava.ru` со свежей БД (eyriehq-миграции).

**Цена решения:** база ≈ Plane 1.3.1, отстаёт от upstream/preview. Свежесть Plane получаем через
eyriehq (они мержат upstream к себе). Детали — `SPEC.md` §2, `SYNC.md`.

---

## Фаза 2 — PWA (MVP мобильного)

**Цель:** «приложение на телефоне» без нативного app.

- [ ] `overlay/features/pwa/`: `manifest.json` (standalone, иконки, start_url), service worker (офлайн-оболочка), splash.
- [ ] Точка подключения в `apps/web` (метка `// OVERLAY: pwa`): manifest в `<head>`, регистрация SW.
- [ ] (Опц.) web-push.
- [ ] Проверка на телефоне: «Добавить на главный экран» → standalone.

**Критерий:** ставится как PWA, открывается без адресной строки, базовый офлайн.

---

## Фаза 3 — Time-tracking из shbvn

**Цель:** учёт времени (единственная фича, которой нет в базе eyriehq).

- [ ] `git fetch shbvn`; изучить их `time-tracking` как референс (модели, API, UI).
- [ ] **Переписать** под текущую схему Plane 1.3.1 в `overlay/features/time-tracking/`.
- [ ] (Опц.) org-chart, custom workflows — только если нужны.

**Критерий:** учёт времени работает; merge `eyriehq/main` остаётся чистым.
**Риски:** shbvn-код (база 0.19) сильно разошёлся — закладывать переписывание, не cherry-pick.

---

## Фаза 4 — Нативная мобильная авторизация (`/m/auth`)

**Цель:** заставить нативное приложение Plane логиниться к нашему инстансу.

- [ ] **Сначала проверить риск edition-check:** не отказывает ли приложение нашему инстансу.
- [ ] Реверс auth-флоу: какие запросы шлёт на `/m/auth*`, формат токенов.
- [ ] Минимальный handshake в `overlay/features/mobile-auth/`: `/m/auth` выдаёт креды → дальше `/api/`.
- [ ] Тест на реальном устройстве.

**Критерий:** приложение из стора логинится и открывает рабочее пространство.
**Риски:** закрытый протокол, edition-check, поломка при обновлении app, серая зона ToS.

---

## Фаза 5 — Мониторинг развития форков

- [ ] `.github/workflows/forks-digest.yml` (cron, раз в день): fetch eyriehq/upstream/shbvn → issue «Новое в форках».
- [ ] **Альтернатива:** cron в Hermes-агенте → GitHub API трёх репо → дайджест в Telegram.

**Критерий:** регулярный дайджест; перенос — вручную (по `SYNC.md`).

---

## Быстрый старт следующей сессии

1. `cd ~/Developer/plane && git checkout plus && git pull`
2. Открыть этот файл и `SPEC.md`.
3. Следующее по плану — **Фаза 2 (PWA)** либо **Фаза 3 (time-tracking)**.

### Текущее состояние (для контекста)

- **Рабочая ветка — `plus`** (база `eyriehq/main` = Plane Plus 1.3.1 + наш overlay/CI).
- CI: `build-images.yml` (канал `:plus` при push в `plus`; `master`/тег `v*` → `:version`+`:latest`).
  Ручной: `gh workflow run build-images.yml -R gentslava/plane --ref plus -f build_ref=plus`.
- Образы (private): `ghcr.io/gentslava/plane-{web,space,admin,live,api,proxy}:plus`.
- Деплой: Dokploy проект **MyPlane**, compose `tuwWKg9EFrrMWWTbdIJOX`, домен `myplane.gentslava.ru`,
  `APP_RELEASE=plus`. GHCR-креды в Dokploy registry `GHCR`. Compose — `overlay/ci/docker-compose.dokploy.yml`.
- Обновить базу: `git fetch eyriehq && git checkout plus && git merge eyriehq/main && git push origin plus`
  → CI пересоберёт `:plus` → в Dokploy redeploy MyPlane.
