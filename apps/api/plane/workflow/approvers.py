"""Pure target-state approver resolution for off-path moves."""
from typing import Any

from plane.db.models import (
    ProjectMember,
    WorkflowProjectApprover,
    WorkflowStateGuardian,
)


def _active_member_ids(project_id: Any) -> set[str]:
    """Ids of users that are still active members of the project."""
    return set(
        str(m.member_id)
        for m in ProjectMember.objects.filter(project_id=project_id, is_active=True)
    )


def resolve_approver_ids(project_id: Any, to_state_id: Any) -> list[str]:
    """Resolved approvers for an off-path move into `to_state_id`.

    Chain: target-state guardians -> project fallback approvers -> [] (four-eyes,
    meaning any *other* project member may approve). Only active project members
    are ever resolved; a guardian/approver that was deactivated or removed from
    the project falls through to the next tier.
    """
    active = _active_member_ids(project_id)
    guardians = [
        str(g.guardian_id)
        for g in WorkflowStateGuardian.objects.filter(project_id=project_id, state_id=to_state_id)
        if str(g.guardian_id) in active
    ]
    if guardians:
        return guardians
    return [
        str(a.approver_id)
        for a in WorkflowProjectApprover.objects.filter(project_id=project_id)
        if str(a.approver_id) in active
    ]
