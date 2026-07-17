/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { set } from "lodash-es";
import { action, makeObservable, observable, runInAction } from "mobx";
import { computedFn } from "mobx-utils";
// plane imports
import type {
  TApprovalRequest,
  TProjectWorkflow,
  TWorkflowProjectApprover,
  TWorkflowStateConfig,
  TWorkflowStateGuardian,
  TWorkflowTransition,
} from "@plane/types";
// services
import { WorkflowService } from "@/services/project/workflow.service";
import type { RootStore } from "@/store/root.store";

/**
 * Transient UI state for the blocked-transition dialog. A single source of truth
 * so every write path (board drag-n-drop, peek, full issue page) drives one dialog
 * instance, and the peek outside-click detector can gate on it. Carries the issue
 * + target state so the modal can POST an approval request for the needs-approval case.
 */
export type TWorkflowBlocker = {
  open: boolean;
  kind: "needs_approval" | "forbidden" | null;
  reviewers: string[]; // resolved approvers (empty => any member)
  anyMember: boolean;
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  toState: string;
};

export interface IWorkflowStore {
  // observables
  workflowByProject: Record<string, TProjectWorkflow>;
  transitionsByProject: Record<string, TWorkflowTransition[]>;
  stateConfigsByProject: Record<string, TWorkflowStateConfig[]>;
  guardiansByState: Record<string, TWorkflowStateGuardian[]>;
  projectApprovers: Record<string, TWorkflowProjectApprover[]>;
  approvalRequestsByIssue: Record<string, TApprovalRequest[]>;
  // blocked-transition dialog (transient UI state, single source of truth so every
  // write path — board drag-n-drop, peek, full issue page — drives one dialog
  // instance, and the peek outside-click detector can gate on it)
  blocker: TWorkflowBlocker;
  closeBlocker: () => void;
  tryOpenBlockerFromError: (
    error: unknown,
    ctx: { workspaceSlug: string; projectId: string; issueId: string; toState: string }
  ) => boolean;
  requestApprovalFromBlocker: (comment?: string) => Promise<void>;
  // computed actions
  isTransitionAllowed: (projectId: string, fromStateId: string, toStateId: string, userId: string) => boolean;
  getTransitionKind: (
    projectId: string,
    fromStateId: string,
    toStateId: string
  ) => "allowed" | "forbidden" | "off_path";
  getPendingRequest: (issueId: string) => TApprovalRequest | undefined;
  // fetch actions
  fetchWorkflow: (workspaceSlug: string, projectId: string) => Promise<TProjectWorkflow>;
  fetchStateConfigs: (workspaceSlug: string, projectId: string) => Promise<TWorkflowStateConfig[]>;
  fetchTransitions: (workspaceSlug: string, projectId: string) => Promise<TWorkflowTransition[]>;
  fetchStateGuardians: (workspaceSlug: string, projectId: string, stateId: string) => Promise<TWorkflowStateGuardian[]>;
  fetchProjectApprovers: (workspaceSlug: string, projectId: string) => Promise<TWorkflowProjectApprover[]>;
  fetchApprovalRequests: (workspaceSlug: string, projectId: string, issueId: string) => Promise<TApprovalRequest[]>;
  // crud actions
  toggleWorkflow: (workspaceSlug: string, projectId: string, isEnabled: boolean) => Promise<TProjectWorkflow>;
  createTransition: (
    workspaceSlug: string,
    projectId: string,
    data: { state: string; transition_state: string; kind?: "allowed" | "forbidden" }
  ) => Promise<TWorkflowTransition>;
  deleteTransition: (workspaceSlug: string, projectId: string, transitionId: string) => Promise<void>;
  addStateGuardian: (
    workspaceSlug: string,
    projectId: string,
    stateId: string,
    guardianId: string
  ) => Promise<TWorkflowStateGuardian>;
  removeStateGuardian: (workspaceSlug: string, projectId: string, stateId: string, guardianId: string) => Promise<void>;
  addProjectApprover: (
    workspaceSlug: string,
    projectId: string,
    approverId: string
  ) => Promise<TWorkflowProjectApprover>;
  removeProjectApprover: (workspaceSlug: string, projectId: string, approverId: string) => Promise<void>;
  decideApprovalRequest: (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    requestId: string,
    decision: "approve" | "reject" | "cancel",
    data?: { reason?: string }
  ) => Promise<TApprovalRequest>;
  updateStateConfig: (
    workspaceSlug: string,
    projectId: string,
    stateId: string,
    data: Partial<TWorkflowStateConfig>
  ) => Promise<TWorkflowStateConfig>;
}

export class WorkflowStore implements IWorkflowStore {
  workflowByProject: Record<string, TProjectWorkflow> = {};
  transitionsByProject: Record<string, TWorkflowTransition[]> = {};
  stateConfigsByProject: Record<string, TWorkflowStateConfig[]> = {};
  guardiansByState: Record<string, TWorkflowStateGuardian[]> = {};
  projectApprovers: Record<string, TWorkflowProjectApprover[]> = {};
  approvalRequestsByIssue: Record<string, TApprovalRequest[]> = {};
  blocker: TWorkflowBlocker = {
    open: false,
    kind: null,
    reviewers: [],
    anyMember: false,
    workspaceSlug: "",
    projectId: "",
    issueId: "",
    toState: "",
  };

  // In-flight promise cache: deduplicates concurrent fetches with the same key.
  // Not observable — plain object used only for request coordination.
  private fetchInFlight: Record<string, Promise<unknown>> = {};

  private readonly workflowService: WorkflowService;

  constructor(_rootStore: RootStore) {
    makeObservable(this, {
      // observables
      workflowByProject: observable,
      transitionsByProject: observable,
      stateConfigsByProject: observable,
      guardiansByState: observable,
      projectApprovers: observable,
      approvalRequestsByIssue: observable,
      blocker: observable,
      // blocker actions
      closeBlocker: action,
      tryOpenBlockerFromError: action,
      requestApprovalFromBlocker: action,
      // fetch actions
      fetchWorkflow: action,
      fetchStateConfigs: action,
      fetchTransitions: action,
      fetchStateGuardians: action,
      fetchProjectApprovers: action,
      fetchApprovalRequests: action,
      // crud actions
      toggleWorkflow: action,
      createTransition: action,
      deleteTransition: action,
      addStateGuardian: action,
      removeStateGuardian: action,
      addProjectApprover: action,
      removeProjectApprover: action,
      decideApprovalRequest: action,
      updateStateConfig: action,
    });
    this.workflowService = new WorkflowService();
  }

  closeBlocker = () => {
    // Keep `kind` (and the rest) intact while the dialog animates closed — clearing
    // it mid-close flips the render to the other branch and flickers the wrong copy.
    // The next opener (tryOpenBlockerFromError) overwrites the whole blocker anyway.
    this.blocker = { ...this.blocker, open: false };
  };

  /**
   * Shared error contract for every state-write path. If the error is one of the
   * workflow 403 codes, open the dialog (carrying the issue + target so it can POST
   * an approval request) and return true. Otherwise return false so the caller can
   * fall back to its own generic error handling.
   */
  tryOpenBlockerFromError = (
    error: unknown,
    ctx: { workspaceSlug: string; projectId: string; issueId: string; toState: string }
  ): boolean => {
    const err = error as Record<string, unknown> | null;
    const code = err?.error_code;
    if (code === "WORKFLOW_TRANSITION_NEEDS_APPROVAL") {
      this.blocker = {
        open: true,
        kind: "needs_approval",
        reviewers: Array.isArray(err?.approvers) ? (err.approvers as string[]) : [],
        anyMember: !!err?.any_member,
        ...ctx,
      };
      return true;
    }
    if (code === "WORKFLOW_TRANSITION_FORBIDDEN") {
      this.blocker = { open: true, kind: "forbidden", reviewers: [], anyMember: false, ...ctx };
      return true;
    }
    return false;
  };

  /**
   * Called by the modal's "Request approval" button: POSTs an approval request for
   * the move recorded in the blocker, then closes the dialog.
   */
  requestApprovalFromBlocker = async (comment?: string): Promise<void> => {
    const { workspaceSlug, projectId, issueId, toState } = this.blocker;
    await this.workflowService.createApprovalRequest(workspaceSlug, projectId, issueId, { to_state: toState, comment });
    this.closeBlocker();
  };

  /**
   * Resolves the kind of a transition row from→to.
   * Returns the row's `kind` (allowed/forbidden), or "off_path" when no row exists.
   */
  getTransitionKind = computedFn(
    (projectId: string, fromStateId: string, toStateId: string): "allowed" | "forbidden" | "off_path" => {
      const transition = (this.transitionsByProject[projectId] ?? []).find(
        (t) => t.state === fromStateId && t.transition_state === toStateId
      );
      return transition ? transition.kind : "off_path";
    }
  );

  /**
   * Returns true if the transition from→to is permitted client-side.
   * Workflow disabled → true; same state → true; a "forbidden" row → false; everything
   * else (allowed rows and off-path moves) → true. Off-path is no longer a hard block —
   * it is requestable, and the drag hook distinguishes "forbidden vs attempt".
   */
  isTransitionAllowed = computedFn(
    (projectId: string, fromStateId: string, toStateId: string, _userId: string): boolean => {
      const wf = this.workflowByProject[projectId];
      if (!wf?.is_enabled) return true; // workflow disabled → everything allowed
      if (fromStateId === toStateId) return true; // not a real transition
      return this.getTransitionKind(projectId, fromStateId, toStateId) !== "forbidden";
    }
  );

  /**
   * Returns the pending approval request for an issue, if any.
   */
  getPendingRequest = computedFn((issueId: string): TApprovalRequest | undefined =>
    (this.approvalRequestsByIssue[issueId] ?? []).find((r) => r.status === "pending")
  );

  /**
   * Fetches the workflow configuration for a project.
   * Concurrent calls with the same key share one in-flight promise.
   */
  fetchWorkflow = (workspaceSlug: string, projectId: string): Promise<TProjectWorkflow> => {
    const key = `workflow:${workspaceSlug}:${projectId}`;
    const existing = this.fetchInFlight[key] as Promise<TProjectWorkflow> | undefined;
    if (existing) return existing;
    const promise = this.workflowService
      .getWorkflow(workspaceSlug, projectId)
      .then((response) => {
        runInAction(() => {
          set(this.workflowByProject, [projectId], response);
        });
        return response;
      })
      .finally(() => {
        delete this.fetchInFlight[key];
      });
    this.fetchInFlight[key] = promise;
    return promise;
  };

  /**
   * Fetches workflow state configurations for a project.
   * Concurrent calls with the same key share one in-flight promise.
   */
  fetchStateConfigs = (workspaceSlug: string, projectId: string): Promise<TWorkflowStateConfig[]> => {
    const key = `stateConfigs:${workspaceSlug}:${projectId}`;
    const existing = this.fetchInFlight[key] as Promise<TWorkflowStateConfig[]> | undefined;
    if (existing) return existing;
    const promise = this.workflowService
      .getStateConfigs(workspaceSlug, projectId)
      .then((response) => {
        runInAction(() => {
          set(this.stateConfigsByProject, [projectId], response);
        });
        return response;
      })
      .finally(() => {
        delete this.fetchInFlight[key];
      });
    this.fetchInFlight[key] = promise;
    return promise;
  };

  /**
   * Fetches workflow transitions for a project.
   * Concurrent calls with the same key share one in-flight promise.
   */
  fetchTransitions = (workspaceSlug: string, projectId: string): Promise<TWorkflowTransition[]> => {
    const key = `transitions:${workspaceSlug}:${projectId}`;
    const existing = this.fetchInFlight[key] as Promise<TWorkflowTransition[]> | undefined;
    if (existing) return existing;
    const promise = this.workflowService
      .getTransitions(workspaceSlug, projectId)
      .then((response) => {
        runInAction(() => {
          set(this.transitionsByProject, [projectId], response);
        });
        return response;
      })
      .finally(() => {
        delete this.fetchInFlight[key];
      });
    this.fetchInFlight[key] = promise;
    return promise;
  };

  /**
   * Fetches the guardians for a single state.
   * Concurrent calls with the same key share one in-flight promise.
   */
  fetchStateGuardians = (
    workspaceSlug: string,
    projectId: string,
    stateId: string
  ): Promise<TWorkflowStateGuardian[]> => {
    const key = `guardians:${workspaceSlug}:${projectId}:${stateId}`;
    const existing = this.fetchInFlight[key] as Promise<TWorkflowStateGuardian[]> | undefined;
    if (existing) return existing;
    const promise = this.workflowService
      .getStateGuardians(workspaceSlug, projectId, stateId)
      .then((response) => {
        runInAction(() => {
          set(this.guardiansByState, [stateId], response);
        });
        return response;
      })
      .finally(() => {
        delete this.fetchInFlight[key];
      });
    this.fetchInFlight[key] = promise;
    return promise;
  };

  /**
   * Fetches the project-level approver list.
   * Concurrent calls with the same key share one in-flight promise.
   */
  fetchProjectApprovers = (workspaceSlug: string, projectId: string): Promise<TWorkflowProjectApprover[]> => {
    const key = `approvers:${workspaceSlug}:${projectId}`;
    const existing = this.fetchInFlight[key] as Promise<TWorkflowProjectApprover[]> | undefined;
    if (existing) return existing;
    const promise = this.workflowService
      .getProjectApprovers(workspaceSlug, projectId)
      .then((response) => {
        runInAction(() => {
          set(this.projectApprovers, [projectId], response);
        });
        return response;
      })
      .finally(() => {
        delete this.fetchInFlight[key];
      });
    this.fetchInFlight[key] = promise;
    return promise;
  };

  /**
   * Fetches the approval requests for an issue.
   * Concurrent calls with the same key share one in-flight promise.
   */
  fetchApprovalRequests = (workspaceSlug: string, projectId: string, issueId: string): Promise<TApprovalRequest[]> => {
    const key = `approvalRequests:${workspaceSlug}:${projectId}:${issueId}`;
    const existing = this.fetchInFlight[key] as Promise<TApprovalRequest[]> | undefined;
    if (existing) return existing;
    const promise = this.workflowService
      .getApprovalRequests(workspaceSlug, projectId, issueId)
      .then((response) => {
        runInAction(() => {
          set(this.approvalRequestsByIssue, [issueId], response);
        });
        return response;
      })
      .finally(() => {
        delete this.fetchInFlight[key];
      });
    this.fetchInFlight[key] = promise;
    return promise;
  };

  /**
   * Toggles the workflow enabled state, reverts on error.
   */
  toggleWorkflow = async (workspaceSlug: string, projectId: string, isEnabled: boolean): Promise<TProjectWorkflow> => {
    const originalWorkflow = this.workflowByProject[projectId];
    try {
      runInAction(() => {
        set(this.workflowByProject, [projectId, "is_enabled"], isEnabled);
      });
      const response = await this.workflowService.updateWorkflow(workspaceSlug, projectId, { is_enabled: isEnabled });
      runInAction(() => {
        set(this.workflowByProject, [projectId], response);
      });
      return response;
    } catch (error) {
      runInAction(() => {
        set(this.workflowByProject, [projectId], originalWorkflow);
      });
      throw error;
    }
  };

  /**
   * Creates a new workflow transition and updates the store.
   * Non-optimistic: server assigns the id; append on success.
   */
  createTransition = async (
    workspaceSlug: string,
    projectId: string,
    data: { state: string; transition_state: string; kind?: "allowed" | "forbidden" }
  ): Promise<TWorkflowTransition> => {
    const response = await this.workflowService.createTransition(workspaceSlug, projectId, data);
    runInAction(() => {
      const existing = this.transitionsByProject[projectId] ?? [];
      set(this.transitionsByProject, [projectId], [...existing, response]);
    });
    return response;
  };

  /**
   * Deletes a workflow transition and removes it from the store, reverts on error.
   */
  deleteTransition = async (workspaceSlug: string, projectId: string, transitionId: string): Promise<void> => {
    const originalTransitions = this.transitionsByProject[projectId] ?? [];
    try {
      runInAction(() => {
        set(
          this.transitionsByProject,
          [projectId],
          originalTransitions.filter((t) => t.id !== transitionId)
        );
      });
      await this.workflowService.deleteTransition(workspaceSlug, projectId, transitionId);
    } catch (error) {
      runInAction(() => {
        set(this.transitionsByProject, [projectId], originalTransitions);
      });
      throw error;
    }
  };

  /**
   * Adds a guardian to a state and updates the store, reverts on error.
   * Non-optimistic: server assigns the id; append on success.
   */
  addStateGuardian = async (
    workspaceSlug: string,
    projectId: string,
    stateId: string,
    guardianId: string
  ): Promise<TWorkflowStateGuardian> => {
    const response = await this.workflowService.addStateGuardian(workspaceSlug, projectId, stateId, guardianId);
    runInAction(() => {
      const existing = this.guardiansByState[stateId] ?? [];
      set(this.guardiansByState, [stateId], [...existing, response]);
    });
    return response;
  };

  /**
   * Removes a guardian from a state and updates the store, reverts on error.
   */
  removeStateGuardian = async (
    workspaceSlug: string,
    projectId: string,
    stateId: string,
    guardianId: string
  ): Promise<void> => {
    const originalGuardians = this.guardiansByState[stateId] ?? [];
    try {
      runInAction(() => {
        set(
          this.guardiansByState,
          [stateId],
          originalGuardians.filter((g) => g.guardian !== guardianId)
        );
      });
      await this.workflowService.removeStateGuardian(workspaceSlug, projectId, stateId, guardianId);
    } catch (error) {
      runInAction(() => {
        set(this.guardiansByState, [stateId], originalGuardians);
      });
      throw error;
    }
  };

  /**
   * Adds a project-level approver and updates the store, reverts on error.
   * Non-optimistic: server assigns the id; append on success.
   */
  addProjectApprover = async (
    workspaceSlug: string,
    projectId: string,
    approverId: string
  ): Promise<TWorkflowProjectApprover> => {
    const response = await this.workflowService.addProjectApprover(workspaceSlug, projectId, approverId);
    runInAction(() => {
      const existing = this.projectApprovers[projectId] ?? [];
      set(this.projectApprovers, [projectId], [...existing, response]);
    });
    return response;
  };

  /**
   * Removes a project-level approver and updates the store, reverts on error.
   */
  removeProjectApprover = async (workspaceSlug: string, projectId: string, approverId: string): Promise<void> => {
    const originalApprovers = this.projectApprovers[projectId] ?? [];
    try {
      runInAction(() => {
        set(
          this.projectApprovers,
          [projectId],
          originalApprovers.filter((a) => a.approver !== approverId)
        );
      });
      await this.workflowService.removeProjectApprover(workspaceSlug, projectId, approverId);
    } catch (error) {
      runInAction(() => {
        set(this.projectApprovers, [projectId], originalApprovers);
      });
      throw error;
    }
  };

  /**
   * Decides an approval request (approve/reject/cancel) and updates the issue's list.
   */
  decideApprovalRequest = async (
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    requestId: string,
    decision: "approve" | "reject" | "cancel",
    data?: { reason?: string }
  ): Promise<TApprovalRequest> => {
    const response = await this.workflowService.decideApprovalRequest(
      workspaceSlug,
      projectId,
      issueId,
      requestId,
      decision,
      data
    );
    runInAction(() => {
      const existing = this.approvalRequestsByIssue[issueId] ?? [];
      set(
        this.approvalRequestsByIssue,
        [issueId],
        existing.map((r) => (r.id === requestId ? response : r))
      );
    });
    return response;
  };

  /**
   * Updates (or creates via backend get_or_create) a workflow state configuration.
   * Non-optimistic for new configs (no local id yet); reverts on error.
   */
  updateStateConfig = async (
    workspaceSlug: string,
    projectId: string,
    stateId: string,
    data: Partial<TWorkflowStateConfig>
  ): Promise<TWorkflowStateConfig> => {
    const originalConfigs = this.stateConfigsByProject[projectId] ?? [];
    // Only apply optimistic update when a config already exists so we don't
    // produce a record without an id for newly-created configs.
    const existingIdx = originalConfigs.findIndex((c) => c.state === stateId);
    if (existingIdx >= 0) {
      runInAction(() => {
        const updated = originalConfigs.map((c) => (c.state === stateId ? { ...c, ...data } : c));
        set(this.stateConfigsByProject, [projectId], updated);
      });
    }
    try {
      const response = await this.workflowService.updateStateConfig(workspaceSlug, projectId, stateId, data);
      runInAction(() => {
        const list = this.stateConfigsByProject[projectId] ?? [];
        const idx = list.findIndex((c) => c.state === stateId);
        const next = idx >= 0 ? list.map((c) => (c.state === stateId ? response : c)) : [...list, response];
        set(this.stateConfigsByProject, [projectId], next);
      });
      return response;
    } catch (error) {
      runInAction(() => {
        set(this.stateConfigsByProject, [projectId], originalConfigs);
      });
      throw error;
    }
  };
}
