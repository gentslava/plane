# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectMember, ProjectWorkflow, State, User, WorkflowTransition


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="WF Config Project", identifier="WFC", workspace=workspace, created_by=create_user
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
def member_user(db, workspace, project):
    import uuid
    unique = uuid.uuid4().hex[:8]
    user = User.objects.create(
        email=f"member-{unique}@plane.so",
        username=f"member-{unique}",
        first_name="Member",
        last_name="User",
    )
    user.set_password("password")
    user.save()
    from plane.db.models import WorkspaceMember
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    ProjectMember.objects.create(project=project, workspace=workspace, member=user, role=15, is_active=True)
    return user


def workflow_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/workflow/"


def transitions_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/workflow-transitions/"


def transition_url(slug, project_id, pk):
    return f"/api/workspaces/{slug}/projects/{project_id}/workflow-transitions/{pk}/"


def state_configs_url(slug, project_id):
    return f"/api/workspaces/{slug}/projects/{project_id}/workflow-states/"


@pytest.mark.contract
@pytest.mark.django_db
def test_get_workflow_when_no_record_returns_default_without_creating(session_client, workspace, project):
    """GET workflow/ when no ProjectWorkflow row exists must return
    is_enabled=False and must NOT create a DB row (M2)."""
    assert ProjectWorkflow.objects.filter(project=project).count() == 0
    url = workflow_url(workspace.slug, project.id)
    response = session_client.get(url)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_enabled"] is False
    # Crucially, no row was created as a side-effect.
    assert ProjectWorkflow.objects.filter(project=project).count() == 0


@pytest.mark.contract
@pytest.mark.django_db
def test_toggle_workflow_enable(session_client, workspace, project):
    url = workflow_url(workspace.slug, project.id)
    response = session_client.patch(url, {"is_enabled": True}, format="json")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_enabled"] is True


@pytest.mark.contract
@pytest.mark.django_db
def test_create_transition(session_client, workspace, project, states):
    backlog, done = states
    url = transitions_url(workspace.slug, project.id)
    response = session_client.post(
        url,
        {"state": str(backlog.id), "transition_state": str(done.id)},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert WorkflowTransition.objects.filter(
        project=project, state=backlog, transition_state=done
    ).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_create_forbidden_transition(session_client, workspace, project, states):
    backlog, done = states
    url = transitions_url(workspace.slug, project.id)
    res = session_client.post(
        url,
        {"state": str(backlog.id), "transition_state": str(done.id), "kind": "forbidden"},
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    assert res.json()["kind"] == "forbidden"
    assert WorkflowTransition.objects.filter(
        project=project, state=backlog, transition_state=done, kind="forbidden"
    ).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_create_transition_defaults_to_allowed(session_client, workspace, project, states):
    backlog, done = states
    url = transitions_url(workspace.slug, project.id)
    res = session_client.post(
        url,
        {"state": str(backlog.id), "transition_state": str(done.id)},
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    assert res.json()["kind"] == "allowed"


@pytest.mark.contract
@pytest.mark.django_db
def test_create_transition_rejects_invalid_kind(session_client, workspace, project, states):
    backlog, done = states
    url = transitions_url(workspace.slug, project.id)
    res = session_client.post(
        url,
        {"state": str(backlog.id), "transition_state": str(done.id), "kind": "bogus"},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.contract
@pytest.mark.django_db
def test_non_admin_patch_workflow_returns_403(api_client, workspace, project, member_user):
    api_client.force_authenticate(user=member_user)
    url = workflow_url(workspace.slug, project.id)
    response = api_client.patch(url, {"is_enabled": True}, format="json")
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
def test_delete_transition(session_client, workspace, project, states):
    backlog, done = states
    transition = WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    url = transition_url(workspace.slug, project.id, transition.id)
    response = session_client.delete(url)
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not WorkflowTransition.objects.filter(pk=transition.id).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_list_state_configs(session_client, workspace, project):
    url = state_configs_url(workspace.slug, project.id)
    response = session_client.get(url)
    assert response.status_code == status.HTTP_200_OK
    assert isinstance(response.json(), list)
