<!-- OVERLAY: mobile-graphql -->

# Phase 4b — план реализации GraphQL-шлюза

Принцип: **capture → implement → repeat**. Сначала снимаем точные тела запросов приложения,
затем реализуем операции входного флоу, приложение идёт дальше — снимаем следующие. Работаем на
ветке `plus`, образы `:plus` → GHCR → redeploy теста `<test-instance>`.

## Раскладка файлов

```
apps/api/plane/graphql/            # новое Django-приложение (overlay)
  __init__.py
  apps.py                          # AppConfig
  views.py                         # GraphQLView (POST /graphql/), auth, capture-лог
  schema/
    __init__.py                    # сборка корневой схемы (Query/Mutation)
    types/                         # типы по доменам (user, workspace, project, issue, …)
    queries/                       # резолверы queries
    mutations/                     # резолверы mutations
  urls.py                          # path("graphql/", GraphQLView)
apps/proxy/Caddyfile.ce            # + reverse_proxy /graphql/* api:8000  (перед /*→web)
```

Подключение: добавить `plane.graphql` в `INSTALLED_APPS`, `path("graphql/", include("plane.graphql.urls"))`
в корневой `plane/urls.py`, зависимость GraphQL-библиотеки в `apps/api/requirements/base.txt`.

## Фаза 1 — каркас + захват контракта (СНАЧАЛА)

1. **Caddy**: `reverse_proxy /graphql/* api:8000` в `Caddyfile.ce` перед `reverse_proxy /* web:3000`.
2. **Capture-view** `POST /graphql/`:
   - аутентификация: `MobileJWTAuthentication` + `BaseSessionAuthentication` (Bearer/кука);
   - логировать `[MOBILE-GQL] op=<operationName> vars=<variables> query=<query>` в stderr
     (как `[MOBILE-AUTH]`, т.к. logger «plane» в деплое глушится);
   - вернуть валидный GraphQL-ответ-ошибку `{"errors":[{"message":"not implemented","extensions":{"op":...}}]}`
     (200), чтобы приложение не падало, а мы видели следующий запрос (если оно ретраит/идёт дальше).
3. Redeploy → залогиниться в приложении (`<test-email>`) → собрать из логов точные тела входного флоу:
   `VersionCheckQuery`, `userInformationAndWorkspacesQuery`, `FeatureFlagQuery`, `WorkspaceFeatureQuery`,
   `ToursQuery`, `WorkspacesQuery`, … (точные selection-set'ы + переменные + `deviceId`).
4. Сохранить captured-запросы в `reference/captured/*.graphql`.

**Выход фазы 1**: точный контракт входного флоу + выбор библиотеки (по реальной форме запросов).

## Фаза 2 — выбор библиотеки и скелет схемы

- Кандидаты: `strawberry-graphql` (типобезопасно, авто-camelCase через `strawberry.field`),
  `graphene-django` (маппинг моделей, camelCase из коробки), `ariadne` (schema-first SDL).
- Критерий: минимум кода для точного совпадения camelCase-имён и кастомных агрегатов.
  Предв. рекомендация — **strawberry** (чистые типы, async-ready, контроль имён); финал — после фазы 1.
- Скелет: корневые `Query`/`Mutation`, интеграция auth (контекст `request.user`), camelCase-конвенция,
  обработка ошибок в GraphQL-формате.

## Фаза 3 — входной флоу (MVP «приложение проходит экран»)

Реализовать целиком (по captured-контракту):

- `versionCheck(platform)` → `{version, minSupportedVersion, minSupportedBackendVersion}`
  (значения из `APP_VERSION`/констант; minSupportedBackend ≤ нашей версии).
- `userInformationAndWorkspaces(deviceId)` → текущий `User` + список `Workspace` (+ membership/роли).
- `workspaces` → список воркспейсов пользователя.
- `workspaceFeatures(slug)` → `{isInitiativeEnabled}` (из `WorkspaceFeature`/дефолты).
- `featureFlag(slug)` → набор булевых флагов (дефолты/`FeatureFlag` если есть).
- `workspaceLicense` → план/лицензия (заглушка community).
- `tours` → `{homeTour}`; `timezoneList`; `updateProfile(...)`.

**Выход фазы 3**: исчезает «Sorry, we are unable to get your info», приложение показывает воркспейсы.

## Фаза 4 — контент (доска read-only)

`projects`/`allProjects`, `projectMembers`/`workspaceMembers`, `states`/`workspaceStates`,
`labels`/`workspaceLabels`, `workspaceIssues`, `issueQuery`/`issueDetails`, `cycles`/`modules`,
`yourWork`, `favorites`, `userRecentVisit`, `notificationCount`/`notifications`.

**Выход фазы 4**: рисуются проекты и доска issues.

## Фаза 5 — детали и мутации

Детали issue/epic/cycle/module/page (комментарии, активности, вложения, реакции, links, sub-issues),
затем мутации: create/update issue, comment, favorite/unfavorite, intake, sticky, страницы, профиль.

## Фаза 6 — финал

- Убрать capture-логирование (verbose stderr) и инструментацию Phase 4.
- Squash коммитов Phase 4 + 4b в чистые `feat(mobile-auth): …`, `feat(mobile-graphql): …`, `docs(mobile): …`.
- Перенести env `APP_BASE_URL`/… в overlay-compose форка.
- Порт на прод (`<deploy-domain>`/MyPlane) — после явного разрешения (тот же тег `:plus`).

## Грабли (учтённые)

- Redeploy Dokploy иногда не подтягивает новый образ с первого раза → редеплоить повторно и поллить.
- GraphQL валидирует **весь** запрос против схемы — операцию реализуем целиком, иначе вся падает.
- camelCase (GraphQL) ↔ snake_case (Django ORM) — настроить авто-конвертацию.
- Auth: приложение шлёт Bearer и/или куку `session-id`; контекст резолверов = `request.user`.
- Логи: `compose-readLogs` с `search` → 500; читать `tail`+`since`. Реальные тела — в web-логе как
  `POST /graphql/`, но без тела; тело видно только в нашей capture-вьюхе (api-лог).
