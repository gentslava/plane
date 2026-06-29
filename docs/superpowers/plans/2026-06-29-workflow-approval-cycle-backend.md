# Workflow Approval Cycle — Backend (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the strict-allowlist enforcement with the approval-cycle model (on-path free, off-path needs approval, hard-forbidden) and add the server-side `ApprovalRequest` lifecycle (request → approve/reject/cancel → auto-apply), per `docs/superpowers/specs/2026-06-29-workflow-approval-cycle-design.md`.

**Architecture:** The pure `guard.evaluate_transition` gains a 3-way decision (ALLOW / NEEDS_APPROVAL / BLOCK) and a target-state approver-resolution chain (state guardians → project approvers → four-eyes). Approvers move from transitions to states. A new `ApprovalRequest` model + DRF endpoints drive the lifecycle; approving applies the move through a guard-bypass context so the signal net does not re-block it. Notifications reuse Plane's `Notification` model.

**Tech Stack:** Django/DRF, PostgreSQL, pytest in Docker (`docker-compose-test.yml`), fork migration series `iw_*` (next: `iw_006`).

**Conventions for every task:**

- Run tests in Docker: `docker compose -f docker-compose-test.yml run --rm api-tests pytest <path> -p no:cacheprovider --create-db -q`
- Commit on a passing task. End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- This branch is `feature/workflow-approval` (do not branch).

---

## File structure

**Create:**

- `apps/api/plane/db/migrations/iw_006_approval_cycle.py` — model evolution
- `apps/api/plane/app/serializers/workflow.py` — extend (already exists; add new serializers)
- `apps/api/plane/app/views/approval.py` — `ApprovalRequest` lifecycle endpoints
- `apps/api/plane/app/serializers/approval.py` — `ApprovalRequestSerializer`
- `apps/api/plane/app/urls/approval.py` — approval routes
- `apps/api/plane/workflow/approvers.py` — pure approver-resolution helpers
- `apps/api/plane/workflow/apply.py` — apply-on-approve helper + `workflow_bypass`
- `apps/api/plane/tests/unit/workflow/test_approvers.py`
- `apps/api/plane/tests/contract/app/test_approval_requests_app.py`
- `apps/api/plane/tests/contract/app/test_workflow_state_guardians_app.py`

**Modify:**

- `apps/api/plane/db/models/workflow.py` — `WorkflowTransition.kind`; new `WorkflowStateGuardian`, `WorkflowProjectApprover`, `ApprovalRequest`; drop `WorkflowTransitionApprover`
- `apps/api/plane/db/models/__init__.py` — export the new models, drop the old
- `apps/api/plane/workflow/guard.py` — rewrite `evaluate_transition`
- `apps/api/plane/workflow/exceptions.py` — new codes → HTTP status
- `apps/api/plane/workflow/signals.py` — `workflow_bypass` context + post-save auto-cancel
- `apps/api/plane/app/views/workflow.py` — drop approver endpoint; add guardians + project-approver endpoints; transition `kind`
- `apps/api/plane/app/urls/workflow.py` — route changes
- `apps/api/plane/graphql/workflow.py` — message text for `NEEDS_APPROVAL` (logic already raises for non-ALLOW)
- `apps/api/plane/config/urls or app url include` — wire `urls/approval.py`
- Tests: `tests/unit/workflow/test_guard.py`, `tests/contract/app/test_workflow_config_app.py`, `tests/contract/app/test_workflow_coverage.py`

---

## Task 1: Model evolution (`iw_006`)

**Files:**

- Modify: `apps/api/plane/db/models/workflow.py`
- Modify: `apps/api/plane/db/models/__init__.py`
- Create: `apps/api/plane/db/migrations/iw_006_approval_cycle.py`
- Test: `apps/api/plane/tests/unit/models/test_workflow_models.py`

- [ ] **Step 1: Write the failing model test**

Append to `apps/api/plane/tests/unit/models/test_workflow_models.py`:

```python
import pytest
from plane.db.models import (
    WorkflowTransition,
    WorkflowStateGuardian,
    WorkflowProjectApprover,
    ApprovalRequest,
)


@pytest.mark.django_db
def test_transition_kind_defaults_allowed(project, workspace, states):
    backlog, done = states
    t = WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    assert t.kind == "allowed"


@pytest.mark.django_db
def test_state_guardian_and_project_approver(project, workspace, states, member_user):
    backlog, done = states
    g = WorkflowStateGuardian.objects.create(
        project=project, workspace=workspace, state=done, guardian=member_user
    )
    pa = WorkflowProjectApprover.objects.create(
        project=project, workspace=workspace, approver=member_user
    )
    assert g.guardian_id == member_user.id
    assert pa.approver_id == member_user.id


@pytest.mark.django_db
def test_approval_request_defaults_pending(project, workspace, states, member_user, create_issue):
    backlog, done = states
    issue = create_issue(project, backlog)
    req = ApprovalRequest.objects.create(
        project=project, workspace=workspace, work_item=issue,
        from_state=backlog, to_state=done, requested_by=member_user,
    )
    assert req.status == "pending"
```

> If `create_issue` fixture is absent in `conftest.py`, add a minimal one that creates an `Issue` in a given state (mirror existing issue-creating helpers in the test suite).

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/models/test_workflow_models.py -p no:cacheprovider --create-db -q`
Expected: FAIL (ImportError: cannot import name `WorkflowStateGuardian`).

- [ ] **Step 3: Edit models**

In `apps/api/plane/db/models/workflow.py`:

Add `kind` to `WorkflowTransition` (next to `transition_state`):

```python
class WorkflowTransition(ProjectBaseModel):
    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"
    KIND_CHOICES = ((ALLOWED, "Allowed"), (FORBIDDEN, "Forbidden"))

    state = models.ForeignKey("db.State", on_delete=models.CASCADE, related_name="outgoing_transitions")
    transition_state = models.ForeignKey("db.State", on_delete=models.CASCADE, related_name="incoming_transitions")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=ALLOWED)
    # ... keep existing Meta/constraints ...
```

Delete the `WorkflowTransitionApprover` class. Add three new classes (mirror the `ProjectBaseModel` + soft-delete-aware `UniqueConstraint` style already used in this file):

```python
class WorkflowStateGuardian(ProjectBaseModel):
    """A person who approves off-path moves INTO `state`."""
    state = models.ForeignKey("db.State", on_delete=models.CASCADE, related_name="workflow_guardians")
    guardian = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workflow_state_guardianships")

    class Meta(ProjectBaseModel.Meta):
        db_table = "workflow_state_guardians"
        constraints = [
            models.UniqueConstraint(
                fields=["state", "guardian"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_workflow_state_guardian_active",
            )
        ]


class WorkflowProjectApprover(ProjectBaseModel):
    """Project-level fallback approver for off-path moves."""
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workflow_project_approvals")

    class Meta(ProjectBaseModel.Meta):
        db_table = "workflow_project_approvers"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "approver"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_workflow_project_approver_active",
            )
        ]


class ApprovalRequest(ProjectBaseModel):
    """A pending off-path move awaiting approval. Not a work item."""
    PENDING, APPROVED, REJECTED, CANCELLED = "pending", "approved", "rejected", "cancelled"
    STATUS_CHOICES = ((PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled"))

    work_item = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="approval_requests")
    from_state = models.ForeignKey("db.State", on_delete=models.CASCADE, related_name="+")
    to_state = models.ForeignKey("db.State", on_delete=models.CASCADE, related_name="+")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workflow_approval_requests")
    comment = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="workflow_approval_decisions")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(null=True, blank=True)

    class Meta(ProjectBaseModel.Meta):
        db_table = "workflow_approval_requests"
        constraints = [
            models.UniqueConstraint(
                fields=["work_item"],
                condition=models.Q(status="pending", deleted_at__isnull=True),
                name="unique_pending_approval_per_work_item",
            )
        ]
```

In `apps/api/plane/db/models/__init__.py`: remove the `WorkflowTransitionApprover` export and add `WorkflowStateGuardian, WorkflowProjectApprover, ApprovalRequest`.

- [ ] **Step 4: Generate and rename the migration**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests python manage.py makemigrations db`
This produces a file auto-named `0001_*.py` (Django numbers from 0001 because the last node `iw_005` is non-numeric — known fork gotcha).

Rename the file to `apps/api/plane/db/migrations/iw_006_approval_cycle.py`, and inside it set:

```python
class Migration(migrations.Migration):
    dependencies = [("db", "iw_005_workflow_models")]
    # ... keep the generated operations (AddField kind, CreateModel x3, DeleteModel WorkflowTransitionApprover, AddConstraint x3) ...
```

Verify no other migration auto-named `0001` remains.

- [ ] **Step 5: Run model tests to verify they pass**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/models/test_workflow_models.py -p no:cacheprovider --create-db -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/plane/db/models/workflow.py apps/api/plane/db/models/__init__.py apps/api/plane/db/migrations/iw_006_approval_cycle.py apps/api/plane/tests/unit/models/test_workflow_models.py
git commit -m "feat(workflow): iw_006 — state guardians, project approvers, approval requests, transition kind"
```

---

## Task 2: Approver resolution (pure)

**Files:**

- Create: `apps/api/plane/workflow/approvers.py`
- Test: `apps/api/plane/tests/unit/workflow/test_approvers.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from plane.db.models import WorkflowStateGuardian, WorkflowProjectApprover
from plane.workflow.approvers import resolve_approver_ids


@pytest.mark.django_db
def test_resolution_prefers_state_guardians(project, workspace, states, member_user):
    _, done = states
    WorkflowStateGuardian.objects.create(project=project, workspace=workspace, state=done, guardian=member_user)
    WorkflowProjectApprover.objects.create(project=project, workspace=workspace, approver=member_user)
    assert resolve_approver_ids(project.id, done.id) == [str(member_user.id)]


@pytest.mark.django_db
def test_resolution_falls_back_to_project_approvers(project, workspace, states, member_user):
    _, done = states
    WorkflowProjectApprover.objects.create(project=project, workspace=workspace, approver=member_user)
    assert resolve_approver_ids(project.id, done.id) == [str(member_user.id)]


@pytest.mark.django_db
def test_resolution_empty_means_four_eyes(project, workspace, states):
    _, done = states
    assert resolve_approver_ids(project.id, done.id) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_approvers.py -p no:cacheprovider --create-db -q`
Expected: FAIL (ModuleNotFoundError `plane.workflow.approvers`).

- [ ] **Step 3: Implement**

`apps/api/plane/workflow/approvers.py`:

```python
"""Pure target-state approver resolution for off-path moves."""
from typing import Any

from plane.db.models import WorkflowProjectApprover, WorkflowStateGuardian


def resolve_approver_ids(project_id: Any, to_state_id: Any) -> list[str]:
    """Resolved approvers for an off-path move into `to_state_id`.

    Chain: target-state guardians -> project fallback approvers -> [] (four-eyes,
    meaning any *other* project member may approve).
    """
    guardians = [
        str(g.guardian_id)
        for g in WorkflowStateGuardian.objects.filter(project_id=project_id, state_id=to_state_id)
    ]
    if guardians:
        return guardians
    project_approvers = [
        str(a.approver_id)
        for a in WorkflowProjectApprover.objects.filter(project_id=project_id)
    ]
    return project_approvers
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_approvers.py -p no:cacheprovider --create-db -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/plane/workflow/approvers.py apps/api/plane/tests/unit/workflow/test_approvers.py
git commit -m "feat(workflow): target-state approver resolution chain"
```

---

## Task 3: Rewrite `guard.evaluate_transition`

**Files:**

- Modify: `apps/api/plane/workflow/guard.py`
- Test: `apps/api/plane/tests/unit/workflow/test_guard.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/plane/tests/unit/workflow/test_guard.py`:

```python
from plane.workflow.guard import evaluate_transition, Action
from plane.db.models import (
    ProjectWorkflow, WorkflowTransition, WorkflowStateGuardian,
)


@pytest.mark.django_db
def test_on_path_is_free(project, workspace, states, member_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowTransition.objects.create(project=project, workspace=workspace, state=backlog, transition_state=done, kind="allowed")
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.ALLOW


@pytest.mark.django_db
def test_forbidden_is_blocked(project, workspace, states, member_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowTransition.objects.create(project=project, workspace=workspace, state=backlog, transition_state=done, kind="forbidden")
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.BLOCK
    assert d.code == "WORKFLOW_TRANSITION_FORBIDDEN"


@pytest.mark.django_db
def test_off_path_needs_approval_four_eyes(project, workspace, states, member_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.NEEDS_APPROVAL
    assert d.code == "WORKFLOW_TRANSITION_NEEDS_APPROVAL"
    assert d.context["approvers"] == []
    assert d.context["any_member"] is True


@pytest.mark.django_db
def test_off_path_approver_acts_directly(project, workspace, states, member_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateGuardian.objects.create(project=project, workspace=workspace, state=done, guardian=member_user)
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.ALLOW


@pytest.mark.django_db
def test_off_path_non_approver_needs_approval(project, workspace, states, member_user, create_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    guardian = create_user(email="guard@plane.so")
    WorkflowStateGuardian.objects.create(project=project, workspace=workspace, state=done, guardian=guardian)
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.NEEDS_APPROVAL
    assert d.context["approvers"] == [str(guardian.id)]
    assert d.context["any_member"] is False
```

> Adapt `create_user(email=...)` to the actual user-factory fixture signature found in `conftest.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_guard.py -p no:cacheprovider --create-db -q`
Expected: FAIL (old guard returns BLOCK for off-path; assertions mismatch).

- [ ] **Step 3: Rewrite `evaluate_transition`**

Replace the body of `evaluate_transition` in `apps/api/plane/workflow/guard.py` (keep `evaluate_creation`, `Action`, `Decision`, `_workflow_enabled` as-is). Import the resolver:

```python
from plane.workflow.approvers import resolve_approver_ids


def evaluate_transition(project_id, from_state_id, to_state_id, actor) -> Decision:
    """Classify a move from_state -> to_state for `actor`."""
    if not _workflow_enabled(project_id):
        return Decision(action=Action.ALLOW)

    ctx = {"from_state": str(from_state_id), "to_state": str(to_state_id)}

    transition = WorkflowTransition.objects.filter(
        project_id=project_id, state_id=from_state_id, transition_state_id=to_state_id
    ).first()

    if transition is not None and transition.kind == WorkflowTransition.FORBIDDEN:
        return Decision(
            action=Action.BLOCK,
            code="WORKFLOW_TRANSITION_FORBIDDEN",
            message="This status transition is forbidden by the project workflow.",
            context=ctx,
        )

    if transition is not None and transition.kind == WorkflowTransition.ALLOWED:
        return Decision(action=Action.ALLOW)  # on-path is free for everyone

    # off-path: needs approval
    approvers = resolve_approver_ids(project_id, to_state_id)
    actor_id = str(actor.id) if actor is not None and getattr(actor, "id", None) else None
    if approvers and actor_id in approvers:
        return Decision(action=Action.ALLOW)  # an approver performs it directly

    return Decision(
        action=Action.NEEDS_APPROVAL,
        code="WORKFLOW_TRANSITION_NEEDS_APPROVAL",
        message="This status change needs approval before it can be applied.",
        context={**ctx, "approvers": approvers, "any_member": not approvers},
    )
```

Ensure `WorkflowTransition` is imported in `guard.py` (it already is in the base).

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_guard.py -p no:cacheprovider --create-db -q`
Expected: PASS. (Existing guard tests that asserted off-path→BLOCK must be updated in the same edit to the new expectations.)

- [ ] **Step 5: Commit**

```bash
git add apps/api/plane/workflow/guard.py apps/api/plane/tests/unit/workflow/test_guard.py
git commit -m "feat(workflow): guard decision matrix — on-path free, off-path needs approval, forbidden blocks"
```

---

## Task 4: Error contract for the new codes

**Files:**

- Modify: `apps/api/plane/workflow/exceptions.py`
- Modify: `apps/api/plane/graphql/workflow.py`
- Test: `apps/api/plane/tests/unit/workflow/test_enforcement.py`

- [ ] **Step 1: Write the failing test**

Append to `apps/api/plane/tests/unit/workflow/test_enforcement.py`:

```python
import pytest
from rest_framework import status
from plane.workflow.enforcement import enforce_transition
from plane.workflow.exceptions import WorkflowBlocked
from plane.db.models import ProjectWorkflow


@pytest.mark.django_db
def test_enforce_transition_off_path_raises_needs_approval(project, workspace, states, member_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    with pytest.raises(WorkflowBlocked) as exc:
        enforce_transition(project.id, backlog.id, done.id, member_user)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["error_code"] == "WORKFLOW_TRANSITION_NEEDS_APPROVAL"
    assert exc.value.detail["any_member"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_enforcement.py -p no:cacheprovider --create-db -q`
Expected: FAIL (`NEEDS_APPROVAL` not mapped → wrong status / enforce treats only BLOCK).

- [ ] **Step 3: Implement**

In `apps/api/plane/workflow/exceptions.py`, extend the status map and make a blocking decision include `NEEDS_APPROVAL`:

```python
_CODE_TO_STATUS = {
    "WORKFLOW_CREATION_BLOCKED": status.HTTP_400_BAD_REQUEST,
    "WORKFLOW_TRANSITION_FORBIDDEN": status.HTTP_403_FORBIDDEN,
    "WORKFLOW_TRANSITION_NEEDS_APPROVAL": status.HTTP_403_FORBIDDEN,
}
```

In `apps/api/plane/workflow/enforcement.py`, ensure `enforce_transition` raises for any non-ALLOW decision (BLOCK **and** NEEDS_APPROVAL):

```python
def enforce_transition(project_id, from_state_id, to_state_id, actor) -> None:
    decision = evaluate_transition(project_id, from_state_id, to_state_id, actor)
    if decision.action != Action.ALLOW:
        raise WorkflowBlocked(decision, block_status_for(decision))
```

In `apps/api/plane/graphql/workflow.py`, `guard_transition` already raises for any non-ALLOW decision — no logic change needed; confirm the GraphQLError extensions carry `decision.context` (approvers/any_member) so mobile can render the same info.

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_enforcement.py -p no:cacheprovider --create-db -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/plane/workflow/exceptions.py apps/api/plane/workflow/enforcement.py apps/api/plane/tests/unit/workflow/test_enforcement.py
git commit -m "feat(workflow): map NEEDS_APPROVAL/FORBIDDEN to 403 across REST + GraphQL"
```

---

## Task 5: Apply-on-approve + guard bypass

**Files:**

- Create: `apps/api/plane/workflow/apply.py`
- Modify: `apps/api/plane/workflow/signals.py`
- Test: `apps/api/plane/tests/unit/workflow/test_apply.py`

- [ ] **Step 1: Write the failing test**

`apps/api/plane/tests/unit/workflow/test_apply.py`:

```python
import pytest
from plane.db.models import ProjectWorkflow, ApprovalRequest, Issue
from plane.workflow.apply import apply_approved_request


@pytest.mark.django_db
def test_apply_moves_issue_and_marks_approved(project, workspace, states, member_user, create_user, create_issue):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = create_issue(project, backlog)
    approver = create_user(email="approver@plane.so")
    req = ApprovalRequest.objects.create(
        project=project, workspace=workspace, work_item=issue,
        from_state=backlog, to_state=done, requested_by=member_user,
    )
    apply_approved_request(req, approver)
    issue.refresh_from_db()
    req.refresh_from_db()
    assert issue.state_id == done.id          # moved despite off-path (bypassed the guard)
    assert req.status == ApprovalRequest.APPROVED
    assert req.decided_by_id == approver.id
    assert req.decided_at is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_apply.py -p no:cacheprovider --create-db -q`
Expected: FAIL (ModuleNotFoundError `plane.workflow.apply`).

- [ ] **Step 3: Implement the bypass in `signals.py`**

Add to `apps/api/plane/workflow/signals.py`:

```python
from contextvars import ContextVar
from contextlib import contextmanager

_bypass_var: ContextVar[bool] = ContextVar("workflow_bypass", default=False)


@contextmanager
def workflow_bypass():
    """Suppress the pre_save guard net for an already-approved server action."""
    token = _bypass_var.set(True)
    try:
        yield
    finally:
        _bypass_var.reset(token)
```

At the top of the existing `guard_issue_transition` receiver, add an early return:

```python
def guard_issue_transition(sender, instance, update_fields=None, **kwargs):
    if _bypass_var.get():
        return
    # ... existing actor resolution + enforce_transition ...
```

- [ ] **Step 4: Implement `apply.py`**

`apps/api/plane/workflow/apply.py`:

```python
"""Apply an approved off-path move (bypassing the guard net)."""
from django.utils import timezone

from plane.db.models import ApprovalRequest
from plane.workflow.signals import workflow_bypass


def apply_approved_request(req: ApprovalRequest, decided_by) -> None:
    """Move the work item to `to_state` and mark the request APPROVED.

    Wrapped in `workflow_bypass` so the pre_save signal does not re-block the
    (still off-path) move. Caller is responsible for authorization.
    """
    issue = req.work_item
    with workflow_bypass():
        issue.state_id = req.to_state_id
        issue.save(update_fields=["state", "updated_at"])
    req.status = ApprovalRequest.APPROVED
    req.decided_by = decided_by
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
```

> Verify `Issue.save` accepts `update_fields=["state", ...]` (FK column is `state`). If the project tracks state via a different field name, adjust.

- [ ] **Step 5: Run to verify it passes**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_apply.py -p no:cacheprovider --create-db -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/plane/workflow/apply.py apps/api/plane/workflow/signals.py apps/api/plane/tests/unit/workflow/test_apply.py
git commit -m "feat(workflow): apply-on-approve with guard bypass"
```

---

## Task 6: Auto-cancel stale pending requests

**Files:**

- Modify: `apps/api/plane/workflow/signals.py`
- Test: `apps/api/plane/tests/unit/workflow/test_apply.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `apps/api/plane/tests/unit/workflow/test_apply.py`:

```python
@pytest.mark.django_db
def test_independent_move_auto_cancels_pending(project, workspace, states, member_user, create_issue):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = create_issue(project, backlog)
    req = ApprovalRequest.objects.create(
        project=project, workspace=workspace, work_item=issue,
        from_state=backlog, to_state=done, requested_by=member_user,
    )
    # the issue is moved another way (here: a raw state change)
    from plane.workflow.signals import workflow_bypass
    with workflow_bypass():
        issue.state_id = done.id
        issue.save(update_fields=["state", "updated_at"])
    req.refresh_from_db()
    assert req.status == ApprovalRequest.CANCELLED
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_apply.py::test_independent_move_auto_cancels_pending -p no:cacheprovider --create-db -q`
Expected: FAIL (still PENDING).

- [ ] **Step 3: Implement a post_save auto-cancel**

Add to `apps/api/plane/workflow/signals.py`:

```python
from django.db.models.signals import post_save
from plane.db.models import ApprovalRequest, Issue


@receiver(post_save, sender=Issue, dispatch_uid="workflow.auto_cancel_pending_requests")
def auto_cancel_pending_requests(sender, instance, **kwargs):
    """Once a work item is no longer in a pending request's from_state, that
    request is moot (the card moved another way or was just applied). Cancel any
    PENDING request that no longer matches the item's current state as its source."""
    ApprovalRequest.objects.filter(
        work_item_id=instance.id, status=ApprovalRequest.PENDING
    ).exclude(from_state_id=instance.state_id).update(status=ApprovalRequest.CANCELLED)
```

> Note: `apply_approved_request` sets `status=APPROVED` **after** the bypassed save, so the just-applied request is no longer PENDING by the time the next query runs — and it is APPROVED, so this filter (PENDING only) never touches it. Confirm ordering: the post_save fires during `issue.save()` while the request is still PENDING but its `from_state` (e.g. backlog) ≠ new state (done) → it would be cancelled. To avoid cancelling the request being applied, have `apply_approved_request` mark APPROVED **before** saving the issue, or filter `.exclude(to_state_id=instance.state_id)`. Use the `.exclude(to_state_id=instance.state_id)` form so a request whose target equals the new state is treated as fulfilled, not cancelled, and explicitly set it APPROVED in `apply.py`.

Adjust the signal to:

```python
    ApprovalRequest.objects.filter(
        work_item_id=instance.id, status=ApprovalRequest.PENDING
    ).exclude(to_state_id=instance.state_id).update(status=ApprovalRequest.CANCELLED)
```

- [ ] **Step 4: Run to verify it passes (and re-run Task 5 test)**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow/test_apply.py -p no:cacheprovider --create-db -q`
Expected: PASS (both apply + auto-cancel).

- [ ] **Step 5: Commit**

```bash
git add apps/api/plane/workflow/signals.py apps/api/plane/tests/unit/workflow/test_apply.py
git commit -m "feat(workflow): auto-cancel stale pending requests on independent moves"
```

---

## Task 7: `ApprovalRequest` lifecycle endpoints

**Files:**

- Create: `apps/api/plane/app/serializers/approval.py`
- Create: `apps/api/plane/app/views/approval.py`
- Create: `apps/api/plane/app/urls/approval.py`
- Modify: the app url include (where `urls/workflow.py` is included — mirror it)
- Test: `apps/api/plane/tests/contract/app/test_approval_requests_app.py`

- [ ] **Step 1: Write the failing contract tests**

`apps/api/plane/tests/contract/app/test_approval_requests_app.py`:

```python
import pytest
from rest_framework import status
from plane.db.models import ProjectWorkflow, ApprovalRequest, WorkflowStateGuardian


def requests_url(slug, pid, issue_id):
    return f"/api/workspaces/{slug}/projects/{pid}/work-items/{issue_id}/approval-requests/"


def action_url(slug, pid, issue_id, pk, action):
    return f"/api/workspaces/{slug}/projects/{pid}/work-items/{issue_id}/approval-requests/{pk}/{action}/"


@pytest.mark.contract
@pytest.mark.django_db
def test_create_request_for_off_path_move(session_client, workspace, project, states, create_issue):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = create_issue(project, backlog)
    res = session_client.post(requests_url(workspace.slug, project.id, issue.id), {"to_state": str(done.id), "comment": "skip"}, format="json")
    assert res.status_code == status.HTTP_201_CREATED
    assert ApprovalRequest.objects.filter(work_item=issue, status="pending").count() == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_duplicate_pending_rejected(session_client, workspace, project, states, create_issue):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = create_issue(project, backlog)
    url = requests_url(workspace.slug, project.id, issue.id)
    session_client.post(url, {"to_state": str(done.id)}, format="json")
    res = session_client.post(url, {"to_state": str(done.id)}, format="json")
    assert res.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.contract
@pytest.mark.django_db
def test_approve_applies_move(session_client, session_user, workspace, project, states, create_issue):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    # session_user is a project admin (guardian by being in project-approver fallback or admin); make them a guardian explicitly
    WorkflowStateGuardian.objects.create(project=project, workspace=workspace, state=done, guardian=session_user)
    issue = create_issue(project, backlog)
    req = ApprovalRequest.objects.create(project=project, workspace=workspace, work_item=issue, from_state=backlog, to_state=done, requested_by=session_user)
    res = session_client.post(action_url(workspace.slug, project.id, issue.id, req.id, "approve"), {}, format="json")
    assert res.status_code == status.HTTP_200_OK
    issue.refresh_from_db()
    assert issue.state_id == done.id
```

> Use whatever the suite calls the authenticated principal of `session_client` (here `session_user`); adapt fixture names to `conftest.py`. If a four-eyes request must be approved by someone _other_ than the requester, create a second user for the approver and authenticate as them.

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/contract/app/test_approval_requests_app.py -p no:cacheprovider --create-db -q`
Expected: FAIL (404 — routes do not exist).

- [ ] **Step 3: Serializer**

`apps/api/plane/app/serializers/approval.py`:

```python
from rest_framework import serializers
from plane.db.models import ApprovalRequest


class ApprovalRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalRequest
        fields = [
            "id", "work_item", "from_state", "to_state", "requested_by",
            "comment", "status", "decided_by", "decided_at", "decision_reason",
            "created_at",
        ]
        read_only_fields = ["status", "requested_by", "decided_by", "decided_at"]
```

- [ ] **Step 4: Views**

`apps/api/plane/app/views/approval.py` — mirror the structure/permissions of `app/views/workflow.py` (`BaseAPIView`, `allow_permission`). Key endpoints:

```python
import uuid
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ROLE, allow_permission
from plane.app.views.base import BaseAPIView
from plane.app.serializers.approval import ApprovalRequestSerializer
from plane.db.models import ApprovalRequest, Issue
from plane.workflow.approvers import resolve_approver_ids
from plane.workflow.apply import apply_approved_request
from plane.workflow.guard import evaluate_transition, Action


class ApprovalRequestListCreateEndpoint(BaseAPIView):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        qs = ApprovalRequest.objects.filter(work_item_id=issue_id, project_id=project_id).order_by("-created_at")
        return Response(ApprovalRequestSerializer(qs, many=True).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = Issue.objects.get(pk=issue_id, project_id=project_id, workspace__slug=slug)
        to_state_id = request.data.get("to_state")
        if not to_state_id:
            return Response({"error": "to_state is required"}, status=status.HTTP_400_BAD_REQUEST)
        decision = evaluate_transition(project_id, issue.state_id, to_state_id, request.user)
        if decision.action != Action.NEEDS_APPROVAL:
            # ALLOW => just move directly (no request); BLOCK => forbidden
            return Response({"error": "This move does not require an approval request."}, status=status.HTTP_400_BAD_REQUEST)
        if ApprovalRequest.objects.filter(work_item_id=issue_id, status=ApprovalRequest.PENDING).exists():
            return Response({"error": "There is already a pending approval request for this work item."}, status=status.HTTP_400_BAD_REQUEST)
        req = ApprovalRequest.objects.create(
            project_id=project_id, workspace=issue.workspace, work_item=issue,
            from_state_id=issue.state_id, to_state_id=to_state_id,
            requested_by=request.user, comment=request.data.get("comment"),
        )
        # notify resolved approvers (Task 8 wires the helper)
        from plane.workflow.notify import notify_approval_requested
        notify_approval_requested(req)
        return Response(ApprovalRequestSerializer(req).data, status=status.HTTP_201_CREATED)


class ApprovalRequestActionEndpoint(BaseAPIView):
    def _request(self, project_id, issue_id, pk):
        return ApprovalRequest.objects.get(pk=pk, work_item_id=issue_id, project_id=project_id)

    def _is_approver(self, req, user):
        approvers = resolve_approver_ids(req.project_id, req.to_state_id)
        if approvers:
            return str(user.id) in approvers
        # four-eyes: any *other* member may approve
        return str(user.id) != str(req.requested_by_id)

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id, pk, action):
        req = self._request(project_id, issue_id, pk)
        if req.status != ApprovalRequest.PENDING:
            return Response({"error": "This request is no longer pending."}, status=status.HTTP_400_BAD_REQUEST)

        if action == "cancel":
            if str(request.user.id) != str(req.requested_by_id):
                return Response({"error": "Only the requester can cancel."}, status=status.HTTP_403_FORBIDDEN)
            req.status = ApprovalRequest.CANCELLED
            req.save(update_fields=["status", "updated_at"])
            return Response(ApprovalRequestSerializer(req).data, status=status.HTTP_200_OK)

        if not self._is_approver(req, request.user):
            return Response({"error": "You are not permitted to decide this request."}, status=status.HTTP_403_FORBIDDEN)

        if action == "approve":
            apply_approved_request(req, request.user)
            from plane.workflow.notify import notify_approval_decided
            notify_approval_decided(req)
            return Response(ApprovalRequestSerializer(req).data, status=status.HTTP_200_OK)

        if action == "reject":
            req.status = ApprovalRequest.REJECTED
            req.decided_by = request.user
            req.decided_at = timezone.now()
            req.decision_reason = request.data.get("reason")
            req.save(update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"])
            from plane.workflow.notify import notify_approval_decided
            notify_approval_decided(req)
            return Response(ApprovalRequestSerializer(req).data, status=status.HTTP_200_OK)

        return Response({"error": "Unknown action."}, status=status.HTTP_400_BAD_REQUEST)
```

- [ ] **Step 5: URLs**

`apps/api/plane/app/urls/approval.py`:

```python
from django.urls import path
from plane.app.views.approval import ApprovalRequestListCreateEndpoint, ApprovalRequestActionEndpoint

urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/approval-requests/",
        ApprovalRequestListCreateEndpoint.as_view(),
        name="approval-requests",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/approval-requests/<uuid:pk>/<str:action>/",
        ApprovalRequestActionEndpoint.as_view(),
        name="approval-request-action",
    ),
]
```

Include it where `urls/workflow.py` is included (find `urls/workflow` in `apps/api/plane/app/urls/__init__.py` and add `approval` the same way).

- [ ] **Step 6: Provide a no-op `notify` module so imports resolve**

Create `apps/api/plane/workflow/notify.py` with real implementations in Task 8; for now stub so Task 7 tests pass:

```python
def notify_approval_requested(req):  # implemented in Task 8
    pass


def notify_approval_decided(req):  # implemented in Task 8
    pass
```

- [ ] **Step 7: Run to verify the contract tests pass**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/contract/app/test_approval_requests_app.py -p no:cacheprovider --create-db -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/api/plane/app/serializers/approval.py apps/api/plane/app/views/approval.py apps/api/plane/app/urls/approval.py apps/api/plane/app/urls/__init__.py apps/api/plane/workflow/notify.py apps/api/plane/tests/contract/app/test_approval_requests_app.py
git commit -m "feat(workflow): approval request lifecycle endpoints (create/approve/reject/cancel)"
```

---

## Task 8: Config API for guardians/approvers/forbidden, notifications, coverage

**Files:**

- Modify: `apps/api/plane/app/views/workflow.py`, `apps/api/plane/app/urls/workflow.py`, `apps/api/plane/app/serializers/workflow.py`
- Modify: `apps/api/plane/workflow/notify.py`
- Modify: `apps/api/plane/tests/contract/app/test_workflow_config_app.py`, `apps/api/plane/tests/contract/app/test_workflow_coverage.py`
- Create: `apps/api/plane/tests/contract/app/test_workflow_state_guardians_app.py`

- [ ] **Step 1: Write failing tests for guardians + project approvers + transition kind**

`apps/api/plane/tests/contract/app/test_workflow_state_guardians_app.py`:

```python
import pytest
from rest_framework import status
from plane.db.models import WorkflowStateGuardian


def guardians_url(slug, pid, state_id):
    return f"/api/workspaces/{slug}/projects/{pid}/workflow-states/{state_id}/guardians/"


@pytest.mark.contract
@pytest.mark.django_db
def test_add_and_remove_state_guardian(session_client, workspace, project, states, member_user):
    _, done = states
    add = session_client.post(guardians_url(workspace.slug, project.id, done.id), {"guardian_id": str(member_user.id)}, format="json")
    assert add.status_code == status.HTTP_201_CREATED
    assert WorkflowStateGuardian.objects.filter(state=done, guardian=member_user).exists()
    rm = session_client.delete(guardians_url(workspace.slug, project.id, done.id) + f"{member_user.id}/")
    assert rm.status_code == status.HTTP_204_NO_CONTENT
```

Add a transition-kind test to `test_workflow_config_app.py`:

```python
@pytest.mark.contract
@pytest.mark.django_db
def test_create_forbidden_transition(session_client, workspace, project, states):
    backlog, done = states
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/workflow-transitions/"
    res = session_client.post(url, {"state": str(backlog.id), "transition_state": str(done.id), "kind": "forbidden"}, format="json")
    assert res.status_code == status.HTTP_201_CREATED
    assert res.json()["kind"] == "forbidden"
```

> Remove the now-obsolete approver-endpoint tests in `test_workflow_config_app.py` (they target `WorkflowTransitionApprover`, dropped in Task 1).

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/contract/app/test_workflow_state_guardians_app.py plane/tests/contract/app/test_workflow_config_app.py -p no:cacheprovider --create-db -q`
Expected: FAIL (no guardians route; serializer lacks `kind`).

- [ ] **Step 3: Implement config API changes**

In `apps/api/plane/app/serializers/workflow.py`: drop `approvers` from `WorkflowTransitionSerializer`, add `kind` to its `fields`. Add:

```python
class WorkflowStateGuardianSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowStateGuardian
        fields = ["id", "state", "guardian"]


class WorkflowProjectApproverSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowProjectApprover
        fields = ["id", "approver"]
```

In `apps/api/plane/app/views/workflow.py`:

- Delete `WorkflowTransitionApproverEndpoint`.
- In `WorkflowTransitionEndpoint.post`, read `kind` (default `"allowed"`, validate in `{"allowed","forbidden"}`) and pass to `WorkflowTransition.objects.create(...)`.
- Add `WorkflowStateGuardianEndpoint` (GET list / POST `guardian_id` → validate active project member → `get_or_create` / DELETE by `guardian_id`), mirroring the (now-removed) approver endpoint's validation, but on `WorkflowStateGuardian` keyed by `state_id`.
- Add `WorkflowProjectApproverEndpoint` (GET / POST `approver_id` / DELETE `approver_id`) at project scope.

In `apps/api/plane/app/urls/workflow.py`: remove the two approver routes; add:

```python
path("workspaces/<str:slug>/projects/<uuid:project_id>/workflow-states/<uuid:state_id>/guardians/", WorkflowStateGuardianEndpoint.as_view(), name="workflow-state-guardians"),
path("workspaces/<str:slug>/projects/<uuid:project_id>/workflow-states/<uuid:state_id>/guardians/<uuid:guardian_id>/", WorkflowStateGuardianEndpoint.as_view(), name="workflow-state-guardian-detail"),
path("workspaces/<str:slug>/projects/<uuid:project_id>/workflow/approvers/", WorkflowProjectApproverEndpoint.as_view(), name="workflow-project-approvers"),
path("workspaces/<str:slug>/projects/<uuid:project_id>/workflow/approvers/<uuid:approver_id>/", WorkflowProjectApproverEndpoint.as_view(), name="workflow-project-approver-detail"),
```

- [ ] **Step 4: Implement notifications**

`apps/api/plane/workflow/notify.py` (use the `Notification` model directly; resolve approvers; never notify the requester as an approver):

```python
from plane.db.models import Notification, ProjectMember
from plane.workflow.approvers import resolve_approver_ids


def _receiver_ids(req):
    approvers = resolve_approver_ids(req.project_id, req.to_state_id)
    if not approvers:
        # four-eyes: any active project member except the requester
        approvers = [
            str(m.member_id)
            for m in ProjectMember.objects.filter(project_id=req.project_id, is_active=True)
        ]
    return [a for a in approvers if str(a) != str(req.requested_by_id)]


def notify_approval_requested(req):
    issue = req.work_item
    rows = [
        Notification(
            workspace_id=req.workspace_id, project_id=req.project_id,
            entity_identifier=issue.id, entity_name="issue",
            title="Approval requested",
            sender="in_app:workflow:approval_requested",
            triggered_by_id=req.requested_by_id, receiver_id=rid,
            data={"approval_request": str(req.id), "to_state": str(req.to_state_id)},
        )
        for rid in _receiver_ids(req)
    ]
    Notification.objects.bulk_create(rows, batch_size=100)


def notify_approval_decided(req):
    Notification.objects.create(
        workspace_id=req.workspace_id, project_id=req.project_id,
        entity_identifier=req.work_item_id, entity_name="issue",
        title=f"Approval {req.status}",
        sender="in_app:workflow:approval_decided",
        triggered_by_id=req.decided_by_id, receiver_id=req.requested_by_id,
        data={"approval_request": str(req.id), "status": req.status},
    )
```

> Confirm `Notification` required fields (`title`, `message`, `sender`, `receiver`) against the model; fill any non-null fields the model demands (e.g. `message`/`message_html` may need a value).

- [ ] **Step 5: Update the coverage registry**

In `apps/api/plane/tests/contract/app/test_workflow_coverage.py`, update the documented decision codes to include `WORKFLOW_TRANSITION_NEEDS_APPROVAL` and `WORKFLOW_TRANSITION_FORBIDDEN`, and assert the same contract is emitted by REST partial_update, draft→issue, public API (`api/views/issue.py` — confirm/добавь enforcement if missing, per recon note), and GraphQL `updateIssueV2`/`updateEpic`.

- [ ] **Step 6: Run the full workflow suite**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow plane/tests/unit/models/test_workflow_models.py plane/tests/contract/app/test_workflow_config_app.py plane/tests/contract/app/test_workflow_state_guardians_app.py plane/tests/contract/app/test_approval_requests_app.py plane/tests/contract/app/test_workflow_coverage.py plane/tests/contract/app/test_workflow_enforcement_app.py plane/tests/contract/app/test_workflow_enforcement_paths_app.py plane/tests/contract/app/test_workflow_graphql.py -p no:cacheprovider --create-db -q`
Expected: PASS (fix any enforcement-path/GraphQL tests that asserted the old `…_BLOCKED` off-path behaviour to the new codes).

- [ ] **Step 7: Commit**

```bash
git add apps/api/plane/app/views/workflow.py apps/api/plane/app/urls/workflow.py apps/api/plane/app/serializers/workflow.py apps/api/plane/workflow/notify.py apps/api/plane/tests/contract/app/
git commit -m "feat(workflow): state guardians + project approvers + forbidden config, approval notifications, coverage"
```

---

## Self-review (done while writing)

- **Spec coverage:** model (T1), approver chain (T2), guard matrix (T3), error contract incl. GraphQL (T4), apply-on-approve + bypass (T5), auto-cancel (T6), request lifecycle endpoints + visibility/permissions (T7), config + notifications + coverage (T8). Frontend (settings redesign, on-item panel) and GraphQL request/approve parity are **out of scope** for this Phase-1 plan (separate plans, per the spec's phasing).
- **Known follow-ups for the engineer:** confirm the public-API enforcement врезка exists (`api/views/issue.py`) — recon could not find it; confirm `Issue` state FK field name for `save(update_fields=["state"])`; confirm `Notification` non-null fields; adapt `conftest` fixture names (`session_user`, `create_user`, `create_issue`).
- **Type consistency:** `resolve_approver_ids(project_id, to_state_id)` used identically in guard, endpoints, notify. `ApprovalRequest.PENDING/APPROVED/...` constants used consistently. `workflow_bypass` defined in `signals.py`, imported in `apply.py`.
