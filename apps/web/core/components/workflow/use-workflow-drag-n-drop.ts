/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import type { TIssueGroupByOptions } from "@plane/types";
// hooks
import { useUser } from "@/hooks/store/user";
import { useWorkflow } from "@/hooks/store/use-workflow";

/**
 * Work item creation in a column: for MVP keep same as stub (false = not disabled).
 * Backend enforces creation rules; drag-drop blocking is handled by handleWorkFlowState.
 * FE Task 3 can wire this up if needed.
 */
const getIsWorkflowWorkItemCreationDisabled = (_groupId: string, _subGroupId?: string): boolean => false;

export const useWorkFlowFDragNDrop = (
  groupBy: TIssueGroupByOptions | undefined,
  _subGroupBy?: TIssueGroupByOptions
) => {
  const { workspaceSlug: rawWorkspaceSlug, projectId: rawProjectId } = useParams();
  const workspaceSlug = rawWorkspaceSlug ? rawWorkspaceSlug.toString() : undefined;
  const projectId = rawProjectId ? rawProjectId.toString() : undefined;

  const { data: currentUser } = useUser();
  const workflow = useWorkflow();

  // Stable ref to avoid listing `workflow` (MobX store singleton) in useEffect deps
  const workflowRef = useRef(workflow);
  workflowRef.current = workflow;

  // Track whether we have already fetched for this project to avoid duplicate calls
  const fetchedProjectRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    if (fetchedProjectRef.current === projectId) return;

    fetchedProjectRef.current = projectId;
    workflowRef.current.fetchWorkflow(workspaceSlug, projectId).catch(() => {});
    workflowRef.current.fetchTransitions(workspaceSlug, projectId).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  // isWorkflowDropDisabled is reactive state updated by handleWorkFlowState
  const [isWorkflowDropDisabled, setIsWorkflowDropDisabled] = useState(false);
  // workflowDisabledSource: non-undefined string = show workflow overlay; undefined = generic overlay
  const [workflowDisabledSource, setWorkflowDisabledSource] = useState<string | undefined>(undefined);

  const handleWorkFlowState = (
    sourceGroupId: string,
    destinationGroupId: string,
    _sourceSubGroupId?: string,
    _destinationSubGroupId?: string
  ) => {
    // Workflow blocking is only meaningful when grouped by state.
    // For other groupings (priority, assignee, etc.) transitions are not state-based.
    if (groupBy !== "state") {
      setIsWorkflowDropDisabled(false);
      setWorkflowDisabledSource(undefined);
      return;
    }

    if (!projectId || !currentUser?.id) {
      // Cannot evaluate – fail open (allow)
      setIsWorkflowDropDisabled(false);
      setWorkflowDisabledSource(undefined);
      return;
    }

    // Preemptively block ONLY forbidden transitions. Off-path moves are NOT blocked
    // here — the drop attempts, gets a 403 NEEDS_APPROVAL, and the blocker modal
    // offers "Request approval" (the optimistic update reverts the card).
    const isForbidden = workflow.getTransitionKind(projectId, sourceGroupId, destinationGroupId) === "forbidden";

    setIsWorkflowDropDisabled(isForbidden);
    // workflowDisabledSource acts as the "source state id" for the overlay component.
    // CE stubs for WorkFlowDisabledOverlay/WorkFlowDisabledMessage render nothing,
    // but the non-undefined value switches GroupDragOverlay into workflow-block mode
    // (red background, hides generic drop message).
    setWorkflowDisabledSource(isForbidden ? sourceGroupId : undefined);
  };

  return {
    workflowDisabledSource,
    isWorkflowDropDisabled,
    getIsWorkflowWorkItemCreationDisabled,
    handleWorkFlowState,
  };
};
