# Workflow Approval — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Серверная часть управляемых переходов статусов work item: per-project конфигурация + железобетонный guard на всех путях записи статуса.

**Architecture:** Чистое ядро решения (`workflow/guard.py`) возвращает `Decision`; тонкий координатор (`workflow/enforcement.py`) маппит его в DRF-исключение и вызывается из всех точек записи статуса (app, публичный API, bulk, sub_issue) + `pre_save`-страховка. Конфигурация — отдельные admin-only эндпоинты.

**Tech Stack:** Django + DRF, PostgreSQL, pytest + pytest-django + factory-boy. Питон-пакеты моделей в `apps/api/plane/`.

**Scope:** Только backend. Frontend (настройки UI, Kanban-блокировка, модалка) — отдельный план `2026-06-25-workflow-approval-frontend.md`.

**Источник истины — спека:** `docs/superpowers/specs/2026-06-25-workflow-approval-design.md`.

---

## Файловая структура

| Файл                                                   | Ответственность                                          | Действие                      |
| ------------------------------------------------------ | -------------------------------------------------------- | ----------------------------- |
| `apps/api/plane/db/models/workflow.py`                 | 5 моделей конфигурации workflow                          | Create                        |
| `apps/api/plane/db/models/__init__.py`                 | Реэкспорт моделей                                        | Modify                        |
| `apps/api/plane/db/migrations/0122_workflow_models.py` | Миграция схемы                                           | Create (через makemigrations) |
| `apps/api/plane/workflow/__init__.py`                  | Пакет workflow-логики                                    | Create                        |
| `apps/api/plane/workflow/guard.py`                     | `Decision` + `evaluate_creation` + `evaluate_transition` | Create                        |
| `apps/api/plane/workflow/exceptions.py`                | `WorkflowBlocked` DRF-исключение                         | Create                        |
| `apps/api/plane/workflow/enforcement.py`               | `enforce_work_item_change` координатор                   | Create                        |
| `apps/api/plane/workflow/signals.py`                   | `pre_save`-страховка на `Issue`                          | Create                        |
| `apps/api/plane/db/apps.py`                            | Подключение signals в `ready()`                          | Modify                        |
| `apps/api/plane/app/serializers/workflow.py`           | Сериализаторы конфигурации                               | Create                        |
| `apps/api/plane/app/serializers/__init__.py`           | Реэкспорт сериализаторов                                 | Modify                        |
| `apps/api/plane/app/views/workflow.py`                 | Вьюсеты конфигурации (admin)                             | Create                        |
| `apps/api/plane/app/views/__init__.py`                 | Реэкспорт вьюсетов                                       | Modify                        |
| `apps/api/plane/app/urls/workflow.py`                  | URL конфигурации                                         | Create                        |
| `apps/api/plane/app/urls/__init__.py`                  | Подключение workflow_urls                                | Modify                        |
| `apps/api/plane/app/views/issue/base.py`               | Врезка enforce в create/partial_update/bulk              | Modify                        |
| `apps/api/plane/app/views/issue/sub_issue.py`          | Врезка enforce                                           | Modify                        |
| `apps/api/plane/api/views/issue.py`                    | Врезка enforce (публичный API)                           | Modify                        |
| `apps/api/plane/tests/unit/workflow/`                  | Unit-тесты guard/enforcement                             | Create                        |
| `apps/api/plane/tests/contract/app/test_workflow_*.py` | Contract-тесты enforcement + API                         | Create                        |
| `overlay/docs/architecture/adr/DECISIONS.md`           | ADR                                                      | Create/Modify                 |

**Тестовые соглашения (из `pytest.ini` и `conftest.py`):**

- Маркеры: `@pytest.mark.unit`, `@pytest.mark.contract`, `@pytest.mark.django_db`.
- Готовые фикстуры: `session_client` (аутентифицированный app-клиент), `workspace` (+ владелец как `WorkspaceMember` role=20), `create_user`, `api_key_client` (публичный API).
- Роли: `ROLE.ADMIN=20`, `ROLE.MEMBER=15`, `ROLE.GUEST=5`.
- Запуск из `apps/api/`. БД — PostgreSQL, тесты идут с `--reuse-db --nomigrations` (схема из моделей).
- URL в contract-тестах строятся вручную строкой (см. `test_project_app.py`), `reverse()` ненадёжен из-за дублей имён.

---

## Task 1: Модели workflow

**Files:**

- Create: `apps/api/plane/db/models/workflow.py`
- Modify: `apps/api/plane/db/models/__init__.py`
- Test: `apps/api/plane/tests/unit/models/test_workflow_models.py`

- [ ] **Step 1: Написать падающий тест моделей**

Create `apps/api/plane/tests/unit/models/test_workflow_models.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from django.db.utils import IntegrityError

from plane.db.models import (
    Project,
    ProjectWorkflow,
    State,
    WorkflowStateConfig,
    WorkflowTransition,
    WorkflowTransitionApprover,
)


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(
        name="WF Project",
        identifier="WF",
        workspace=workspace,
        created_by=create_user,
    )


@pytest.fixture
def states(db, project, workspace):
    backlog = State.objects.create(
        name="Backlog", color="#000", group="backlog", project=project, workspace=workspace
    )
    done = State.objects.create(
        name="Done", color="#000", group="completed", project=project, workspace=workspace
    )
    return backlog, done


@pytest.mark.unit
@pytest.mark.django_db
def test_project_workflow_defaults(project, workspace):
    wf = ProjectWorkflow.objects.create(project=project, workspace=workspace)
    assert wf.is_enabled is False


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_unique_active(project, workspace, states):
    backlog, done = states
    WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    with pytest.raises(IntegrityError):
        WorkflowTransition.objects.create(
            project=project, workspace=workspace, state=backlog, transition_state=done
        )


@pytest.mark.unit
@pytest.mark.django_db
def test_state_config_default_allows_creation(project, workspace, states):
    backlog, _ = states
    cfg = WorkflowStateConfig.objects.create(project=project, workspace=workspace, state=backlog)
    assert cfg.allow_issue_creation is True
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd apps/api && python -m pytest plane/tests/unit/models/test_workflow_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'ProjectWorkflow'`.

- [ ] **Step 3: Создать модели**

Create `apps/api/plane/db/models/workflow.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.conf import settings
from django.db import models

from .project import ProjectBaseModel


class ProjectWorkflow(ProjectBaseModel):
    """Master toggle for workflow enforcement on a project."""

    is_enabled = models.BooleanField(default=False)

    class Meta:
        unique_together = [("project", "workspace")]
        db_table = "project_workflows"
        verbose_name = "Project Workflow"
        verbose_name_plural = "Project Workflows"

    def __str__(self):
        return f"ProjectWorkflow({self.project_id}, enabled={self.is_enabled})"


class WorkflowStateConfig(ProjectBaseModel):
    """Per-state configuration: whether new issues can be created in this state."""

    state = models.ForeignKey(
        "db.State", on_delete=models.CASCADE, related_name="workflow_config"
    )
    allow_issue_creation = models.BooleanField(default=True)

    class Meta:
        unique_together = [("project", "state")]
        db_table = "workflow_state_configs"
        verbose_name = "Workflow State Config"
        verbose_name_plural = "Workflow State Configs"

    def __str__(self):
        return f"WorkflowStateConfig(state={self.state_id}, allow={self.allow_issue_creation})"


class WorkflowTransition(ProjectBaseModel):
    """A permitted state transition: issues in `state` can move to `transition_state`."""

    state = models.ForeignKey(
        "db.State", on_delete=models.CASCADE, related_name="outgoing_transitions"
    )
    transition_state = models.ForeignKey(
        "db.State", on_delete=models.CASCADE, related_name="incoming_transitions"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "state", "transition_state"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_workflow_transition_active",
            )
        ]
        db_table = "workflow_transitions"
        verbose_name = "Workflow Transition"
        verbose_name_plural = "Workflow Transitions"

    def __str__(self):
        return f"WorkflowTransition({self.state_id} -> {self.transition_state_id})"


class WorkflowTransitionApprover(ProjectBaseModel):
    """User authorized to perform a specific workflow transition."""

    transition = models.ForeignKey(
        WorkflowTransition, on_delete=models.CASCADE, related_name="approvers"
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workflow_approvals"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["transition", "approver"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_workflow_approver_active",
            )
        ]
        db_table = "workflow_transition_approvers"
        verbose_name = "Workflow Transition Approver"
        verbose_name_plural = "Workflow Transition Approvers"

    def __str__(self):
        return f"WorkflowTransitionApprover(transition={self.transition_id}, approver={self.approver_id})"


class WorkflowActivity(ProjectBaseModel):
    """Audit log for workflow configuration changes."""

    field = models.CharField(max_length=255)
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="workflow_activities",
    )

    class Meta:
        db_table = "workflow_activities"
        ordering = ["-created_at"]
        verbose_name = "Workflow Activity"
        verbose_name_plural = "Workflow Activities"

    def __str__(self):
        return f"WorkflowActivity(field={self.field}, project={self.project_id})"
```

- [ ] **Step 4: Зарегистрировать модели в `__init__.py`**

Modify `apps/api/plane/db/models/__init__.py` — добавить рядом с прочими импортами:

```python
from .workflow import (
    ProjectWorkflow,
    WorkflowStateConfig,
    WorkflowTransition,
    WorkflowTransitionApprover,
    WorkflowActivity,
)
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `cd apps/api && python -m pytest plane/tests/unit/models/test_workflow_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Сгенерировать миграцию**

Run: `cd apps/api && python manage.py makemigrations db --name workflow_models`
Expected: создан файл `plane/db/migrations/0122_workflow_models.py` с 5 таблицами. Проверить: `python manage.py makemigrations --check --dry-run` → "No changes detected".

- [ ] **Step 7: Commit**

```bash
git add apps/api/plane/db/models/workflow.py apps/api/plane/db/models/__init__.py \
        apps/api/plane/db/migrations/0122_workflow_models.py \
        apps/api/plane/tests/unit/models/test_workflow_models.py
git commit -m "feat(workflow): models for guarded state transitions"
```

---

## Task 2: Guard-ядро (Decision + evaluate)

**Files:**

- Create: `apps/api/plane/workflow/__init__.py`, `apps/api/plane/workflow/guard.py`
- Test: `apps/api/plane/tests/unit/workflow/test_guard.py`

- [ ] **Step 1: Написать падающий тест guard**

Create `apps/api/plane/tests/unit/workflow/__init__.py` (пустой файл).

Create `apps/api/plane/tests/unit/workflow/test_guard.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import (
    Project,
    ProjectWorkflow,
    State,
    WorkflowStateConfig,
    WorkflowTransition,
    WorkflowTransitionApprover,
)
from plane.workflow.guard import Action, evaluate_creation, evaluate_transition


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(
        name="WF Project", identifier="WF", workspace=workspace, created_by=create_user
    )


@pytest.fixture
def states(db, project, workspace):
    backlog = State.objects.create(
        name="Backlog", color="#000", group="backlog", project=project, workspace=workspace
    )
    done = State.objects.create(
        name="Done", color="#000", group="completed", project=project, workspace=workspace
    )
    return backlog, done


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_allowed_when_workflow_disabled(project, states, create_user):
    backlog, done = states
    decision = evaluate_transition(project.id, backlog.id, done.id, create_user)
    assert decision.action == Action.ALLOW


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_blocked_when_no_record(project, workspace, states, create_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    decision = evaluate_transition(project.id, backlog.id, done.id, create_user)
    assert decision.action == Action.BLOCK
    assert decision.code == "WORKFLOW_TRANSITION_BLOCKED"


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_allowed_when_whitelisted_no_approvers(project, workspace, states, create_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    decision = evaluate_transition(project.id, backlog.id, done.id, create_user)
    assert decision.action == Action.ALLOW


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_blocked_when_actor_not_approver(project, workspace, states, create_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    transition = WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    other = create_user.__class__.objects.create(email="other@plane.so")
    WorkflowTransitionApprover.objects.create(
        project=project, workspace=workspace, transition=transition, approver=other
    )
    decision = evaluate_transition(project.id, backlog.id, done.id, create_user)
    assert decision.action == Action.BLOCK
    assert str(other.id) in decision.context["allowed_reviewers"]


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_allowed_when_actor_is_approver(project, workspace, states, create_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    transition = WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    WorkflowTransitionApprover.objects.create(
        project=project, workspace=workspace, transition=transition, approver=create_user
    )
    decision = evaluate_transition(project.id, backlog.id, done.id, create_user)
    assert decision.action == Action.ALLOW


@pytest.mark.unit
@pytest.mark.django_db
def test_creation_blocked_when_state_disallows(project, workspace, states, create_user):
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )
    decision = evaluate_creation(project.id, backlog.id, create_user)
    assert decision.action == Action.BLOCK
    assert decision.code == "WORKFLOW_CREATION_BLOCKED"


@pytest.mark.unit
@pytest.mark.django_db
def test_creation_allowed_when_no_config(project, workspace, states, create_user):
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    decision = evaluate_creation(project.id, backlog.id, create_user)
    assert decision.action == Action.ALLOW
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd apps/api && python -m pytest plane/tests/unit/workflow/test_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plane.workflow'`.

- [ ] **Step 3: Создать guard-ядро**

Create `apps/api/plane/workflow/__init__.py` (пустой файл).

Create `apps/api/plane/workflow/guard.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Pure decision logic for guarded workflow transitions. No HTTP/DRF here."""

from dataclasses import dataclass, field
from enum import Enum

from plane.db.models import (
    ProjectWorkflow,
    WorkflowStateConfig,
    WorkflowTransition,
)


class Action(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    NEEDS_APPROVAL = "needs_approval"  # reserved for future approval flow


@dataclass
class Decision:
    action: Action
    code: str = ""
    message: str = ""
    context: dict = field(default_factory=dict)


_ALLOW = Decision(action=Action.ALLOW)


def _workflow_enabled(project_id) -> bool:
    return ProjectWorkflow.objects.filter(project_id=project_id, is_enabled=True).exists()


def evaluate_creation(project_id, state_id, actor) -> Decision:
    """Decide whether a new work item may be created in `state_id`."""
    if not _workflow_enabled(project_id):
        return _ALLOW

    config = WorkflowStateConfig.objects.filter(
        project_id=project_id, state_id=state_id
    ).first()
    if config is None or config.allow_issue_creation:
        return _ALLOW

    return Decision(
        action=Action.BLOCK,
        code="WORKFLOW_CREATION_BLOCKED",
        message="New work items cannot be created in this state due to workflow restrictions.",
        context={"state": str(state_id)},
    )


def evaluate_transition(project_id, from_state_id, to_state_id, actor) -> Decision:
    """Decide whether `actor` may move a work item from `from_state_id` to `to_state_id`."""
    if not _workflow_enabled(project_id):
        return _ALLOW

    transition = (
        WorkflowTransition.objects.filter(
            project_id=project_id,
            state_id=from_state_id,
            transition_state_id=to_state_id,
        )
        .prefetch_related("approvers")
        .first()
    )

    blocked_context = {
        "from_state": str(from_state_id),
        "to_state": str(to_state_id),
    }

    if transition is None:
        return Decision(
            action=Action.BLOCK,
            code="WORKFLOW_TRANSITION_BLOCKED",
            message="This status transition is not allowed by the project workflow.",
            context={**blocked_context, "allowed_reviewers": []},
        )

    approver_ids = [str(a.approver_id) for a in transition.approvers.all()]
    if not approver_ids or str(actor.id) in approver_ids:
        return _ALLOW

    return Decision(
        action=Action.BLOCK,
        code="WORKFLOW_TRANSITION_BLOCKED",
        message="You are not permitted to perform this status transition.",
        context={**blocked_context, "allowed_reviewers": approver_ids},
    )
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `cd apps/api && python -m pytest plane/tests/unit/workflow/test_guard.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/api/plane/workflow/__init__.py apps/api/plane/workflow/guard.py \
        apps/api/plane/tests/unit/workflow/
git commit -m "feat(workflow): pure guard decision core"
```

---

## Task 3: Исключение + координатор enforcement

**Files:**

- Create: `apps/api/plane/workflow/exceptions.py`, `apps/api/plane/workflow/enforcement.py`
- Test: `apps/api/plane/tests/unit/workflow/test_enforcement.py`

- [ ] **Step 1: Написать падающий тест координатора**

Create `apps/api/plane/tests/unit/workflow/test_enforcement.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectWorkflow, State
from plane.workflow.enforcement import enforce_creation, enforce_transition
from plane.workflow.exceptions import WorkflowBlocked


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(
        name="WF Project", identifier="WF", workspace=workspace, created_by=create_user
    )


@pytest.fixture
def states(db, project, workspace):
    backlog = State.objects.create(
        name="Backlog", color="#000", group="backlog", project=project, workspace=workspace
    )
    done = State.objects.create(
        name="Done", color="#000", group="completed", project=project, workspace=workspace
    )
    return backlog, done


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_transition_raises_403(project, workspace, states, create_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    with pytest.raises(WorkflowBlocked) as exc:
        enforce_transition(project.id, backlog.id, done.id, create_user)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["error_code"] == "WORKFLOW_TRANSITION_BLOCKED"


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_transition_noop_when_disabled(project, states, create_user):
    backlog, done = states
    # Must not raise.
    enforce_transition(project.id, backlog.id, done.id, create_user)


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_transition_noop_when_state_unchanged(project, states, create_user):
    backlog, _ = states
    # from == to (no transition) must never be blocked even if workflow on.
    enforce_transition(project.id, backlog.id, backlog.id, create_user)
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd apps/api && python -m pytest plane/tests/unit/workflow/test_enforcement.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plane.workflow.enforcement'`.

- [ ] **Step 3: Создать исключение**

Create `apps/api/plane/workflow/exceptions.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework import status
from rest_framework.exceptions import APIException

from .guard import Decision


class WorkflowBlocked(APIException):
    """Raised when a workflow rule blocks a creation or transition.

    Carries the structured Decision payload as DRF `detail` so every call site
    returns an identical error contract.
    """

    def __init__(self, decision: Decision, http_status: int):
        self.status_code = http_status
        detail = {
            "error_code": decision.code,
            "message": decision.message,
            **decision.context,
        }
        super().__init__(detail=detail)


def block_status_for(decision: Decision) -> int:
    """Map a blocking Decision code to its HTTP status."""
    if decision.code == "WORKFLOW_CREATION_BLOCKED":
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_403_FORBIDDEN
```

- [ ] **Step 4: Создать координатор**

Create `apps/api/plane/workflow/enforcement.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Single choke point that turns guard Decisions into DRF errors.

This is the policy layer: today it only calls the workflow guard, but future
rules (e.g. Field Permissions) plug in here without touching the call sites.
"""

from .exceptions import WorkflowBlocked, block_status_for
from .guard import Action, evaluate_creation, evaluate_transition


def enforce_creation(project_id, state_id, actor) -> None:
    """Raise WorkflowBlocked if a new work item may not be created in `state_id`."""
    if state_id is None:
        return
    decision = evaluate_creation(project_id, state_id, actor)
    if decision.action != Action.ALLOW:
        raise WorkflowBlocked(decision, block_status_for(decision))


def enforce_transition(project_id, from_state_id, to_state_id, actor) -> None:
    """Raise WorkflowBlocked if `actor` may not move from `from_state_id` to `to_state_id`."""
    # No status change → nothing to enforce.
    if to_state_id is None or str(from_state_id) == str(to_state_id):
        return
    decision = evaluate_transition(project_id, from_state_id, to_state_id, actor)
    if decision.action != Action.ALLOW:
        raise WorkflowBlocked(decision, block_status_for(decision))
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `cd apps/api && python -m pytest plane/tests/unit/workflow/test_enforcement.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add apps/api/plane/workflow/exceptions.py apps/api/plane/workflow/enforcement.py \
        apps/api/plane/tests/unit/workflow/test_enforcement.py
git commit -m "feat(workflow): enforcement coordinator and DRF exception"
```

---

## Task 4: Врезка в app `IssueViewSet` (создание + переход)

**Files:**

- Modify: `apps/api/plane/app/views/issue/base.py` (`create` ~418, `partial_update` ~644)
- Test: `apps/api/plane/tests/contract/app/test_workflow_enforcement_app.py`

- [ ] **Step 1: Написать падающий contract-тест**

Create `apps/api/plane/tests/contract/app/test_workflow_enforcement_app.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import (
    Project,
    ProjectMember,
    ProjectWorkflow,
    State,
    Issue,
)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="WF Project", identifier="WF", workspace=workspace, created_by=create_user
    )
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def states(db, project, workspace):
    backlog = State.objects.create(
        name="Backlog", color="#000", group="backlog", project=project, workspace=workspace
    )
    done = State.objects.create(
        name="Done", color="#000", group="completed", project=project, workspace=workspace
    )
    return backlog, done


def issue_url(slug, project_id, pk=None):
    base = f"/api/workspaces/{slug}/projects/{project_id}/issues/"
    return f"{base}{pk}/" if pk else base


@pytest.mark.contract
@pytest.mark.django_db
def test_partial_update_blocks_disallowed_transition(
    session_client, workspace, project, states, create_user
):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="I1", project=project, workspace=workspace, state=backlog, created_by=create_user
    )

    url = issue_url(workspace.slug, project.id, issue.id)
    response = session_client.patch(url, {"state_id": str(done.id)}, format="json")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "WORKFLOW_TRANSITION_BLOCKED"
    issue.refresh_from_db()
    assert issue.state_id == backlog.id  # unchanged


@pytest.mark.contract
@pytest.mark.django_db
def test_partial_update_allows_when_workflow_off(
    session_client, workspace, project, states, create_user
):
    backlog, done = states
    issue = Issue.objects.create(
        name="I2", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = issue_url(workspace.slug, project.id, issue.id)
    response = session_client.patch(url, {"state_id": str(done.id)}, format="json")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    issue.refresh_from_db()
    assert issue.state_id == done.id
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd apps/api && python -m pytest plane/tests/contract/app/test_workflow_enforcement_app.py -v`
Expected: FAIL — первый тест получает `204` вместо `403` (enforcement ещё не врезан).

- [ ] **Step 3: Врезать enforce в `partial_update`**

Modify `apps/api/plane/app/views/issue/base.py`. Добавить импорт в начало файла рядом с прочими `from plane...`:

```python
from plane.workflow.enforcement import enforce_creation, enforce_transition
```

В методе `partial_update` (после получения `issue` и проверки `if not issue:` на строке ~691, перед `current_instance = ...`) вставить:

```python
        # Workflow guard: block disallowed status transitions before any write.
        new_state_id = request.data.get("state_id")
        if new_state_id is not None:
            enforce_transition(project_id, issue.state_id, new_state_id, request.user)
```

- [ ] **Step 4: Врезать enforce в `create`**

В методе `create` (`apps/api/plane/app/views/issue/base.py:418`) — после валидации сериализатора, непосредственно перед `serializer.save()`. Найти блок `if serializer.is_valid():` и первой строкой внутри, до `serializer.save()`, вставить:

```python
            # Workflow guard: block creation in restricted states.
            enforce_creation(project_id, serializer.validated_data.get("state_id")
                             or request.data.get("state_id"), request.user)
```

> Если в `create` статус берётся из `serializer.validated_data["state"]` (объект State, а не id), используй `getattr(serializer.validated_data.get("state"), "id", None)`. Сверься с реальным телом метода при реализации и возьми тот источник state, который там фактически используется.

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `cd apps/api && python -m pytest plane/tests/contract/app/test_workflow_enforcement_app.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Запустить весь workflow-набор — нет регрессий**

Run: `cd apps/api && python -m pytest plane/tests/unit/workflow plane/tests/contract/app/test_workflow_enforcement_app.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/plane/app/views/issue/base.py \
        apps/api/plane/tests/contract/app/test_workflow_enforcement_app.py
git commit -m "feat(workflow): enforce guard in app IssueViewSet create/update"
```

---

## Task 5: Врезка в bulk-update и sub_issue

**Files:**

- Modify: `apps/api/plane/app/views/issue/base.py` (bulk-операция со `state_id`, ~890)
- Modify: `apps/api/plane/app/views/issue/sub_issue.py` (~145)
- Test: `apps/api/plane/tests/contract/app/test_workflow_enforcement_app.py` (дополнить)

- [ ] **Step 1: Дополнить тест bulk/sub_issue**

В конец `test_workflow_enforcement_app.py` добавить (сначала определи реальные URL bulk и sub_issue, найдя их в `apps/api/plane/app/urls/issue.py` по именам вьюх; ниже шаблон для bulk-обновления):

```python
@pytest.mark.contract
@pytest.mark.django_db
def test_bulk_update_blocks_disallowed_transition(
    session_client, workspace, project, states, create_user
):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="B1", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    # URL берётся из apps/api/plane/app/urls/issue.py (имя содержит "bulk").
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/bulk-issue-operation/"
    payload = {"issue_ids": [str(issue.id)], "properties": {"state_id": str(done.id)}}
    response = session_client.post(url, payload, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    issue.refresh_from_db()
    assert issue.state_id == backlog.id
```

> Точные URL/имя поля payload зафиксируй по реальному коду bulk-вьюхи при реализации. Если bulk-эндпоинта со сменой `state_id` в нашей сборке нет — удали этот тест и отметь в плане, что точка отсутствует (см. Task 8 — регресс-тест охвата зафиксирует фактический список точек).

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd apps/api && python -m pytest plane/tests/contract/app/test_workflow_enforcement_app.py::test_bulk_update_blocks_disallowed_transition -v`
Expected: FAIL (403 не возвращается).

- [ ] **Step 3: Врезать enforce в bulk-вьюху**

В `apps/api/plane/app/views/issue/base.py`, в методе bulk-обновления (где `state_id` применяется к набору issue — около строки 890). Для каждого затрагиваемого issue, до записи, вызвать:

```python
        new_state_id = properties.get("state_id")
        if new_state_id is not None:
            for issue in issues_qs:  # issues_qs — выборка изменяемых issue в этой вьюхе
                enforce_transition(project_id, issue.state_id, new_state_id, request.user)
```

> Имена `properties`/`issues_qs` приведи к фактическим в методе. Импорт `enforce_transition` уже добавлен в Task 4.

- [ ] **Step 4: Врезать enforce в sub_issue (если меняет state)**

Открыть `apps/api/plane/app/views/issue/sub_issue.py`. Если метод обновляет `state_id` дочерних issue — перед записью вставить тот же паттерн `enforce_transition(project_id, child.state_id, new_state_id, request.user)`. Если sub_issue только связывает иерархию и не меняет статус — пропустить, отметив это в Task 8.

Добавить импорт в начало `sub_issue.py`:

```python
from plane.workflow.enforcement import enforce_transition
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `cd apps/api && python -m pytest plane/tests/contract/app/test_workflow_enforcement_app.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/plane/app/views/issue/base.py apps/api/plane/app/views/issue/sub_issue.py \
        apps/api/plane/tests/contract/app/test_workflow_enforcement_app.py
git commit -m "feat(workflow): enforce guard in bulk and sub-issue updates"
```

---

## Task 6: Врезка в публичный REST API (+ исключение intake)

**Files:**

- Modify: `apps/api/plane/api/views/issue.py`
- Test: `apps/api/plane/tests/contract/api/test_workflow_enforcement_api.py`

- [ ] **Step 1: Написать падающий contract-тест публичного API**

Create `apps/api/plane/tests/contract/api/test_workflow_enforcement_api.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectMember, ProjectWorkflow, State, Issue


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="WF Project", identifier="WF", workspace=workspace, created_by=create_user
    )
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def states(db, project, workspace):
    backlog = State.objects.create(
        name="Backlog", color="#000", group="backlog", project=project, workspace=workspace
    )
    done = State.objects.create(
        name="Done", color="#000", group="completed", project=project, workspace=workspace
    )
    return backlog, done


@pytest.mark.contract
@pytest.mark.django_db
def test_public_api_update_blocks_disallowed_transition(
    api_key_client, workspace, project, states, create_user
):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="A1", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/"
    response = api_key_client.patch(url, {"state": str(done.id)}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    issue.refresh_from_db()
    assert issue.state_id == backlog.id
```

> Точный префикс публичного API (`/api/v1/...`) и имя поля (`state` vs `state_id`) сверь с `apps/api/plane/api/urls/` и публичным сериализатором при реализации.

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd apps/api && python -m pytest plane/tests/contract/api/test_workflow_enforcement_api.py -v`
Expected: FAIL (нет 403).

- [ ] **Step 3: Врезать enforce в публичный API**

В `apps/api/plane/api/views/issue.py` добавить импорт:

```python
from plane.workflow.enforcement import enforce_creation, enforce_transition
```

В методе обновления issue (partial_update/patch) — после загрузки текущего issue и до сохранения нового статуса:

```python
        new_state_id = request.data.get("state") or request.data.get("state_id")
        if new_state_id is not None:
            enforce_transition(project_id, issue.state_id, new_state_id, request.user)
```

В методе создания issue — до записи:

```python
        enforce_creation(project_id, request.data.get("state") or request.data.get("state_id"),
                         request.user)
```

- [ ] **Step 4: Подтвердить исключение intake**

Открыть `apps/api/plane/api/views/intake.py:201`. Это служебное создание в triage-состоянии. Убедиться, что enforce там НЕ вызывается (по спеке intake исключён). Никаких изменений; зафиксировать факт комментарием в коде:

```python
        # Workflow guard intentionally skipped: intake creates into the triage
        # state as a service flow (see workflow spec §6).
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `cd apps/api && python -m pytest plane/tests/contract/api/test_workflow_enforcement_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/plane/api/views/issue.py apps/api/plane/api/views/intake.py \
        apps/api/plane/tests/contract/api/test_workflow_enforcement_api.py
git commit -m "feat(workflow): enforce guard in public REST API, exempt intake"
```

---

## Task 7: `pre_save`-страховка (defense-in-depth)

**Files:**

- Create: `apps/api/plane/workflow/signals.py`
- Modify: `apps/api/plane/db/apps.py`
- Test: `apps/api/plane/tests/unit/workflow/test_signal_safety_net.py`

- [ ] **Step 1: Написать падающий тест сигнала**

Create `apps/api/plane/tests/unit/workflow/test_signal_safety_net.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import Project, ProjectWorkflow, State, Issue
from plane.workflow.exceptions import WorkflowBlocked


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(
        name="WF Project", identifier="WF", workspace=workspace, created_by=create_user
    )


@pytest.fixture
def states(db, project, workspace):
    backlog = State.objects.create(
        name="Backlog", color="#000", group="backlog", project=project, workspace=workspace
    )
    done = State.objects.create(
        name="Done", color="#000", group="completed", project=project, workspace=workspace
    )
    return backlog, done


@pytest.mark.unit
@pytest.mark.django_db
def test_direct_save_blocked_when_actor_in_context(project, workspace, states, create_user):
    from plane.workflow.signals import workflow_actor_context

    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="S1", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    issue.state = done
    with workflow_actor_context(create_user):
        with pytest.raises(WorkflowBlocked):
            issue.save()
    issue.refresh_from_db()
    assert issue.state_id == backlog.id


@pytest.mark.unit
@pytest.mark.django_db
def test_direct_save_allowed_without_actor_context(project, workspace, states, create_user):
    # No actor in context → safety net is dormant, primary защита — во вьюхах.
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="S2", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    issue.state = done
    issue.save()  # must not raise
    issue.refresh_from_db()
    assert issue.state_id == done.id
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd apps/api && python -m pytest plane/tests/unit/workflow/test_signal_safety_net.py -v`
Expected: FAIL — `cannot import name 'workflow_actor_context'`.

- [ ] **Step 3: Создать сигнал и actor-контекст**

Create `apps/api/plane/workflow/signals.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Defense-in-depth: a pre_save safety net that blocks disallowed transitions
on any direct Issue.save() — but only when an actor is explicitly in context.

The primary enforcement is the explicit enforce_* call in each view. This net
exists so a future/forgotten write path cannot silently bypass the workflow.
"""

import contextvars
from contextlib import contextmanager

from django.db.models.signals import pre_save
from django.dispatch import receiver

from plane.db.models import Issue
from .enforcement import enforce_transition

_actor_var = contextvars.ContextVar("workflow_actor", default=None)


@contextmanager
def workflow_actor_context(actor):
    """Bind the acting user so the pre_save net can evaluate transitions."""
    token = _actor_var.set(actor)
    try:
        yield
    finally:
        _actor_var.reset(token)


@receiver(pre_save, sender=Issue)
def guard_issue_transition(sender, instance, **kwargs):
    actor = _actor_var.get()
    if actor is None or instance.pk is None or instance.state_id is None:
        return
    previous = Issue.objects.filter(pk=instance.pk).values_list("state_id", flat=True).first()
    if previous is None or str(previous) == str(instance.state_id):
        return
    enforce_transition(instance.project_id, previous, instance.state_id, actor)
```

- [ ] **Step 4: Подключить сигнал в `AppConfig.ready()`**

Открыть `apps/api/plane/db/apps.py`. В методе `ready()` класса конфигурации добавить:

```python
    def ready(self):
        import plane.workflow.signals  # noqa: F401
```

> Если `ready()` уже есть — добавить строку импорта внутрь него, не дублируя метод.

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `cd apps/api && python -m pytest plane/tests/unit/workflow/test_signal_safety_net.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add apps/api/plane/workflow/signals.py apps/api/plane/db/apps.py \
        apps/api/plane/tests/unit/workflow/test_signal_safety_net.py
git commit -m "feat(workflow): pre_save safety net for direct saves"
```

---

## Task 8: Регресс-тест охвата

**Files:**

- Test: `apps/api/plane/tests/contract/app/test_workflow_coverage.py`

- [ ] **Step 1: Написать тест охвата**

Этот тест — живой реестр всех путей записи статуса. Каждый кортеж — (описание, callable, ожидание блокировки). Заполняется фактическими точками, найденными в Task 4–6.

Create `apps/api/plane/tests/contract/app/test_workflow_coverage.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectMember, ProjectWorkflow, State, Issue


@pytest.fixture
def setup(db, workspace, create_user):
    project = Project.objects.create(
        name="WF", identifier="WF", workspace=workspace, created_by=create_user
    )
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=create_user, role=20, is_active=True
    )
    backlog = State.objects.create(
        name="Backlog", color="#000", group="backlog", project=project, workspace=workspace
    )
    done = State.objects.create(
        name="Done", color="#000", group="completed", project=project, workspace=workspace
    )
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    return project, backlog, done


def _new_issue(setup, workspace, create_user):
    project, backlog, _ = setup
    return Issue.objects.create(
        name="cov", project=project, workspace=workspace, state=backlog, created_by=create_user
    )


@pytest.mark.contract
@pytest.mark.django_db
def test_app_partial_update_is_covered(session_client, workspace, setup, create_user):
    project, backlog, done = setup
    issue = _new_issue(setup, workspace, create_user)
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/"
    resp = session_client.patch(url, {"state_id": str(done.id)}, format="json")
    assert resp.status_code == status.HTTP_403_FORBIDDEN


# Добавь по одному test_*_is_covered на КАЖДУЮ фактическую точку записи state,
# подтверждённую в Task 4–6 (bulk, sub_issue, public API). Если точка в этой
# сборке отсутствует — добавь комментарий `# нет в сборке: <причина>` вместо теста,
# чтобы реестр оставался честным.
```

- [ ] **Step 2: Запустить — убедиться, что зелёный для существующих точек**

Run: `cd apps/api && python -m pytest plane/tests/contract/app/test_workflow_coverage.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/api/plane/tests/contract/app/test_workflow_coverage.py
git commit -m "test(workflow): coverage registry for status write paths"
```

---

## Task 9: API конфигурации workflow (admin-only)

**Files:**

- Create: `apps/api/plane/app/serializers/workflow.py`, `apps/api/plane/app/views/workflow.py`, `apps/api/plane/app/urls/workflow.py`
- Modify: `apps/api/plane/app/serializers/__init__.py`, `apps/api/plane/app/views/__init__.py`, `apps/api/plane/app/urls/__init__.py`
- Test: `apps/api/plane/tests/contract/app/test_workflow_config_app.py`

- [ ] **Step 1: Написать падающий contract-тест конфигурации**

Create `apps/api/plane/tests/contract/app/test_workflow_config_app.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectMember, State, WorkflowTransition


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="WF", identifier="WF", workspace=workspace, created_by=create_user
    )
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=create_user, role=20, is_active=True
    )
    return project


@pytest.fixture
def states(db, project, workspace):
    backlog = State.objects.create(
        name="Backlog", color="#000", group="backlog", project=project, workspace=workspace
    )
    done = State.objects.create(
        name="Done", color="#000", group="completed", project=project, workspace=workspace
    )
    return backlog, done


def wf_url(slug, project_id, suffix=""):
    return f"/api/workspaces/{slug}/projects/{project_id}/workflow/{suffix}"


@pytest.mark.contract
@pytest.mark.django_db
def test_toggle_workflow(session_client, workspace, project):
    url = wf_url(workspace.slug, project.id)
    resp = session_client.patch(url, {"is_enabled": True}, format="json")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["is_enabled"] is True


@pytest.mark.contract
@pytest.mark.django_db
def test_create_transition(session_client, workspace, project, states):
    backlog, done = states
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/workflow-transitions/"
    resp = session_client.post(
        url, {"state": str(backlog.id), "transition_state": str(done.id)}, format="json"
    )
    assert resp.status_code == status.HTTP_201_CREATED
    assert WorkflowTransition.objects.filter(project=project).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_member_cannot_configure(api_client, workspace, project, states, create_user):
    # Downgrade the member to MEMBER role.
    ProjectMember.objects.filter(project=project, member=create_user).update(role=15)
    api_client.force_authenticate(user=create_user)
    url = wf_url(workspace.slug, project.id)
    resp = api_client.patch(url, {"is_enabled": True}, format="json")
    assert resp.status_code == status.HTTP_403_FORBIDDEN
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd apps/api && python -m pytest plane/tests/contract/app/test_workflow_config_app.py -v`
Expected: FAIL (404, маршрутов ещё нет).

- [ ] **Step 3: Создать сериализаторы**

Create `apps/api/plane/app/serializers/workflow.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework import serializers

from plane.db.models import (
    ProjectWorkflow,
    WorkflowActivity,
    WorkflowStateConfig,
    WorkflowTransition,
)

from .base import BaseSerializer


class ProjectWorkflowSerializer(BaseSerializer):
    class Meta:
        model = ProjectWorkflow
        fields = ["id", "project", "is_enabled"]
        read_only_fields = ["workspace", "project"]


class WorkflowStateConfigSerializer(BaseSerializer):
    class Meta:
        model = WorkflowStateConfig
        fields = ["id", "state", "allow_issue_creation"]
        read_only_fields = ["workspace", "project", "state"]


class WorkflowTransitionSerializer(BaseSerializer):
    approvers = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowTransition
        fields = ["id", "state", "transition_state", "approvers"]
        read_only_fields = ["workspace", "project"]

    def get_approvers(self, obj):
        return [str(uid) for uid in obj.approvers.values_list("approver_id", flat=True)]


class WorkflowActivitySerializer(BaseSerializer):
    class Meta:
        model = WorkflowActivity
        fields = ["id", "field", "old_value", "new_value", "actor", "created_at"]
        read_only_fields = ["workspace", "project", "actor"]
```

Modify `apps/api/plane/app/serializers/__init__.py` — добавить:

```python
from .workflow import (
    ProjectWorkflowSerializer,
    WorkflowStateConfigSerializer,
    WorkflowTransitionSerializer,
    WorkflowActivitySerializer,
)
```

- [ ] **Step 4: Создать вьюсеты**

Create `apps/api/plane/app/views/workflow.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers.workflow import (
    ProjectWorkflowSerializer,
    WorkflowStateConfigSerializer,
    WorkflowTransitionSerializer,
)
from plane.db.models import (
    ProjectMember,
    ProjectWorkflow,
    WorkflowStateConfig,
    WorkflowTransition,
    WorkflowTransitionApprover,
)

from .base import BaseAPIView


class ProjectWorkflowView(BaseAPIView):
    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id):
        workflow, _ = ProjectWorkflow.objects.get_or_create(
            project_id=project_id, workspace__slug=slug,
            defaults={"workspace_id": request.user and None},
        )
        return Response(ProjectWorkflowSerializer(workflow).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def patch(self, request, slug, project_id):
        workflow = ProjectWorkflow.objects.filter(
            project_id=project_id, workspace__slug=slug
        ).first()
        if workflow is None:
            from plane.db.models import Project

            project = Project.objects.get(id=project_id, workspace__slug=slug)
            workflow = ProjectWorkflow.objects.create(
                project=project, workspace=project.workspace
            )
        serializer = ProjectWorkflowSerializer(workflow, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class WorkflowStateConfigView(BaseAPIView):
    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id):
        configs = WorkflowStateConfig.objects.filter(
            project_id=project_id, workspace__slug=slug
        )
        return Response(
            WorkflowStateConfigSerializer(configs, many=True).data, status=status.HTTP_200_OK
        )

    @allow_permission([ROLE.ADMIN])
    def patch(self, request, slug, project_id, state_id):
        from plane.db.models import Project

        project = Project.objects.get(id=project_id, workspace__slug=slug)
        config, _ = WorkflowStateConfig.objects.get_or_create(
            project=project, workspace=project.workspace, state_id=state_id
        )
        serializer = WorkflowStateConfigSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class WorkflowTransitionView(BaseAPIView):
    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id):
        from plane.db.models import Project

        project = Project.objects.get(id=project_id, workspace__slug=slug)
        transition = WorkflowTransition.objects.create(
            project=project,
            workspace=project.workspace,
            state_id=request.data["state"],
            transition_state_id=request.data["transition_state"],
        )
        return Response(
            WorkflowTransitionSerializer(transition).data, status=status.HTTP_201_CREATED
        )

    @allow_permission([ROLE.ADMIN])
    def delete(self, request, slug, project_id, pk):
        WorkflowTransition.objects.filter(
            project_id=project_id, workspace__slug=slug, pk=pk
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowTransitionApproverView(BaseAPIView):
    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id, transition_id):
        from plane.db.models import Project

        project = Project.objects.get(id=project_id, workspace__slug=slug)
        approver_id = request.data["approver"]
        # Approver must be an active member of the project.
        if not ProjectMember.objects.filter(
            project_id=project_id, member_id=approver_id, is_active=True
        ).exists():
            return Response(
                {"error_code": "APPROVER_NOT_A_MEMBER",
                 "message": "Approver must be an active project member."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        approver = WorkflowTransitionApprover.objects.create(
            project=project,
            workspace=project.workspace,
            transition_id=transition_id,
            approver_id=approver_id,
        )
        return Response({"id": str(approver.id)}, status=status.HTTP_201_CREATED)

    @allow_permission([ROLE.ADMIN])
    def delete(self, request, slug, project_id, transition_id, pk):
        WorkflowTransitionApprover.objects.filter(
            project_id=project_id, transition_id=transition_id, pk=pk
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
```

> Сверь базовый класс: используй тот же, что и соседние app-вью (`BaseAPIView` из `plane/app/views/base.py`). Метод `get_or_create` в `ProjectWorkflowView.get` упрощён — приведи к фактической загрузке проекта по `slug`/`project_id`, как в `patch`.

Modify `apps/api/plane/app/views/__init__.py` — добавить:

```python
from .workflow import (
    ProjectWorkflowView,
    WorkflowStateConfigView,
    WorkflowTransitionView,
    WorkflowTransitionApproverView,
)
```

- [ ] **Step 5: Создать и подключить urls**

Create `apps/api/plane/app/urls/workflow.py`:

```python
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views.workflow import (
    ProjectWorkflowView,
    WorkflowStateConfigView,
    WorkflowTransitionView,
    WorkflowTransitionApproverView,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow/",
        ProjectWorkflowView.as_view(),
        name="project-workflow",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-states/",
        WorkflowStateConfigView.as_view(),
        name="workflow-state-configs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-states/<uuid:state_id>/",
        WorkflowStateConfigView.as_view(),
        name="workflow-state-config-detail",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-transitions/",
        WorkflowTransitionView.as_view(),
        name="workflow-transitions",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-transitions/<uuid:pk>/",
        WorkflowTransitionView.as_view(),
        name="workflow-transition-detail",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-transitions/<uuid:transition_id>/approvers/",
        WorkflowTransitionApproverView.as_view(),
        name="workflow-transition-approvers",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-transitions/<uuid:transition_id>/approvers/<uuid:pk>/",
        WorkflowTransitionApproverView.as_view(),
        name="workflow-transition-approver-detail",
    ),
]
```

Modify `apps/api/plane/app/urls/__init__.py`:

1. Добавить импорт рядом с прочими: `from .workflow import urlpatterns as workflow_urls`
2. Добавить `+ workflow_urls` в сборку общего `urlpatterns` (в том же стиле, что соседние группы).

- [ ] **Step 6: Запустить тест — убедиться, что проходит**

Run: `cd apps/api && python -m pytest plane/tests/contract/app/test_workflow_config_app.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
git add apps/api/plane/app/serializers/workflow.py apps/api/plane/app/serializers/__init__.py \
        apps/api/plane/app/views/workflow.py apps/api/plane/app/views/__init__.py \
        apps/api/plane/app/urls/workflow.py apps/api/plane/app/urls/__init__.py \
        apps/api/plane/tests/contract/app/test_workflow_config_app.py
git commit -m "feat(workflow): admin config API (toggle, transitions, approvers, state configs)"
```

---

## Task 10: Полный прогон + ADR

**Files:**

- Create/Modify: `overlay/docs/architecture/adr/DECISIONS.md`

- [ ] **Step 1: Прогнать весь workflow-набор тестов**

Run:

```bash
cd apps/api && python -m pytest plane/tests/unit/workflow plane/tests/unit/models/test_workflow_models.py \
  plane/tests/contract/app/test_workflow_enforcement_app.py \
  plane/tests/contract/app/test_workflow_config_app.py \
  plane/tests/contract/app/test_workflow_coverage.py \
  plane/tests/contract/api/test_workflow_enforcement_api.py -v
```

Expected: все PASS.

- [ ] **Step 2: Проверить миграции в согласованном состоянии**

Run: `cd apps/api && python manage.py makemigrations --check --dry-run`
Expected: "No changes detected".

- [ ] **Step 3: Записать ADR**

Создать (или дополнить) `overlay/docs/architecture/adr/DECISIONS.md` записью:

```markdown
## ADR: Workflow approval — собственный guard-слой

**Дата:** 2026-06-25
**Статус:** принято

**Контекст.** Нужны управляемые переходы статусов work item. В upstream Plane это
EE-проприетарная фича; форк shbvn реализовал упрощённый аналог в open-core, но
переплёл его с банковской спецификой и чужой цепочкой миграций.

**Решение.**

1. Не cherry-pick shbvn и не тянуть EE — собственная чистая реализация.
2. Enforcement — единый guard-слой (`plane/workflow/`) на всех серверных путях
   записи статуса + pre_save-страховка, а не разрозненные проверки во вьюхах.
3. Задел под полное согласование — в архитектуре (`Decision.NEEDS_APPROVAL`), а не
   в схеме БД (без мёртвых колонок).

**Последствия.** Имена таблиц совпадают с EE-терминологией Plane — при будущем
подтягивании EE возможны коллизии (принято осознанно). Полнота охвата точек
записи держится регресс-тестом `test_workflow_coverage.py`.
```

- [ ] **Step 4: Commit**

```bash
git add overlay/docs/architecture/adr/DECISIONS.md
git commit -m "docs(workflow): ADR for guard-layer approach"
```

---

## Self-Review (выполнено при написании плана)

**Покрытие спеки:**

- §4 Модель данных → Task 1 ✓
- §5 Guard-слой (Decision/evaluate) → Task 2 ✓; координатор enforcement → Task 3 ✓
- §6 Точки enforcement: app create/update → Task 4; bulk/sub_issue → Task 5; публичный API + исключение intake → Task 6; pre_save-страховка → Task 7; регресс-тест охвата → Task 8 ✓
- §7 Контракт ошибок (403/400, error_code) → Task 3 (exceptions) + проверки в Task 4/6 ✓
- §8.1 API настроек → Task 9 ✓ (§8.2 frontend — отдельный план)
- §9 Тестирование → распределено по всем задачам ✓
- §10 Миграции/именование → Task 1 Step 6 ✓
- §12 ADR → Task 10 ✓
- §11 Риски (EE-коллизия, полнота охвата) → ADR + Task 8 ✓

**Открытые места, требующие сверки с кодом при реализации (помечены в шагах):** точный источник `state` в `create` (id vs объект), фактические URL/поля bulk и публичного API, наличие смены state в sub_issue, базовый класс app-вью (`BaseAPIView`), сигнатура `ready()` в `db/apps.py`. Все они локальны и не меняют архитектуру.

**Согласованность имён:** `is_enabled` (не `is_live`) везде; `Action.ALLOW/BLOCK/NEEDS_APPROVAL`; `Decision`; `evaluate_creation`/`evaluate_transition`; `enforce_creation`/`enforce_transition`; `WorkflowBlocked`; коды `WORKFLOW_TRANSITION_BLOCKED`/`WORKFLOW_CREATION_BLOCKED` — единообразны между задачами.
