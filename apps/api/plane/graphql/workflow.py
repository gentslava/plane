# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Thin GraphQL-layer helpers that wrap the workflow guard.

These eliminate the inline boilerplate from every mutation that needs to
enforce creation / transition rules and ensure the same early-exit logic
(None state, same-state no-op) is applied consistently.
"""

from graphql import GraphQLError

from plane.workflow.guard import Action, evaluate_creation, evaluate_transition
from plane.workflow.service import ApprovalError, create_approval_request


def guard_creation(project_id, state_id, actor):
    """Raise GraphQLError if a work item may not be created in `state_id`."""
    if state_id is None:
        return
    decision = evaluate_creation(project_id, state_id, actor)
    if decision.action != Action.ALLOW:
        raise GraphQLError(decision.message, extensions={"code": decision.code, "context": decision.context})


def guard_transition(project_id, from_state_id, to_state_id, actor):
    """Raise GraphQLError if `actor` may not move from `from_state_id` to `to_state_id`."""
    if to_state_id is None or from_state_id is None or str(from_state_id) == str(to_state_id):
        return
    decision = evaluate_transition(project_id, from_state_id, to_state_id, actor)
    if decision.action != Action.ALLOW:
        raise GraphQLError(decision.message, extensions={"code": decision.code, "context": decision.context})


def guard_transition_or_request(project_id, from_state_id, to_state_id, actor, work_item):
    """Like guard_transition, but on an off-path move (NEEDS_APPROVAL) auto-files an
    approval request and raises WORKFLOW_APPROVAL_REQUESTED so a native client (no
    Request-approval button) requests just by attempting the move. FORBIDDEN stays a
    hard block; ALLOW returns and the move proceeds."""
    if to_state_id is None or from_state_id is None or str(from_state_id) == str(to_state_id):
        return
    decision = evaluate_transition(project_id, from_state_id, to_state_id, actor)
    if decision.action == Action.ALLOW:
        return
    if decision.action == Action.NEEDS_APPROVAL:
        try:
            req = create_approval_request(actor, work_item, to_state_id)
            message = "This status change needs approval; a request has been submitted."
        except ApprovalError as e:
            if e.code == "APPROVAL_DUPLICATE_PENDING" and e.existing is not None:
                req = e.existing
                message = "This work item is already pending approval."
            else:
                raise GraphQLError(
                    e.message, extensions={"code": e.code, "context": decision.context}
                )
        raise GraphQLError(
            message,
            extensions={
                "code": "WORKFLOW_APPROVAL_REQUESTED",
                "requestId": str(req.id),
                "context": decision.context,
            },
        )
    # FORBIDDEN / any other block
    raise GraphQLError(decision.message, extensions={"code": decision.code, "context": decision.context})
