# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Approval-request notifications.

Creates in-app ``Notification`` rows for the resolved approvers when an approval
is requested, and for the requester when a decision is made. The requester is
never notified as an approver of their own request (four-eyes principle).
"""

from plane.db.models import Notification, ProjectMember
from plane.workflow.approvers import resolve_approver_ids


def _receiver_ids(req):
    """Resolve the set of approver user-ids for an approval request.

    Chain: target-state guardians -> project fallback approvers -> four-eyes
    (any active project member). The requester is always excluded so a person
    can never approve their own off-path move.
    """
    approvers = resolve_approver_ids(req.project_id, req.to_state_id)
    if not approvers:
        # four-eyes: any active project member except the requester
        approvers = [
            str(m.member_id)
            for m in ProjectMember.objects.filter(
                project_id=req.project_id, is_active=True
            )
        ]
    return [a for a in approvers if str(a) != str(req.requested_by_id)]


def notify_approval_requested(req):
    """Create in-app notifications for every resolved approver of `req`."""
    issue = req.work_item
    rows = [
        Notification(
            workspace_id=req.workspace_id,
            project_id=req.project_id,
            entity_identifier=issue.id,
            entity_name="issue",
            title="Approval requested",
            sender="in_app:workflow:approval_requested",
            triggered_by_id=req.requested_by_id,
            receiver_id=rid,
            data={
                "approval_request": str(req.id),
                "to_state": str(req.to_state_id),
            },
        )
        for rid in _receiver_ids(req)
    ]
    if rows:
        Notification.objects.bulk_create(rows, batch_size=100)


def notify_approval_decided(req):
    """Notify the requester that their approval request was decided."""
    Notification.objects.create(
        workspace_id=req.workspace_id,
        project_id=req.project_id,
        entity_identifier=req.work_item_id,
        entity_name="issue",
        title=f"Approval {req.status}",
        sender="in_app:workflow:approval_decided",
        triggered_by_id=req.decided_by_id,
        receiver_id=req.requested_by_id,
        data={"approval_request": str(req.id), "status": req.status},
    )
