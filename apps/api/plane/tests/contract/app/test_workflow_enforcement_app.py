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


def issue_url(slug, project_id, pk=None):
    base = f"/api/workspaces/{slug}/projects/{project_id}/issues/"
    return f"{base}{pk}/" if pk else base


@pytest.mark.contract
@pytest.mark.django_db
def test_partial_update_blocks_disallowed_transition(session_client, workspace, project, states, create_user):
    """An off-path move (no transition row, actor is not an approver) needs
    approval and must be rejected with 403 NEEDS_APPROVAL — the issue stays put."""
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="I1", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = issue_url(workspace.slug, project.id, issue.id)
    response = session_client.patch(url, {"state_id": str(done.id)}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "WORKFLOW_TRANSITION_NEEDS_APPROVAL"
    issue.refresh_from_db()
    assert issue.state_id == backlog.id  # unchanged


@pytest.mark.contract
@pytest.mark.django_db
def test_partial_update_forbidden_transition(session_client, workspace, project, states, create_user):
    """A transition row with kind='forbidden' is a hard block (403 FORBIDDEN),
    distinct from an off-path move that merely needs approval."""
    from plane.db.models import WorkflowTransition

    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done,
        kind=WorkflowTransition.FORBIDDEN,
    )
    issue = Issue.objects.create(
        name="I-forbid", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = issue_url(workspace.slug, project.id, issue.id)
    response = session_client.patch(url, {"state_id": str(done.id)}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "WORKFLOW_TRANSITION_FORBIDDEN"
    issue.refresh_from_db()
    assert issue.state_id == backlog.id  # unchanged


@pytest.mark.contract
@pytest.mark.django_db
def test_partial_update_allows_on_path_transition(session_client, workspace, project, states, create_user):
    """An on-path move (allowed transition row exists) is free for everyone."""
    from plane.db.models import WorkflowTransition

    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done,
    )  # kind defaults to "allowed"
    issue = Issue.objects.create(
        name="I-allowed", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = issue_url(workspace.slug, project.id, issue.id)
    response = session_client.patch(url, {"state_id": str(done.id)}, format="json")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    issue.refresh_from_db()
    assert issue.state_id == done.id


@pytest.mark.contract
@pytest.mark.django_db
def test_partial_update_allows_when_workflow_off(session_client, workspace, project, states, create_user):
    backlog, done = states
    issue = Issue.objects.create(
        name="I2", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    url = issue_url(workspace.slug, project.id, issue.id)
    response = session_client.patch(url, {"state_id": str(done.id)}, format="json")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    issue.refresh_from_db()
    assert issue.state_id == done.id


@pytest.mark.contract
@pytest.mark.django_db
def test_create_blocked_in_restricted_state(session_client, workspace, project, states, create_user):
    from plane.db.models import WorkflowStateConfig
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )
    url = issue_url(workspace.slug, project.id)
    response = session_client.post(
        url, {"name": "Blocked", "state_id": str(backlog.id)}, format="json"
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "WORKFLOW_CREATION_BLOCKED"


@pytest.mark.contract
@pytest.mark.django_db
def test_create_blocked_via_default_state_when_state_id_omitted(
    session_client, workspace, project, states, create_user
):
    """POST issue without state_id must be blocked when the project's default
    state has allow_issue_creation=False and workflow is enabled (I2)."""
    from plane.db.models import WorkflowStateConfig
    backlog, _ = states
    # Mark backlog as the project default state.
    backlog.default = True
    backlog.save(update_fields=["default"])
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )
    url = issue_url(workspace.slug, project.id)
    # No state_id in the payload → should resolve default and block.
    response = session_client.post(url, {"name": "No state supplied"}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "WORKFLOW_CREATION_BLOCKED"
