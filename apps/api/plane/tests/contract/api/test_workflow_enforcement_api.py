# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Issue, Project, ProjectMember, ProjectWorkflow, State, WorkflowStateConfig


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="WF API Project", identifier="WFAP", workspace=workspace, created_by=create_user
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
def test_public_api_update_blocks_disallowed_transition(api_key_client, workspace, project, states, create_user):
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


@pytest.mark.contract
@pytest.mark.django_db
def test_public_api_create_blocked_in_default_state_when_state_omitted(
    api_key_client, workspace, project, states, create_user
):
    """Public POST without `state` must resolve the project default state and
    block creation when that default state forbids it (field name is `state`)."""
    backlog, _ = states
    backlog.default = True
    backlog.save()
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )
    url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/issues/"
    response = api_key_client.post(url, {"name": "API No State"}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error_code"] == "WORKFLOW_CREATION_BLOCKED"
