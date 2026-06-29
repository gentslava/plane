# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""GraphQL contract tests for the workflow approval-request lifecycle (Phase 3 A).

Coverage (direct-resolver pattern, mirroring test_workflow_graphql.py):
  - requestWorkflowApproval: happy path, duplicate-pending, not-required.
  - approveWorkflowRequest: success (state applied), self-approval blocked.
  - cancelWorkflowRequest: requester cancels, non-requester blocked.
  - rejectWorkflowRequest: success (state unchanged).
  - workflowApprovalRequests: visibility for requester + guardian, empty for an
    unrelated member.
"""
from types import SimpleNamespace

import pytest
from graphql import GraphQLError

from plane.db.models import (
    ApprovalRequest,
    Issue,
    Project,
    ProjectMember,
    ProjectWorkflow,
    State,
    WorkflowStateGuardian,
    Workspace,
    WorkspaceMember,
)
from plane.graphql.areas.workflow_approval import (
    resolve_approve_workflow_request,
    resolve_cancel_workflow_request,
    resolve_reject_workflow_request,
    resolve_request_workflow_approval,
    resolve_workflow_approval_requests,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _info(user):
    """Minimal ariadne-compatible info object."""
    return SimpleNamespace(context=SimpleNamespace(user=user))


@pytest.fixture
def workspace(db, create_user):
    ws = Workspace.objects.create(name="GQL WF Approval", slug="gql-wf-appr", owner=create_user)
    WorkspaceMember.objects.create(workspace=ws, member=create_user, role=20, is_active=True)
    return ws


@pytest.fixture
def project(db, workspace, create_user):
    proj = Project.objects.create(
        name="GQL WF Approval Project",
        identifier="GQLWFA",
        workspace=workspace,
        created_by=create_user,
    )
    ProjectMember.objects.create(
        project=proj, workspace=workspace, member=create_user, role=20, is_active=True
    )
    return proj


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
def workflow_on(db, project, workspace):
    return ProjectWorkflow.objects.create(project=project, workspace=workspace, is_enabled=True)


@pytest.fixture
def existing_issue(db, project, workspace, states, create_user):
    backlog, _ = states
    return Issue.objects.create(
        name="Existing", project=project, workspace=workspace, state=backlog,
        sequence_id=1, created_by=create_user, updated_by=create_user,
    )


@pytest.fixture
def second_user(db, workspace, project):
    """A second user that is BOTH an active WorkspaceMember (needed by
    _member_project) and an active ProjectMember (needed by approver resolution)."""
    import uuid

    from plane.db.models import User

    unique = uuid.uuid4().hex[:8]
    user = User.objects.create(
        email=f"approver-{unique}@plane.so",
        username=f"approver-{unique}",
        first_name="App", last_name="Rover",
    )
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15, is_active=True)
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=user, role=15, is_active=True
    )
    return user


@pytest.fixture
def third_user(db, workspace, project):
    """An unrelated active member: neither requester nor guardian/approver."""
    import uuid

    from plane.db.models import User

    unique = uuid.uuid4().hex[:8]
    user = User.objects.create(
        email=f"bystander-{unique}@plane.so",
        username=f"bystander-{unique}",
        first_name="By", last_name="Stander",
    )
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15, is_active=True)
    ProjectMember.objects.create(
        project=project, workspace=workspace, member=user, role=15, is_active=True
    )
    return user


# ---------------------------------------------------------------------------
# requestWorkflowApproval
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_request_approval_happy(
    workspace, project, states, workflow_on, existing_issue, create_user, second_user
):
    """Off-path backlog->done by a non-approver requester creates a PENDING request."""
    _, done = states
    info = _info(create_user)

    req = resolve_request_workflow_approval(
        None, info,
        slug=workspace.slug,
        project=str(project.id),
        workItem=str(existing_issue.id),
        toState=str(done.id),
    )

    assert req is not None
    assert req.status == ApprovalRequest.PENDING
    assert (
        ApprovalRequest.objects.filter(
            work_item=existing_issue, status=ApprovalRequest.PENDING
        ).count()
        == 1
    )


@pytest.mark.contract
@pytest.mark.django_db
def test_request_approval_duplicate(
    workspace, project, states, workflow_on, existing_issue, create_user, second_user
):
    """A second request for the same work item raises APPROVAL_DUPLICATE_PENDING."""
    _, done = states
    info = _info(create_user)

    resolve_request_workflow_approval(
        None, info,
        slug=workspace.slug, project=str(project.id),
        workItem=str(existing_issue.id), toState=str(done.id),
    )

    with pytest.raises(GraphQLError) as exc_info:
        resolve_request_workflow_approval(
            None, info,
            slug=workspace.slug, project=str(project.id),
            workItem=str(existing_issue.id), toState=str(done.id),
        )
    assert exc_info.value.extensions.get("code") == "APPROVAL_DUPLICATE_PENDING"


@pytest.mark.contract
@pytest.mark.django_db
def test_request_approval_not_required_when_workflow_off(
    workspace, project, states, existing_issue, create_user
):
    """With workflow OFF the move is ALLOW, so requesting raises APPROVAL_NOT_REQUIRED."""
    _, done = states
    info = _info(create_user)

    with pytest.raises(GraphQLError) as exc_info:
        resolve_request_workflow_approval(
            None, info,
            slug=workspace.slug, project=str(project.id),
            workItem=str(existing_issue.id), toState=str(done.id),
        )
    assert exc_info.value.extensions.get("code") == "APPROVAL_NOT_REQUIRED"


# ---------------------------------------------------------------------------
# approveWorkflowRequest
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_approve_by_second_user_applies_move(
    workspace, project, states, workflow_on, existing_issue, create_user, second_user
):
    """The SECOND user approves: request becomes APPROVED and the issue moves to done."""
    _, done = states

    req = resolve_request_workflow_approval(
        None, _info(create_user),
        slug=workspace.slug, project=str(project.id),
        workItem=str(existing_issue.id), toState=str(done.id),
    )

    decided = resolve_approve_workflow_request(
        None, _info(second_user),
        slug=workspace.slug, project=str(project.id), request=str(req.id),
    )

    assert decided.status == ApprovalRequest.APPROVED
    existing_issue.refresh_from_db()
    assert existing_issue.state_id == done.id


@pytest.mark.contract
@pytest.mark.django_db
def test_approve_by_requester_blocked(
    workspace, project, states, workflow_on, existing_issue, create_user, second_user
):
    """Self-approval is blocked: the requester gets APPROVAL_NOT_APPROVER."""
    _, done = states

    req = resolve_request_workflow_approval(
        None, _info(create_user),
        slug=workspace.slug, project=str(project.id),
        workItem=str(existing_issue.id), toState=str(done.id),
    )

    with pytest.raises(GraphQLError) as exc_info:
        resolve_approve_workflow_request(
            None, _info(create_user),
            slug=workspace.slug, project=str(project.id), request=str(req.id),
        )
    assert exc_info.value.extensions.get("code") == "APPROVAL_NOT_APPROVER"

    req.refresh_from_db()
    assert req.status == ApprovalRequest.PENDING


# ---------------------------------------------------------------------------
# cancelWorkflowRequest
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_cancel_by_requester_and_blocked_for_others(
    workspace, project, states, workflow_on, existing_issue, create_user, second_user
):
    """Requester cancels (CANCELLED); a non-requester is blocked (APPROVAL_NOT_REQUESTER)."""
    _, done = states

    # Non-requester cannot cancel a pending request.
    req = resolve_request_workflow_approval(
        None, _info(create_user),
        slug=workspace.slug, project=str(project.id),
        workItem=str(existing_issue.id), toState=str(done.id),
    )
    with pytest.raises(GraphQLError) as exc_info:
        resolve_cancel_workflow_request(
            None, _info(second_user),
            slug=workspace.slug, project=str(project.id), request=str(req.id),
        )
    assert exc_info.value.extensions.get("code") == "APPROVAL_NOT_REQUESTER"

    # The requester can cancel.
    cancelled = resolve_cancel_workflow_request(
        None, _info(create_user),
        slug=workspace.slug, project=str(project.id), request=str(req.id),
    )
    assert cancelled.status == ApprovalRequest.CANCELLED


# ---------------------------------------------------------------------------
# rejectWorkflowRequest
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_reject_by_second_user_keeps_state(
    workspace, project, states, workflow_on, existing_issue, create_user, second_user
):
    """The SECOND user rejects: request becomes REJECTED and the issue state is unchanged."""
    backlog, done = states

    req = resolve_request_workflow_approval(
        None, _info(create_user),
        slug=workspace.slug, project=str(project.id),
        workItem=str(existing_issue.id), toState=str(done.id),
    )

    decided = resolve_reject_workflow_request(
        None, _info(second_user),
        slug=workspace.slug, project=str(project.id), request=str(req.id),
        reason="not yet",
    )

    assert decided.status == ApprovalRequest.REJECTED
    existing_issue.refresh_from_db()
    assert existing_issue.state_id == backlog.id


# ---------------------------------------------------------------------------
# workflowApprovalRequests (visibility)
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_query_visibility(
    workspace, project, states, workflow_on, existing_issue,
    create_user, second_user, third_user,
):
    """With a guardian on `done` and a PENDING request by the first user:
      - the requester (first user) sees it,
      - the guardian (second user) sees it,
      - an unrelated third member sees an empty list."""
    _, done = states
    WorkflowStateGuardian.objects.create(project=project, state=done, guardian=second_user)

    req = resolve_request_workflow_approval(
        None, _info(create_user),
        slug=workspace.slug, project=str(project.id),
        workItem=str(existing_issue.id), toState=str(done.id),
    )

    # Requester sees their own pending request (default status).
    requester_view = resolve_workflow_approval_requests(
        None, _info(create_user), slug=workspace.slug, project=str(project.id),
    )
    assert [r.id for r in requester_view] == [req.id]

    # Guardian (an approver of `done`) sees it too.
    guardian_view = resolve_workflow_approval_requests(
        None, _info(second_user), slug=workspace.slug, project=str(project.id),
    )
    assert [r.id for r in guardian_view] == [req.id]

    # Unrelated member sees nothing (a guardian exists, so four-eyes does not apply).
    bystander_view = resolve_workflow_approval_requests(
        None, _info(third_user), slug=workspace.slug, project=str(project.id),
    )
    assert bystander_view == []
