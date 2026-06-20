# Архитектура: официальное мобильное приложение Plane

> Контекст для AI/разработчика: как устроено официальное приложение Plane
> (`com.plane.so`) и как оно общается с бэкендом — чтобы заставить его работать
> против self-hosted. Связано: [03-graphql-gateway](03-graphql-gateway.md),
> [04-auth-flows](04-auth-flows.md), реверс-плейбук
> `overlay/features/mobile-graphql/APP-PLAYBOOK.md`,
> `overlay/features/mobile-auth/REVERSE-ENGINEERING.md`.

## Стек

- **Flutter / Dart 3.9** (AOT-снапшот в `libapp.so`). Приложение в сторе:
  «Plane: Projects, Wiki, AI».
- **GraphQL — Ferry** (контент): весь основной контент идёт через `POST
api.plane.so/graphql/` с `Authorization: Bearer <access-JWT>`, UA `Dart/3.9 (dart:io)`.
- **REST — dio** (часть auth + ассеты).
- **State:** HydratedBloc/Cubit (часть стейтов гидратируется из локального хранилища
  на cold-start), bloc/flutter_bloc, провайдеры.
- **Прочее:** flutter_inappwebview (web-вью контента), flutter_web_auth_2 (deep-link
  callback), firebase (messaging/app_check), Sentry, PostHog, app_links.
- **Локальное хранилище:** Hive (`hydrated_box.hive`), Isar (`isar_community` — локальный
  кэш воркспейсов), flutter_secure_storage (токены — Keystore-backed, не читается plain).

## Cloud vs self-hosted: где развилка

- `isSelfHosted()` (`src/helpers/self_host_helper.dart`) читает сохранённый
  **`server_url_key` из SecureStorage** — это КЛИЕНТСКОЕ решение, НЕ `/api/instances/`.
  Развилку сервером не переключить. `edition`/`is_self_managed` в home/workspace-init не читаются.
- Cloud-базы захардкожены: API `api.plane.so`, GraphQL `api.plane.so/graphql/`, веб/auth
  `app.plane.so`, AI `pi.plane.so`, live `live.plane.so`.
- Для self-hosted приложение берёт введённый URL инстанса как базу для `/auth/*` и `/graphql/`.

## ⚠️ Приложение НЕ honor-ит прокси для своего трафика

GraphQL-клиент (Ferry) и dio шлют прямые `dart:io`-соединения **мимо** Android global
proxy и QEMU `-http-proxy`. Charles/mitm ловят только Sentry/PostHog. Единственный способ
видеть/менять трафик приложения — **frida** (TLS-хук) или патч-APK (reFlutter). См.
REVERSE-ENGINEERING.md.

## Чувствительность к контракту

Приложение писалось под ТОЧНЫЕ ответы Cloud. Любое расхождение принимается молча
(пустой список) ИЛИ ломает экран:

- **non-null нарушение** (резолвер вернул null для SDL `Field!`) → падает ВЕСЬ запрос →
  экран показывает «ошибку загрузки». Это главный класс багов нашего шлюза (см.
  [03-graphql-gateway](03-graphql-gateway.md), грабля non-null).
- неверная форма/enum-член → краш сериализации.
- пустой стаб → молча пустой экран (не видно как ошибку).

## Стартовый флоу (cold-start)

1. `GET /api/instances/` (host-config; UA `Dart/3.9`). Питает `SelfHostedVersionCubit`.
2. `userInformationAndWorkspacesQuery` (GraphQL) → user/profile/last-workspace/deviceInfo +
   список воркспейсов.
3. Дальше веер home-запросов: workspaceStates/Labels/Members, featureFlag, workspaceLicense,
   catchUps, notificationCount, userRecentVisit, userFavorites, allProjects, **stickies**.
4. `userInformation.deviceId` → приложение шлёт deviceId; сервер лениво создаёт `Device`
   (иначе `deviceInfo=null` → приложение считает девайс несинхронизированным и ресинкает home
   с нуля на каждом cold-start).

### Версионный гейт home (важно)

`router_config.dart` оборачивает home-shell в **`SelfHostedVersionInfoWrapper`** ТОЛЬКО
когда `isSelfHosted()`. На self-hosted его `build` держит `AppHomeShimmer` (скелетон), пока
`SelfHostedVersionCubit` не в статусе `loaded`/`loadedFromCache`. Версию парсит
`VersionParseHelper._normalizeVersion` регуляркой **`^v?(\d+\.\d+\.\d+)`** → `Version.parse`.
Префикс `v` **опциональный**: `2.2.1` и `v2.2.1` парсятся одинаково; не-семвер (`latest`) → null.
(Прежнее «`2.2.1` без `v` → null» — ОШИБКА, опровергнуто.) Важно: гейт инвертирует порядок НЕ
из-за парсинга версии — `2.2.1` валиден, а стики всё равно скелетон → причина структурная
(self-hosted-поддерево обёртки). На Cloud `isSelfHosted()=false` → обёртки нет, child монтируется сразу.

### Баг «стики на cold-start = скелетон» — РАЗОБРАН

Клиентская гонка монтирования: `_HomeStickiesWidgetState.initState` зовёт
`fetchInitialStickies` ОДИН раз; бейлит, если `currentWorkspace==null`, и **не слушает**
появление воркспейса (другие home-виджеты слушают `WorkspaceSwitchListener`). На self-hosted
`fetchInitialStickies` (синхронно на mount) выигрывает ~18мс у сетевого `_setCurrentWorkspace`
→ бейл → вечный скелетон, Stickies-запрос даже не уходит. На Cloud порядок обратный.
**Сервером не чинится детерминированно** (задержка `/api/instances/` ОПРОВЕРГНУТА — двигает
оба синхронно). Корень — `isSelfHosted`-гейт `SelfHostedVersionInfoWrapper` инвертирует порядок
монтирования; виджет стиков без листенера на WorkspaceBloc бейлит на null-workspace.
**ДОКАЗАНО frida-интервенцией** (форс `isSelfHosted()→false` переворачивает порядок в Cloud-овский
и стики грузятся) — рецепт и трейс в `mobile-graphql/APP-PLAYBOOK.md` §4. Детали — `PARITY-AUDIT.md`.

## Реверс-инструментарий (как мы это выяснили)

- **blutter** (github.com/worawit/blutter) — декомпиль `libapp.so` → `/tmp/blutter_out/asm/`
  (per-package .dart с ARM-дизасмом + Dart-символами; каждый `bl` аннотирован именем
  Dart-функции — можно трассировать вызовы). Пакет приложения — `plane`.
- **frida** 16.7 + frida-server-16 на эмуляторе — хук Dart-AOT по адресам blutter:
  `Interceptor.attach(Module.findBaseAddress('libapp.so').add(0xADDR), {...})`. Ключевые
  адреса (пере-снять при обновлении APK): `fetchInitialStickies` 0xd0bc8c,
  `_setCurrentWorkspace` 0x11024d0.
- **Эмулятор-грабли:** DNS QEMU сломан → запуск с `-dns-server 8.8.8.8,8.8.4.4`;
  captive-portal сбрасывать (`settings put global captive_portal_mode 0`); `-gpu host`.
  Подробности — APP-PLAYBOOK.md.

## Авто-логин для отладки (без UI)

curl-флоу даёт deep-link с токеном без ручного ввода в UI — см.
[04-auth-flows](04-auth-flows.md) и APP-PLAYBOOK.md §1. Инжект deeplink:
`adb shell "am start -a android.intent.action.VIEW -d '<app.plane.so://...>'"` БЕЗ пакета
(резолвится в CallbackActivity), приложение должно ждать callback (после Continue открыт Custom Tab).
