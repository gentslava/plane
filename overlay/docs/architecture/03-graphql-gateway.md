# Архитектура: GraphQL-шлюз нативного приложения

> Контекст для AI/разработчика: как устроен наш GraphQL-шлюз, реализующий
> коммерческий GraphQL-контракт Plane Cloud на self-hosted, чтобы официальное
> мобильное приложение `com.plane.so` работало против нашего инстанса.
> Код: `apps/api/plane/graphql/`. Связанные доки: [02-mobile-app](02-mobile-app.md),
> [04-auth-flows](04-auth-flows.md), `overlay/features/mobile-graphql/PARITY-AUDIT.md`.

## Зачем это вообще

Официальное приложение Plane писалось под **коммерческий** GraphQL Plane Cloud
(`https://api.plane.so/graphql/`). В Community/self-hosted этого эндпоинта нет —
там только REST (`/api/...`). Поэтому приложение против self-hosted не работало.
Мы **вручную реализуем тот же GraphQL-контракт** поверх наших Django-моделей, и
приложение «не замечает» подмены.

Ключевой принцип: **контракт = Cloud SDL** (`reference/cloud-schema.graphql`), снятый
интроспекцией с Cloud. Это источник истины (типы, nullability, enum-члены). Логику
каждого поля **реплицируем из существующих REST-вью Plane** (`apps/api/plane/app/views/`) —
там ground truth (querysets/фильтры/сериализаторы).

## Сборка схемы (`schema.py`)

```python
schema = make_executable_schema(type_defs, *AREA_BINDABLES, *RESOLVERS, *MUTATION_BINDABLES, *SCALARS)
install_safe_query_defaults(schema)   # непривязанные root-поля -> безопасный пустой дефолт
install_smart_fallback(schema)        # ВСЕ прочие поля -> camelCase->snake_case / FK->_id
```

- `type_defs` — SDL целиком (`schema.graphql`, ~220 типов, 158 Query-полей, 183 Mutation-поля).
- **Порядок bindables = last-wins на коллизиях.** `AREA_BINDABLES` идут ПЕРЕД `RESOLVERS`/
  `MUTATION_BINDABLES`, поэтому established core выигрывает на полях, что область
  перепривязала (эпик-мутации, initiativesCount). Внутри областей `pages_workspace` после
  `pages_project` (общий `PageType`).
- `install_smart_fallback` ставится ПОСЛЕ build, **глобально** ко всем типам — новым
  ObjectType'ам ручной fallback не нужен. Поэтому AREA_BINDABLES обязаны быть ВНУТРИ
  `make_executable_schema(...)`, а не после.
- `install_safe_query_defaults` привязывает ЛЮБОЕ непривязанное Query/Mutation-поле к
  пустышке (`[]`/`0`/`null`/empty-paginator). **Грабли:** «нет GraphQL-ошибок» НЕ значит
  «реализовано» — молчаливый стаб проходит проверку. Аудитить надо сверкой с БД/SDL.

## smart_default_resolver — что делает сам

`resolver_utils.install_smart_fallback` для КАЖДОГО поля без явного резолвера:

- `camelCase` → `snake_case` атрибут модели / ключ dict (`createdAt`→`created_at`).
- Поле SDL-типа `ID`/`ID!` для FK → `<field>_id` как строка (`project`→`project_id`).
- Поэтому **привязывать вручную нужно ТОЛЬКО**: (a) root `@query.field`/`@mutation.field`;
  (b) вычисляемые/агрегатные поля; (c) M2M-как-список-id
  (`[str(pk) for pk in obj.m2m.values_list("id", flat=True)]`); (d) FK-как-вложенный-объект,
  когда приложение выбирает суб-поля (вернуть объект + `select_related`).
  Тривиальные скаляр/FK-id поля НЕ привязывать — за них работает fallback.

## ⚠️ ГЛАВНАЯ ГРАБЛЯ: non-null поля, которых нет на модели

Самый коварный класс багов. SDL-тип имеет `Field!` (non-null), но это **вычисляемое**
поле, которого НЕТ колонкой на модели. Тогда smart-fallback вернёт `None` →
**нарушение non-null → падает ВЕСЬ запрос** (не одно поле). Приложение показывает
«ошибку загрузки» на экране.

Пример (реальный баг этой сессии): `NotificationType.isIntakeIssue: Boolean!`,
`isEpic: Boolean!` — на модели `Notification` таких колонок нет (REST-вью аннотирует их
per-row). Без явных резолверов → `Cannot return null for non-nullable field
NotificationType.isIntakeIssue` → инбокс уведомлений в приложении пустой/с ошибкой,
хотя `notificationCount` (бейдж) работает.

**Правило:** для каждого non-null SDL-поля проверь, что его реально кто-то заполняет —
либо колонка модели, либо явный field-резолвер. Вычисляемые non-null (is*, *Count,
analytics, \*Identifier) почти всегда требуют явного резолвера.

## Пагинация (`_page`)

`*PaginatorResponse` типы. Курсор формата `"limit:page:is_prev"` (напр. `"100:0:0"`).
`_page(rows, cursor)` нарезает по offset и считает `nextCursor`/`prevPageResults` и т.д.
**Грабли:** если игнорировать offset и возвращать полный список каждой страницей —
приложение в бесконечном лоадере (никогда не дойдёт до пустой страницы). Реальная
нарезка обязательна.

## Структура кода

```
apps/api/plane/graphql/
  schema.py            # сборка: SDL + RESOLVERS + MUTATION_BINDABLES + AREA_BINDABLES + SCALARS
  schema.graphql       # Cloud SDL (контракт)
  resolvers.py         # ЯДРО: query/mutation инстансы, ObjectType'ы, RESOLVERS-список,
                       #   _user/_page/_track_visit/_entity_lite хелперы, стартовый+home+board флоу
  resolver_utils.py    # install_smart_fallback, install_safe_query_defaults, smart_default_resolver
  scalars.py           # JSON/DateTime/UUID скаляры
  context.py           # контекст запроса
  views.py             # POST /graphql/ (MobileJWTAuthentication; [MOBILE-GQL]-лог за MOBILE_DEBUG_LOG; csrf_exempt)
  urls.py
  mutations/           # core-мутации: work_items.py, workspace.py, planning.py (каждый — свой MutationType + BINDABLES)
  areas/               # ПОФИЧНЫЕ модули (см. ниже)
    __init__.py        # агрегирует BINDABLES всех областей
    epics.py initiatives.py intake.py pages_project.py pages_workspace.py
    collections.py assets.py cycles_modules.py issue_extras.py invites_misc.py
```

### Модули-области (`areas/`)

Каждый модуль **самодостаточен**: свои `QueryType()`/`MutationType()`/`ObjectType("X")`,
импорт хелперов `from plane.graphql.resolvers import _user, _page`, в конце
`BINDABLES = [query, mutation, <ObjectType'ы>]`. ariadne мёржит несколько
QueryType/MutationType-инстансов (last-wins на коллизии поля). Это позволяет писать
области **параллельно в отдельных файлах** без конфликтов (так и делали — агент на область).

## Аутентификация GraphQL

`POST /graphql/` → `MobileJWTAuthentication` читает `Authorization: Bearer <access-JWT>`.
Без токена → `{"message":"Authentication required"}` 401 (паритет с Cloud). Резолверы
берут юзера через `_user(info)` = `info.context.user`. См. [04-auth-flows](04-auth-flows.md).

## Состояние реализации (на момент сессии)

- **Ядро (resolvers.py + mutations/):** стартовый флоу, home (favorites/recent/catchUps/
  notificationCount/notifications-инбокс/stickies), доска (issues/states/labels/members/
  cycles/modules list), work-item CRUD (createIssueV2/updateIssueV2), комменты/ссылки/
  аттачменты/реакции, проекты.
- **Области (areas/, 119 query + 102 mutation):** epics, intake, pages (project+workspace),
  collections, assets, cycles/modules-детали, issue-extras, invites/misc — реплика REST.
- **Стабы (намеренно):** `initiatives` — EE-фича, в community-коде НЕТ модели/таблицы/REST,
  поэтому безопасные non-null-дефолты (фича гейтнута off через featureFlag/workspaceFeatures).
  `featureFlag`/`workspaceFeatures` — all-false (совпадает с Cloud для базового ws). `issueUserProperties`/
  `epicUserProperty` — sane-дефолты (нет модели пользовательских view-prefs).

## Data-parity находки (полный список — `PARITY-AUDIT.md`)

Метод: SDL-аудит (workflow) + ЖИВОЙ Cloud-кросс-чек (реальный Cloud-токен → те же
операции против `api.plane.so/graphql/`). Итог:

- **HIGH (краш):** `catchUps.firstUnread/lastUnread.type` отдавал сырой `entity_name` в
  enum `CatchUpActivityTypeEnum{COMMENT,ACTIVITY}` → краш сериализации при любом
  непрочитанном. Фикс: маппинг из `data.issue_activity.field`.
- **Реальные расхождения:** `timezoneList` (Cloud: friendly label + offset-сортировка;
  было сырой IANA), `logoProps={}` для page/cycle/module, `notifications`-инбокс (был
  пустой стаб), `notificationCount.mentioned` (был 0), `isFavorite`/`isEpic`/`parentIsEpic`
  (были хардкод False), `NotificationType.isIntakeIssue/isEpic` (non-null, см. граблю).
- **Ложная тревога:** `featureFlag` all-false — Cloud для базового ws отдаёт так же.

## Локальная разработка (быстрая итерация, без деплоя)

Django + ariadne ставятся локально; схему можно собрать и гонять `graphql_sync` без HTTP:

```bash
python3 -m venv /tmp/planenv
# psycopg-c требует pg_config (postgres-исходники) — ставим БЕЗ него (psycopg-binary хватает):
grep -v "psycopg-c==" apps/api/requirements/base.txt > /tmp/r.txt
/tmp/planenv/bin/pip install --prefer-binary -r /tmp/r.txt
# локальный postgres + миграции:
docker run -d --name plane-pg -e POSTGRES_USER=plane -e POSTGRES_PASSWORD=plane -e POSTGRES_DB=plane -p 5433:5432 postgres:15.7-alpine
cd apps/api
env DJANGO_SETTINGS_MODULE=plane.settings.test SECRET_KEY=x \
    DATABASE_URL="postgresql://plane:plane@localhost:5433/plane" \
    REDIS_URL="redis://localhost:6379/0" AMQP_URL="amqp://p:p@localhost:5672/p" \
    /tmp/planenv/bin/python manage.py migrate --noinput
# сборка схемы (ловит ошибки привязок/импортов ДО деплоя):
... python -c "import django;django.setup();from plane.graphql.schema import schema;print(len(schema.type_map))"
```

Сборка схемы локально — **главный гейт перед деплоем**: одна плохая привязка/импорт в
любом модуле роняет ВЕСЬ шлюз на старте api-контейнера.

## Деплой / CI

Ветка `plus` → push → CI `build-images.yml` собирает `ghcr.io/gentslava/plane-*:plus`
(paths-ignore `overlay/**`, `**/*.md` — доковые правки сборку не триггерят, код — триггерит).
Redeploy через Dokploy (`compose-redeploy {composeId}`). Тест и прод — общий тег `:plus`,
прод НЕ редеплоить без явного разрешения. Координаты — в приватной памяти.
