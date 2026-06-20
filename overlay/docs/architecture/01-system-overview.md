# Архитектура: система Plane (обзор) и наш форк

> Точка входа в архитектурную документацию форка. Дальше:
> [02-mobile-app](02-mobile-app.md) · [03-graphql-gateway](03-graphql-gateway.md) ·
> [04-auth-flows](04-auth-flows.md) · ADR в `../adr/`. Спека/дорожная карта форка —
> `../SPEC.md`, `../ROADMAP.md`. Синхронизация — `../../../SYNC.md`.

## Что такое Plane

Self-hosted система управления проектами (альтернатива Jira/Linear). Компоненты:

| Сервис            | Стек                                  | Роль                                                                       |
| ----------------- | ------------------------------------- | -------------------------------------------------------------------------- |
| **web**           | Next.js / React Router (SPA)          | основной веб-UI                                                            |
| **admin**         | Next.js                               | god-mode / instance-админка (настройки инстанса, auth-провайдеры, SMTP, …) |
| **space**         | Next.js                               | публичные страницы/проекты                                                 |
| **live**          | Node (Hocuspocus/Yjs)                 | realtime-коллаборация (документы)                                          |
| **api**           | Django + DRF + Celery                 | REST `/api/*`, auth `/auth/*`, **наш GraphQL `/graphql/`**, воркеры        |
| **proxy**         | **Caddy** (`apps/proxy/Caddyfile.ce`) | реверс-прокси (НЕ nginx в 1.3.x)                                           |
| db/redis/mq/minio | Postgres / Valkey / RabbitMQ / MinIO  | данные / кэш / очереди / файлы                                             |

Мобильное приложение (`com.plane.so`, Flutter) — **отдельный официальный клиент**, ходит
в `api` через GraphQL (контент) + REST (часть). См. [02-mobile-app](02-mobile-app.md).

## Community vs Enterprise (важно для паритета)

Часть фич Plane — **EE/коммерческий слой** (`@/plane-web/`), в upstream Community отключён:
App Rail, Wiki-как-приложение, AI-ассистент, first-class epics, API-key auth, **Initiatives**,
**мобильное приложение + его GraphQL**. Наша база — **`eyriehq/plane-plus`**, которая
переоткрыла EE-фичи в AGPL-форке. Поэтому у нас есть epics/App Rail/Wiki/AI «из коробки»,
но НЕТ, например, Initiatives (модели нет в community-коде — в нашем GraphQL это безопасные стабы).

## Наш форк — суть

Дистрибутив **«Plane Plus»** на базе eyriehq + наши фичи. Ключевая фича — **заставить
официальное мобильное приложение работать против self-hosted**, чего из коробки нет
(мобильный GraphQL — EE-only). Решение: вручную реализовать GraphQL-контракт Cloud поверх
наших Django-моделей (см. [03-graphql-gateway](03-graphql-gateway.md)) + мост авторизации
`/m/auth` + `/auth/mobile/*` (см. [04-auth-flows](04-auth-flows.md)).

```
                 ┌─────────────────── api.plane.so (Cloud) ──────────────────┐
  Приложение ───▶│  GraphQL /graphql/ (EE-only)   REST /api/   auth /auth/    │
  com.plane.so   └────────────────────────────────────────────────────────────┘
       │  isSelfHosted()? (server_url_key в SecureStorage — КЛИЕНТСКИ)
       ▼
                 ┌────────────── наш self-hosted (Caddy → api) ───────────────┐
                 │  НАШ GraphQL /graphql/ (ariadne, реплика Cloud SDL)         │
                 │  REST /api/ (штатный Plane)   /m/auth + /auth/mobile/* (наш)│
                 └────────────────────────────────────────────────────────────┘
```

## Принцип изоляции форка (overlay)

Весь наш код — в `overlay/` или помечен `// OVERLAY: <feature>` в файлах базы. Это держит
merge с `eyriehq/main` чистым. Точки: `overlay/features/` (mobile-auth, mobile-graphql, pwa,
time-tracking), `overlay/ci/`, `overlay/docs/`. Sync-процедуры — `SYNC.md`. Ветки: `plus`
(основная, = eyriehq/main + overlay), `master`/`preview` (зеркала upstream, наблюдение).

## Развёртывание

CI (GitHub Actions `build-images.yml`) собирает `ghcr.io/gentslava/plane-*:plus` из ветки
`plus`. Деплой — **Dokploy** (проект «Plane»): два compose на общем теге `:plus` — тест и
прод. **Прод не редеплоить без явного разрешения** (общий тег). Точные composeId/домены —
в приватной памяти (НЕ в публичном репо).

## Карта документации (AIDD)

- `architecture/01-system-overview.md` (этот) — обзор.
- `architecture/02-mobile-app.md` — клиент: стек, auth, стартовый флоу, гонка стиков, реверс.
- `architecture/03-graphql-gateway.md` — наш шлюз: сборка схемы, smart-резолвер, области,
  грабли non-null, паритет, локальная разработка.
- `architecture/04-auth-flows.md` — контракт авторизации (web/mobile, Cloud/self-hosted).
- `adr/` — записи решений (почему так).
- `overlay/features/mobile-graphql/PARITY-AUDIT.md` — полный data-parity аудит + Cloud-кросс-чек.
- `overlay/features/mobile-graphql/APP-PLAYBOOK.md` — операционка приложения (frida/эмулятор).
- `overlay/features/mobile-auth/{API-CONTRACT,REVERSE-ENGINEERING}.md` — детали auth + реверс.
