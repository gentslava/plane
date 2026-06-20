# Статус реализации мобильного GraphQL (handoff)

> Снимок состояния для продолжения работы (в т.ч. новой AI-сессией после сжатия контекста).
> Архитектура — `architecture/`, решения — `adr/DECISIONS.md`.

## Готово и на проде (`plane.gentslava.ru`, `:plus` = `b60d5b3`, 2026-06-20)

- Мобильный auth (`/m/auth` + `/auth/mobile/*` + JWT), толерантный token-check.
- **Deploy-конфиг авторизации** — env `APP_BASE_URL`/`SPACE_BASE_URL`/`ADMIN_BASE_URL`/`APP_VERSION`
  (+ проброс в `x-app-env`). Без них `app_base_url=None` → приложение НЕ входит. См. `architecture/04-auth-flows.md`.
- GraphQL-**ядро** + **все области** (`areas/*.py`, 119 query + 102 mutation: epics, intake, pages,
  collections, assets, cycles-modules, issue-extras, invites-misc; `initiatives` — EE-стабы).
- Data-parity фиксы (catchUps enum-краш, timezone, logoProps, isEpic, notifications-инбокс,
  notificationCount.mentioned, isFavorite). Non-null фиксы (`NotificationType.*`, `workspaceWorkItemMention`).
- **Security:** enforce workspace-membership в шлюзе (`_member_project`, закрыт cross-tenant IDOR) +
  влиты upstream-фиксы bulk-эндпоинтов (#9269/#9270). См. `architecture/03-graphql-gateway.md` §Авторизация.
- Синхронизация с `upstream/preview` (5 коммитов) влита; история `plus` собрана (6 коммитов + merge).
- Инструментация за `MOBILE_DEBUG_LOG` (off в проде).

## Регресс-тесты (коммит-guard'ы)

- `tests/contract/app/test_graphql_app_corpus.py` — сеет по строке каждого типа, исполняет все 105
  query приложения + валидирует 188 операций. См. ниже.
- `tests/contract/app/test_graphql_authz.py` — не-член заблокирован на `_member_project` и на мутации.
- `tests/contract/app/test_graphql_mobile_parity.py` — стартовый контракт (catchUps enum, timezone).

## Детали corpus-guard

**Регресс-тест** `apps/api/plane/tests/contract/app/test_graphql_app_corpus.py` сеет по строке
каждого типа и:

- **Pass 1** — исполняет все **105 query** приложения через `graphql_sync` → **чисто**, ни одной
  non-null/exception-ошибки.
- **Pass 2** — статически валидирует все **188 операций** (query+mutation) против SDL → **0 реальных
  контрактных дыр**. Единственные «Cannot query field» — legacy `createIssue`/`updateIssue` (V1),
  которых нет и в Cloud SDL; остальное — обрезка дампа (корневые поля проверены: есть в нашем SDL и Cloud).

Нашло **1 реальный баг**: `workspaceWorkItemMention` был непривязан → null на non-null root →
краш `WorkspaceWorkItemMentionQuery` (@-упоминания work-item). Реализован в `areas/issue_extras.py`
(populated-dict + smart-fallback, как `issueShortenedMetaInfo`).

Локальный гейт: `docker plane-pg` (порт 5433, мигрирован) + `/tmp/planenv`. Схема — 220 типов, OK.

## Незакрытое / известные ограничения

- **Стики cold-start** — клиентский баг (ADR-0003): `isSelfHosted`-гейт обёртки инвертирует порядок
  монтирования, доказано frida-интервенцией (`mobile-graphql/APP-PLAYBOOK.md` §4). **Сервером не
  чинится** (версия не причина — `v` опционален; развилку решает client-side `isSelfHosted`).
  Клиентский патч обёртки работает, но требует переподписи APK (ломает Play-обновления/App-Check) →
  принято как известное ограничение. Стики появляются на тёплом старте / переключении / pull-refresh.
- `initiatives` — стабы (нет EE-модели). При появлении модели — заменить тела на реальные querysets.
- guard-тесты не гоняются в CI (нет тест-воркфлоу) — опционально добавить.

## Синхронизация с upstream

Процедура — `SYNC.md`. Последующие синки `upstream/preview → plus` идут обычным `git merge`
(preview уже предок plus). После merge — сверять, не нужно ли поддержать апстрим-изменения в шлюзе
(обычно нет: шлюз scope'ит по project+membership). Зеркала `master`/`preview` = `upstream/*`.
