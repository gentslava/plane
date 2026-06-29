/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export type TProjectWorkflow = {
  // null until the workflow row is first created (PATCH workflow/)
  id: string | null;
  project?: string;
  workspace?: string | null;
  is_enabled: boolean;
};

export type TWorkflowStateConfig = {
  id: string;
  state: string;
  allow_issue_creation: boolean;
};

export type TWorkflowTransition = {
  id: string;
  state: string; // from-state id
  transition_state: string; // to-state id
  kind: "allowed" | "forbidden";
};

export type TWorkflowStateGuardian = { id: string; state: string; guardian: string };
export type TWorkflowProjectApprover = { id: string; approver: string };

export type TApprovalRequestStatus = "pending" | "approved" | "rejected" | "cancelled";
export type TApprovalRequest = {
  id: string;
  work_item: string;
  from_state: string;
  to_state: string;
  requested_by: string;
  comment: string | null;
  status: TApprovalRequestStatus;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  created_at: string;
};
