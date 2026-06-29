# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.db.models import (
    ApprovalRequest,
    Issue,
    Project,
    ProjectWorkflow,
    State,
    User,
)
from plane.workflow.apply import apply_approved_request


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(
        name="WF Apply Project",
        identifier="WFA",
        workspace=workspace,
        created_by=create_user,
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
def test_apply_moves_issue_and_marks_approved(project, workspace, states, create_user):
    backlog, done = states
    # Workflow enabled with NO whitelisted transition → backlog→done is off-path
    # and would be blocked by the pre_save guard net for any authenticated actor.
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="Apply S1", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    approver = User.objects.create(email="approver@plane.so", username="approver")
    req = ApprovalRequest.objects.create(
        project=project,
        workspace=workspace,
        work_item=issue,
        from_state=backlog,
        to_state=done,
        requested_by=create_user,
    )

    apply_approved_request(req, approver)

    issue.refresh_from_db()
    req.refresh_from_db()
    assert issue.state_id == done.id  # moved despite off-path (bypassed the guard)
    assert req.status == ApprovalRequest.APPROVED
    assert req.decided_by_id == approver.id
    assert req.decided_at is not None


@pytest.mark.unit
@pytest.mark.django_db
def test_independent_move_auto_cancels_pending(project, workspace, states, create_user):
    backlog, done = states
    # A third state the card actually lands in — *different* from the request's
    # to_state (done), so the request's target no longer matches where the card
    # went and it must be cancelled.
    in_progress = State.objects.create(
        name="In Progress", color="#000", group="started", project=project, workspace=workspace
    )
    ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)
    issue = Issue.objects.create(
        name="Apply S2", project=project, workspace=workspace, state=backlog, created_by=create_user
    )
    # A pending request asks to move backlog→done.
    req = ApprovalRequest.objects.create(
        project=project,
        workspace=workspace,
        work_item=issue,
        from_state=backlog,
        to_state=done,
        requested_by=create_user,
    )
    # The issue is moved another way (here: a raw state change to in_progress, a
    # target other than the request's to_state). The pending request is now moot.
    from plane.workflow.signals import workflow_bypass

    with workflow_bypass():
        issue.state_id = in_progress.id
        issue.save(update_fields=["state", "updated_at"])

    req.refresh_from_db()
    assert req.status == ApprovalRequest.CANCELLED
