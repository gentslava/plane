# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid

import pytest
from rest_framework import status

from plane.db.models import (
    ApprovalRequest,
    Issue,
    Project,
    ProjectMember,
    ProjectWorkflow,
    State,
    User,
    WorkflowStateGuardian,
    WorkspaceMember,
)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="Approval Project", identifier="APR", workspace=workspace, created_by=create_user
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


@pytest.fixture
def session_user(create_user):
    """The authenticated principal of `session_client` (see conftest)."""
    return create_user


@pytest.fixture
def approver_user(db, workspace, project):
    """A second active project member, distinct from `session_user`."""
    unique = uuid.uuid4().hex[:8]
    user = User.objects.create(
        email=f"approver-{unique}@plane.so",
        username=f"approver-{unique}",
        first_name="Approver",
        last_name="User",
    )
    user.set_password("password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=user, role=15, is_active=True
    )
    return user


@pytest.fixture
def create_issue(db, workspace, create_user):
    """Factory: build an Issue in a given project/state, authored by the principal."""

    def _create(project, state, name="Work Item"):
        return Issue.objects.create(
            name=name,
            project=project,
            workspace=workspace,
            state=state,
            created_by=create_user,
        )

    return _create


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
    res = session_client.post(
        requests_url(workspace.slug, project.id, issue.id),
        {"to_state": str(done.id), "comment": "skip"},
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    assert ApprovalRequest.objects.filter(work_item=issue, status="pending").count() == 1


@pytest.mark.contract
@pytest.mark.django_db
def test_notify_failure_does_not_break_create(
    session_client, workspace, project, states, create_issue, monkeypatch
):
    """A failure inside the (best-effort) notification must not roll back or
    fail the approval-request creation."""
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)

    def _boom(req):
        raise RuntimeError("notify exploded")

    monkeypatch.setattr(
        "plane.workflow.service.notify_approval_requested", _boom
    )
    issue = create_issue(project, backlog)
    res = session_client.post(
        requests_url(workspace.slug, project.id, issue.id),
        {"to_state": str(done.id)},
        format="json",
    )
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
def test_approve_applies_move(
    api_client, session_user, approver_user, workspace, project, states, create_issue
):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    # The requester (`session_user`) creates the request; a DISTINCT user
    # (`approver_user`) who is the guardian of the target state approves it.
    WorkflowStateGuardian.objects.create(
        project=project, workspace=workspace, state=done, guardian=approver_user
    )
    issue = create_issue(project, backlog)
    req = ApprovalRequest.objects.create(
        project=project,
        workspace=workspace,
        work_item=issue,
        from_state=backlog,
        to_state=done,
        requested_by=session_user,
    )
    api_client.force_authenticate(user=approver_user)
    res = api_client.post(
        action_url(workspace.slug, project.id, issue.id, req.id, "approve"),
        {},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    issue.refresh_from_db()
    assert issue.state_id == done.id


@pytest.mark.contract
@pytest.mark.django_db
def test_requester_guardian_cannot_self_approve(
    api_client, session_user, workspace, project, states, create_issue
):
    """A requester who is also a guardian of the target state must not be able
    to approve their own request (self-approval forbidden)."""
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateGuardian.objects.create(
        project=project, workspace=workspace, state=done, guardian=session_user
    )
    issue = create_issue(project, backlog)
    req = ApprovalRequest.objects.create(
        project=project,
        workspace=workspace,
        work_item=issue,
        from_state=backlog,
        to_state=done,
        requested_by=session_user,
    )
    api_client.force_authenticate(user=session_user)
    res = api_client.post(
        action_url(workspace.slug, project.id, issue.id, req.id, "approve"),
        {},
        format="json",
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN
    req.refresh_from_db()
    assert req.status == ApprovalRequest.PENDING
    issue.refresh_from_db()
    assert issue.state_id == backlog.id


@pytest.mark.contract
@pytest.mark.django_db
def test_create_request_rejects_foreign_project_state(
    session_client, session_user, workspace, project, states, create_issue
):
    """A `to_state` belonging to a DIFFERENT project must be rejected with 400
    and no ApprovalRequest may be created."""
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    other_project = Project.objects.create(
        name="Other Project", identifier="OTH", workspace=workspace, created_by=session_user
    )
    foreign_state = State.objects.create(
        name="Foreign", color="#000", group="completed", project=other_project, workspace=workspace
    )
    issue = create_issue(project, backlog)
    res = session_client.post(
        requests_url(workspace.slug, project.id, issue.id),
        {"to_state": str(foreign_state.id)},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert ApprovalRequest.objects.filter(work_item=issue).count() == 0
