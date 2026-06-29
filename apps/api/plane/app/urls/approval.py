# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views.approval import (
    ApprovalRequestActionEndpoint,
    ApprovalRequestListCreateEndpoint,
)

urlpatterns = [
    # list + create approval requests for a work item
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/approval-requests/",
        ApprovalRequestListCreateEndpoint.as_view(),
        name="approval-requests",
    ),
    # decide on a request (approve/reject/cancel)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/approval-requests/<uuid:pk>/<str:action>/",
        ApprovalRequestActionEndpoint.as_view(),
        name="approval-request-action",
    ),
]
