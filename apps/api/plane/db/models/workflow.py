# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.conf import settings
from django.db import models

from .project import ProjectBaseModel


class ProjectWorkflow(ProjectBaseModel):
    """Master toggle for workflow enforcement on a project."""

    is_enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "workspace"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_project_workflow_active",
            )
        ]
        db_table = "project_workflows"
        verbose_name = "Project Workflow"
        verbose_name_plural = "Project Workflows"

    def __str__(self):
        return f"ProjectWorkflow({self.project_id}, enabled={self.is_enabled})"


class WorkflowStateConfig(ProjectBaseModel):
    """Per-state configuration: whether new issues can be created in this state."""

    state = models.ForeignKey(
        "db.State", on_delete=models.CASCADE, related_name="workflow_config"
    )
    allow_issue_creation = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "state"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_workflow_state_config_active",
            )
        ]
        db_table = "workflow_state_configs"
        verbose_name = "Workflow State Config"
        verbose_name_plural = "Workflow State Configs"

    def __str__(self):
        return f"WorkflowStateConfig(state={self.state_id}, allow={self.allow_issue_creation})"


class WorkflowTransition(ProjectBaseModel):
    """A configured state transition: issues in `state` may move to `transition_state`.

    `kind` distinguishes on-path moves (`allowed`) from hard-forbidden ones
    (`forbidden`). Moves with no transition row are off-path and require approval.
    """

    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"
    KIND_CHOICES = ((ALLOWED, "Allowed"), (FORBIDDEN, "Forbidden"))

    state = models.ForeignKey(
        "db.State", on_delete=models.CASCADE, related_name="outgoing_transitions"
    )
    transition_state = models.ForeignKey(
        "db.State", on_delete=models.CASCADE, related_name="incoming_transitions"
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=ALLOWED)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "state", "transition_state"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_workflow_transition_active",
            )
        ]
        db_table = "workflow_transitions"
        verbose_name = "Workflow Transition"
        verbose_name_plural = "Workflow Transitions"

    def __str__(self):
        return f"WorkflowTransition({self.state_id} -> {self.transition_state_id})"


class WorkflowStateGuardian(ProjectBaseModel):
    """A person who approves off-path moves INTO `state`."""

    state = models.ForeignKey(
        "db.State", on_delete=models.CASCADE, related_name="workflow_guardians"
    )
    guardian = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workflow_state_guardianships",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["state", "guardian"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_workflow_state_guardian_active",
            )
        ]
        db_table = "workflow_state_guardians"
        verbose_name = "Workflow State Guardian"
        verbose_name_plural = "Workflow State Guardians"

    def __str__(self):
        return f"WorkflowStateGuardian(state={self.state_id}, guardian={self.guardian_id})"


class WorkflowProjectApprover(ProjectBaseModel):
    """Project-level fallback approver for off-path moves."""

    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workflow_project_approvals",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "approver"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_workflow_project_approver_active",
            )
        ]
        db_table = "workflow_project_approvers"
        verbose_name = "Workflow Project Approver"
        verbose_name_plural = "Workflow Project Approvers"

    def __str__(self):
        return f"WorkflowProjectApprover(project={self.project_id}, approver={self.approver_id})"


class ApprovalRequest(ProjectBaseModel):
    """A pending off-path move awaiting approval. Not a work item."""

    PENDING, APPROVED, REJECTED, CANCELLED = (
        "pending",
        "approved",
        "rejected",
        "cancelled",
    )
    STATUS_CHOICES = (
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    )

    work_item = models.ForeignKey(
        "db.Issue", on_delete=models.CASCADE, related_name="approval_requests"
    )
    from_state = models.ForeignKey(
        "db.State", on_delete=models.CASCADE, related_name="+"
    )
    to_state = models.ForeignKey(
        "db.State", on_delete=models.CASCADE, related_name="+"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workflow_approval_requests",
    )
    comment = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflow_approval_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["work_item"],
                condition=models.Q(status="pending", deleted_at__isnull=True),
                name="unique_pending_approval_per_work_item",
            )
        ]
        db_table = "workflow_approval_requests"
        ordering = ["-created_at"]
        verbose_name = "Approval Request"
        verbose_name_plural = "Approval Requests"

    def __str__(self):
        return f"ApprovalRequest(work_item={self.work_item_id}, status={self.status})"


class WorkflowActivity(ProjectBaseModel):
    """Audit log for workflow configuration changes."""

    field = models.CharField(max_length=255)
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "workflow_activities"
        ordering = ["-created_at"]
        verbose_name = "Workflow Activity"
        verbose_name_plural = "Workflow Activities"

    def __str__(self):
        return f"WorkflowActivity(field={self.field}, project={self.project_id})"
