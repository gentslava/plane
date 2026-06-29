# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views.workflow import (
    ProjectWorkflowEndpoint,
    WorkflowStateConfigEndpoint,
    WorkflowStateConfigDetailEndpoint,
    WorkflowTransitionEndpoint,
    WorkflowTransitionDetailEndpoint,
    WorkflowStateGuardianEndpoint,
    WorkflowProjectApproverEndpoint,
)

urlpatterns = [
    # workflow toggle
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow/",
        ProjectWorkflowEndpoint.as_view(),
        name="project-workflow",
    ),
    # state configs list
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-states/",
        WorkflowStateConfigEndpoint.as_view(),
        name="project-workflow-states",
    ),
    # state config detail (patch by state_id)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-states/<uuid:state_id>/",
        WorkflowStateConfigDetailEndpoint.as_view(),
        name="project-workflow-state-detail",
    ),
    # transitions list + create
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-transitions/",
        WorkflowTransitionEndpoint.as_view(),
        name="project-workflow-transitions",
    ),
    # transition delete
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-transitions/<uuid:pk>/",
        WorkflowTransitionDetailEndpoint.as_view(),
        name="project-workflow-transition-detail",
    ),
    # state guardians list + add
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-states/<uuid:state_id>/guardians/",
        WorkflowStateGuardianEndpoint.as_view(),
        name="workflow-state-guardians",
    ),
    # state guardian detail (delete by guardian_id)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow-states/<uuid:state_id>/guardians/<uuid:guardian_id>/",
        WorkflowStateGuardianEndpoint.as_view(),
        name="workflow-state-guardian-detail",
    ),
    # project-level approvers list + add
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow/approvers/",
        WorkflowProjectApproverEndpoint.as_view(),
        name="workflow-project-approvers",
    ),
    # project approver detail (delete by approver_id)
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/workflow/approvers/<uuid:approver_id>/",
        WorkflowProjectApproverEndpoint.as_view(),
        name="workflow-project-approver-detail",
    ),
]
