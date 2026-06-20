# Архитектура: auth-флоу (web + mobile, Cloud + self-hosted)

> Контекст для AI/разработчика: точный контракт авторизации приложения и веба,
> реверснутый по живому Cloud + декомпилю. Связано:
> `overlay/features/mobile-auth/API-CONTRACT.md`,
> [02-mobile-app](02-mobile-app.md), [03-graphql-gateway](03-graphql-gateway.md).
> Плейсхолдеры вместо реальных доменов/токенов; координаты тест-стенда — вне репо.

## Хосты Plane Cloud (важно — где что)

- `api.plane.so` — **API** (Django apiserver): `/auth/*`, `/api/*`, `/graphql/`,
  `/m/auth`, `/auth/mobile/*`. Сюда бьёт приложение.
- `app.plane.so` — **веб-фронт** (Next.js/React Router SPA). НЕ API: `/auth/get-csrf-token/`
  на нём отдаёт HTML, `/auth/magic-generate/` → 405. Часто путают — бить надо в `api.plane.so`.
- `pi.plane.so` (AI), `live.plane.so` (collab), `cover-images.plane.so`, `silo`/`payment`/
  `prime`/`feature-flag` `.plane.so` — Cloud-only сервисы (приложение их в HostConfig НЕ читает).

## Веб-флоу (magic-link, в браузере/Custom Tab)

```
GET  /auth/get-csrf-token/                 -> {"csrf_token":"..."}  (+cookie .plane.so)
POST /auth/magic-generate/  (csrf+email)   -> {"key":"magic_<email>"}  (код на почту)
POST /auth/magic-sign-in/   (csrf+email+code) -> 302, set-cookie session-id (.plane.so), вход в ВЕБ
```

Имя session-куки — **`session-id`** (не `sessionid`!). Эта сессия работает для REST
(`GET /api/users/me/` → 200), но **НЕ для `/graphql/`** (там нужен Bearer-JWT).
`magic-sign-in` хочет именно **email+code** (не key); ошибка `5085 MAGIC_SIGN_IN_EMAIL_CODE_REQUIRED`
= не передан email. Пароль на sign-up должен быть СИЛЬНЫМ (`5021 PASSWORD_TOO_WEAK`).

## Мобильный флоу (то, что делает приложение)

Мобильные эндпоинты — под `/auth/mobile/*` (НЕ веб `/auth/*`). Из бандла `mobile-*.js`
веб-страницы `/m/auth`:

```
POST /auth/mobile/magic-generate/  ({email})                 -> код на почту
POST /auth/mobile/magic-sign-in/   (form: csrf+email+code)   -> 302 Location: {web}/m/auth/?token=<opaque-64>
```

`token=<64 символа>` — это **НЕ JWT**, а одноразовый session-token. Веб-страница `/m/auth`
(SPA) просто пробрасывает его в deep-link приложения: `app.plane.so://?token=<64char>`.
Приложение ловит deeplink и **обменивает** 64-символьный токен на пару JWT:

```
POST /auth/mobile/token-check/  ({"token":"<64char>"})  -> {"access_token":"<JWT>","refresh_token":"<JWT>"}
```

`token-check` — он же `fetchAuthTokens` в декомпиле
(`auth_remote_data_provider_impl.dart`, POST с body `{"token": <arg>}`). Это И начальный
обмен, И проверка/рефреш на открытии приложения. **Наш фикс:** token-check толерантен к
ИСТЁКШЕМУ access (`decode_token(verify_exp=False)`) — обменивает на свежую пару, иначе
403 → разлогин-цикл после простоя.

`/auth/mobile/session-token/` (`fetchSessionToken`, POST) — отдельный эндпоинт
(`{session_id, session_name}`), требует уже валидной аутентификации; в обмене 64char→JWT
НЕ участвует (его пробовали — тупик). Итоговый минт JWT = `token-check`.

### Получить Cloud-токен скриптом (для кросс-чека шлюза)

```bash
H=https://api.plane.so; JAR=/tmp/j; rm -f $JAR
CSRF=$(curl -s -c $JAR $H/auth/get-csrf-token/ | jq -r .csrf_token)
curl -s -b $JAR -c $JAR -X POST $H/auth/mobile/magic-generate/ -H "X-CSRFToken: $CSRF" \
  -H "Content-Type: application/json" -H "Referer: https://app.plane.so/" -d '{"email":"<EMAIL>"}'
# <пользователь читает код с почты>
curl -s -b $JAR -c $JAR -X POST $H/auth/mobile/magic-sign-in/ -H "X-CSRFToken: $CSRF" \
  --data-urlencode "csrfmiddlewaretoken=$CSRF" --data-urlencode "email=<EMAIL>" --data-urlencode "code=<CODE>" \
  -D /tmp/hdr -o /dev/null    # 302 Location: .../m/auth/?token=<64char>
ST=$(grep -oiE "token=[A-Za-z0-9._-]+" /tmp/hdr | head -1 | cut -d= -f2)
curl -s -X POST $H/auth/mobile/token-check/ -H "Content-Type: application/json" -d "{\"token\":\"$ST\"}"
#  -> {"access_token":"<JWT>", ...}  -> Bearer для POST api.plane.so/graphql/
```

## Self-hosted флоу (наш) — авто-логин без UI

На self-hosted приложение использует `/m/auth`-мост (наша Фаза 4). Пароль-вход даёт
deeplink с токеном напрямую:

```bash
H=https://<INSTANCE>; JAR=/tmp/j; rm -f $JAR
CSRF=$(curl -s -c $JAR $H/auth/get-csrf-token/ | jq -r .csrf_token)
curl -s -b $JAR -c $JAR -X POST $H/auth/sign-in/ -H "X-CSRFToken: $CSRF" -H "Referer: $H/" \
  --data-urlencode "csrfmiddlewaretoken=$CSRF" --data-urlencode "email=<EMAIL>" --data-urlencode "password=<PASSWORD>"
TOK=$(curl -s -b $JAR "$H/m/auth" | grep -oE "token=[^&\"']+" | head -1 | cut -d= -f2)
#  TOK -> Bearer для POST $H/graphql/ (живёт 15 мин). Инжект в приложение:
adb shell "am start -a android.intent.action.VIEW -d '<deeplink из /m/auth>'"   # БЕЗ пакета
```

`/m/auth` self-hosted возвращает HTML с deeplink
`app.plane.so://auth?token=<JWT>&access_token=...&refresh_token=...`. Наш token-check/
session-token — `apps/api/plane/authentication/mobile/`. TTL: access 15 мин, refresh 90 дней.

## Серверная сторона (наш код)

`apps/api/plane/authentication/mobile/`:

- `jwt.py` — `decode_token(token, expected_type, verify_exp=True)`, `MobileJWTAuthentication`.
- `views.py` — `/m/auth` мост, `/auth/mobile/{token-check,session-token,refresh-token}`.
  `csrf_exempt` — функционален (приложение не шлёт Django-CSRF). `[MOBILE-AUTH]`-лог за
  env `MOBILE_DEBUG_LOG=1` (по умолчанию off в проде).
- Прокси-маршрут `/m/auth` — `apps/proxy/Caddyfile.ce`.

## ⚠️ Deploy-требование: env для авторизации приложения

`/api/instances/` отдаёт `app_base_url` напрямую из `settings.APP_BASE_URL`
(`os.environ.get("APP_BASE_URL")`, `common.py`) — **из env, не из БД**. Приложение читает
`app_base_url` для auth-флоу: если пусто (`None`) → **не авторизуется**. Поэтому в env инстанса
(и в проброс `x-app-env` compose) ОБЯЗАТЕЛЬНЫ:

```
APP_BASE_URL=https://<instance>
SPACE_BASE_URL=https://<instance>/spaces
ADMIN_BASE_URL=https://<instance>/god-mode
APP_VERSION=<vX.Y.Z или X.Y.Z>   # → current_version; иначе дефолт 1.3.1 < min-версии приложения 1.5.0
```

Одного `WEB_URL` НЕ хватает. Грабля прода 2026-06-20: было только `WEB_URL` → `app_base_url=None` →
приложение не входило. (Парсер версии `VersionParseHelper` принимает `vX.Y.Z` И `X.Y.Z` — префикс `v`
опционален; не-семвер `latest` → null.)
