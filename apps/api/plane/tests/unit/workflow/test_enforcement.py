# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import Project, ProjectWorkflow, State
from plane.workflow.enforcement import enforce_creation, enforce_transition
from plane.workflow.exceptions import WorkflowBlocked


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


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_transition_raises_403(project, workspace, states, create_user):
    """Off-path transition (no WorkflowTransition row) → NEEDS_APPROVAL → 403."""
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    with pytest.raises(WorkflowBlocked) as exc:
        enforce_transition(project.id, backlog.id, done.id, create_user)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["error_code"] == "WORKFLOW_TRANSITION_NEEDS_APPROVAL"
    assert exc.value.detail["any_member"] is True


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_transition_off_path_raises_needs_approval(project, workspace, states, create_user):
    """Off-path transition → NEEDS_APPROVAL code, 403, any_member=True (no approvers configured)."""
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    with pytest.raises(WorkflowBlocked) as exc:
        enforce_transition(project.id, backlog.id, done.id, create_user)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["error_code"] == "WORKFLOW_TRANSITION_NEEDS_APPROVAL"
    assert exc.value.detail["any_member"] is True


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_transition_noop_when_disabled(project, states, create_user):
    backlog, done = states
    # Must not raise.
    enforce_transition(project.id, backlog.id, done.id, create_user)


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_transition_noop_when_state_unchanged(project, workspace, states, create_user):
    backlog, _ = states
    # Workflow ON so the early-out for from == to is what's under test.
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    # from == to (no transition) must never be blocked even if workflow on.
    enforce_transition(project.id, backlog.id, backlog.id, create_user)


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_creation_raises_400(project, workspace, states, create_user):
    from plane.db.models import WorkflowStateConfig
    backlog, _ = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )
    with pytest.raises(WorkflowBlocked) as exc:
        enforce_creation(project.id, backlog.id, create_user)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail["error_code"] == "WORKFLOW_CREATION_BLOCKED"


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_creation_noop_when_state_none(project, create_user):
    enforce_creation(project.id, None, create_user)  # must not raise


@pytest.mark.unit
@pytest.mark.django_db
def test_enforce_creation_noop_when_disabled(project, states, create_user):
    backlog, _ = states
    enforce_creation(project.id, backlog.id, create_user)  # workflow off → no raise
