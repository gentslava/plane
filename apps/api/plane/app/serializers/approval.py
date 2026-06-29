# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from .base import BaseSerializer
from plane.db.models import ApprovalRequest


class ApprovalRequestSerializer(BaseSerializer):
    class Meta:
        model = ApprovalRequest
        fields = [
            "id",
            "work_item",
            "from_state",
            "to_state",
            "requested_by",
            "comment",
            "status",
            "decided_by",
            "decided_at",
            "decision_reason",
            "created_at",
        ]
        read_only_fields = ["status", "requested_by", "decided_by", "decided_at"]
