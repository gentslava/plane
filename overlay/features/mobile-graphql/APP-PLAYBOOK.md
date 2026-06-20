# Native App Playbook — операционка для `com.plane.so`

Хард-вон знания по реверсу/отладке официального мобильного приложения Plane
(Flutter, Dart 3.9.2 AOT) против self-hosted. Чтобы **не разведывать заново**.
Дополняет `../mobile-auth/REVERSE-ENGINEERING.md` (патч-APK, mitm) и
`../mobile-auth/API-CONTRACT.md` (контракт `/auth/mobile/*`).

> **Координаты стенда (домен, тест-аккаунт, Dokploy-ID, IP, UUID) НЕ хранятся в этом
> публичном репозитории** — они в приватных заметках. Ниже всё через плейсхолдеры:
> `<INSTANCE>` (хост инстанса), `<EMAIL>`/`<PASSWORD>` (тест-аккаунт), `<COMPOSE_ID>`
> (Dokploy compose), `<EMULATOR>` (`adb -s` серийник эмулятора).

## 0. Где что искать

- **Тест-инстанс:** self-hosted Plane в Dokploy (compose `<COMPOSE_ID>`), api-контейнер
  `…-api-1` (ID меняется при каждом redeploy — переспрашивать через
  `docker-getContainersByAppNameMatch`).
- **Тест-юзер:** `<EMAIL>` / `<PASSWORD>` — пароль-вход включён
  (`is_email_password_enabled=true`, magic-login выключен).
- **Эмулятор:** rooted AVD, `<EMULATOR>` (обычно `emulator-5554`).
  **Реальное устройство:** для патч-APK под Charles (arm64-v8a).
- Логи деплоя: `compose-readLogs` (tail+since, БЕЗ `search` — он даёт 500).
  Инструментация в коде: `[MOBILE-AUTH]` (auth) и `[MOBILE-GQL]` (graphql: op+vars+query+errors),
  печатается в stderr (logger «plane» в деплое заглушён). **Перед портом на прод
  инструментацию и `csrf_exempt` снять.**

## 1. Автономный вход в приложение (КЛЮЧЕВОЙ метод)

Приложение НЕ honor-ит прокси для своего трафика → mitm/Charles ловят только
Sentry/PostHog. Вход — через **curl-генерацию deep-link токена + инжект**:

```bash
H=https://<INSTANCE>; JAR=/tmp/j.txt; rm -f $JAR
CSRF=$(curl -s -c $JAR $H/auth/get-csrf-token/ | python3 -c "import sys,json;print(json.load(sys.stdin)['csrf_token'])")
curl -s -b $JAR -c $JAR -o /dev/null -X POST $H/auth/sign-in/ \
  -H "X-CSRFToken: $CSRF" -H "Referer: $H/" \
  --data-urlencode "csrfmiddlewaretoken=$CSRF" --data-urlencode "email=<EMAIL>" --data-urlencode "password=<PASSWORD>"
DL=$(curl -s -b $JAR $H/m/auth | grep -oE "app\.plane\.so://auth\?[^'\"]+" | head -1)
# приложение должно БЫТЬ в состоянии ожидания callback: Self hosted sign in → URL → Continue (открыт Custom Tab)
adb -s <EMULATOR> shell "am start -a android.intent.action.VIEW -d '$DL'"   # БЕЗ пакета! (иначе MainActivity, а не CallbackActivity)
```

Грабли входа:

- Инжект срабатывает ТОЛЬКО когда приложение ждёт callback (после Continue открыт
  Custom Tab). Иначе CallbackActivity отрабатывает в пустоту → возврат на URL-экран.
- `am start ... com.plane.so` (с пакетом) запускает MainActivity, не CallbackActivity.
  Указывать БЕЗ пакета — резолвится в `com.linusu.flutter_web_auth_2.CallbackActivity`.
- `access`-токен из deep-link (`token=`) можно дёрнуть для прямых GraphQL-запросов:
  `Authorization: Bearer <token>` на `POST /graphql/` (живёт 15 мин).
- **token из `/m/auth` = опачный access-JWT.** На открытии приложение шлёт его в
  `/auth/mobile/token-check/`. Истёкший токен наш token-check теперь обменивает на
  свежую пару (фикс `decode_token(verify_exp=False)` — иначе 403 → разлогин-цикл).

## 2. Эмулятор — обязательная настройка (иначе всё «не работает»)

- **DNS сломан** у QEMU-резолвера → внешний хост = `unknown host`, приложение висит
  «Connection is limited», вход не доходит. Фикс: запускать с `-dns-server 8.8.8.8,8.8.4.4`.
  (Bind-mount в `/system/etc/hosts` НЕ помогает — netd в другом mount-namespace.)
  ```bash
  emulator -avd <AVD> -gpu host -memory 4096 -cores 4 -dns-server 8.8.8.8,8.8.4.4 &
  ```
- **Captive-portal** ложно метит сеть «limited». Сбрасывается при каждом запуске:
  ```bash
  adb shell settings put global captive_portal_mode 0
  adb shell settings put global captive_portal_detection_enabled 0
  ```
- **GPU:** только `-gpu host` (Metal). `-gpu auto`/`swiftshader` → грей-скрин / ломают
  touch-ввод Flutter.
- **Прокси-захват трафика приложения НЕВОЗМОЖЕН** ни Android-`http_proxy`, ни QEMU
  `-http-proxy`: GraphQL-клиент (Ferry) шлёт прямые `dart:io`-соединения мимо прокси.
  `-http-proxy 10.0.2.2:8080` ещё и неверно (10.0.2.2 — это хост ИЗНУТРИ гостя; для
  `-http-proxy` нужен `127.0.0.1:8080`, т.к. процесс эмулятора на хосте). Для трафика
  приложения — только **frida** (см. §3).
- Эмулятор **рутован** (`adb root` → uid=0), API 33. Hive-хранилище приложения:
  `/data/data/com.plane.so/app_flutter/hydrated_box.hive` (читается `strings`/`python`,
  ground-truth для HydratedCubit-стейтов: AppFeatureCubit.currentSHVersionString и т.п.).

## 3. frida — единственный способ видеть/менять поведение Dart-кода

frida-server кладётся в `/data/local/tmp/frida-server-16` (под frida 16.7.x на хосте).

```bash
adb shell "su 0 sh -c '/data/local/tmp/frida-server-16 &'"
frida -U -f com.plane.so -l hook.js -o out.log --kill-on-exit </dev/null &   # -f = spawn (cold-start), авто-резюм; НЕ --no-pause (убран)
```

Хук Dart-AOT функций по адресам из blutter (паттерн из `blutter_frida.js`):

```js
var libapp = Module.findBaseAddress('libapp.so');
Interceptor.attach(libapp.add(0xADDR), { onEnter:function(){ console.log("hit"); }});  // ADDR = смещение из blutter
```

blutter (github.com/worawit/blutter) декомпилирует `libapp.so`: вывод `asm/` (per-package
.dart с ARM-дизасмом + Dart-символами), `blutter_frida.js` (хелперы getObjectValue/
getInstanceValue/getArg — ЧТЕНИЕ Dart-объектов; ВЫЗОВ Dart-функций из хука НЕ поддержан —
нужен THR/PP-трамплин + дескриптор аргументов, research-level, рискованно роняет приложение).

Ключевые адреса конкретной сборки libapp.so (пере-снять blutter при обновлении APK):

- `fetchInitialStickies` (StickiesCubit) — `0xd0bc8c`
- `_setCurrentWorkspace` (WorkspaceBloc, ставит `WorkspaceRepository.field_f`) — `0x11024d0`

## 4. Баг «стики на cold-start = скелетон» — РАЗОБРАН ДО КОНЦА

**Это клиентская гонка порядка, НЕ data-баг, НЕ наш сервер.** Frida-доказательство:

```
self-hosted:  fetchInitialStickies [T]   → _setCurrentWorkspace [T+4ms]   (стики РАНЬШЕ воркспейса → бейл)
Cloud:        _setCurrentWorkspace [T]    → fetchInitialStickies [T+23ms]  (воркспейс РАНЬШЕ → грузятся)
```

- `_HomeStickiesWidgetState.initState` зовёт `fetchInitialStickies` ОДИН раз на mount.
  `build` = `BlocBuilder<StickiesCubit>`. **Нет листенера на WorkspaceBloc** → не реагирует
  на появление воркспейса (в отличие от прочих home-виджетов, которые рефетчатся через
  `WorkspaceSwitchListener._onWorkspaceUpdated` — но стиков там НЕТ).
- `fetchInitialStickies` бейлит, если `WorkspaceRepository.currentWorkspace == null`.
- Воркспейс ставит `SetCurrentWorkspace` (листенер на success-**переходе** UserDetailCubit).
  На cold-start кубит гидратируется в success без перехода; воркспейс ставит ре-фетч
  userInformation — и это гонка с рендером home.
- **Механизм (по логам cold-start):** `/api/instances/` стоит ВЫШЕ по потоку обоих. После
  него приложение запускает И монтирование home (`fetchInitialStickies`, синхронно), И
  `userInformationAndWorkspacesQuery` (сеть ~27мс → `_setCurrentWorkspace`).
  `fetchInitialStickies` синхронный → стабильно на ~18мс раньше сетевого воркспейса.
  Приложение шлёт `/api/instances/` с UA `Dart/3.9 (dart:io)`. **На null-воркспейсе
  Stickies-запрос НЕ уходит вообще** (бейл) — сервер «дослать» стики не может.
- **`isSelfHosted()` читает сохранённый `server_url_key` в SecureStorage (КЛИЕНТ), не
  `/api/instances/`** → развилку self-hosted/Cloud сервером НЕ переключить. `edition`/
  `is_self_managed` в home/workspace-init НЕ читаются. Версия/feature-гейтинг проверены:
  `current_version="latest"` → currentSHVersionString=latest (ground-truth Hive), все фичи
  вкл — стики ВСЁ РАВНО скелетон → feature-гейтинг ни при чём.

**Серверный рычаг `/api/instances/`-delay — ОПРОВЕРГНУТ (не повторять).** Замедление
`/api/instances/` (+600мс под `Dart/`, проверено: фильтр ловит реальный UA приложения)
НЕ переворачивает порядок — оно сдвигает И home-mount, И userInformation одинаково (оба
ниже по потоку), разрыв 18мс держится. Подтверждено frida + логами + визуалом (стики =
скелетон). Откатано.

**Почему у Cloud порядок обратный — ДОКАЗАНО (frida-интервенция, 2026-06-20).** Причина —
именно `isSelfHosted`-гейт `SelfHostedVersionInfoWrapper`. Доказательство — форс
`isSelfHosted()→false` рантайм-хуком (геттер `0xca4d34` синхронный: `read("server_url_key")
.length!=0`, возвращает bool; `true=null+0x20`, `false=null+0x30`, т.е. в `onLeave`
`retval.replace(retval.add(0x10))`):

```
baseline (isSelfHosted=true):   fetchInitialStickies(+934ms) -> _setCurrentWorkspace(+941ms)  => СКЕЛЕТОН
интервенция (isSelfHosted=false): _setCurrentWorkspace(+411ms) -> fetchInitialStickies(+437ms) => СТИКИ ГРУЗЯТСЯ
```

Форс `false` убирает обёртку-гейт → home монтируется как на Cloud → порядок переворачивается
(воркспейс РАНЬШЕ) → `fetchInitialStickies` читает НЕ-null workspace → стик отрисовался
(визуально: заметка «Тест» вместо скелетона). Приложение НЕ сломалось — base-URL берётся из
`environment` (выставлен при self-host-входе), НЕ из `isSelfHosted()`, поэтому остаётся
self-hosted-контент. **Корень: клиентский `isSelfHosted`-гейт инвертирует порядок монтирования;
`_HomeStickiesWidgetState` без листенера на WorkspaceBloc бейлит на null-workspace.**

**Серверного фикса НЕТ — ЗАКРЫТО (2026-06-20).** Проверены оба возможных рычага:

1. _Формат версии_ — `VersionParseHelper._normalizeVersion` = regex `^v?(\d+\.\d+\.\d+)` →
   `Version.parse`. Префикс `v` ОПЦИОНАЛЕН: `2.2.1` парсится так же, как `v2.2.1`. На инстансе
   уже `current_version="2.2.1"` (валиден!), а стики всё равно скелетон → **версия НЕ причина**.
   (Прежняя гипотеза «нужен `vX.Y.Z`» — опровергнута.)
2. _Снять шиммер в self-hosted-ветке_ — frida-форс `showLoader→false` (`0x10c2fcc`) при
   `isSelfHosted=true` дал СПЛОШНОЙ скелетон страницы, не чистый child → self-hosted-поддерево
   инвертирует структурно, гейт-тайминг ни при чём.

Развилку «self-hosted vs Cloud-ветка» решает `isSelfHosted` (SecureStorage клиента) — сервер не
переключит. **Фиксит только клиент.** Рабочий хирургический патч: на сайте вызова обёртки
`0x10c2e60` (`BL isSelfHosted`) положить `add x0, x22(NULL), #0x30` (=false) → обёртка идёт в
Cloud-ветку, геттер цел (auth жив, без security-разлогина). Проверено на эмуляторе: стики на
cold-start, вход держится. Глобальный NOP геттера (`0xca4d90`) — НЕ годится: ломает token-check
→ «logged out for security». Стики и так появляются на тёплом старте / переключении / pull-refresh.

## 5. GraphQL-шлюз — грабли стабов

`resolver_utils.install_safe_query_defaults` авто-привязывает ЛЮБОЕ непривязанное
Query/Mutation-поле к пустышке (`[]`/`0`/`null`/empty-paginator). → проверка «нет
GraphQL-ошибок + валидный JSON» проходит и для незаявленных стабов. **Аудитить надо
сверкой с БД, не по ошибкам.** Найденные/починенные стабы:
`userFavorites`/`favorites`/`catchUps`/`notificationCount`/`workspaceLabels` (не привязан!),
read-сторона `issueCommentActivities`/`issueLink`/`issueAttachment`/`subIssues`,
`IssuesType.descriptionJson`, `issueStats`. Write-сторона work-item была, read — нет
(можно постить коммент/ссылку, но не прочитать). Non-null ловушки: `UserFavoriteType.
projectDetails: ProjectLiteType!` и `workspace: Int!` — нужны явные field-резолверы,
иначе non-null-краш всего запроса.

## 6. UI-автоматизация (adb) — приёмы

- Координаты кнопок Flutter — через `uiautomator dump /sdcard/u.xml` + grep по тексту
  (Flutter-семантика отдаёт текст НЕ всегда; «self hosted» не нашёлся, «Sign out» — да).
  Центр из `bounds="[x1,y1][x2,y2]"`.
- **Поле URL-инстанса капризное:** серый текст = и плейсхолдер, и введённое значение
  (не отличить). Если приложение помнит инстанс — ввод задваивается. На свежем пустом
  поле ввод работает; Continue серый = поле реально пусто.
- Скриншоты для чтения: `screencap` даёт 1080×2400 (>2000 → Read отклоняет при многих
  картинках). Уменьшать: `sips -Z 1300 in.png --out out.png`. Координаты тапа — в
  device-пикселях (1080×2400), не в уменьшенных.
- Cold-start: `am force-stop com.plane.so; am start -n com.plane.so/.MainActivity`.
- Логаут: avatar (верх-право) → Profile → Sign Out → красный «Sign out» в диалоге.

## 7. Что НЕ работает (потерянное время — не повторять)

- mitm/Charles через любой прокси для трафика приложения (Ferry мимо прокси).
- `-http-proxy 10.0.2.2:8080` (неверный адрес; и всё равно Dart мимо).
- bind-mount `/system/etc/hosts` (mount-namespace netd).
- Изменение `/api/instances/` полей (`is_self_managed`/`edition`/`current_version`) для
  фикса cold-start стиков — гейт клиентский (`isSelfHosted` = сохранённый URL).
- `is_self_managed=false` — ломает self-hosted вход (приложение уходит в Cloud-флоу).
