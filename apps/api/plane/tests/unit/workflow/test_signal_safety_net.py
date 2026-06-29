# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from crum import set_current_user

from plane.db.models import Project, ProjectWorkflow, State, Issue
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
def test_direct_save_blocked_when_actor_in_context(project, workspace, states, create_user):
    from plane.workflow.signals import workflow_actor_context

    backlog, done = states
    # No WorkflowTransition row is configured → every transition is blocked
    # (a transition must be explicitly whitelisted to be allowed).
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="S1", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    issue.state = done
    with workflow_actor_context(create_user):
        with pytest.raises(WorkflowBlocked):
            issue.save()
    issue.refresh_from_db()
    assert issue.state_id == backlog.id


@pytest.mark.unit
@pytest.mark.django_db
def test_direct_save_allowed_without_actor_context(project, workspace, states, create_user):
    # No actor in context and no crum user → safety net is dormant.
    # Primary protection lives in the views.
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="S2", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    issue.state = done
    issue.save()  # must not raise
    issue.refresh_from_db()
    assert issue.state_id == done.id


@pytest.mark.unit
@pytest.mark.django_db
def test_direct_save_blocked_via_crum_when_workflow_enabled(project, workspace, states, create_user):
    """Simulates an HTTP request: crum sets the user; the net must block the
    disallowed transition even without an explicit workflow_actor_context."""
    backlog, done = states
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="S3", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    issue.state = done
    set_current_user(create_user)
    try:
        with pytest.raises(WorkflowBlocked):
            issue.save()
    finally:
        set_current_user(None)
    issue.refresh_from_db()
    assert issue.state_id == backlog.id


@pytest.mark.unit
@pytest.mark.django_db
def test_direct_save_via_crum_passes_when_workflow_disabled(project, workspace, states, create_user):
    """When workflow is disabled, crum-activated net must not block any transition."""
    backlog, done = states
    # No ProjectWorkflow created → disabled by default.
    issue = Issue.objects.create(
        name="S4", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    issue.state = done
    set_current_user(create_user)
    try:
        issue.save()  # must not raise
    finally:
        set_current_user(None)
    issue.refresh_from_db()
    assert issue.state_id == done.id
