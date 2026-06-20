# Статус реализации мобильного GraphQL (handoff)

> Снимок состояния для продолжения работы (в т.ч. новой AI-сессией после сжатия контекста).
> Архитектура — `architecture/`, решения — `adr/DECISIONS.md`.

## Готово и на проде (`plane.gentslava.ru`)

- Мобильный auth (`/m/auth` + `/auth/mobile/*` + JWT), толерантный token-check.
- GraphQL-**ядро**: стартовый/home/board-флоу, work-item CRUD, комменты/ссылки/аттачменты,
  favorites/recent/catchUps/notificationCount, stickies.
- Data-parity фиксы (catchUps enum-краш, timezone-формат, logoProps, isEpic, notifications-инбокс,
  notificationCount.mentioned, isFavorite). Живой Cloud-кросс-чек выполнен (PARITY-AUDIT.md).
- god-mode/instance-config сверка — чисто.
- Инструментация за `MOBILE_DEBUG_LOG`. История `plus` сжата (5 коммитов).

## Готово, задеплоено на ТЕСТ, ждёт прода

- **Все непривязанные области** (`areas/*.py`): 119 query + 102 mutation (epics, initiatives*,
  intake, pages-project, pages-workspace, collections, assets, cycles-modules, issue-extras,
  invites-misc). `*initiatives` — стабы (EE, нет модели). Схема собирается локально (220 типов);
  smoke 17/17 OK на тесте.
- **Фикс `NotificationType.isIntakeIssue/isEpic/data`** (коммит `c8b6db1abb`) — инбокс
  уведомлений в приложении падал на non-null. CI собирал на момент паузы; нужен redeploy теста
  - проверка, что инбокс грузится.

## Готово — исчерпывающая проверка корпуса (коммит-guard)

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

## Осталось

1. ⏭️ Деплой теста → **пошариться в эмуляторе по экранам** (включая раздел @-упоминаний/уведомлений).
2. ⏭️ Редеплой прода (после явного подтверждения).

**Альтернатива для рантайм-диагностики:** `MOBILE_DEBUG_LOG=1` на тесте + навигация в эмуляторе →
`[MOBILE-GQL] errors=[...]` в логах.

## Незакрытое / известные ограничения

- `initiatives` — стабы (нет EE-модели). При появлении модели — заменить тела на реальные querysets.
- Стики cold-start — клиентский баг (ADR-0003), сервером не чинить.
- Полнота: smoke покрыл 17 представительных полей; нужен полный прогон (шаг «в полёте»).
- guard-тест `test_graphql_mobile_parity.py` не гоняется в CI (нет тест-воркфлоу) — опционально добавить.
