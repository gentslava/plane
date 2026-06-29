# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""GraphQL contract tests for workflow enforcement (C1 fix).

Coverage:
  - createIssueV2: blocked when workflow enabled and state forbids creation.
  - createIssueV2: allowed when workflow is off.
  - updateIssueV2: blocked when workflow enabled and transition is disallowed.
  - updateIssueV2: allowed when workflow is off (any transition passes).
  - createEpic: blocked when workflow enabled and state forbids creation.
  - updateEpic: blocked when workflow enabled and transition is disallowed.

Test approach: direct resolver call (SimpleNamespace info) — same pattern as
test_graphql_authz.py, avoiding the need for a live HTTP server / full schema
wiring while still testing the real resolver path including the guard call.
"""
from types import SimpleNamespace

import pytest
from graphql import GraphQLError

from plane.db.models import (
    ApprovalRequest,
    Issue,
    IssueType,
    Project,
    ProjectMember,
    ProjectWorkflow,
    State,
    WorkflowStateConfig,
    WorkflowStateGuardian,
    WorkflowTransition,
    Workspace,
    WorkspaceMember,
)
from plane.graphql.mutations.planning import resolve_create_epic, resolve_update_epic
from plane.graphql.mutations.work_items import resolve_create_issue_v2, resolve_update_issue_v2


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _info(user):
    """Minimal ariadne-compatible info object."""
    return SimpleNamespace(context=SimpleNamespace(user=user))


@pytest.fixture
def workspace(db, create_user):
    ws = Workspace.objects.create(name="GQL WF", slug="gql-wf", owner=create_user)
    WorkspaceMember.objects.create(workspace=ws, member=create_user, role=20)
    return ws


@pytest.fixture
def project(db, workspace, create_user):
    proj = Project.objects.create(
        name="GQL WF Project", identifier="GQLWF", workspace=workspace, created_by=create_user
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
def creation_blocked(db, project, workspace, states):
    backlog, _ = states
    return WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )


@pytest.fixture
def existing_issue(db, project, workspace, states, create_user):
    backlog, _ = states
    return Issue.objects.create(
        name="Existing", project=project, workspace=workspace, state=backlog,
        sequence_id=1, created_by=create_user, updated_by=create_user,
    )


# ---------------------------------------------------------------------------
# createIssueV2
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_create_issue_blocked_in_restricted_state(
    workspace, project, states, workflow_on, creation_blocked, create_user
):
    """createIssueV2: raises GraphQLError when workflow blocks creation in state."""
    backlog, _ = states
    info = _info(create_user)

    with pytest.raises(GraphQLError) as exc_info:
        resolve_create_issue_v2(
            None, info,
            slug=workspace.slug,
            project=str(project.id),
            issueInput={"name": "Blocked Issue", "state": str(backlog.id)},
        )

    err = exc_info.value
    assert "WORKFLOW_CREATION_BLOCKED" in str(err.extensions.get("code", ""))
    # No issue should have been created.
    assert not Issue.objects.filter(name="Blocked Issue", project=project).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_create_issue_allowed_when_workflow_off(workspace, project, states, create_user):
    """createIssueV2: succeeds when workflow is disabled (no ProjectWorkflow row)."""
    backlog, _ = states
    info = _info(create_user)

    result = resolve_create_issue_v2(
        None, info,
        slug=workspace.slug,
        project=str(project.id),
        issueInput={"name": "Allowed Issue", "state": str(backlog.id)},
    )

    assert result is not None
    assert result.name == "Allowed Issue"
    assert Issue.objects.filter(name="Allowed Issue", project=project).exists()


# ---------------------------------------------------------------------------
# updateIssueV2
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_update_issue_transition_blocked(
    workspace, project, states, workflow_on, existing_issue, create_user
):
    """updateIssueV2: an off-path move (no transition row, actor not an approver)
    auto-files an approval request, raises GraphQLError with APPROVAL_REQUESTED
    and leaves the state unchanged."""
    backlog, done = states
    info = _info(create_user)

    # No WorkflowTransition row → off-path move that auto-files a request.
    with pytest.raises(GraphQLError) as exc_info:
        resolve_update_issue_v2(
            None, info,
            slug=workspace.slug,
            project=str(project.id),
            id=str(existing_issue.id),
            issueInput={"state": str(done.id)},
        )

    err = exc_info.value
    assert "WORKFLOW_APPROVAL_REQUESTED" in str(err.extensions.get("code", ""))
    existing_issue.refresh_from_db()
    assert existing_issue.state_id == backlog.id  # must be unchanged
    assert (
        ApprovalRequest.objects.filter(
            work_item=existing_issue, status="pending"
        ).count()
        == 1
    )


@pytest.mark.contract
@pytest.mark.django_db
def test_update_issue_transition_allowed_when_workflow_off(
    workspace, project, states, existing_issue, create_user
):
    """updateIssueV2: succeeds when workflow is off (no ProjectWorkflow row)."""
    _, done = states
    info = _info(create_user)

    result = resolve_update_issue_v2(
        None, info,
        slug=workspace.slug,
        project=str(project.id),
        id=str(existing_issue.id),
        issueInput={"state": str(done.id)},
    )

    assert result is not None
    existing_issue.refresh_from_db()
    assert existing_issue.state_id == done.id


@pytest.mark.contract
@pytest.mark.django_db
def test_update_issue_transition_allowed_by_workflow(
    workspace, project, states, workflow_on, existing_issue, create_user
):
    """updateIssueV2: allowed when a WorkflowTransition row permits the move."""
    backlog, done = states
    # Create an explicit allowed transition backlog → done.
    WorkflowTransition.objects.create(
        project=project, state=backlog, transition_state=done,
    )
    info = _info(create_user)

    result = resolve_update_issue_v2(
        None, info,
        slug=workspace.slug,
        project=str(project.id),
        id=str(existing_issue.id),
        issueInput={"state": str(done.id)},
    )

    assert result is not None
    existing_issue.refresh_from_db()
    assert existing_issue.state_id == done.id


# ---------------------------------------------------------------------------
# createEpic
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_create_epic_blocked_in_restricted_state(
    workspace, project, states, workflow_on, creation_blocked, create_user
):
    """createEpic: raises GraphQLError when workflow blocks creation in state."""
    backlog, _ = states
    # Ensure the workspace has an epic IssueType.
    IssueType.objects.get_or_create(workspace=workspace, is_epic=True, defaults={"name": "Epic"})
    info = _info(create_user)

    with pytest.raises(GraphQLError) as exc_info:
        resolve_create_epic(
            None, info,
            slug=workspace.slug,
            project=str(project.id),
            epicInput={"name": "Blocked Epic", "state": str(backlog.id)},
        )

    err = exc_info.value
    assert "WORKFLOW_CREATION_BLOCKED" in str(err.extensions.get("code", ""))
    assert not Issue.objects.filter(name="Blocked Epic", project=project).exists()


# ---------------------------------------------------------------------------
# updateEpic
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_update_epic_transition_blocked(
    workspace, project, states, workflow_on, create_user
):
    """updateEpic: an off-path move (no transition row) auto-files an approval
    request, raises GraphQLError with APPROVAL_REQUESTED and leaves the epic's
    state unchanged."""
    backlog, done = states
    epic_type, _ = IssueType.objects.get_or_create(
        workspace=workspace, is_epic=True, defaults={"name": "Epic"}
    )
    epic = Issue.objects.create(
        name="Epic to update", project=project, workspace=workspace, state=backlog,
        type=epic_type, sequence_id=10,
    )
    info = _info(create_user)

    # No WorkflowTransition row → off-path move that auto-files a request.
    with pytest.raises(GraphQLError) as exc_info:
        resolve_update_epic(
            None, info,
            slug=workspace.slug,
            project=str(project.id),
            epic=str(epic.id),
            epicInput={"state": str(done.id)},
        )

    err = exc_info.value
    assert "WORKFLOW_APPROVAL_REQUESTED" in str(err.extensions.get("code", ""))
    epic.refresh_from_db()
    assert epic.state_id == backlog.id  # must be unchanged
    assert (
        ApprovalRequest.objects.filter(work_item=epic, status="pending").count() == 1
    )


# ---------------------------------------------------------------------------
# createEpic — allowed paths (Правка 3)
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_create_epic_allowed_without_state_config(
    workspace, project, states, workflow_on, create_user
):
    """createEpic: succeeds when workflow is on but no WorkflowStateConfig restricts the state.

    allow_issue_creation defaults to True so the guard should return ALLOW.
    """
    backlog, _ = states
    IssueType.objects.get_or_create(workspace=workspace, is_epic=True, defaults={"name": "Epic"})
    info = _info(create_user)

    # No WorkflowStateConfig row → creation is allowed by default.
    result = resolve_create_epic(
        None, info,
        slug=workspace.slug,
        project=str(project.id),
        epicInput={"name": "Allowed Epic", "state": str(backlog.id)},
    )

    assert result is not None
    assert result.name == "Allowed Epic"
    assert Issue.objects.filter(name="Allowed Epic", project=project).exists()


# ---------------------------------------------------------------------------
# updateEpic — allowed paths (Правка 3)
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_update_epic_transition_allowed_by_workflow(
    workspace, project, states, workflow_on, create_user
):
    """updateEpic: succeeds when an explicit WorkflowTransition permits the move."""
    backlog, done = states
    epic_type, _ = IssueType.objects.get_or_create(
        workspace=workspace, is_epic=True, defaults={"name": "Epic"}
    )
    epic = Issue.objects.create(
        name="Epic for allowed update", project=project, workspace=workspace,
        state=backlog, type=epic_type, sequence_id=20,
    )
    # Register the allowed transition backlog → done.
    WorkflowTransition.objects.create(
        project=project, state=backlog, transition_state=done,
    )
    info = _info(create_user)

    result = resolve_update_epic(
        None, info,
        slug=workspace.slug,
        project=str(project.id),
        epic=str(epic.id),
        epicInput={"state": str(done.id)},
    )

    assert result is not None
    epic.refresh_from_db()
    assert epic.state_id == done.id


# ---------------------------------------------------------------------------
# updateIssueV2 — same-state no-op (Правка 1 coverage)
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_update_issue_same_state_not_blocked(
    workspace, project, states, workflow_on, existing_issue, create_user
):
    """updateIssueV2: same-state update must NOT raise GraphQLError.

    With workflow enabled but no WorkflowTransition, a transition would normally
    be blocked. However, sending the current state again is a no-op and must be
    let through unconditionally (guard_transition early-exit: from == to).
    """
    backlog, _ = states
    info = _info(create_user)

    # existing_issue is already in backlog; send the same state back.
    result = resolve_update_issue_v2(
        None, info,
        slug=workspace.slug,
        project=str(project.id),
        id=str(existing_issue.id),
        issueInput={"name": "Updated name", "state": str(backlog.id)},
    )

    assert result is not None
    existing_issue.refresh_from_db()
    assert existing_issue.state_id == backlog.id
    assert existing_issue.name == "Updated name"


# ---------------------------------------------------------------------------
# I2 fix: default-state resolution when no state is sent
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_create_issue_v2_blocks_when_default_state_restricted_and_no_state_sent(
    workspace, project, states, workflow_on, create_user
):
    """createIssueV2: if no state is provided and the project default state has
    allow_issue_creation=False, the guard must fire and raise GraphQLError.

    Regression guard for I2: previously guard_creation received None when state
    was omitted, causing it to return early without enforcing the policy; Issue.save()
    would then silently assign the default state.
    """
    backlog, _ = states
    # Mark backlog as the project's default state.
    backlog.default = True
    backlog.save(update_fields=["default"])

    WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )
    info = _info(create_user)

    with pytest.raises(GraphQLError) as exc_info:
        resolve_create_issue_v2(
            None, info,
            slug=workspace.slug,
            project=str(project.id),
            issueInput={"name": "Should Be Blocked — no state"},
            # No "state" key in issueInput → resolver must resolve default state.
        )

    err = exc_info.value
    assert "WORKFLOW_CREATION_BLOCKED" in str(err.extensions.get("code", ""))
    assert not Issue.objects.filter(
        name="Should Be Blocked — no state", project=project
    ).exists()


@pytest.mark.contract
@pytest.mark.django_db
def test_create_epic_blocks_when_default_state_restricted_and_no_state_sent(
    workspace, project, states, workflow_on, create_user
):
    """createEpic: if no state is provided and the project default state has
    allow_issue_creation=False, the guard must fire and raise GraphQLError.

    Regression guard for I2: mirrors the createIssueV2 case above, for the epic path.
    """
    backlog, _ = states
    backlog.default = True
    backlog.save(update_fields=["default"])

    WorkflowStateConfig.objects.create(
        project=project, workspace=workspace, state=backlog, allow_issue_creation=False
    )
    IssueType.objects.get_or_create(workspace=workspace, is_epic=True, defaults={"name": "Epic"})
    info = _info(create_user)

    with pytest.raises(GraphQLError) as exc_info:
        resolve_create_epic(
            None, info,
            slug=workspace.slug,
            project=str(project.id),
            epicInput={"name": "Epic Should Be Blocked — no state"},
            # No "state" key → resolver must resolve default state.
        )

    err = exc_info.value
    assert "WORKFLOW_CREATION_BLOCKED" in str(err.extensions.get("code", ""))
    assert not Issue.objects.filter(
        name="Epic Should Be Blocked — no state", project=project
    ).exists()


# ---------------------------------------------------------------------------
# Phase 3 B: auto-file approval request on off-path mobile transition
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.django_db
def test_update_issue_forbidden_stays_hard_no_request(
    workspace, project, states, workflow_on, existing_issue, create_user
):
    """updateIssueV2: a FORBIDDEN transition is a hard block — it raises
    WORKFLOW_TRANSITION_FORBIDDEN and does NOT file an approval request."""
    backlog, done = states
    WorkflowTransition.objects.create(
        project=project, state=backlog, transition_state=done,
        kind=WorkflowTransition.FORBIDDEN,
    )
    info = _info(create_user)

    with pytest.raises(GraphQLError) as exc_info:
        resolve_update_issue_v2(
            None, info,
            slug=workspace.slug,
            project=str(project.id),
            id=str(existing_issue.id),
            issueInput={"state": str(done.id)},
        )

    err = exc_info.value
    assert "WORKFLOW_TRANSITION_FORBIDDEN" in str(err.extensions.get("code", ""))
    existing_issue.refresh_from_db()
    assert existing_issue.state_id == backlog.id  # unchanged
    # No request filed for a hard-forbidden move.
    assert ApprovalRequest.objects.filter(work_item=existing_issue).count() == 0


@pytest.mark.contract
@pytest.mark.django_db
def test_update_issue_approver_moves_directly_no_request(
    workspace, project, states, workflow_on, existing_issue, create_user
):
    """updateIssueV2: an approver (guardian of the target state) moves directly —
    the transition succeeds and NO approval request is created."""
    backlog, done = states
    WorkflowStateGuardian.objects.create(
        project=project, state=done, guardian=create_user
    )
    info = _info(create_user)

    result = resolve_update_issue_v2(
        None, info,
        slug=workspace.slug,
        project=str(project.id),
        id=str(existing_issue.id),
        issueInput={"state": str(done.id)},
    )

    assert result is not None
    existing_issue.refresh_from_db()
    assert existing_issue.state_id == done.id
    assert ApprovalRequest.objects.filter(work_item=existing_issue).count() == 0


@pytest.mark.contract
@pytest.mark.django_db
def test_update_issue_second_off_path_attempt_already_pending(
    workspace, project, states, workflow_on, existing_issue, create_user
):
    """updateIssueV2: a second off-path attempt does not duplicate the request —
    both attempts raise WORKFLOW_APPROVAL_REQUESTED, exactly one PENDING request
    exists, and the second error carries the 'already pending' message."""
    _, done = states
    info = _info(create_user)

    with pytest.raises(GraphQLError) as first_exc:
        resolve_update_issue_v2(
            None, info,
            slug=workspace.slug,
            project=str(project.id),
            id=str(existing_issue.id),
            issueInput={"state": str(done.id)},
        )
    assert "WORKFLOW_APPROVAL_REQUESTED" in str(
        first_exc.value.extensions.get("code", "")
    )

    with pytest.raises(GraphQLError) as second_exc:
        resolve_update_issue_v2(
            None, info,
            slug=workspace.slug,
            project=str(project.id),
            id=str(existing_issue.id),
            issueInput={"state": str(done.id)},
        )

    err = second_exc.value
    assert "WORKFLOW_APPROVAL_REQUESTED" in str(err.extensions.get("code", ""))
    assert str(err) == "This work item is already pending approval."
    assert (
        ApprovalRequest.objects.filter(
            work_item=existing_issue, status="pending"
        ).count()
        == 1
    )
