# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Single choke point that turns guard Decisions into DRF errors.

This is the policy layer: today it only calls the workflow guard, but future
rules (e.g. Field Permissions) plug in here without touching the call sites.
"""

from typing import Any

from .exceptions import WorkflowBlocked, block_status_for
from .guard import Action, evaluate_creation, evaluate_transition


def enforce_creation(project_id: Any, state_id: Any, actor: object) -> None:
    """Raise WorkflowBlocked if a new work item may not be created in `state_id`."""
    if state_id is None:
        return
    decision = evaluate_creation(project_id, state_id, actor)
    if decision.action != Action.ALLOW:
        raise WorkflowBlocked(decision, block_status_for(decision))


def enforce_transition(
    project_id: Any, from_state_id: Any, to_state_id: Any, actor: object
) -> None:
    """Raise WorkflowBlocked if `actor` may not move from `from_state_id` to `to_state_id`."""
    # No status change → nothing to enforce. A None `from_state_id` is the initial
    # status assignment (not a managed transition), so it is never blocked here.
    if (
        to_state_id is None
        or from_state_id is None
        or str(from_state_id) == str(to_state_id)
    ):
        return
    decision = evaluate_transition(project_id, from_state_id, to_state_id, actor)
    if decision.action != Action.ALLOW:
        raise WorkflowBlocked(decision, block_status_for(decision))
