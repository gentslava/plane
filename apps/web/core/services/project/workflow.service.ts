/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// services
import { API_BASE_URL } from "@plane/constants";
import type {
  TApprovalRequest,
  TProjectWorkflow,
  TWorkflowProjectApprover,
  TWorkflowStateConfig,
  TWorkflowStateGuardian,
  TWorkflowTransition,
} from "@plane/types";
import { APIService } from "@/services/api.service";

export class WorkflowService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  private base(workspaceSlug: string, projectId: string): string {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}`;
  }

  async getWorkflow(workspaceSlug: string, projectId: string): Promise<TProjectWorkflow> {
    return this.get(`${this.base(workspaceSlug, projectId)}/workflow/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateWorkflow(
    workspaceSlug: string,
    projectId: string,
    data: Partial<TProjectWorkflow>
  ): Promise<TProjectWorkflow> {
    return this.patch(`${this.base(workspaceSlug, projectId)}/workflow/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getStateConfigs(workspaceSlug: string, projectId: string): Promise<TWorkflowStateConfig[]> {
    return this.get(`${this.base(workspaceSlug, projectId)}/workflow-states/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateStateConfig(
    workspaceSlug: string,
    projectId: string,
    stateId: string,
    data: Partial<TWorkflowStateConfig>
  ): Promise<TWorkflowStateConfig> {
    return this.patch(`${this.base(workspaceSlug, projectId)}/workflow-states/${stateId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async getTransitions(workspaceSlug: string, projectId: string): Promise<TWorkflowTransition[]> {
    return this.get(`${this.base(workspaceSlug, projectId)}/workflow-transitions/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createTransition(
    workspaceSlug: string,
    projectId: string,
    data: { state: string; transition_state: string; kind?: "allowed" | "forbidden" }
  ): Promise<TWorkflowTransition> {
    return this.post(`${this.base(workspaceSlug, projectId)}/workflow-transitions/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteTransition(workspaceSlug: string, projectId: string, transitionId: string): Promise<void> {
    return this.delete(`${this.base(workspaceSlug, projectId)}/workflow-transitions/${transitionId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // State guardians

  async getStateGuardians(
    workspaceSlug: string,
    projectId: string,
    stateId: string
  ): Promise<TWorkflowStateGuardian[]> {
    return this.get(`${this.base(workspaceSlug, projectId)}/workflow-states/${stateId}/guardians/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async addStateGuardian(
    workspaceSlug: string,
    projectId: string,
    stateId: string,
    guardianId: string
  ): Promise<TWorkflowStateGuardian> {
    return this.post(`${this.base(workspaceSlug, projectId)}/workflow-states/${stateId}/guardians/`, {
      guardian_id: guardianId,
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async removeStateGuardian(
    workspaceSlug: string,
    projectId: string,
    stateId: string,
    guardianId: string
  ): Promise<void> {
    return this.delete(`${this.base(workspaceSlug, projectId)}/workflow-states/${stateId}/guardians/${guardianId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // Project approvers

  async getProjectApprovers(workspaceSlug: string, projectId: string): Promise<TWorkflowProjectApprover[]> {
    return this.get(`${this.base(workspaceSlug, projectId)}/workflow/approvers/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async addProjectApprover(
    workspaceSlug: string,
    projectId: string,
    approverId: string
  ): Promise<TWorkflowProjectApprover> {
    return this.post(`${this.base(workspaceSlug, projectId)}/workflow/approvers/`, { approver_id: approverId })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async removeProjectApprover(workspaceSlug: string, projectId: string, approverId: string): Promise<void> {
    return this.delete(`${this.base(workspaceSlug, projectId)}/workflow/approvers/${approverId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // Approval requests

  async getApprovalRequests(workspaceSlug: string, projectId: string, issueId: string): Promise<TApprovalRequest[]> {
    return this.get(`${this.base(workspaceSlug, projectId)}/work-items/${issueId}/approval-requests/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async createApprovalRequest(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    data: { to_state: string; comment?: string }
  ): Promise<TApprovalRequest> {
    return this.post(`${this.base(workspaceSlug, projectId)}/work-items/${issueId}/approval-requests/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async decideApprovalRequest(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    requestId: string,
    action: "approve" | "reject" | "cancel",
    data?: { reason?: string }
  ): Promise<TApprovalRequest> {
    return this.post(
      `${this.base(workspaceSlug, projectId)}/work-items/${issueId}/approval-requests/${requestId}/${action}/`,
      data ?? {}
    )
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}
