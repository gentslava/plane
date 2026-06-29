# Workflow Approval (управляемые переходы статусов) — дизайн

- **Статус:** одобрен на брейншторме, готов к плану реализации
- **Дата:** 2026-06-25
- **Ветка:** `feature/workflow-approval`
- **Происхождение идеи:** форк `shbvn` (Shinhan Bank), фича «workflow approval». Берём идею, реализацию переписываем заново и чисто.

## 1. Проблема и мотивация

В Plane (open-core) нет управления переходами между статусами work item. Любой участник с правом редактирования может перевести задачу в любой статус. Управление переходами (guarded workflow) есть только в проприетарной EE-версии Plane.

Форк `shbvn` реализовал в open-core упрощённый аналог. Мы хотим эту возможность в нашем дистрибутиве `plus`, но не cherry-pick'ом (его реализация переплетена с банковской спецификой и чужой цепочкой миграций), а собственной чистой реализацией.

## 2. Семантика (ключевые решения брейншторма)

- **Что делаем сейчас:** «управляемые переходы». Админ проекта задаёт граф разрешённых переходов статусов и список людей, кому конкретный переход разрешён. Недозволенный переход блокируется сервером сразу.
- **Задел на будущее:** архитектура должна позволить добавить «полное согласование» (заявка → ожидание → решение апрувера) позже, **без переписывания**. Задел делаем в архитектуре guard, а не в схеме БД (никаких мёртвых колонок сейчас).
- **Кто выполняет переход:** конкретные люди (участники проекта). Пустой список на переходе = переход разрешён любому участнику. (Роли — сознательно вне scope.)
- **Строгость:** железобетонный серверный enforcement на всех путях записи статуса (app API, публичный REST API, bulk). Обойти нельзя. Фронт даёт превентивный UX поверх, но не является единственной защитой.

## 3. Цели и не-цели

**Цели:**

- Per-project конфигурация: мастер-тумблер, разрешённые переходы, исполнители на переход, запрет создания issue в выбранных статусах.
- Единый серверный guard, перехватывающий все точки записи статуса.
- Структурированный контракт ошибок (стабильные `error_code`).
- Превентивная блокировка на Kanban + реактивная модалка на 403.
- Аудит изменений конфигурации workflow.

**Не-цели (YAGNI для первой версии):**

- Полное согласование с заявками и очередью (только архитектурный задел).
- Роли как исполнители переходов (только конкретные люди).
- Визуальный canvas-редактор графа переходов (карточный список).
- Workspace-уровень, шаблоны workflow, массовое редактирование.

## 4. Модель данных

Пакет моделей в `apps/api/plane/db/models/workflow.py`, регистрация в `db/models/__init__.py`. Базовый класс — `ProjectBaseModel` (как принято в Plane).

| Модель                       | db_table                        | Назначение                                                                                                                                                                                                                                                                        |
| ---------------------------- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ProjectWorkflow`            | `project_workflows`             | Мастер-тумблер на проект: `is_enabled: bool = False`. Одна активная строка на проект (`UniqueConstraint(project, workspace, condition=deleted_at IS NULL)` — soft-delete-aware).                                                                                                  |
| `WorkflowStateConfig`        | `workflow_state_configs`        | Per-state: `allow_issue_creation: bool = True`. `UniqueConstraint(project, state, condition=deleted_at IS NULL)` — soft-delete-aware.                                                                                                                                             |
| `WorkflowTransition`         | `workflow_transitions`          | Разрешённый переход `state → transition_state`. Уникальность активного: `UniqueConstraint(project, state, transition_state, condition=deleted_at IS NULL)`. Нет записи = переход запрещён.                                                                                        |
| `WorkflowTransitionApprover` | `workflow_transition_approvers` | Конкретный человек, кому разрешён переход. FK на `transition` и `User`. Уникальность активного: `(transition, approver, deleted_at IS NULL)`. Пусто на переходе = любой участник.                                                                                                 |
| `WorkflowActivity`           | `workflow_activities`           | Аудит изменений **конфигурации** workflow: `field`, `old_value`, `new_value`. Актор берётся из унаследованного `created_by` (`AuditModel`), отдельное поле `actor` не вводим (DRY). Отдельная таблица — это про настройки, а не про issue, поэтому не смешиваем с issue-activity. |

**Отличия от shbvn:** `is_live` → `is_enabled`; запросы в guard оптимизированы (см. §5). `db_table` оставлены идентичными shbvn — безопасно и упрощает возможную сверку.

**Задел под согласование — в архитектуре, не в схеме.** Сейчас НЕ добавляем `requires_approval` и таблицу заявок. Когда понадобится — отдельной миграцией появится `WorkflowApprovalRequest`, а guard научится возвращать `NEEDS_APPROVAL` (см. §5). Существующие модели при этом не меняются.

## 5. Guard-слой и поток данных

Новый пакет `apps/api/plane/workflow/` (наравне с `db`/`app`/`api`, не в общий `utils`).

### 5.1 `workflow/guard.py` — ядро решения

Чистые функции без зависимости от HTTP/DRF:

```
evaluate_creation(project_id, state_id, actor) -> Decision
evaluate_transition(project_id, from_state_id, to_state_id, actor) -> Decision
```

`Decision` — dataclass: `action: ALLOW | BLOCK | NEEDS_APPROVAL`, `code: str`, `message: str`, `context: dict` (allowed_reviewers, from_state, to_state). Единственный источник правды о решении.

**Логика перехода:** если `ProjectWorkflow.is_enabled is False` → ранний `ALLOW` (без лишних запросов). Иначе найти `WorkflowTransition(from, to)` с `prefetch_related("approvers")` одним запросом: нет записи → `BLOCK`; approvers пусто → `ALLOW`; actor в approvers → `ALLOW`; иначе → `BLOCK` с `allowed_reviewers`.

**Логика создания:** `is_enabled is False` → `ALLOW`. Иначе `WorkflowStateConfig(project, state)`: нет записи → `ALLOW` (дефолт); `allow_issue_creation is False` → `BLOCK`.

**Оптимизация против shbvn:** один проход с `prefetch_related` вместо 3–4 отдельных запросов; ранний выход на выключенном workflow.

**Задел:** `NEEDS_APPROVAL` зарезервирован в enum, пока недостижим. Будущее согласование вернёт его, и точки enforcement уже умеют его обрабатывать (сейчас — как «не ALLOW»).

### 5.2 `workflow/enforcement.py` — единая точка врезки

Тонкий координатор `enforce_work_item_change(issue, requested_changes, actor)`:

- извлекает old_state/new_state (и факт создания) из `requested_changes`;
- зовёт соответствующий `guard.evaluate_*`;
- маппит `Decision` в DRF-исключение (см. §7) либо пропускает дальше.

Это обещанный общий policy-слой: сейчас зовёт только workflow-guard, **Field Permissions встанет сюда же вторым правилом** (следующий цикл). Без преждевременного generic-фреймворка — один понятный координатор.

### 5.3 Поток (переход)

```
HTTP write → view получает old_state/new_state
          → enforce_work_item_change(...)
          → guard.evaluate_transition(...) → Decision
   ALLOW          → save продолжается
   BLOCK          → 403 со структурой
   NEEDS_APPROVAL → (пока недостижимо)
```

## 6. Точки enforcement

Все серверные пути записи статуса вызывают `enforce_work_item_change`:

- **Создание:** app `IssueViewSet.create`; публичный `api/views/issue.py` (create). `intake.py` (создание в triage) — **исключён**, служебный поток.
- **Переход:** app `IssueViewSet.partial_update`; bulk-update в `app/views/issue/base.py`; `app/views/issue/sub_issue.py`; публичный `api/views/issue.py` (update).
- **Defense-in-depth:** `pre_save`-сигнал на `Issue` — если actor доступен в контексте и переход запрещён, поднимает то же исключение. Решение всегда принимает guard; сигнал лишь страхует от забытых/будущих точек.
- **Регресс-тест охвата:** перечисляет все write-эндпоинты state и прогоняет один недозволенный переход — «зелёный» = ни одной дыры (см. §9).

## 7. Контракт ошибок

Единый маппер `Decision → ответ` во всех точках (никакого разнобоя app vs публичный API):

- Переход запрещён → **HTTP 403**:
  ```json
  {
    "error_code": "WORKFLOW_TRANSITION_BLOCKED",
    "message": "...",
    "from_state": "<uuid>",
    "to_state": "<uuid>",
    "allowed_reviewers": ["<user_id>", ...]
  }
  ```
- Создание в запрещённом статусе → **HTTP 400**, `error_code: "WORKFLOW_CREATION_BLOCKED"`.
- Коды ошибок — в общий реестр кодов; фронт и внешние клиенты опираются на стабильный `error_code`, а не на текст.

## 8. API настроек и Frontend

### 8.1 Backend API конфигурации

В `app/views/workflow.py` + `app/serializers/workflow.py` + `app/urls/workflow.py` (публичный `api/views/issue.py` не трогаем). Права — только project/workspace **admin** (`@allow_permission([ROLE.ADMIN])`).

- `workflow/` — get/update тумблера `is_enabled`
- `workflow/state-configs/` — управление `allow_issue_creation` по статусам
- `workflow/transitions/` — CRUD разрешённых переходов
- `workflow/transitions/<id>/approvers/` — управление исполнителями перехода
- `workflow/activity/` — чтение аудита

Approver валидируется как активный `ProjectMember` — нельзя назначить не-участника.

### 8.2 Frontend (MVP)

Слои: `ce/store/workflow.store.ts` (MobX), `ce/services/workflow.service.ts`, `core/hooks/store/use-workflow.ts`, компоненты в `ce/components/.../workflows/`. Структура повторяет shbvn, код пишем заново под наши текущие стор/паттерны (не cherry-pick).

1. **Настройки** — страница в Settings проекта: тумблер включения; на каждый статус — карточка с разрешёнными исходящими переходами и пикером людей (штатный member-picker Plane). Граф — карточный список «статус → [цели] → [кто может]», без кастомного canvas.
2. **Превентивный UX** — на Kanban запрещённые целевые колонки для drag-n-drop блокируются заранее (fail-closed: пока конфиг не загружен — не пускаем).
3. **Реактивный фолбэк** — на 403 ловим структурированную ошибку и показываем модалку «переход запрещён, разрешённые исполнители: …».

## 9. Тестирование

- **Unit `guard.evaluate_*`** (чистые функции): workflow выключен; переход в whitelist; нет записи (блок); approvers пусто (любой); actor в списке / не в списке; создание разрешено/запрещено.
- **API-тесты** на каждую точку записи (app create/update/bulk/sub_issue; публичный API create/update): недозволенный переход = 403 с правильной структурой.
- **Регресс-тест охвата**: все write-эндпоинты state прогоняются одним недозволенным переходом.
- **Тест сигнальной страховки**: прямой `issue.save()` с запрещённым переходом тоже блокируется.

## 10. Миграции и именование

- **Одна** чистая миграция `workflow_models` со всеми 5 таблицами, номер — следующий свободный в `plus` (вершина сейчас `0121` → ожидаемо `0122_workflow_models`; точный номер фиксируем при реализации).
- Номера и merge-узлы shbvn (`0133` и т.д.) **не тащим**.
- Фича разрабатывается в ветке `feature/workflow-approval` → squash-merge в `plus` (наша стратегия веток).

## 11. Риски и грабли

- **Коллизия с EE-Workflows Plane.** Имена `ProjectWorkflow/WorkflowTransition/...` повторяют терминологию проприетарной EE-фичи. Если в будущем подтянем EE — возможны коллизии имён таблиц/моделей. Принимаем осознанно (как и shbvn); фиксируем в ADR.
- **Полнота покрытия точек записи.** Главный риск железобетонного enforcement — забытая точка. Закрываем регресс-тестом охвата + `pre_save`-страховкой.
- **`actor` в сигнале.** `pre_save` не всегда имеет request.user. Сигнал — только страховка: если actor недоступен, основную защиту даёт явный вызов во вью. Не полагаемся на thread-local как на единственный механизм.

## 12. Документация

Короткий ADR («почему свой guard-слой, а не EE-Workflows и не cherry-pick shbvn; почему задел в архитектуре, а не в схеме») в `overlay/docs/architecture/adr/DECISIONS.md` (создать или дополнить).
