<!-- OVERLAY: mobile-auth -->

# Plane Mobile App — Auth API Contract

Реверс-инжиниринг официального приложения **Plane: Projects, Wiki, AI** (`com.plane.so`,
Flutter/Dart). Контракт снят mitm-перехватом реального Cloud-логина (`api.plane.so`) +
анализом self-hosted-трафика. Эталонные ответы — из Plane Cloud.

> Хост API: в Cloud — `api.plane.so`; в self-hosted — тот же домен, что и веб (через Caddy).
> Веб-хост (`{web}`): в Cloud — `app.plane.so`.

## Общий поток входа

1. Приложение открывает **внешний браузер** (flutter_web_auth_2 → Chrome Custom Tab) на
   логин-страницу. callbackUrlScheme — `app.plane.so` (из `AndroidManifest` CallbackActivity
   `com.linusu.flutter_web_auth_2.CallbackActivity`).
2. Пользователь логинится (magic-link / email-password / OAuth) в браузере.
3. Бэкенд редиректит на `{web}/m/auth/?token=<opaque>` → HTML-страница делает deep link
   `app.plane.so://...?token=<opaque>` → ОС возвращает в приложение.
4. Приложение (Dart) **обменивает опачный токен на JWT** и создаёт веб-сессию.

## Эндпоинты

### Magic-link (в браузере)

| Метод/путь                          | Запрос                                                                      | Ответ                                                 |
| ----------------------------------- | --------------------------------------------------------------------------- | ----------------------------------------------------- |
| `POST /auth/mobile/email-check/`    | `{"email":"..."}`                                                           | `{"existing":true,"status":"MAGIC_CODE"}`             |
| `GET /auth/get-csrf-token/`         | —                                                                           | `{"csrf_token":"..."}`                                |
| `POST /auth/mobile/magic-generate/` | `{"email":"..."}` (X-CSRFToken)                                             | `{"key":"magic_<email>"}` (код на почту)              |
| `POST /auth/mobile/magic-sign-in/`  | form: `csrfmiddlewaretoken=&email=&invitation_id=&code=` (Referer: `{web}`) | **302** → `Location: {web}/m/auth/?token=<opaque-64>` |

### Мост deep-link

| `GET {web}/m/auth/?token=<opaque>` | — | **200** HTML, редиректит в `app.plane.so://...?token=<opaque>` |

### Обмен токена (приложение, UA `Dart/3.9`)

| Метод/путь                         | Запрос                                                | Ответ (эталон Cloud)                                                                                                                                                                 |
| ---------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POST /auth/mobile/token-check/`   | `{"token":"<opaque>"}`                                | **200** `{"access_token":"<JWT>","refresh_token":"<JWT>"}` · invalid → **403** `{"error":"Invalid token"}` · missing → **400** `{"error":"Token is required"}`                       |
| `POST /auth/mobile/session-token/` | header `Authorization: Bearer <access>` (тело пустое) | **200** `{"session_name":"session-id","session_id":"<django-session-key>"}` · invalid → **401** `{"detail":"Given token not valid for any token type","code":"token_not_valid",...}` |
| `POST /auth/mobile/refresh-token/` | `{"refresh_token":"<JWT>"}`                           | **200** `{"access_token":"<JWT>","refresh_token":"<JWT>"}`                                                                                                                           |
| `POST /auth/mobile/sign-out/`      | —                                                     | **200** `{"message":"User sign out successfully"}`                                                                                                                                   |

> ⚠️ Ключевой нюанс: `session-token` возвращает **`{session_name, session_id}`** (ключ веб-сессии),
> а НЕ токены. Приложение парсит `session_id` для cookie-доступа (WebView). Если его нет —
> приложение падает с «Sorry, we are unable to get your info».

## Формат JWT (djangorestframework-simplejwt, HS256 / SECRET_KEY)

```json
// access — claims (TTL 15 минут):
{"token_type":"access","exp":<unix>,"iat":<unix>,"jti":"<hex32>","user_id":"<uuid>"}
// refresh — TTL 90 дней:
{"token_type":"refresh","exp":<unix>,"iat":<unix>,"jti":"<hex32>","user_id":"<uuid>"}
```

## Версионный гейт

Приложение читает `GET /api/instances/` → `instance.current_version` и требует **≥ v1.12.0**,
иначе экран «Update self hosted version to continue». (Self-hosted: задаётся env `APP_VERSION`,
который `register_instance` пишет в `Instance.current_version`.)

## User / Profile (веб-SPA, `api.plane.so`)

Стандартные Plane-шейпы: `GET /api/users/me/`, `/api/users/me/profile/`,
`/api/users/me/settings/`. Профиль содержит мобильные поля: `is_mobile_onboarded`,
`mobile_onboarding_step`, `mobile_product_tour`, `mobile_timezone_auto_set`.

## Полный инвентарь эндпоинтов (из `libapp.so`)

> Нативный Dart-трафик приложения **обходит глобальный http_proxy** (ловится только веб-логин в
> Custom Tab). Поэтому динамические пути (`/api/workspaces/<slug>/projects/...`) сняты не runtime-захватом,
> а извлечены из бинарника + это **стандартный Plane API** (есть в нашем self-hosted коде). Для runtime-захвата
> нативного API нужен прозрачный перехват (VPN-tap типа PCAPdroid, или iptables-redirect на руте).

**Auth** (`api`-хост): `/auth/get-csrf-token/`, `/auth/mobile/{email-check,magic-generate,magic-sign-in,token-check,session-token,refresh-token,sign-out}/`.

**AI / Chat / Pages** (хост `pi.plane.so`, `/api/v1/mobile/...`):

- chat: `start/auth-check/`, `start/set-prompts/`, `stream-answer/`, `get-models/`, `get-templates/`,
  `get-user-threads/`, `get-chat-history-object/`, `favorite-chat/`, `unfavorite-chat/`, `get-favorite-chats/`,
  `rename-chat/`, `delete-chat/`, `execute-action/`, `feedback/`.
- pages: `pages/`, `pages/blocks/generate/`, `pages/blocks/types/`, `pages/embeds/`, `pages/summarize/`.
- artifacts: `artifacts/`, `artifacts/chat/`; chat-ctas: `chat-ctas/save-as-page/`.
- attachments: `attachments/upload-attachment/`, `attachments/complete-upload/`; `transcription/transcribe`;
  `feedback/ai-block/`.

**Стандартный Plane API** (`api`-хост, динамические пути): `/api/instances/`, `/api/workspaces/`,
`/api/workspaces/file-assets/`, `/api/assets/v2/workspaces/`, `/api/mobile-banners/open`,
`/api/users/me/`, `/api/users/me/{profile,settings,workspaces,workspaces/invitations}/`,
и весь `/api/workspaces/<slug>/{projects,issues,cycles,modules,pages,...}/` (= наш self-hosted Plane API).

**Dart-модели (из путей пакетов):** `auth_token_model`, `auth_token_response_model` (ответ token-check),
`csrf_response_model`, `session_token_model`, `session_token_reponse_model` (sic, ответ session-token).

## Прочее

- Приложение защищено **PAIRIP** (Google Play licensing) — сайдлоад/патч без обхода `LicenseClient.initializeLicenseCheck` приводит к мгновенному закрытию.
- Cloud-домены AI/телеметрии: `pi.plane.so` (AI, `/api/v1/mobile/...`), Sentry, PostHog, Clarity; `accounts.google.com` (OAuth, cert-pinned).
- `apple-itunes-app` app-id `6657986465`.
