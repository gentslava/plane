# Аудит data-parity GraphQL-шлюза (self-hosted Plane) vs Plane Cloud

## Методология

- Контракт сверки — **Cloud SDL** (`overlay/features/mobile-graphql/reference/cloud-schema.graphql`), снятый интроспекцией Plane Cloud, и набор реальных операций приложения (`reference/operations-raw.graphql`). Это типовой контракт (типы + nullability + enum-члены), **не** захваченные живые ответы Cloud.
- Каждая стартовая операция выполнена **вживую** против нашего шлюза (`apps/api/plane/graphql/`) на тестовом workspace `test` (HTTP 200, `errors=null` зафиксированы). По каждому выбранному полю сверены: тип/nullability против SDL, источник значения (реальный DB-резолвер / smart-fallback / хардкод) и легитимность пустых значений.
- Правило отсева ложных срабатываний: поле, легитимно `null`/пустое для этого единственного тестового workspace (нет аватара, нет лейблов, нет непрочитанных), **не** считается дефектом — резолвер реальный и при наличии данных вернёт корректное значение. Подтверждено перекрёстно: соседние реальные резолверы того же workspace вернули данные (`workspaceStates`=20 строк, `workspaceMembers`=1, `stickies`=1).
- Критичные находки перепроверены по исходникам: `resolvers.py:344-346` (featureFlag), `resolvers.py:440-484` (catchUps), `schema.py:21` (сборка схемы), `schema.graphql:1575-1598` (enum CatchUp), `operations-raw.graphql:76` (CatchUpQuery selection set).

## Таблица расхождений

| Severity | Операция              | Поле                                   | Verdict        | Фикс (кратко)                                                                                                                                                                                                                                 |
| -------- | --------------------- | -------------------------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| high     | CatchUpQuery          | `firstUnread.type` / `lastUnread.type` | shape_mismatch | Резолвер кладёт свободный `entity_name` ('issue') в `CatchUpActivityTypeEnum {COMMENT,ACTIVITY}`; enum не привязан → ошибка сериализации при первой непрочитанной нотификации. Возвращать реальный COMMENT/ACTIVITY (`resolvers.py:481-482`). |
| med      | CatchUpQuery          | `type`                                 | hardcoded      | Жёстко `WORK_ITEM` для всех групп; INTAKE/EPIC мисклассифицируются. Выводить тип из сущности (`resolvers.py:459`).                                                                                                                            |
| med      | FeatureFlagQuery      | `featureFlag.*`                        | hardcoded      | `{flag: False ...}` игнорирует workspace/БД; ни один флаг не включить. Читать реальную конфигурацию фич (`resolvers.py:344-346`).                                                                                                             |
| low      | WorkspaceFeatureQuery | `isInitiativeEnabled`                  | hardcoded      | Константа `False`, не отражает реальную настройку Initiative (`resolvers.py:334-338`).                                                                                                                                                        |
| low      | VersionCheckQuery     | `minSupportedBackendVersion`           | hardcoded      | Константа `v0.0.1`, формат-связана с парсером приложения (нужен префикс `v`). Зафиксировать тестом (`resolvers.py:147-161`).                                                                                                                  |
| low      | UserRecentVisitQuery  | `entityData.isEpic`                    | hardcoded      | `False` для всех; реальный Epic был бы помечен как обычный work item. Вычислять из типа issue (`resolvers.py:414-437,553-571`).                                                                                                               |
| low      | CatchUpQuery          | `workItem.intakeId`                    | hardcoded      | Всегда `null`; intake-связь не заполняется (`resolvers.py:478`).                                                                                                                                                                              |
| low      | TimezoneListQuery     | `label` / `query`                      | cosmetic       | Равны сырому IANA-имени вместо friendly-строки; функционально безвредно (`resolvers.py:~241`).                                                                                                                                                |

> Не вошли в таблицу как дефекты (легитимно пустые для этого workspace, резолверы реальные): `logoUrl`/`avatarUrl`/`coverImageUrl`=null, `workspaceLabels`=[], `notificationCount`={unread:0,workspaces:[]}, `userFavorites/entityData.logoProps`={}, `stickies.results.color`=null, `allProjects[].logoProps`={} на части проектов.

## Архитектурный вердикт по стикам (стартовый race)

Это **гонка времени монтирования, а не проблема null-поля**. На self-hosted `SelfHostedVersionInfoWrapper.build` (`self_hosted_version_info_wrapper.dart:36`) оборачивает `UserDetailInitWrapper` в `BlocBuilder<SelfHostedVersionCubit>` и показывает `AppHomeShimmer`, пока статус загрузки версии != `loaded`/`loadedFromCache`. Поэтому `UserDetailInitWrapper.initState` диспатчит `SetCurrentWorkspace` позже, чем `StickiesCubit.fetchInitialStickies`, который читает `currentWorkspace`, находит `null` и уходит в `ReturnAsyncNotFuture` без запроса — остаётся вечный скелетон. На Cloud `isSelfHosted()` ложно, child монтируется сразу, и порядок обратный. Поле `userInformation.workspace` non-null и **не является** причиной — гонка структурная.

- **Серверное смягчение (частичное):** `GET /api/instances/` должен отвечать мгновенно валидной поддерживаемой версией, чтобы куб переключился в `loaded` до первого кадра — это лишь сужает окно гонки.
- **Детерминированный клиентский фикс (рекомендуется):** монтировать child сразу и накладывать шиммер оверлеем; ИЛИ сидировать `SelfHostedVersionState` из гидрированного кэша; ИЛИ заставить `StickiesCubit` перезапускать `fetchInitialStickies` на переходе `currentWorkspace` вместо разового bail.

## Рекомендуемый guard от регрессий

`pytest apps/api/plane/tests/graphql/test_startup_parity.py`, проигрывающий все стартовые операции против засеянного тестового workspace и утверждающий по дереву ответа против нашего `schema.graphql`:

1. HTTP 200 и `errors is None` для каждой операции.
2. Ни одно SDL-non-null поле (`Type!` и элементы `[Type!]!`) не равно `None`.
3. Каждое значение enum-поля принадлежит множеству членов соответствующего enum в SDL.

**Обязательный кейс:** засеять ровно одну непрочитанную `Notification` и выполнить `CatchUpQuery` — это снимает маску пустого workspace и ловит high-регрессию `firstUnread.type`/`lastUnread.type`. Запускать в CI на каждом PR, трогающем `apps/api/plane/graphql/`.

## Живой Cloud-кросс-чек (выполнен)

Получен реальный мобильный access-токен Cloud (`/auth/mobile/magic-generate/` → `magic-sign-in/` → обмен deeplink-токена через `/auth/mobile/token-check/`), ключевые операции проиграны против `api.plane.so/graphql/` (Cloud-workspace) и сверены с нашим шлюзом (workspace `test`). Сравниваются **паттерны заполнения полей** (данные разных workspace отличаются).

| Поле                   | Наш шлюз                                                                                                               | Cloud (живой)                                                                                                        | Вердикт                                                                                                                                                 |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `featureFlag.*`        | всё `false` (хардкод)                                                                                                  | всё `false` (базовый ws)                                                                                             | ✅ **совпадает** — для Community/базового ws корректно; severity SDL-аудита (med) **завышена**. Разойдётся лишь на enterprise-ws с включёнными флагами. |
| `timezoneList[]`       | `value=query=label` = сырой IANA, алфавит                                                                              | `label="American Samoa"`, `query="Pacific/Pago_Pago American Samoa, GMT-11:00, UTC-11:00"`, сортировка по UTC-офсету | ⚠️ **расхождение подтверждено** — Cloud отдаёт дружелюбный label + богатую строку поиска + офсет-сортировку; косметика (UX пикера), но реальное.        |
| `entityData.isEpic`    | хардкод `false`                                                                                                        | `false` для проекта (вычислено)                                                                                      | ✅ совпало для не-эпиков; латентно для реальных Epic.                                                                                                   |
| `entityData.logoProps` | проекты — реальный `item.logo_props`; **issue/page/cycle/module — хардкод `{}`** (`_entity_lite` resolvers.py:424-436) | реальный `{emoji:{value},in_use}`                                                                                    | ⚠️ проекты ОК (пустота нашего теста = нет эмодзи, данные); для page/cycle/module иконки в Recent/Favorites не отдаём — низкое.                          |
| `catchUps[]`           | `[]` (нет непрочитанных)                                                                                               | `[]` (нет непрочитанных)                                                                                             | enum-краш (firstUnread.type) **не воспроизводится вживую** на пустых ws — остаётся доказан кодом (см. high-находку).                                    |

**Итог кросс-чека:** наш шлюз в целом **консистентен** с Cloud по стартовому набору. Подтверждено реальное расхождение только в `timezoneList` (формат/сортировка) и хардкод `logoProps` для не-проектов; `featureFlag` оказался ложной тревогой (Cloud так же). High-краш `catchUps` остаётся (код), но на «живых» данных нашего/Cloud ws не срабатывает из-за отсутствия непрочитанных.

## god-mode / instance-config кросс-чек (выполнен)

Снят публичный `/api/instances/` Cloud vs наш. Все различия **легитимны**: Cloud-only фичи (github/google/magic-login/smtp/posthog/llm/chat-support включены у Cloud, у нас выключены — мы email+password), env-специфичные `*_base_url`, и `is_self_managed=True` (верно для нас). Ключи, которые Cloud отдаёт, а мы опускаем (`silo_base_url`/`payment_server_base_url`/`prime_server_base_url`/`feature_flag_server_base_url`/`drawio_embed_url`), **приложением не читаются** (проверено по декомпилю) → не дефект. Поля, которые `HostConfigModel` читает, добавлены ранее в `InstanceEndpoint`. Вывод: instance-config консистентен.

## Статус фиксов (применено + верифицировано)

Все расхождения, кроме ложной тревоги `featureFlag`, исправлены в `apps/api/plane/graphql/resolvers.py`, задеплоены на тест и проверены:

- **timezoneList** — live: friendly label + offset-query + сортировка по UTC-офсету.
- **catchUps `firstUnread`/`lastUnread.type`** — **live (через 2-й аккаунт)**: реальные `ACTIVITY`+`COMMENT` без краша; `type=WORK_ITEM`, `workItem` заполнен. Старый код вернул бы `entity_name`='issue' → краш сериализации.
- **catchUps.type / intakeId / isEpic / logoProps(page,cycle,module)** — по конструкции + guard-тест `apps/api/plane/tests/contract/app/test_graphql_mobile_parity.py` (сеет `Notification` напрямую, минуя self-исключение).

Не покрыто живьём (нужны данные в ws): реальный Epic для `isEpic`, иконки page/cycle/module.

_Плейсхолдеры вместо реальных доменов/токенов; операционные координаты тест-стенда — вне публичного репозитория._
