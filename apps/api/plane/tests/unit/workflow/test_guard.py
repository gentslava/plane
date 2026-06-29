# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import uuid

import pytest

from plane.db.models import (
    Project,
    ProjectMember,
    ProjectWorkflow,
    State,
    User,
    WorkflowStateConfig,
    WorkflowStateGuardian,
    WorkflowTransition,
    WorkspaceMember,
)
from plane.workflow.guard import Action, evaluate_creation, evaluate_transition


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(
        name="WF Project", identifier="WF", workspace=workspace, created_by=create_user
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


@pytest.fixture
def make_user(db, workspace, project):
    """Factory that creates an additional project member user."""

    def _make_user(email):
        user = User.objects.create(
            email=email, username=email.split("@")[0] + "-" + uuid.uuid4().hex[:6]
        )
        user.set_password("password")
        user.save()
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
        ProjectMember.objects.create(
            project=project, workspace=workspace, member=user, role=15, is_active=True
        )
        return user

    return _make_user


# --- transition decision matrix ------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_allowed_when_workflow_disabled(project, states, member_user):
    backlog, done = states
    decision = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert decision.action == Action.ALLOW


@pytest.mark.unit
@pytest.mark.django_db
def test_on_path_is_free(project, workspace, states, member_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done, kind="allowed"
    )
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.ALLOW


@pytest.mark.unit
@pytest.mark.django_db
def test_forbidden_is_blocked(project, workspace, states, member_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done, kind="forbidden"
    )
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.BLOCK
    assert d.code == "WORKFLOW_TRANSITION_FORBIDDEN"


@pytest.mark.unit
@pytest.mark.django_db
def test_off_path_needs_approval_four_eyes(project, workspace, states, member_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.NEEDS_APPROVAL
    assert d.code == "WORKFLOW_TRANSITION_NEEDS_APPROVAL"
    assert d.context["approvers"] == []
    assert d.context["any_member"] is True


@pytest.mark.unit
@pytest.mark.django_db
def test_off_path_approver_acts_directly(project, workspace, states, member_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateGuardian.objects.create(
        project=project, workspace=workspace, state=done, guardian=member_user
    )
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.ALLOW


@pytest.mark.unit
@pytest.mark.django_db
def test_off_path_non_approver_needs_approval(project, workspace, states, member_user, make_user):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    guardian = make_user(email="guard@plane.so")
    WorkflowStateGuardian.objects.create(
        project=project, workspace=workspace, state=done, guardian=guardian
    )
    d = evaluate_transition(project.id, backlog.id, done.id, member_user)
    assert d.action == Action.NEEDS_APPROVAL
    assert d.context["approvers"] == [str(guardian.id)]
    assert d.context["any_member"] is False


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_actor_none_off_path_needs_approval(project, workspace, states):
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    d = evaluate_transition(project.id, backlog.id, done.id, None)
    assert d.action == Action.NEEDS_APPROVAL
    assert d.code == "WORKFLOW_TRANSITION_NEEDS_APPROVAL"


# --- creation decision matrix --------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_creation_blocked_when_state_disallows(project, workspace, states, member_user):
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )
    decision = evaluate_creation(project.id, backlog.id, member_user)
    assert decision.action == Action.BLOCK
    assert decision.code == "WORKFLOW_CREATION_BLOCKED"


@pytest.mark.unit
@pytest.mark.django_db
def test_creation_allowed_when_no_config(project, workspace, states, member_user):
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    decision = evaluate_creation(project.id, backlog.id, member_user)
    assert decision.action == Action.ALLOW


@pytest.mark.unit
@pytest.mark.django_db
def test_creation_allowed_when_config_permits(project, workspace, states, member_user):
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=True
    )
    decision = evaluate_creation(project.id, backlog.id, member_user)
    assert decision.action == Action.ALLOW
