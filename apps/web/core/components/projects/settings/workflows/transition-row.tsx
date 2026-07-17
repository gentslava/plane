/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { Trash2 } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { setPromiseToast } from "@plane/propel/toast";
import type { IState, TWorkflowTransition } from "@plane/types";
// hooks
import { useWorkflow } from "@/hooks/store/use-workflow";

type Props = {
  workspaceSlug: string;
  projectId: string;
  transition: TWorkflowTransition;
  targetState: IState | undefined;
  projectStates: IState[];
};

export const TransitionRow = observer(function TransitionRow(props: Props) {
  const { workspaceSlug, projectId, transition, targetState, projectStates: _projectStates } = props;
  // hooks
  const { t } = useTranslation();
  const { deleteTransition, transitionsByProject } = useWorkflow();

  // Keep transition reactive from store
  const currentTransitions = transitionsByProject[projectId] ?? [];
  const liveTransition = currentTransitions.find((tx) => tx.id === transition.id) ?? transition;
  const isForbidden = liveTransition.kind === "forbidden";

  const handleDeleteTransition = () => {
    const promise = deleteTransition(workspaceSlug, projectId, transition.id);
    setPromiseToast(promise, {
      loading: t("project_settings.workflow.toast.removing_transition"),
      success: { title: t("toast.success"), message: () => t("project_settings.workflow.toast.transition_removed") },
      error: { title: t("toast.error"), message: () => t("project_settings.workflow.toast.remove_transition_failed") },
    });
  };

  return (
    <div className="rounded-sm border border-subtle bg-surface-2 px-3 py-2 text-13">
      {/* Target state row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-13 text-secondary">{t("project_settings.workflow.select_target_state")}:</span>
          {targetState ? (
            <div className="flex items-center gap-1.5">
              <span className="size-2.5 flex-shrink-0 rounded-full" style={{ backgroundColor: targetState.color }} />
              <span className="font-medium text-primary">{targetState.name}</span>
            </div>
          ) : (
            <span className="text-tertiary italic">Unknown state</span>
          )}
          {/* Kind badge (read-only — kind is chosen at create time) */}
          <span
            className={
              isForbidden
                ? "rounded-full bg-danger-subtle px-2 py-0.5 text-11 font-medium text-danger-primary"
                : "rounded-full bg-success-subtle px-2 py-0.5 text-11 font-medium text-success-primary"
            }
          >
            {isForbidden ? t("project_settings.workflow.forbidden") : t("project_settings.workflow.allowed")}
          </span>
        </div>
        <button
          type="button"
          onClick={handleDeleteTransition}
          className="rounded p-1 text-secondary transition-colors hover:bg-layer-1 hover:text-danger-primary"
          title={t("project_settings.workflow.remove_transition")}
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
    </div>
  );
});
