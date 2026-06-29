# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework import status
from rest_framework.exceptions import APIException

from .guard import Decision


class WorkflowBlocked(APIException):
    """Raised when a workflow rule blocks a creation or transition.

    Carries the structured Decision payload as DRF `detail` so every call site
    returns an identical error contract.

    We bypass DRF's `_get_error_details` serialisation by setting `self.detail`
    directly so native Python types (bool, list) in `decision.context` are
    preserved for test assertions and downstream JSON serialisation.
    """

    def __init__(self, decision: Decision, http_status: int):
        self.status_code = http_status
        # Fixed contract fields go last so a stray `error_code`/`message` key in
        # `context` can never overwrite them.
        self.detail = {
            **decision.context,
            "error_code": decision.code,
            "message": decision.message,
        }


_CODE_TO_STATUS = {
    "WORKFLOW_CREATION_BLOCKED": status.HTTP_400_BAD_REQUEST,
    "WORKFLOW_TRANSITION_FORBIDDEN": status.HTTP_403_FORBIDDEN,
    "WORKFLOW_TRANSITION_NEEDS_APPROVAL": status.HTTP_403_FORBIDDEN,
}


def block_status_for(decision: Decision) -> int:
    """Map a blocking Decision code to its HTTP status."""
    return _CODE_TO_STATUS.get(decision.code, status.HTTP_403_FORBIDDEN)
