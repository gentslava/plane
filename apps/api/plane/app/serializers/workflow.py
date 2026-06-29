# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from rest_framework import serializers

from .base import BaseSerializer
from plane.db.models import (
    ProjectWorkflow,
    State,
    WorkflowProjectApprover,
    WorkflowStateConfig,
    WorkflowStateGuardian,
    WorkflowTransition,
    WorkflowActivity,
)


class ProjectWorkflowSerializer(BaseSerializer):
    class Meta:
        model = ProjectWorkflow
        fields = ["id", "project", "workspace", "is_enabled"]
        read_only_fields = ["id", "project", "workspace"]


class WorkflowStateConfigSerializer(BaseSerializer):
    class Meta:
        model = WorkflowStateConfig
        fields = ["id", "project", "workspace", "state", "allow_issue_creation"]
        read_only_fields = ["id", "project", "workspace", "state"]


class WorkflowTransitionSerializer(BaseSerializer):
    def validate(self, data):
        project_id = self.context.get("project_id")
        if project_id:
            state = data.get("state")
            transition_state = data.get("transition_state")
            if state and not State.objects.filter(project_id=project_id, pk=state.pk).exists():
                raise serializers.ValidationError(
                    {"state": "State does not belong to this project."}
                )
            if transition_state and not State.objects.filter(
                project_id=project_id, pk=transition_state.pk
            ).exists():
                raise serializers.ValidationError(
                    {"transition_state": "Transition state does not belong to this project."}
                )
        return data

    class Meta:
        model = WorkflowTransition
        fields = ["id", "project", "workspace", "state", "transition_state", "kind"]
        read_only_fields = ["id", "project", "workspace"]


class WorkflowStateGuardianSerializer(BaseSerializer):
    class Meta:
        model = WorkflowStateGuardian
        fields = ["id", "state", "guardian"]


class WorkflowProjectApproverSerializer(BaseSerializer):
    class Meta:
        model = WorkflowProjectApprover
        fields = ["id", "approver"]


class WorkflowActivitySerializer(BaseSerializer):
    class Meta:
        model = WorkflowActivity
        fields = ["id", "field", "old_value", "new_value", "created_at"]
        read_only_fields = ["id", "created_at"]
