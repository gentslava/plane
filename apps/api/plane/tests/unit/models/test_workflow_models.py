# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from django.db import transaction
from django.db.utils import IntegrityError

from plane.db.models import (
    ApprovalRequest,
    Issue,
    Project,
    ProjectMember,
    ProjectWorkflow,
    State,
    User,
    WorkflowActivity,
    WorkflowProjectApprover,
    WorkflowStateConfig,
    WorkflowStateGuardian,
    WorkflowTransition,
    WorkspaceMember,
)


@pytest.fixture
def project(db, workspace, create_user):
    return Project.objects.create(
        name="WF Project", identifier="WF", workspace=workspace, created_by=create_user,
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


@pytest.fixture
def create_issue(db):
    """Factory: create an Issue for a given project in a given state."""

    def _create(project, state, name="WF Issue"):
        return Issue.objects.create(
            name=name,
            project=project,
            workspace=project.workspace,
            state=state,
        )

    return _create


@pytest.mark.unit
@pytest.mark.django_db
def test_project_workflow_defaults(project, workspace):
    wf = ProjectWorkflow.objects.create(project=project, workspace=workspace)
    assert wf.is_enabled is False


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_unique_active(project, workspace, states):
    backlog, done = states
    WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    with pytest.raises(IntegrityError):
        WorkflowTransition.objects.create(
            project=project, workspace=workspace, state=backlog, transition_state=done
        )


@pytest.mark.unit
@pytest.mark.django_db
def test_state_config_default_allows_creation(project, workspace, states):
    backlog, _ = states
    cfg = WorkflowStateConfig.objects.create(project=project, workspace=workspace, state=backlog)
    assert cfg.allow_issue_creation is True


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_recreate_after_soft_delete(project, workspace, states):
    backlog, done = states
    transition = WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    # Soft delete sets deleted_at, which the partial unique constraint excludes.
    transition.delete()
    # Recreating the same active transition must not raise IntegrityError.
    recreated = WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    assert recreated.id is not None


@pytest.mark.unit
@pytest.mark.django_db
def test_workflow_activity_create(project, workspace, create_user):
    # BaseModel.save() resolves the auditing user from the request context
    # (crum) and ignores a `created_by` kwarg passed to create(); the explicit
    # `created_by_id` save hook is how callers (e.g. the Task 9 service layer)
    # attribute the activity to an actor.
    activity = WorkflowActivity(
        project=project,
        workspace=workspace,
        field="is_enabled",
        old_value="False",
        new_value="True",
    )
    activity.save(created_by_id=create_user.id)
    assert activity.id is not None
    assert activity.created_by == create_user


@pytest.mark.unit
@pytest.mark.django_db
def test_transition_kind_defaults_allowed(project, workspace, states):
    backlog, done = states
    t = WorkflowTransition.objects.create(
        project=project, workspace=workspace, state=backlog, transition_state=done
    )
    assert t.kind == "allowed"


@pytest.mark.unit
@pytest.mark.django_db
def test_state_guardian_and_project_approver(project, workspace, states, member_user):
    backlog, done = states
    g = WorkflowStateGuardian.objects.create(
        project=project, workspace=workspace, state=done, guardian=member_user
    )
    pa = WorkflowProjectApprover.objects.create(
        project=project, workspace=workspace, approver=member_user
    )
    assert g.guardian_id == member_user.id
    assert pa.approver_id == member_user.id


@pytest.mark.unit
@pytest.mark.django_db
def test_approval_request_defaults_pending(project, workspace, states, member_user, create_issue):
    backlog, done = states
    issue = create_issue(project, backlog)
    req = ApprovalRequest.objects.create(
        project=project, workspace=workspace, work_item=issue,
        from_state=backlog, to_state=done, requested_by=member_user,
    )
    assert req.status == "pending"


@pytest.mark.unit
@pytest.mark.django_db
def test_only_one_pending_approval_per_work_item(project, workspace, states, member_user, create_issue):
    backlog, done = states
    issue = create_issue(project, backlog)
    ApprovalRequest.objects.create(
        project=project, workspace=workspace, work_item=issue,
        from_state=backlog, to_state=done, requested_by=member_user,
    )
    # A second PENDING request for the same work item violates the partial
    # unique constraint. Wrap in atomic() so the poisoned transaction does not
    # break the surrounding test transaction.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ApprovalRequest.objects.create(
                project=project, workspace=workspace, work_item=issue,
                from_state=backlog, to_state=done, requested_by=member_user,
            )


@pytest.mark.unit
@pytest.mark.django_db
def test_pending_constraint_ignores_non_pending(project, workspace, states, member_user, create_issue):
    backlog, done = states
    issue = create_issue(project, backlog)
    first = ApprovalRequest.objects.create(
        project=project, workspace=workspace, work_item=issue,
        from_state=backlog, to_state=done, requested_by=member_user,
    )
    # Once the first request leaves the pending state, the partial constraint
    # (condition status="pending") no longer applies to it, so a new pending
    # request for the same work item is allowed.
    first.status = ApprovalRequest.APPROVED
    first.save()
    second = ApprovalRequest.objects.create(
        project=project, workspace=workspace, work_item=issue,
        from_state=backlog, to_state=done, requested_by=member_user,
    )
    assert second.status == "pending"
