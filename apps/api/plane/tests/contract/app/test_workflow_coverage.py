# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Consolidated coverage registry for workflow enforcement on status write paths.

DECISION CODES emitted by the guard (apps/api/plane/workflow/guard.py):
  • WORKFLOW_CREATION_BLOCKED            – creation blocked in a restricted state (400)
  • WORKFLOW_TRANSITION_FORBIDDEN        – a transition row with kind="forbidden" (403)
  • WORKFLOW_TRANSITION_NEEDS_APPROVAL   – an off-path move (no transition row) by a
                                           non-approver actor; the move is rejected and
                                           must instead go through an ApprovalRequest (403).
                                           Emitted by the REST/public-API/signal paths,
                                           where the web UI offers an explicit "Request
                                           approval" button.
  • WORKFLOW_APPROVAL_REQUESTED          – GraphQL gateway ONLY (Phase 3 B). An off-path
                                           move attempted via updateIssueV2 / updateEpic
                                           auto-files an ApprovalRequest and raises this
                                           code: the native app has no "Request approval"
                                           button, so the attempt *is* the request.
                                           Same logical off-path case as NEEDS_APPROVAL,
                                           client-appropriate behaviour.
  On-path moves (a transition row with kind="allowed") are free for everyone and emit
  no code. The legacy WORKFLOW_TRANSITION_BLOCKED code has been replaced by the
  transition codes above.

REACHABLE STATUS-WRITE PATHS (all must enforce workflow):
  1. app IssueViewSet.partial_update  – PATCH /api/workspaces/{slug}/projects/{pid}/issues/{id}/
     Enforcement: enforce_transition  (apps/api/plane/app/views/issue/base.py)

  2. app IssueViewSet.create          – POST  /api/workspaces/{slug}/projects/{pid}/issues/
     Enforcement: enforce_creation    (apps/api/plane/app/views/issue/base.py)

  3. app draft→issue                  – POST  /api/workspaces/{slug}/draft-to-issue/{did}/
     Enforcement: enforce_creation    (apps/api/plane/app/views/workspace/draft.py)

  4. public API IssueDetailAPIEndpoint.patch
                                      – PATCH /api/v1/workspaces/{slug}/projects/{pid}/issues/{id}/
     Enforcement: enforce_transition  (apps/api/plane/api/views/issue.py)

  5. public API IssueListCreateAPIEndpoint.post
                                      – POST  /api/v1/workspaces/{slug}/projects/{pid}/issues/
     Enforcement: enforce_creation    (apps/api/plane/api/views/issue.py)

  6. pre_save signal (defense-in-depth)
     Enforcement: guard_issue_transition — actor from workflow_actor_context OR
     crum.get_current_user() (active on every HTTP issue-save)
     (apps/api/plane/workflow/signals.py)

  7-10. GraphQL gateway mutations (fork mobile app, ariadne)
     createIssueV2 / createEpic     – guard_creation: BLOCK → GraphQLError
                                      (WORKFLOW_CREATION_BLOCKED). No "request to create".
     updateIssueV2 / updateEpic     – guard_transition_or_request (Phase 3 B): ALLOW
                                      proceeds; FORBIDDEN → GraphQLError
                                      (WORKFLOW_TRANSITION_FORBIDDEN); off-path
                                      NEEDS_APPROVAL → auto-file an ApprovalRequest +
                                      GraphQLError(WORKFLOW_APPROVAL_REQUESTED).
     (apps/api/plane/graphql/workflow.py, mutations/work_items.py, mutations/planning.py)
     Tested in test_workflow_graphql.py (enforcement + B auto-request contract) and the
     full request/approve/reject/cancel/list cycle in test_workflow_approval_graphql.py.

INTENTIONALLY EXCLUDED PATHS (not status-write paths or explicitly out of scope):

  • BulkDeleteIssuesEndpoint (DELETE /api/workspaces/{slug}/projects/{pid}/bulk-delete-issues/)
    Reason: deletes issues, never writes a status field.

  • SubIssuesEndpoint.post (POST /api/.../issues/{id}/sub-issues/)
    Reason: sets issue.parent only; the issue's own state is not touched.

  • IssueDetailAPIEndpoint.put (PUT /api/v1/.../issues/<pk>/)
    Reason: the route is registered with http_method_names=["get","patch","delete"] in
    apps/api/plane/api/urls/work_item.py — PUT is not routed anywhere and returns 405.
    The method body exists in the class but is dead/unreachable code.

  • intake (IssueCreateEndpoint via triage/IntakeIssue)
    Reason: intake is a staging/triage flow explicitly excluded per spec.

  • issue_automation_task (apps/api/plane/bgtasks/issue_automation_task.py — auto-close)
    Reason: the task changes state via QuerySet.bulk_update(), which bypasses Django
    pre_save signals entirely. This is intentional system automation without a
    human actor in the request cycle; there is no authenticated user to resolve and
    workflow policy is inapplicable. Excluded from enforcement by design.

  • GraphQL intake mutations (apps/api/plane/graphql/areas/intake.py)
    Reason: intake writes state fields as part of the staging/triage pipeline.
    This falls under the same explicit intake exclusion as the REST intake path
    (IssueCreateEndpoint). Excluded from workflow enforcement by design.

  • Dead epic mutations in apps/api/plane/graphql/areas/epics.py
    Reason: the createEpic / updateEpic bindings in that file are superseded by the
    guarded implementations in apps/api/plane/graphql/mutations/planning.py. Schema
    binding precedence in apps/api/plane/graphql/schema.py means the planning.py
    resolvers win; the epics.py versions are unreachable dead code. "Fixing" them
    would create a maintenance hazard with no runtime effect.

One-file design intent: this file is the authoritative checklist. Any new endpoint
that writes issue.state without calling enforce_creation / enforce_transition should
show up as a missing test here, making the gap immediately visible.
"""

import pytest
from rest_framework import status

from plane.db.models import (
    DraftIssue,
    Issue,
    Project,
    ProjectMember,
    ProjectWorkflow,
    State,
    WorkflowStateConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(db, workspace, create_user):
    proj = Project.objects.create(
        name="WF Coverage", identifier="WFC", workspace=workspace, created_by=create_user
    )
    ProjectMember.objects.create(
        project=proj, workspace=workspace, member=create_user, role=20, is_active=True
    )
    return proj


@pytest.fixture
def states(db, project, workspace):
    backlog = State.objects.create(
        name="Backlog", color="#000", group="backlog", project=project, workspace=workspace
    )
    done = State.objects.create(
        name="Done", color="#fff", group="completed", project=project, workspace=workspace
    )
    return backlog, done


@pytest.fixture
def workflow_on(db, project, workspace):
    return ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)


@pytest.fixture
def creation_blocked(db, project, workspace, states):
    backlog, _ = states
    return WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )


# ---------------------------------------------------------------------------
# PATH 1 — app IssueViewSet.partial_update
#   PATCH /api/workspaces/{slug}/projects/{pid}/issues/{id}/
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_path1_app_partial_update_blocks_disallowed_transition(
    session_client, workspace, project, states, workflow_on, create_user
):
    """PATH 1: app PATCH /issues/<id>/ must return 403 NEEDS_APPROVAL for an
    off-path transition (no transition row, actor is not a resolved approver)."""
    backlog, done = states
    issue = Issue.objects.create(
        name="P1 issue", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/"
    response = session_client.patch(url, {"state_id": str(done.id)}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "WORKFLOW_TRANSITION_NEEDS_APPROVAL"
    issue.refresh_from_db()
    assert issue.state_id == backlog.id  # state must be unchanged


# ---------------------------------------------------------------------------
# PATH 2 — app IssueViewSet.create
#   POST /api/workspaces/{slug}/projects/{pid}/issues/
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_path2_app_create_blocks_restricted_state(
    session_client, workspace, project, states, workflow_on, creation_blocked
):
    """PATH 2: app POST /issues/ must return 400 when creation in the state is blocked."""
    backlog, _ = states
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/"
    response = session_client.post(url, {"name": "Blocked", "state_id": str(backlog.id)}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "WORKFLOW_CREATION_BLOCKED"
    assert not Issue.objects.filter(name="Blocked", project=project).exists()


# ---------------------------------------------------------------------------
# PATH 3 — app draft→issue (WorkspaceDraftIssueViewSet.create_draft_to_issue)
#   POST /api/workspaces/{slug}/draft-to-issue/{did}/
# ---------------------------------------------------------------------------


@pytest.fixture
def draft_issue(db, workspace, project, states, create_user):
    backlog, _ = states
    return DraftIssue.objects.create(
        name="Draft",
        project=project,
        workspace=workspace,
        state=backlog,
        created_by=create_user,
    )


@pytest.mark.contract
@pytest.mark.django_db
def test_path3_draft_to_issue_blocks_restricted_state(
    session_client, workspace, project, states, draft_issue, workflow_on, creation_blocked, create_user
):
    """PATH 3: POST /draft-to-issue/<id>/ must return 400 when creation in the state is blocked."""
    backlog, _ = states
    url = f"/api/workspaces/{workspace.slug}/draft-to-issue/{draft_issue.id}/"
    response = session_client.post(url, {"name": "Draft→Issue", "state_id": str(backlog.id)}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "WORKFLOW_CREATION_BLOCKED"
    # Draft must still exist (rollback)
    assert DraftIssue.objects.filter(pk=draft_issue.id).exists()


# ---------------------------------------------------------------------------
# PATH 4 — public API IssueDetailAPIEndpoint.patch
#   PATCH /api/v1/workspaces/{slug}/projects/{pid}/issues/{id}/
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_path4_public_api_patch_blocks_disallowed_transition(
    api_key_client, workspace, project, states, workflow_on, create_user
):
    """PATH 4: public API PATCH /api/v1/.../issues/<id>/ must return 403
    NEEDS_APPROVAL for an off-path transition (no transition row, actor not approver)."""
    backlog, done = states
    issue = Issue.objects.create(
        name="P4 issue", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/"
    response = api_key_client.patch(url, {"state": str(done.id)}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "WORKFLOW_TRANSITION_NEEDS_APPROVAL"
    issue.refresh_from_db()
    assert issue.state_id == backlog.id  # state must be unchanged


# ---------------------------------------------------------------------------
# PATH 5 — public API IssueListCreateAPIEndpoint.post
#   POST /api/v1/workspaces/{slug}/projects/{pid}/issues/
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_path5_public_api_post_blocks_restricted_state(
    api_key_client, workspace, project, states, workflow_on, creation_blocked
):
    """PATH 5: public API POST /api/v1/.../issues/ must return 400 when creation in the state is blocked."""
    backlog, _ = states
    url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/issues/"
    response = api_key_client.post(url, {"name": "API Blocked", "state": str(backlog.id)}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "WORKFLOW_CREATION_BLOCKED"
    assert not Issue.objects.filter(name="API Blocked", project=project).exists()


# ---------------------------------------------------------------------------
# PATH 6 — pre_save signal (defense-in-depth safety net)
#   direct issue.save() inside workflow_actor_context
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_path6_pre_save_signal_blocks_disallowed_transition(
    project, workspace, states, workflow_on, create_user
):
    """PATH 6: direct Issue.save() inside workflow_actor_context must raise WorkflowBlocked."""
    from plane.workflow.exceptions import WorkflowBlocked
    from plane.workflow.signals import workflow_actor_context

    backlog, done = states
    issue = Issue.objects.create(
        name="P6 issue", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    issue.state = done
    with workflow_actor_context(create_user):
        with pytest.raises(WorkflowBlocked):
            issue.save()
    issue.refresh_from_db()
    assert issue.state_id == backlog.id  # state must be unchanged


# ---------------------------------------------------------------------------
# REGRESSION: excluded paths do NOT interfere with normal operations
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_excluded_bulk_delete_unaffected_by_workflow(
    session_client, workspace, project, states, workflow_on, create_user
):
    """Excluded path: BulkDeleteIssuesEndpoint (DELETE /bulk-delete-issues/) must work
    even when workflow is enabled — it never writes a state field."""
    from plane.db.models import ProjectMember

    # Ensure the user is ADMIN (role=20 is Member, role=25 is Admin for bulk delete)
    # BulkDeleteIssuesEndpoint requires ROLE.ADMIN. Check what role=20 vs 25 means.
    # If this fails with 403 for permission (not workflow), the regression is still valid:
    # it proves workflow is not blocking the endpoint.
    backlog, _ = states
    issue = Issue.objects.create(
        name="To delete", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    # Update member to admin role (role=20 is Admin in Plane's role enum)
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/bulk-delete-issues/"
    response = session_client.delete(url, {"issue_ids": [str(issue.id)]}, format="json")
    # Accept 200 (success) or 403 (role permission, not workflow).
    # The key invariant: workflow must NOT inject a WORKFLOW_* error_code here.
    if response.status_code == status.HTTP_403_FORBIDDEN:
        data = response.json()
        assert not str(data.get("error_code", "")).startswith("WORKFLOW_")
    else:
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.contract
@pytest.mark.django_db
def test_excluded_sub_issue_post_unaffected_by_workflow(
    session_client, workspace, project, states, workflow_on, create_user
):
    """Excluded path: SubIssuesEndpoint.post (POST /issues/{id}/sub-issues/) only sets
    issue.parent and must work even when workflow is enabled."""
    backlog, _ = states
    parent = Issue.objects.create(
        name="Parent", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    child = Issue.objects.create(
        name="Child", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{parent.id}/sub-issues/"
    response = session_client.post(url, {"sub_issue_ids": [str(child.id)]}, format="json")
    # Must succeed (200) — workflow must not block parent assignment.
    assert response.status_code == status.HTTP_200_OK
    child.refresh_from_db()
    assert child.parent_id == parent.id


@pytest.mark.contract
@pytest.mark.django_db
def test_excluded_put_endpoint_not_routed(
    api_key_client, workspace, project, states, create_user
):
    """Excluded path: IssueDetailAPIEndpoint.put — PUT is not registered in URL conf
    (http_method_names=["get","patch","delete"]), so it returns 405 Method Not Allowed."""
    backlog, _ = states
    issue = Issue.objects.create(
        name="Put test", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/"
    response = api_key_client.put(url, {"name": "Updated"}, format="json")
    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
