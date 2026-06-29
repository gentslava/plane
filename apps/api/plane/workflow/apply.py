# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Apply an approved off-path move (bypassing the pre_save guard net)."""

from django.utils import timezone

from plane.db.models import ApprovalRequest
from plane.workflow.signals import workflow_bypass


def apply_approved_request(req: ApprovalRequest, decided_by) -> None:
    """Move the work item to ``to_state`` and mark the request APPROVED.

    Wrapped in :func:`workflow_bypass` so the pre_save signal does not re-block
    the (still off-path) move. The caller is responsible for authorization.
    """
    issue = req.work_item
    with workflow_bypass():
        issue.state_id = req.to_state_id
        issue.save(update_fields=["state", "updated_at"])
    req.status = ApprovalRequest.APPROVED
    req.decided_by = decided_by
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
