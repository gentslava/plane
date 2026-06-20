<!-- OVERLAY: mobile-graphql -->

# Phase 4b — GraphQL-шлюз для нативного мобильного приложения: оценка объёма

## Контекст

Phase 4 (нативный вход в официальное приложение Plane, см. `../mobile-auth/`) довела **авторизацию**
до рабочего состояния end-to-end. Но после логина приложение упирается в бесконечный лоадер
(«Sorry, we are unable to get your info»). Диагностика (логи web-контейнера) показала причину:

```
POST /graphql/  405  "Dart/3.9 (dart:io)"   ← приложение, дважды, потом сдаётся
```

Нативное приложение Plane v2.x грузит **весь контент** (профиль, воркспейсы, проекты, доску,
issues, циклы, модули, страницы, …) через единый эндпоинт **`POST {server}/graphql/`**.
В Caddy этот путь не сматчен (`/api/*`,`/auth/*`→api), проваливается в `/*`→web (Next.js),
который на POST отдаёт 405.

## Почему этого нет в базе

- В нашей базе **eyriehq/plane-plus** (≈ Plane 1.3.1) GraphQL отсутствует целиком: ни эндпоинта,
  ни библиотеки (`strawberry`/`graphene`/`ariadne` — 0 совпадений).
- В официальном open-source **`makeplane/plane`** (проверено через GitHub code search + дерево
  ветки `preview`) GraphQL-сервера **тоже нет**: `search/code repo:makeplane/plane graphql` → 1
  случайное упоминание, каталогов `graphql`/`gql` в `apiserver` нет, отдельного репозитория в org
  `makeplane` нет.
- Вывод: **GraphQL-шлюз — проприетарный компонент Plane Cloud.** Self-hosted (и наш форк) обязан
  реализовать его сам, чтобы нативное приложение заработало.

## Размер контракта

- **188 операций** (111 queries / 77 mutations) — см. `operations-inventory.md`. Это фактически
  весь data-API Plane, выраженный в GraphQL: workspaces, projects, work items/issues, cycles,
  modules, epics, initiatives, intake, pages, collections, stickies, favorites, notifications,
  search, members, labels, states, assets, comments, reactions, activities, user-properties.
- Схема **camelCase**, кастомные агрегаты (`versionCheck`, `featureFlag`, `workspaceFeatures`,
  `userInformationAndWorkspaces`, `yourWork`) поверх стандартных моделей.
- Эталонная схема на Cloud (`api.plane.so/graphql/`) закрыта авторизацией; интроспекция недоступна
  без токена (и, вероятно, отключена). Поэтому контракт снимаем из двух источников:
  1. имена операций + мелкие запросы — из `libapp.so` (готово, `reference/`);
  2. точные selection-set'ы + input-типы + переменные — **рантайм-перехватом** реальных POST-тел
     приложения на нашем `/graphql/` (фаза 1 плана).

## Оценка трудозатрат

| Уровень                  | Объём                                                                                                            | Что даёт                                                                                             |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| **MVP «доска грузится»** | ~10-15 операций входного флоу (см. inventory → «Точка входа» + Workspaces/Projects/Issues/States/Labels/Members) | приложение проходит экран «unable to get your info», показывает воркспейсы/проекты/доску в read-only |
| **Рабочий просмотр**     | ~40-60 операций (детали issue/epic/cycle/module/page, комментарии, активности, вложения)                         | полноценный просмотр контента                                                                        |
| **Полный паритет**       | все 188 (вкл. мутации: создание/редактирование, реакции, favorites, intake, sticky)                              | редактирование наравне с Cloud                                                                       |

Это **многонедельный под-проект**, не точечная правка. Реализуем **инкрементально**, операция за
операцией, начиная со входного флоу. Каждая операция реализуется целиком (GraphQL валидирует весь
запрос против схемы — частичная поддержка операции невозможна).

## Технические решения (детали — в PLAN.md)

- **Эндпоинт**: новое Django-приложение в `apps/api/plane/graphql/`, маршрут `POST /graphql/`,
  добавить `reverse_proxy /graphql/* api:8000` в `apps/proxy/Caddyfile.ce` (перед `/*`→web).
- **Auth**: переиспользовать `MobileJWTAuthentication` + сессию (приложение шлёт Bearer и/или куку
  `session-id`) — уже реализовано в Phase 4.
- **Библиотека**: кандидаты — `strawberry-graphql` (типобезопасно, modern), `graphene-django`
  (авто-camelCase, маппинг моделей), `ariadne` (schema-first SDL = контракт буквально). Решение
  зафиксировать после захвата 1-2 реальных запросов (фаза 1).
- **Подход**: «capture → implement → repeat». Сначала `/graphql/` в режиме логирования снимает
  точные тела; затем реализуем операции входного флоу; приложение идёт дальше → снимаем следующие.

## Что уже готово (Phase 4, ветка `plus`)

- Auth-контракт `/auth/mobile/*`, deep link `/m/auth`, SimpleJWT — `../mobile-auth/`.
- `MobileJWTAuthentication` на `/api/` (Bearer) и сессия (Set-Cookie) — работают.
- `APP_BASE_URL`/`SPACE_BASE_URL`/`ADMIN_BASE_URL` в env тестового инстанса (нужны приложению для
  WebView-контента) — заданы.
- Тестовый инстанс `<test-instance>` (email+password, `<test-email>`), пропатченный APK на
  реальном устройстве + эмулятор `emulator-5554`.
