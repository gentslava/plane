# Architecture Decision Records

Журнал архитектурных решений форка (ветка `plus`). Каждая запись — контекст, решение, последствия.

---

## ADR-001: Workflow approval — собственный guard-слой управляемых переходов

- **Дата:** 2026-06-25
- **Статус:** принято, реализовано (backend)
- **Ветка:** `feature/workflow-approval`
- **Спека:** `docs/superpowers/specs/2026-06-25-workflow-approval-design.md`
- **План:** `docs/superpowers/plans/2026-06-25-workflow-approval-backend.md`

### Контекст

Нужны управляемые переходы статусов work item: админ проекта задаёт граф разрешённых переходов и список людей, кому переход разрешён; недозволенный переход блокируется. В upstream Plane это EE-проприетарная фича. Форк `shbvn` (Shinhan Bank) реализовал упрощённый аналог в open-core, но переплёл его с банковской спецификой (departments/staff) и чужой цепочкой миграций. Брать идею, реализацию писать заново и чисто.

### Решения

1. **Не cherry-pick shbvn и не тянуть EE — собственная чистая реализация.** Имена таблиц совпадают с терминологией EE Plane (`project_workflows`, `workflow_transitions`, ...) — осознанный риск коллизии при будущем подтягивании EE (см. Последствия).

2. **Семантика: «управляемые переходы» сейчас, задел под «полное согласование» — в архитектуре, не в схеме.** Guard возвращает `Decision(action=ALLOW|BLOCK|NEEDS_APPROVAL)`; `NEEDS_APPROVAL` зарезервирован, пока недостижим. Будущая очередь заявок появится отдельной миграцией (`WorkflowApprovalRequest`) без изменения существующих моделей. Никаких мёртвых колонок (`requires_approval` и т.п.) сейчас не вводим.

3. **Единый guard-слой, а не разрозненные проверки во вьюхах.** Пакет `apps/api/plane/workflow/`: чистое ядро `guard.py` (без HTTP/DRF, возвращает `Decision`), координатор `enforcement.py` (`enforce_creation`/`enforce_transition`, маппит `Decision` в DRF-исключение `WorkflowBlocked`), исключение `exceptions.py` (единый контракт ошибок: 403 `WORKFLOW_TRANSITION_BLOCKED` / 400 `WORKFLOW_CREATION_BLOCKED`). Координатор — точка роста под будущие правила (например Field Permissions встанет туда же).

4. **Железобетонный enforcement на всех ДОСТИЖИМЫХ серверных путях записи статуса** + `pre_save`-страховка (defense-in-depth) + регресс-тест охвата. Покрыто: `IssueViewSet.create`/`partial_update` (app), `create_draft_to_issue` (draft→issue), публичный REST API `post`/`patch`, **GraphQL-шлюз форка** (мобильное приложение, ariadne): `createIssueV2`/`updateIssueV2`/`createEpic`/`updateEpic` — через тонкий `graphql/workflow.py` (`guard_creation`/`guard_transition`, `raise GraphQLError`, т.к. в GraphQL DRF-исключение неуместно). Создание без явного статуса резолвит дефолтный статус проекта (нельзя обойти запрет, не прислав `state`). `pre_save`-сигнал на `Issue` берёт actor из явного `workflow_actor_context` ИЛИ из `crum.get_current_user()` (активен на всех HTTP issue-saves), эффективен только при включённом workflow → ловит забытые/будущие точки. **Важно (урок финального ревью):** GraphQL-шлюз — это отдельная от REST точка записи статуса; межзадачный holistic-review для того и нужен, чтобы её не упустить.

5. **Осознанные исключения** (зафиксированы регресс-тестом `test_workflow_coverage.py`): bulk-эндпоинты и sub_issue статус не пишут; публичный `put` (upsert) не маршрутизирован ни в одном URL → мёртвый недостижимый код, не активируем; intake создаёт в triage как служебный поток.

6. **Решения из код-ревью, влияющие на архитектуру:**
   - Уникальность конфиг-моделей — soft-delete-aware (`UniqueConstraint` с `condition=deleted_at IS NULL`), а не `unique_together`, чтобы soft-deleted строка не блокировала пересоздание.
   - Аудит-актор берётся из унаследованного `created_by` (`AuditModel`), отдельное поле `actor` не вводим (DRY). Внимание: `BaseModel.save()` берёт `created_by` из `crum.get_current_user()`, поэтому в сервисах ставить через `save(created_by_id=...)`.
   - Конфиг-API валидирует, что `state`/`transition_state` принадлежат текущему проекту (защита от cross-project FK).
   - Миграция продолжает форк-серию `iw_*` (`iw_005_workflow_models`), а не числовую нумерацию (см. конвенцию форка).

### Последствия

- **Плюс:** функционал, которого нет в open-core Plane; чистый переиспользуемый policy-слой; полнота охвата (REST + GraphQL) держится регресс-тестом + crum-активной сигнальной страховкой; полный backend-набор тестов зелёный.
- **Минус/риск:** имена таблиц/моделей совпадают с EE-терминологией Plane — при будущем подтягивании EE-Workflows возможны коллизии имён; разрешать вручную в момент мерджа.
- **Открыто:**
  - frontend (настройки UI, блокировка Kanban, модалка на 403) — отдельный план/цикл.
  - Полное согласование (очередь заявок) — будущая фича на готовом заделе `NEEDS_APPROVAL`.
  - **Аудит конфигурации:** модель `WorkflowActivity` готова, но запись активности в конфиг-эндпоинтах (toggle/transition/approver) пока НЕ реализована — следующий цикл. Текущий статус: «модель готова, writers — позже».
