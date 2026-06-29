# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers.approval import ApprovalRequestSerializer
from plane.db.models import ApprovalRequest, Issue
from plane.workflow.service import (
    ApprovalError,
    create_approval_request,
    decide_approval_request,
)

from .base import BaseAPIView


class ApprovalRequestListCreateEndpoint(BaseAPIView):
    """GET list of approval requests for a work item; POST create one for an
    off-path move that the requester is not permitted to perform directly."""

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        qs = ApprovalRequest.objects.filter(
            work_item_id=issue_id, project_id=project_id
        ).order_by("-created_at")
        return Response(
            ApprovalRequestSerializer(qs, many=True).data, status=status.HTTP_200_OK
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = Issue.objects.get(pk=issue_id, project_id=project_id, workspace__slug=slug)
        to_state_id = request.data.get("to_state")
        if not to_state_id:
            return Response(
                {"error": "to_state is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            req = create_approval_request(
                request.user, issue, to_state_id, comment=request.data.get("comment")
            )
        except ApprovalError as e:
            return Response({"error": e.message}, status=e.status)

        return Response(
            ApprovalRequestSerializer(req).data, status=status.HTTP_201_CREATED
        )


class ApprovalRequestActionEndpoint(BaseAPIView):
    """POST a decision (approve/reject/cancel) on a pending approval request."""

    def _request(self, project_id, issue_id, pk):
        return ApprovalRequest.objects.get(
            pk=pk, work_item_id=issue_id, project_id=project_id
        )

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id, pk, action):
        req = self._request(project_id, issue_id, pk)
        try:
            decide_approval_request(
                request.user, req, action, reason=request.data.get("reason")
            )
        except ApprovalError as e:
            return Response({"error": e.message}, status=e.status)

        return Response(
            ApprovalRequestSerializer(req).data, status=status.HTTP_200_OK
        )
