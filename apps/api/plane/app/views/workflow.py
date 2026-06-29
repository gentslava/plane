# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Standard library imports
import uuid

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import allow_permission, ROLE
from plane.app.serializers.workflow import (
    ProjectWorkflowSerializer,
    WorkflowProjectApproverSerializer,
    WorkflowStateConfigSerializer,
    WorkflowStateGuardianSerializer,
    WorkflowTransitionSerializer,
)
from plane.db.models import (
    Project,
    ProjectMember,
    ProjectWorkflow,
    State,
    WorkflowProjectApprover,
    WorkflowStateConfig,
    WorkflowStateGuardian,
    WorkflowTransition,
)

from .base import BaseAPIView


def _is_active_project_member(slug, project_id, member_id):
    """Return True iff `member_id` is an active member of the project."""
    return ProjectMember.objects.filter(
        project_id=project_id,
        workspace__slug=slug,
        member_id=member_id,
        is_active=True,
    ).exists()


def _valid_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


class ProjectWorkflowEndpoint(BaseAPIView):
    """GET / PATCH the single ProjectWorkflow record for a project."""

    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id):
        workflow = ProjectWorkflow.objects.filter(
            project_id=project_id, workspace__slug=slug
        ).first()
        if workflow is None:
            # Return a default response without creating a DB row; creation
            # happens only in PATCH (idempotent GET). Shape mirrors the
            # serializer so clients see a stable schema before the row exists.
            return Response(
                {"id": None, "project": str(project_id), "workspace": None, "is_enabled": False},
                status=status.HTTP_200_OK,
            )
        return Response(ProjectWorkflowSerializer(workflow).data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def patch(self, request, slug, project_id):
        project = Project.objects.get(id=project_id, workspace__slug=slug)
        workflow, _ = ProjectWorkflow.objects.get_or_create(
            project_id=project_id,
            workspace=project.workspace,
        )
        serializer = ProjectWorkflowSerializer(workflow, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class WorkflowStateConfigEndpoint(BaseAPIView):
    """GET list of WorkflowStateConfig for a project; PATCH a specific state config."""

    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id):
        configs = WorkflowStateConfig.objects.filter(
            project_id=project_id,
            workspace__slug=slug,
        )
        serializer = WorkflowStateConfigSerializer(configs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class WorkflowStateConfigDetailEndpoint(BaseAPIView):
    """PATCH a specific state config (get_or_create by state_id)."""

    @allow_permission([ROLE.ADMIN])
    def patch(self, request, slug, project_id, state_id):
        if not State.objects.filter(pk=state_id, project_id=project_id).exists():
            return Response(
                {"error": "State does not belong to this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        project = Project.objects.get(id=project_id, workspace__slug=slug)
        config, _ = WorkflowStateConfig.objects.get_or_create(
            project_id=project_id,
            workspace=project.workspace,
            state_id=state_id,
        )
        serializer = WorkflowStateConfigSerializer(config, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class WorkflowTransitionEndpoint(BaseAPIView):
    """GET list + POST create WorkflowTransitions for a project."""

    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id):
        transitions = WorkflowTransition.objects.filter(
            project_id=project_id,
            workspace__slug=slug,
        )
        serializer = WorkflowTransitionSerializer(transitions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id):
        project = Project.objects.get(id=project_id, workspace__slug=slug)
        kind = request.data.get("kind", WorkflowTransition.ALLOWED)
        if kind not in (WorkflowTransition.ALLOWED, WorkflowTransition.FORBIDDEN):
            return Response(
                {"error": "kind must be one of 'allowed' or 'forbidden'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = WorkflowTransitionSerializer(
            data=request.data, context={"project_id": project_id}
        )
        if serializer.is_valid():
            transition = serializer.save(
                project_id=project_id, workspace=project.workspace, kind=kind
            )
            return Response(WorkflowTransitionSerializer(transition).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class WorkflowTransitionDetailEndpoint(BaseAPIView):
    """DELETE a specific WorkflowTransition."""

    @allow_permission([ROLE.ADMIN])
    def delete(self, request, slug, project_id, pk):
        transition = WorkflowTransition.objects.get(
            pk=pk,
            project_id=project_id,
            workspace__slug=slug,
        )
        transition.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowStateGuardianEndpoint(BaseAPIView):
    """Manage the guardians of a state (who approve off-path moves INTO it).

    GET    list guardians of `state_id`
    POST   add a guardian by `guardian_id` (must be an active project member)
    DELETE remove a guardian by `guardian_id`
    """

    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id, state_id):
        guardians = WorkflowStateGuardian.objects.filter(
            project_id=project_id,
            workspace__slug=slug,
            state_id=state_id,
        )
        serializer = WorkflowStateGuardianSerializer(guardians, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id, state_id):
        guardian_id = request.data.get("guardian_id")
        if not guardian_id:
            return Response(
                {"error": "guardian_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        if not _valid_uuid(guardian_id):
            return Response(
                {"error": "guardian_id must be a valid UUID."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not State.objects.filter(pk=state_id, project_id=project_id, workspace__slug=slug).exists():
            return Response(
                {"error": "State does not belong to this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _is_active_project_member(slug, project_id, guardian_id):
            return Response(
                {"error": "The guardian must be an active member of this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        project = Project.objects.get(id=project_id, workspace__slug=slug)
        guardian, _ = WorkflowStateGuardian.objects.get_or_create(
            project_id=project_id,
            state_id=state_id,
            guardian_id=guardian_id,
            defaults={"workspace": project.workspace},
        )
        return Response(
            WorkflowStateGuardianSerializer(guardian).data,
            status=status.HTTP_201_CREATED,
        )

    @allow_permission([ROLE.ADMIN])
    def delete(self, request, slug, project_id, state_id, guardian_id):
        guardian = WorkflowStateGuardian.objects.filter(
            project_id=project_id,
            workspace__slug=slug,
            state_id=state_id,
            guardian_id=guardian_id,
        ).first()
        if guardian is None:
            return Response(
                {"error": "This guardian is not assigned to the state."},
                status=status.HTTP_404_NOT_FOUND,
            )
        guardian.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowProjectApproverEndpoint(BaseAPIView):
    """Manage project-level fallback approvers for off-path moves.

    GET    list project approvers
    POST   add an approver by `approver_id` (must be an active project member)
    DELETE remove an approver by `approver_id`
    """

    @allow_permission([ROLE.ADMIN])
    def get(self, request, slug, project_id):
        approvers = WorkflowProjectApprover.objects.filter(
            project_id=project_id,
            workspace__slug=slug,
        )
        serializer = WorkflowProjectApproverSerializer(approvers, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission([ROLE.ADMIN])
    def post(self, request, slug, project_id):
        approver_id = request.data.get("approver_id")
        if not approver_id:
            return Response(
                {"error": "approver_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        if not _valid_uuid(approver_id):
            return Response(
                {"error": "approver_id must be a valid UUID."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _is_active_project_member(slug, project_id, approver_id):
            return Response(
                {"error": "The approver must be an active member of this project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        project = Project.objects.get(id=project_id, workspace__slug=slug)
        approver, _ = WorkflowProjectApprover.objects.get_or_create(
            project_id=project_id,
            approver_id=approver_id,
            defaults={"workspace": project.workspace},
        )
        return Response(
            WorkflowProjectApproverSerializer(approver).data,
            status=status.HTTP_201_CREATED,
        )

    @allow_permission([ROLE.ADMIN])
    def delete(self, request, slug, project_id, approver_id):
        approver = WorkflowProjectApprover.objects.filter(
            project_id=project_id,
            workspace__slug=slug,
            approver_id=approver_id,
        ).first()
        if approver is None:
            return Response(
                {"error": "This approver is not assigned to the project."},
                status=status.HTTP_404_NOT_FOUND,
            )
        approver.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
