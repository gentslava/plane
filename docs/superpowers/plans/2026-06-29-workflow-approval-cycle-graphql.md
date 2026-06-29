# Workflow Approval Cycle — Phase 3 (GraphQL A+B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the workflow approval cycle on the GraphQL gateway — full request/decide/list parity (A) plus native-friendly auto-request on off-path transitions (B).

**Architecture:** Extract the request-create + decide logic out of the REST views into `plane/workflow/service.py` (single source of truth, raising a domain `ApprovalError`). REST views and new GraphQL resolvers both call it. GraphQL transitions (`updateIssueV2`/`updateEpic`) use a new `guard_transition_or_request` helper that auto-files a request on off-path and raises an informative `GraphQLError`. The gateway has no `ATOMIC_REQUESTS`/`transaction.atomic`, so the request commits before the error is raised.

**Tech Stack:** Django/DRF, ariadne schema-first GraphQL, Docker pytest (`docker compose -f docker-compose-test.yml run --rm api-tests pytest <path> -p no:cacheprovider --create-db -q`).

**Branch:** `feature/workflow-approval`.

---

## Reference — current behaviour to preserve (REST `app/views/approval.py`)

Exact error strings/statuses the service must reproduce so `test_approval_requests_app.py` stays green:

- request: `"to_state is required"` (400, kept in the REST view), `"Invalid target state for this project."` (400), `"This move does not require an approval request."` (400), `"There is already a pending approval request for this work item."` (400); success → 201.
- decide: `"This request is no longer pending."` (400), `"Only the requester can cancel."` (403), `"You are not permitted to decide this request."` (403), `"Unknown action."` (400); success → 200.

`_is_approver(req, user)`: if `resolve_approver_ids(project, to_state)` non-empty → `user.id in approvers and user.id != requested_by`; else (four-eyes) → `user.id != requested_by`.

`ApprovalRequest` constants: `PENDING APPROVED REJECTED CANCELLED`. Fields: `work_item, from_state, to_state, requested_by, comment, status, decided_by, decided_at, decision_reason, project, workspace, created_at, updated_at`.

---

## Task 1: Extract approval service (`plane/workflow/service.py`)

Behaviour-preserving extraction. The existing `test_approval_requests_app.py` is the guard.

**Files:**

- Create: `apps/api/plane/workflow/service.py`
- Modify: `apps/api/plane/app/views/approval.py` (delegate to the service)
- Test (existing, must stay green): `apps/api/plane/tests/contract/app/test_approval_requests_app.py`

- [ ] **Step 1: Create the service module**

```python
# apps/api/plane/workflow/service.py
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Approval-request domain service — the single source of truth shared by the
REST views (plane/app/views/approval.py) and the GraphQL resolvers
(plane/graphql/areas/workflow_approval.py)."""

from django.utils import timezone

from plane.db.models import ApprovalRequest, State
from plane.utils.exception_logger import log_exception
from plane.workflow.apply import apply_approved_request
from plane.workflow.approvers import resolve_approver_ids
from plane.workflow.guard import Action, evaluate_transition
from plane.workflow.notify import notify_approval_decided, notify_approval_requested


class ApprovalError(Exception):
    """Domain error carrying a stable code, a human message, an HTTP status, and
    (for duplicate-pending) the pre-existing request."""

    def __init__(self, code, message, status, existing=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.existing = existing


def is_approver(req, user):
    """Whether `user` may decide `req` (four-eyes aware; never the requester)."""
    approvers = resolve_approver_ids(req.project_id, req.to_state_id)
    if approvers:
        return str(user.id) in approvers and str(user.id) != str(req.requested_by_id)
    return str(user.id) != str(req.requested_by_id)


def create_approval_request(actor, work_item, to_state_id, comment=None):
    """Validate and create a PENDING approval request for an off-path move of
    `work_item` to `to_state_id`. Raises ApprovalError on any rule violation."""
    if not State.objects.filter(pk=to_state_id, project_id=work_item.project_id).exists():
        raise ApprovalError(
            "APPROVAL_INVALID_STATE", "Invalid target state for this project.", 400
        )

    decision = evaluate_transition(
        work_item.project_id, work_item.state_id, to_state_id, actor
    )
    if decision.action != Action.NEEDS_APPROVAL:
        # ALLOW => requester may move directly; BLOCK => forbidden, request is moot.
        raise ApprovalError(
            "APPROVAL_NOT_REQUIRED",
            "This move does not require an approval request.",
            400,
        )

    existing = ApprovalRequest.objects.filter(
        work_item_id=work_item.id, status=ApprovalRequest.PENDING
    ).first()
    if existing is not None:
        raise ApprovalError(
            "APPROVAL_DUPLICATE_PENDING",
            "There is already a pending approval request for this work item.",
            400,
            existing=existing,
        )

    req = ApprovalRequest.objects.create(
        project_id=work_item.project_id,
        workspace=work_item.workspace,
        work_item=work_item,
        from_state_id=work_item.state_id,
        to_state_id=to_state_id,
        requested_by=actor,
        comment=comment,
    )
    try:
        notify_approval_requested(req)
    except Exception as e:
        log_exception(e)
    return req


def decide_approval_request(actor, req, action, reason=None):
    """Apply a decision (approve/reject/cancel) to a PENDING request.
    Mutates and returns `req`. Raises ApprovalError on rule/permission violation."""
    if req.status != ApprovalRequest.PENDING:
        raise ApprovalError(
            "APPROVAL_NOT_PENDING", "This request is no longer pending.", 400
        )

    if action == "cancel":
        if str(actor.id) != str(req.requested_by_id):
            raise ApprovalError(
                "APPROVAL_NOT_REQUESTER", "Only the requester can cancel.", 403
            )
        req.status = ApprovalRequest.CANCELLED
        req.save(update_fields=["status", "updated_at"])
        return req

    if not is_approver(req, actor):
        raise ApprovalError(
            "APPROVAL_NOT_APPROVER",
            "You are not permitted to decide this request.",
            403,
        )

    if action == "approve":
        apply_approved_request(req, actor)  # also marks APPROVED + decided_by/at
        try:
            notify_approval_decided(req)
        except Exception as e:
            log_exception(e)
        return req

    if action == "reject":
        req.status = ApprovalRequest.REJECTED
        req.decided_by = actor
        req.decided_at = timezone.now()
        req.decision_reason = reason
        req.save(
            update_fields=[
                "status", "decided_by", "decided_at", "decision_reason", "updated_at",
            ]
        )
        try:
            notify_approval_decided(req)
        except Exception as e:
            log_exception(e)
        return req

    raise ApprovalError("APPROVAL_UNKNOWN_ACTION", "Unknown action.", 400)
```

- [ ] **Step 2: Refactor the REST views to delegate**

Rewrite `apps/api/plane/app/views/approval.py` so the endpoints become thin wrappers. Keep the `"to_state is required"` presence check in the POST (request parsing). Map `ApprovalError` to `Response({"error": e.message}, status=e.status)`.

```python
# imports
from plane.workflow.service import ApprovalError, create_approval_request, decide_approval_request
# (drop the now-unused apply/approvers/guard/notify/timezone imports that moved into the service)

# POST (list-create):
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = Issue.objects.get(pk=issue_id, project_id=project_id, workspace__slug=slug)
        to_state_id = request.data.get("to_state")
        if not to_state_id:
            return Response({"error": "to_state is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            req = create_approval_request(request.user, issue, to_state_id, comment=request.data.get("comment"))
        except ApprovalError as e:
            return Response({"error": e.message}, status=e.status)
        return Response(ApprovalRequestSerializer(req).data, status=status.HTTP_201_CREATED)

# action endpoint:
    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id, pk, action):
        req = self._request(project_id, issue_id, pk)
        try:
            decide_approval_request(request.user, req, action, reason=request.data.get("reason"))
        except ApprovalError as e:
            return Response({"error": e.message}, status=e.status)
        return Response(ApprovalRequestSerializer(req).data, status=status.HTTP_200_OK)
```

Keep the GET list endpoint unchanged (it stays unfiltered — REST behaviour preserved). Remove `_is_approver` from the view (moved to the service).

- [ ] **Step 3: Run the existing contract tests — must stay green**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/contract/app/test_approval_requests_app.py -p no:cacheprovider --create-db -q`
Expected: all pass (no behaviour change). If the suite asserted exact strings, they are reproduced verbatim.

- [ ] **Step 4: Commit**

```bash
git add apps/api/plane/workflow/service.py apps/api/plane/app/views/approval.py
git commit -m "refactor(workflow): extract approval-request service shared by REST + GraphQL"
```

---

## Task 2: GraphQL A — request/decide/list operations

**Files:**

- Modify: `apps/api/plane/graphql/schema.graphql` (new type + 4 mutations + 1 query)
- Create: `apps/api/plane/graphql/areas/workflow_approval.py` (resolvers + ObjectType)
- Modify: `apps/api/plane/graphql/areas/__init__.py` (register BINDABLES)
- Modify: `apps/api/plane/workflow/service.py` (add `visible_approval_requests`)
- Test: `apps/api/plane/tests/contract/app/test_workflow_approval_graphql.py` (new)

- [ ] **Step 1: Add the visibility query helper to the service**

```python
# append to apps/api/plane/workflow/service.py
def visible_approval_requests(actor, project_id, work_item_id=None, status="PENDING"):
    """Requests in `project_id` the actor may see/act on: their own or ones they
    can decide. Optionally narrowed to a work item and/or status (default PENDING)."""
    qs = ApprovalRequest.objects.filter(project_id=project_id)
    if work_item_id:
        qs = qs.filter(work_item_id=work_item_id)
    if status:
        qs = qs.filter(status=status)
    return [
        req
        for req in qs.order_by("-created_at")
        if str(req.requested_by_id) == str(actor.id) or is_approver(req, actor)
    ]
```

- [ ] **Step 2: Add SDL to `schema.graphql`**

Add the type near other workflow/issue types; add the fields inside the existing `type Query { ... }` and `type Mutation { ... }` blocks:

```graphql
type WorkflowApprovalRequestType {
  id: ID!
  workspace: ID!
  project: ID!
  workItem: ID!
  fromState: ID!
  toState: ID!
  requestedBy: ID!
  comment: String
  status: String!
  decidedBy: ID
  decidedAt: DateTime
  decisionReason: String
  createdAt: DateTime!
  updatedAt: DateTime!
}
```

In `type Query`:

```graphql
  workflowApprovalRequests(slug: String!, project: String!, workItem: String = null, status: String = "PENDING"): [WorkflowApprovalRequestType!]!
```

In `type Mutation`:

```graphql
  requestWorkflowApproval(slug: String!, project: String!, workItem: String!, toState: String!, comment: String = null): WorkflowApprovalRequestType!
  approveWorkflowRequest(slug: String!, project: String!, request: String!, reason: String = null): WorkflowApprovalRequestType!
  rejectWorkflowRequest(slug: String!, project: String!, request: String!, reason: String = null): WorkflowApprovalRequestType!
  cancelWorkflowRequest(slug: String!, project: String!, request: String!): WorkflowApprovalRequestType!
```

- [ ] **Step 3: Create the resolver area module**

```python
# apps/api/plane/graphql/areas/workflow_approval.py
# OVERLAY: mobile-graphql — workflow approval-request lifecycle (A).
from ariadne import MutationType, ObjectType, QueryType
from graphql import GraphQLError

from plane.db.models import ApprovalRequest, Issue
from plane.graphql.context import get_user
from plane.graphql.resolvers import _member_project
from plane.workflow.service import (
    ApprovalError,
    create_approval_request,
    decide_approval_request,
    visible_approval_requests,
)

query = QueryType()
mutation = MutationType()
approval_type = ObjectType("WorkflowApprovalRequestType")


def _raise(e: ApprovalError):
    raise GraphQLError(e.message, extensions={"code": e.code})


@mutation.field("requestWorkflowApproval")
def resolve_request_workflow_approval(_, info, slug, project, workItem, toState, comment=None):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    issue = Issue.objects.filter(project=p, id=workItem).first()
    if issue is None:
        return None
    try:
        return create_approval_request(user, issue, toState, comment=comment)
    except ApprovalError as e:
        _raise(e)


def _decide(info, slug, project, request, action, reason=None):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    req = ApprovalRequest.objects.filter(project=p, id=request).first()
    if req is None:
        return None
    try:
        return decide_approval_request(user, req, action, reason=reason)
    except ApprovalError as e:
        _raise(e)


@mutation.field("approveWorkflowRequest")
def resolve_approve_workflow_request(_, info, slug, project, request, reason=None):
    return _decide(info, slug, project, request, "approve", reason)


@mutation.field("rejectWorkflowRequest")
def resolve_reject_workflow_request(_, info, slug, project, request, reason=None):
    return _decide(info, slug, project, request, "reject", reason)


@mutation.field("cancelWorkflowRequest")
def resolve_cancel_workflow_request(_, info, slug, project, request):
    return _decide(info, slug, project, request, "cancel")


@query.field("workflowApprovalRequests")
def resolve_workflow_approval_requests(_, info, slug, project, workItem=None, status="PENDING"):
    user = get_user(info)
    if user is None:
        return []
    p = _member_project(info, slug, project)
    if p is None:
        return []
    return visible_approval_requests(user, p.id, work_item_id=workItem, status=status)


# FK fields → id strings (do not rely on smart fallback for FK-as-ID).
@approval_type.field("workItem")
def _r_work_item(obj, info):
    return str(obj.work_item_id)


@approval_type.field("fromState")
def _r_from_state(obj, info):
    return str(obj.from_state_id)


@approval_type.field("toState")
def _r_to_state(obj, info):
    return str(obj.to_state_id)


@approval_type.field("requestedBy")
def _r_requested_by(obj, info):
    return str(obj.requested_by_id)


@approval_type.field("decidedBy")
def _r_decided_by(obj, info):
    return str(obj.decided_by_id) if obj.decided_by_id else None


@approval_type.field("project")
def _r_project(obj, info):
    return str(obj.project_id)


@approval_type.field("workspace")
def _r_workspace(obj, info):
    return str(obj.workspace_id)


BINDABLES = [query, mutation, approval_type]
```

- [ ] **Step 4: Register the area**

In `apps/api/plane/graphql/areas/__init__.py` add `from .workflow_approval import BINDABLES as _workflow_approval` and append `*_workflow_approval,` to the `BINDABLES` list.

- [ ] **Step 5: Write the contract tests (direct-resolver pattern)**

`apps/api/plane/tests/contract/app/test_workflow_approval_graphql.py`. Reuse the fixture style from `test_workflow_graphql.py` (`_info`, `workspace`, `project`, `states`, `workflow_on`, `existing_issue`). Add a **second** user that is BOTH a `WorkspaceMember(is_active=True)` and a `ProjectMember(is_active=True)` — `_member_project` requires the active workspace membership, and four-eyes/`resolve_approver_ids` requires the active project membership. (A third such user is needed only for the visibility-exclusion case.)

Cases (each `@pytest.mark.contract @pytest.mark.django_db`):

1. `requestWorkflowApproval` happy: workflow on, off-path `backlog→done`, requester not approver → returns a request, `ApprovalRequest.objects.filter(work_item=issue, status=PENDING).count() == 1`.
2. `requestWorkflowApproval` duplicate: a second call raises `GraphQLError` with code `APPROVAL_DUPLICATE_PENDING`.
3. `requestWorkflowApproval` not-required: with workflow OFF (move is ALLOW) → raises `GraphQLError` code `APPROVAL_NOT_REQUIRED`.
4. `approveWorkflowRequest` by a _different_ member → request `status == APPROVED` and `issue.state_id == done.id` (applied).
5. `approveWorkflowRequest` by the requester → raises `GraphQLError` code `APPROVAL_NOT_APPROVER` (self-approval blocked).
6. `cancelWorkflowRequest` by the requester → `status == CANCELLED`; by a non-requester → `APPROVAL_NOT_REQUESTER`.
7. `rejectWorkflowRequest` by a different member → `status == REJECTED`, issue state unchanged.
8. `workflowApprovalRequests` visibility: a PENDING request is returned for the requester and for an eligible approver, and is **excluded** for an unrelated third member (no guardians/project-approvers set ⇒ four-eyes, so any _other_ member is an approver — to assert exclusion, set a specific guardian via `WorkflowStateGuardian` so the third member is neither requester nor approver and gets an empty list).

Each mutation test calls the resolver function directly, e.g.:

```python
from plane.graphql.areas.workflow_approval import (
    resolve_request_workflow_approval, resolve_approve_workflow_request,
    resolve_cancel_workflow_request, resolve_reject_workflow_request,
    resolve_workflow_approval_requests,
)
req = resolve_request_workflow_approval(
    None, _info(user), slug=workspace.slug, project=str(project.id),
    workItem=str(existing_issue.id), toState=str(done.id), comment=None,
)
```

- [ ] **Step 6: Run the new tests**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/contract/app/test_workflow_approval_graphql.py -p no:cacheprovider --create-db -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/plane/graphql/schema.graphql apps/api/plane/graphql/areas/workflow_approval.py apps/api/plane/graphql/areas/__init__.py apps/api/plane/workflow/service.py apps/api/plane/tests/contract/app/test_workflow_approval_graphql.py
git commit -m "feat(workflow-graphql): request/approve/reject/cancel/list approval requests (Phase 3 A)"
```

---

## Task 3: GraphQL B — auto-request on off-path transitions

**Files:**

- Modify: `apps/api/plane/graphql/workflow.py` (new `guard_transition_or_request`)
- Modify: `apps/api/plane/graphql/mutations/work_items.py:301` (use it in `updateIssueV2`)
- Modify: `apps/api/plane/graphql/mutations/planning.py:320` (use it in `updateEpic`)
- Test: `apps/api/plane/tests/contract/app/test_workflow_graphql.py` (update 2 cases + add B cases)

- [ ] **Step 1: Add the helper**

```python
# append to apps/api/plane/graphql/workflow.py
from plane.workflow.service import ApprovalError, create_approval_request


def guard_transition_or_request(project_id, from_state_id, to_state_id, actor, work_item):
    """Like guard_transition, but on an off-path move (NEEDS_APPROVAL) auto-files
    an approval request and raises a WORKFLOW_APPROVAL_REQUESTED GraphQLError so a
    native client (no Request-approval button) requests just by attempting the move.
    FORBIDDEN stays a hard block; ALLOW returns and the move proceeds."""
    if to_state_id is None or from_state_id is None or str(from_state_id) == str(to_state_id):
        return
    decision = evaluate_transition(project_id, from_state_id, to_state_id, actor)
    if decision.action == Action.ALLOW:
        return
    if decision.action == Action.NEEDS_APPROVAL:
        try:
            req = create_approval_request(actor, work_item, to_state_id)
            message = "This status change needs approval; a request has been submitted."
        except ApprovalError as e:
            if e.code == "APPROVAL_DUPLICATE_PENDING" and e.existing is not None:
                req = e.existing
                message = "This work item is already pending approval."
            else:
                raise GraphQLError(e.message, extensions={"code": e.code})
        raise GraphQLError(
            message,
            extensions={
                "code": "WORKFLOW_APPROVAL_REQUESTED",
                "requestId": str(req.id),
                "context": decision.context,
            },
        )
    # FORBIDDEN / any other block
    raise GraphQLError(decision.message, extensions={"code": decision.code, "context": decision.context})
```

(Adjust the existing imports at the top of `workflow.py`: it already imports `Action, evaluate_creation, evaluate_transition` and `GraphQLError`.)

- [ ] **Step 2: Use it in `updateIssueV2`**

In `apps/api/plane/graphql/mutations/work_items.py`, replace the `guard_transition(...)` call (~line 301) with `guard_transition_or_request(p.id, issue.state_id, new_state.id, user, issue)`. Update the import to add `guard_transition_or_request`.

- [ ] **Step 3: Use it in `updateEpic`**

In `apps/api/plane/graphql/mutations/planning.py`, replace the `guard_transition(...)` call (~line 320) with `guard_transition_or_request(project, epic_obj.state_id, state.id, user, epic_obj)`. Update the import.

- [ ] **Step 4: Update the two existing off-path tests to the B contract**

In `test_workflow_graphql.py`, `test_update_issue_transition_blocked` and `test_update_epic_transition_blocked` currently assert `WORKFLOW_TRANSITION_NEEDS_APPROVAL` and no side effect. Under B the off-path attempt now also creates a request and raises `WORKFLOW_APPROVAL_REQUESTED`. Update both:

```python
err = exc_info.value
assert "WORKFLOW_APPROVAL_REQUESTED" in str(err.extensions.get("code", ""))
existing_issue.refresh_from_db()
assert existing_issue.state_id == backlog.id            # still unchanged
assert ApprovalRequest.objects.filter(                  # request was auto-filed
    work_item=existing_issue, status="PENDING"
).count() == 1
```

(Import `ApprovalRequest`; for the epic test use the epic instance.)

- [ ] **Step 5: Add B-specific cases**

Add to `test_workflow_graphql.py`:

1. **Forbidden stays hard:** create `WorkflowTransition(kind=FORBIDDEN)` for `backlog→done`; `updateIssueV2` raises `WORKFLOW_TRANSITION_FORBIDDEN` and **no** `ApprovalRequest` is created.
2. **Approver moves directly:** add a `WorkflowStateGuardian(state=done, guardian=create_user)`; `updateIssueV2` succeeds (state → done), no request created.
3. **Second attempt → already pending:** call `updateIssueV2` off-path twice; both raise `WORKFLOW_APPROVAL_REQUESTED`, exactly **one** PENDING request exists, and the second error message is the "already pending" text.

- [ ] **Step 6: Run the workflow GraphQL tests**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/contract/app/test_workflow_graphql.py -p no:cacheprovider --create-db -q`
Expected: all pass (updated + new).

- [ ] **Step 7: Commit**

```bash
git add apps/api/plane/graphql/workflow.py apps/api/plane/graphql/mutations/work_items.py apps/api/plane/graphql/mutations/planning.py apps/api/plane/tests/contract/app/test_workflow_graphql.py
git commit -m "feat(workflow-graphql): auto-file approval request on off-path mobile transition (Phase 3 B)"
```

---

## Task 4: Coverage registry — assert the GraphQL contract

**Files:**

- Modify: `apps/api/plane/tests/contract/app/test_workflow_coverage.py`

- [ ] **Step 1: Read the registry test** and find where it enumerates write paths / asserts the error contract per path. Determine what it currently asserts for the GraphQL transition path.

- [ ] **Step 2: Update/extend** so the GraphQL transition path expects `WORKFLOW_APPROVAL_REQUESTED` (B) while creation paths still expect `WORKFLOW_CREATION_BLOCKED` and forbidden transitions `WORKFLOW_TRANSITION_FORBIDDEN`. Keep the REST paths asserting `WORKFLOW_TRANSITION_NEEDS_APPROVAL` (unchanged — REST has no auto-request). If the registry is a single shared expectation, split it per surface (REST vs GraphQL).

- [ ] **Step 3: Run the full workflow suite**

Run: `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/workflow plane/tests/contract/app/test_workflow_graphql.py plane/tests/contract/app/test_workflow_approval_graphql.py plane/tests/contract/app/test_approval_requests_app.py plane/tests/contract/app/test_workflow_coverage.py -p no:cacheprovider --create-db -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add apps/api/plane/tests/contract/app/test_workflow_coverage.py
git commit -m "test(workflow): coverage registry asserts GraphQL auto-request contract"
```

---

## Final review

After all four tasks: dispatch a holistic code review across the Phase 3 diff (service extraction correctness, no behaviour drift on REST, GraphQL auth/IDOR via `_member_project`, the transactional assumption for B, no self-approval hole on the GraphQL path, SDL/resolver field-name match). Then the feature is ready for the squash-merge decision into `plus`.
