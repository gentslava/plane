# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from plane.db.models import (
    Project,
    ProjectMember,
    State,
    User,
    WorkflowProjectApprover,
    WorkflowStateGuardian,
    WorkspaceMember,
)
from plane.workflow.approvers import resolve_approver_ids


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(
        name="WF Project", identifier="WFA", workspace=workspace, created_by=create_user
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
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=user, role=15, is_active=True
    )
    return user


@pytest.mark.unit
@pytest.mark.django_db
def test_resolution_prefers_state_guardians(project, workspace, states, member_user):
    _, done = states
    WorkflowStateGuardian.objects.create(project=project, workspace=workspace, state=done, guardian=member_user)
    WorkflowProjectApprover.objects.create(project=project, workspace=workspace, approver=member_user)
    assert resolve_approver_ids(project.id, done.id) == [str(member_user.id)]


@pytest.mark.unit
@pytest.mark.django_db
def test_resolution_falls_back_to_project_approvers(project, workspace, states, member_user):
    _, done = states
    WorkflowProjectApprover.objects.create(project=project, workspace=workspace, approver=member_user)
    assert resolve_approver_ids(project.id, done.id) == [str(member_user.id)]


@pytest.mark.unit
@pytest.mark.django_db
def test_resolution_empty_means_four_eyes(project, workspace, states):
    _, done = states
    assert resolve_approver_ids(project.id, done.id) == []


@pytest.fixture
def inactive_member_user(db, workspace, project):
    """A user who is a guardian/approver but NOT an active project member."""
    import uuid

    unique = uuid.uuid4().hex[:8]
    user = User.objects.create(
        email=f"inactive-{unique}@plane.so",
        username=f"inactive-{unique}",
        first_name="Inactive",
        last_name="User",
    )
    user.set_password("password")
    user.save()
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=user, role=15, is_active=False
    )
    return user


@pytest.mark.unit
@pytest.mark.django_db
def test_inactive_guardian_is_not_resolved(project, workspace, states, inactive_member_user):
    """A guardian who is not an active project member must not be resolved;
    resolution falls through (here, to four-eyes => [])."""
    _, done = states
    WorkflowStateGuardian.objects.create(
        project=project, workspace=workspace, state=done, guardian=inactive_member_user
    )
    assert resolve_approver_ids(project.id, done.id) == []


@pytest.mark.unit
@pytest.mark.django_db
def test_inactive_guardian_falls_through_to_active_project_approver(
    project, workspace, states, inactive_member_user, member_user
):
    """An inactive guardian is skipped; an active project approver is resolved."""
    _, done = states
    WorkflowStateGuardian.objects.create(
        project=project, workspace=workspace, state=done, guardian=inactive_member_user
    )
    WorkflowProjectApprover.objects.create(
        project=project, workspace=workspace, approver=member_user
    )
    assert resolve_approver_ids(project.id, done.id) == [str(member_user.id)]


@pytest.mark.unit
@pytest.mark.django_db
def test_inactive_project_approver_is_not_resolved(
    project, workspace, states, inactive_member_user
):
    """An inactive project approver must not be resolved (falls through to []
    four-eyes)."""
    _, done = states
    WorkflowProjectApprover.objects.create(
        project=project, workspace=workspace, approver=inactive_member_user
    )
    assert resolve_approver_ids(project.id, done.id) == []
