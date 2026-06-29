# apps/api/plane/workflow/service.py
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Approval-request domain service — the single source of truth shared by the
REST views (plane/app/views/approval.py) and the GraphQL resolvers
(plane/graphql/areas/workflow_approval.py)."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from plane.db.models import ApprovalRequest, State
from plane.utils.exception_logger import log_exception
from plane.workflow.apply import apply_approved_request
from plane.workflow.approvers import resolve_approver_ids
from plane.workflow.guard import Action, evaluate_transition
from plane.workflow.notify import notify_approval_decided, notify_approval_requested


class ApprovalError(Exception):
    """Domain error carrying a stable code, a human message, an HTTP status, and
    (for duplicate-pending) the pre-existing request."""

    def __init__(self, code, message, status, existing=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.existing = existing


def is_approver(req, user):
    """Whether `user` may decide `req` (four-eyes aware; never the requester)."""
    approvers = resolve_approver_ids(req.project_id, req.to_state_id)
    if approvers:
        return str(user.id) in approvers and str(user.id) != str(req.requested_by_id)
    return str(user.id) != str(req.requested_by_id)


def create_approval_request(actor, work_item, to_state_id, comment=None):
    """Validate and create a PENDING approval request for an off-path move of
    `work_item` to `to_state_id`. Raises ApprovalError on any rule violation."""
    if not State.objects.filter(pk=to_state_id, project_id=work_item.project_id).exists():
        raise ApprovalError(
            "APPROVAL_INVALID_STATE", "Invalid target state for this project.", 400
        )

    decision = evaluate_transition(
        work_item.project_id, work_item.state_id, to_state_id, actor
    )
    if decision.action != Action.NEEDS_APPROVAL:
        # ALLOW => requester may move directly; BLOCK => forbidden, request is moot.
        raise ApprovalError(
            "APPROVAL_NOT_REQUIRED",
            "This move does not require an approval request.",
            400,
        )

    existing = ApprovalRequest.objects.filter(
        work_item_id=work_item.id, status=ApprovalRequest.PENDING
    ).first()
    if existing is not None:
        raise ApprovalError(
            "APPROVAL_DUPLICATE_PENDING",
            "There is already a pending approval request for this work item.",
            400,
            existing=existing,
        )

    try:
        # Savepoint so a lost check-then-create race (the partial-unique
        # `unique_pending_approval_per_work_item` constraint) surfaces as the
        # same clean APPROVAL_DUPLICATE_PENDING 400 instead of a 500, and does
        # not poison an enclosing transaction (e.g. the test request wrapper).
        with transaction.atomic():
            req = ApprovalRequest.objects.create(
                project_id=work_item.project_id,
                workspace=work_item.workspace,
                work_item=work_item,
                from_state_id=work_item.state_id,
                to_state_id=to_state_id,
                requested_by=actor,
                comment=comment,
            )
    except IntegrityError:
        existing = ApprovalRequest.objects.filter(
            work_item_id=work_item.id, status=ApprovalRequest.PENDING
        ).first()
        raise ApprovalError(
            "APPROVAL_DUPLICATE_PENDING",
            "There is already a pending approval request for this work item.",
            400,
            existing=existing,
        )
    try:
        notify_approval_requested(req)
    except Exception as e:
        log_exception(e)
    return req


def decide_approval_request(actor, req, action, reason=None):
    """Apply a decision (approve/reject/cancel) to a PENDING request.
    Mutates and returns `req`. Raises ApprovalError on rule/permission violation."""
    if req.status != ApprovalRequest.PENDING:
        raise ApprovalError(
            "APPROVAL_NOT_PENDING", "This request is no longer pending.", 400
        )

    if action == "cancel":
        if str(actor.id) != str(req.requested_by_id):
            raise ApprovalError(
                "APPROVAL_NOT_REQUESTER", "Only the requester can cancel.", 403
            )
        req.status = ApprovalRequest.CANCELLED
        req.save(update_fields=["status", "updated_at"])
        return req

    if not is_approver(req, actor):
        raise ApprovalError(
            "APPROVAL_NOT_APPROVER",
            "You are not permitted to decide this request.",
            403,
        )

    if action == "approve":
        apply_approved_request(req, actor)  # also marks APPROVED + decided_by/at
        try:
            notify_approval_decided(req)
        except Exception as e:
            log_exception(e)
        return req

    if action == "reject":
        req.status = ApprovalRequest.REJECTED
        req.decided_by = actor
        req.decided_at = timezone.now()
        req.decision_reason = reason
        req.save(
            update_fields=[
                "status", "decided_by", "decided_at", "decision_reason", "updated_at",
            ]
        )
        try:
            notify_approval_decided(req)
        except Exception as e:
            log_exception(e)
        return req

    raise ApprovalError("APPROVAL_UNKNOWN_ACTION", "Unknown action.", 400)


def visible_approval_requests(actor, project_id, work_item_id=None, status="pending"):
    """Requests in `project_id` the actor may see/act on: their own or ones they
    can decide. Optionally narrowed to a work item and/or status (default PENDING).

    `status` is matched against the model's stored value (lowercase choices), so
    callers may pass either the SDL-style "PENDING" or the model constant; it is
    normalised here."""
    qs = ApprovalRequest.objects.filter(project_id=project_id)
    if work_item_id:
        qs = qs.filter(work_item_id=work_item_id)
    if status:
        qs = qs.filter(status=str(status).lower())
    return [
        req
        for req in qs.order_by("-created_at")
        if str(req.requested_by_id) == str(actor.id) or is_approver(req, actor)
    ]
