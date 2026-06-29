# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid

import pytest
from rest_framework import status

from plane.db.models import (
    Project,
    ProjectMember,
    State,
    User,
    WorkflowProjectApprover,
    WorkflowStateGuardian,
    WorkspaceMember,
)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(
        name="WF Guardians Project", identifier="WFG", workspace=workspace, created_by=create_user
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
    unique = uuid.uuid4().hex[:8]
    user = User.objects.create(
        email=f"guardian-{unique}@plane.so",
        username=f"guardian-{unique}",
        first_name="Guardian",
        last_name="User",
    )
    user.set_password("password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    ProjectMember.objects.create(project=project, workspace=workspace, member=user, role=15, is_active=True)
    return user


def guardians_url(slug, pid, state_id):
    return f"/api/workspaces/{slug}/projects/{pid}/workflow-states/{state_id}/guardians/"


def approvers_url(slug, pid):
    return f"/api/workspaces/{slug}/projects/{pid}/workflow/approvers/"


@pytest.mark.contract
@pytest.mark.django_db
def test_add_and_remove_state_guardian(session_client, workspace, project, states, member_user):
    _, done = states
    add = session_client.post(
        guardians_url(workspace.slug, project.id, done.id),
        {"guardian_id": str(member_user.id)},
        format="json",
    )
    assert add.status_code == status.HTTP_201_CREATED
    assert WorkflowStateGuardian.objects.filter(state=done, guardian=member_user).exists()
    rm = session_client.delete(
        guardians_url(workspace.slug, project.id, done.id) + f"{member_user.id}/"
    )
    assert rm.status_code == status.HTTP_204_NO_CONTENT
    assert not WorkflowStateGuardian.objects.filter(state=done, guardian=member_user).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_list_state_guardians(session_client, workspace, project, states, member_user):
    _, done = states
    WorkflowStateGuardian.objects.create(
        project=project, workspace=workspace, state=done, guardian=member_user
    )
    res = session_client.get(guardians_url(workspace.slug, project.id, done.id))
    assert res.status_code == status.HTTP_200_OK
    body = res.json()
    assert isinstance(body, list)
    assert str(member_user.id) in [g["guardian"] for g in body]


@pytest.mark.contract
@pytest.mark.django_db
def test_add_guardian_rejects_non_member(session_client, workspace, project, states):
    """A user who is not an active project member cannot be a state guardian."""
    _, done = states
    res = session_client.post(
        guardians_url(workspace.slug, project.id, done.id),
        {"guardian_id": str(uuid.uuid4())},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert WorkflowStateGuardian.objects.filter(state=done).count() == 0


@pytest.mark.contract
@pytest.mark.django_db
def test_add_and_remove_project_approver(session_client, workspace, project, member_user):
    add = session_client.post(
        approvers_url(workspace.slug, project.id),
        {"approver_id": str(member_user.id)},
        format="json",
    )
    assert add.status_code == status.HTTP_201_CREATED
    assert WorkflowProjectApprover.objects.filter(project=project, approver=member_user).exists()
    rm = session_client.delete(
        approvers_url(workspace.slug, project.id) + f"{member_user.id}/"
    )
    assert rm.status_code == status.HTTP_204_NO_CONTENT
    assert not WorkflowProjectApprover.objects.filter(project=project, approver=member_user).exists()
