# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for Task 5: workflow enforcement on additional write paths.

Coverage:
- Point 1 (bulk update): no state-changing bulk endpoint exists → no test.
- Point 2 (sub_issue POST): only sets parent, never state → no test.
- Point 3 (draft → issue): create_draft_to_issue must obey enforce_creation.
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
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="WF Paths Project", identifier="WFP", workspace=workspace, created_by=create_user
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
        name="Done", color="#fff", group="completed", project=project, workspace=workspace
    )
    return backlog, done


@pytest.fixture
def draft_issue(db, workspace, project, states, create_user):
    backlog, _ = states
    return DraftIssue.objects.create(
        name="Draft issue",
        project=project,
        workspace=workspace,
        state=backlog,
        created_by=create_user,
    )


# ---------------------------------------------------------------------------
# Point 3: draft → issue (create_draft_to_issue)
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_draft_to_issue_blocks_creation_in_restricted_state(
    session_client, workspace, project, states, draft_issue, create_user
):
    """When workflow is enabled and allow_issue_creation=False for a state,
    converting a draft to a real issue in that state must return 400."""
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateConfig.objects.create(
        project=project,
        workspace=workspace,
        state=backlog,
        allow_issue_creation=False,
    )

    url = f"/api/workspaces/{workspace.slug}/draft-to-issue/{draft_issue.id}/"
    payload = {
        "name": "From Draft",
        "state_id": str(backlog.id),
    }
    response = session_client.post(url, payload, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "WORKFLOW_CREATION_BLOCKED"
    # Draft must still exist (not deleted)
    assert DraftIssue.objects.filter(pk=draft_issue.id).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_draft_to_issue_allows_when_workflow_disabled(
    session_client, workspace, project, states, draft_issue, create_user
):
    """When workflow is disabled, draft-to-issue conversion must succeed."""
    backlog, _ = states
    # No ProjectWorkflow → workflow disabled

    url = f"/api/workspaces/{workspace.slug}/draft-to-issue/{draft_issue.id}/"
    payload = {
        "name": "From Draft OK",
        "state_id": str(backlog.id),
    }
    response = session_client.post(url, payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert not DraftIssue.objects.filter(pk=draft_issue.id).exists()
    # The real issue was actually written, not just the draft removed.
    assert Issue.objects.filter(name="From Draft OK", project=project).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_draft_to_issue_allows_permitted_state(
    session_client, workspace, project, states, draft_issue, create_user
):
    """When workflow is enabled but state allows creation, draft-to-issue succeeds."""
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    # No WorkflowStateConfig → allow_issue_creation defaults to True

    url = f"/api/workspaces/{workspace.slug}/draft-to-issue/{draft_issue.id}/"
    payload = {
        "name": "From Draft Allowed",
        "state_id": str(backlog.id),
    }
    response = session_client.post(url, payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert not DraftIssue.objects.filter(pk=draft_issue.id).exists()
    # The real issue was actually written, not just the draft removed.
    assert Issue.objects.filter(name="From Draft Allowed", project=project).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_draft_to_issue_blocks_creation_in_default_state_when_no_state_sent(
    session_client, workspace, project, states, create_user
):
    """When workflow is enabled and the default state has allow_issue_creation=False,
    converting a draft to an issue WITHOUT sending a state must also return 400.

    This covers the I2 fix: if the client omits state, the guard must resolve the
    project's default state and enforce the policy rather than letting Issue.save()
    silently assign the default state in bypass of the guard.
    """
    backlog, _ = states
    # Mark backlog as the project's default state.
    backlog.default = True
    backlog.save(update_fields=["default"])

    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateConfig.objects.create(
        project=project,
        workspace=workspace,
        state=backlog,
        allow_issue_creation=False,
    )

    # Create a fresh draft without a state so draft_issue fixture doesn't interfere.
    draft = DraftIssue.objects.create(
        name="Draft no-state",
        project=project,
        workspace=workspace,
        created_by=create_user,
    )

    url = f"/api/workspaces/{workspace.slug}/draft-to-issue/{draft.id}/"
    # Deliberately omit state_id — the guard must still fire on the default state.
    payload = {"name": "Should Be Blocked"}
    response = session_client.post(url, payload, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "WORKFLOW_CREATION_BLOCKED"
    # Draft must still exist — creation was rejected, nothing was promoted.
    assert DraftIssue.objects.filter(pk=draft.id).exists()
