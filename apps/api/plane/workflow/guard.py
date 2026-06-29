# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Pure decision logic for guarded workflow transitions. No HTTP/DRF here."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from plane.db.models import (
    ProjectWorkflow,
    WorkflowStateConfig,
    WorkflowTransition,
)
from plane.workflow.approvers import resolve_approver_ids


class Action(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    NEEDS_APPROVAL = "needs_approval"  # reserved for future approval flow


@dataclass
class Decision:
    action: Action
    code: str = ""
    message: str = ""
    context: dict = field(default_factory=dict)


def _workflow_enabled(project_id: Any) -> bool:
    return ProjectWorkflow.objects.filter(project_id=project_id, is_enabled=True).exists()


def evaluate_creation(project_id: Any, state_id: Any, actor: object) -> Decision:
    """Decide whether a new work item may be created in `state_id`.

    `actor` is currently unused; it is reserved for future per-user creation
    rules (mirroring the reserved `Action.NEEDS_APPROVAL`).
    """
    if not _workflow_enabled(project_id):
        return Decision(action=Action.ALLOW)

    config = WorkflowStateConfig.objects.filter(
        project_id=project_id, state_id=state_id
    ).first()
    if config is None or config.allow_issue_creation:
        return Decision(action=Action.ALLOW)

    return Decision(
        action=Action.BLOCK,
        code="WORKFLOW_CREATION_BLOCKED",
        message="New work items cannot be created in this state due to workflow restrictions.",
        context={"state": str(state_id)},
    )


def evaluate_transition(
    project_id: Any, from_state_id: Any, to_state_id: Any, actor: object
) -> Decision:
    """Classify a move from_state -> to_state for `actor`.

    Decision matrix:
      * workflow disabled                          -> ALLOW
      * `(from, to)` row with kind="forbidden"     -> BLOCK (hard-forbidden)
      * `(from, to)` row with kind="allowed"       -> ALLOW (on-path, free for all)
      * otherwise (off-path):
          - actor is a resolved approver           -> ALLOW (acts directly)
          - else                                   -> NEEDS_APPROVAL
    """
    if not _workflow_enabled(project_id):
        return Decision(action=Action.ALLOW)

    ctx = {"from_state": str(from_state_id), "to_state": str(to_state_id)}

    transition = WorkflowTransition.objects.filter(
        project_id=project_id,
        state_id=from_state_id,
        transition_state_id=to_state_id,
    ).first()

    if transition is not None and transition.kind == WorkflowTransition.FORBIDDEN:
        return Decision(
            action=Action.BLOCK,
            code="WORKFLOW_TRANSITION_FORBIDDEN",
            message="This status transition is forbidden by the project workflow.",
            context=ctx,
        )

    if transition is not None and transition.kind == WorkflowTransition.ALLOWED:
        return Decision(action=Action.ALLOW)  # on-path is free for everyone

    # off-path: needs approval
    approvers = resolve_approver_ids(project_id, to_state_id)
    actor_id = str(actor.id) if actor is not None and getattr(actor, "id", None) else None
    if approvers and actor_id in approvers:
        return Decision(action=Action.ALLOW)  # an approver performs it directly

    return Decision(
        action=Action.NEEDS_APPROVAL,
        code="WORKFLOW_TRANSITION_NEEDS_APPROVAL",
        message="This status change needs approval before it can be applied.",
        context={**ctx, "approvers": approvers, "any_member": not approvers},
    )
