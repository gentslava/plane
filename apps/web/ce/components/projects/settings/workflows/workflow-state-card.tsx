/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Plus, ShieldX, X } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { setPromiseToast } from "@plane/propel/toast";
import type { IState } from "@plane/types";
import { Avatar, CustomSearchSelect, ToggleSwitch } from "@plane/ui";
// helpers
import { getFileURL } from "@plane/utils";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useWorkflow } from "@/hooks/store/use-workflow";
// local imports
import { TransitionRow } from "./transition-row";

type Props = {
  workspaceSlug: string;
  projectId: string;
  state: IState;
  projectStates: IState[];
};

export const WorkflowStateCard = observer(function WorkflowStateCard(props: Props) {
  const { workspaceSlug, projectId, state, projectStates } = props;
  const [addingGuardian, setAddingGuardian] = useState(false);
  const [createForbidden, setCreateForbidden] = useState(false);
  // hooks
  const { t } = useTranslation();
  const {
    transitionsByProject,
    stateConfigsByProject,
    guardiansByState,
    createTransition,
    updateStateConfig,
    fetchStateGuardians,
    addStateGuardian,
    removeStateGuardian,
  } = useWorkflow();
  const {
    project: { getProjectMemberDetails, projectMemberIds },
  } = useMember();

  // Fetch guardians for this state on mount
  useEffect(() => {
    if (!workspaceSlug || !projectId || !state.id) return;
    fetchStateGuardians(workspaceSlug, projectId, state.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId, state.id]);

  // Transitions leaving this state
  const allTransitions = transitionsByProject[projectId] ?? [];
  const stateTransitions = allTransitions.filter((tx) => tx.state === state.id);

  // State config for allow_issue_creation
  const stateConfigs = stateConfigsByProject[projectId] ?? [];
  const stateConfig = stateConfigs.find((sc) => sc.state === state.id);
  const allowCreation = stateConfig?.allow_issue_creation ?? true;

  // Guardians for this state
  const guardians = guardiansByState[state.id] ?? [];
  const guardianIds = new Set(guardians.map((g) => g.guardian));

  // States available as transition targets (exclude current state and already-added targets)
  const usedTargetIds = new Set(stateTransitions.map((tx) => tx.transition_state));
  const targetOptions = projectStates
    .filter((s) => s.id !== state.id && !usedTargetIds.has(s.id))
    .map((s) => ({
      value: s.id,
      query: s.name,
      content: (
        <div className="flex items-center gap-2">
          <span className="size-2.5 flex-shrink-0 rounded-full" style={{ backgroundColor: s.color }} />
          <span>{s.name}</span>
        </div>
      ),
    }));

  // Member options for the guardian picker — exclude members already added as guardians
  const guardianOptions =
    projectMemberIds
      ?.map((userId) => {
        const details = getProjectMemberDetails(userId, projectId);
        if (!details?.member) return null;
        if (guardianIds.has(userId)) return null;
        return {
          value: userId,
          query: details.member.display_name ?? "",
          content: (
            <div className="flex items-center gap-2">
              <Avatar name={details.member.display_name} src={getFileURL(details.member.avatar_url)} size="sm" />
              <span>{details.member.display_name}</span>
            </div>
          ),
        };
      })
      .filter(Boolean) ?? [];

  const handleAddTransition = (targetStateId: string) => {
    const promise = createTransition(workspaceSlug, projectId, {
      state: state.id,
      transition_state: targetStateId,
      kind: createForbidden ? "forbidden" : "allowed",
    });
    setPromiseToast(promise, {
      loading: t("project_settings.workflow.toast.adding_transition"),
      success: { title: t("toast.success"), message: () => t("project_settings.workflow.toast.transition_added") },
      error: { title: t("toast.error"), message: () => t("project_settings.workflow.toast.add_transition_failed") },
    });
  };

  const handleToggleAllowCreation = (value: boolean) => {
    const promise = updateStateConfig(workspaceSlug, projectId, state.id, { allow_issue_creation: value });
    setPromiseToast(promise, {
      loading: t("project_settings.workflow.toast.updating_state"),
      success: { title: t("toast.success"), message: () => t("project_settings.workflow.toast.state_updated") },
      error: { title: t("toast.error"), message: () => t("project_settings.workflow.toast.update_state_failed") },
    });
  };

  const handleAddGuardian = (userId: string) => {
    setAddingGuardian(false);
    const promise = addStateGuardian(workspaceSlug, projectId, state.id, userId);
    setPromiseToast(promise, {
      loading: t("project_settings.workflow.toast.adding_guardian"),
      success: { title: t("toast.success"), message: () => t("project_settings.workflow.toast.guardian_added") },
      error: { title: t("toast.error"), message: () => t("project_settings.workflow.toast.add_guardian_failed") },
    });
  };

  const handleRemoveGuardian = (userId: string) => {
    const promise = removeStateGuardian(workspaceSlug, projectId, state.id, userId);
    setPromiseToast(promise, {
      loading: t("project_settings.workflow.toast.removing_guardian"),
      success: { title: t("toast.success"), message: () => t("project_settings.workflow.toast.guardian_removed") },
      error: { title: t("toast.error"), message: () => t("project_settings.workflow.toast.remove_guardian_failed") },
    });
  };

  return (
    <div className="rounded-sm border border-subtle bg-surface-1 px-3.5 py-3">
      {/* State header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="size-3 flex-shrink-0 rounded-full" style={{ backgroundColor: state.color }} />
          <span className="text-13 font-medium text-primary">{state.name}</span>
          <span className="text-13 text-tertiary capitalize">({state.group})</span>
        </div>
        {/* allow_issue_creation toggle */}
        <div className="flex items-center gap-2">
          <span className="text-13 text-secondary">{t("project_settings.workflow.allow_creation")}</span>
          <ToggleSwitch value={allowCreation} onChange={handleToggleAllowCreation} size="sm" />
        </div>
      </div>

      {/* Transitions */}
      <div className="mt-3 flex flex-col gap-2">
        {stateTransitions.map((transition) => {
          const targetState = projectStates.find((s) => s.id === transition.transition_state);
          return (
            <TransitionRow
              key={transition.id}
              workspaceSlug={workspaceSlug}
              projectId={projectId}
              transition={transition}
              targetState={targetState}
              projectStates={projectStates}
            />
          );
        })}

        {/* Add transition */}
        {targetOptions.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <CustomSearchSelect
              value=""
              label={
                <div className="flex items-center gap-1.5 text-13 text-secondary">
                  <Plus className="size-3.5" />
                  {t("project_settings.workflow.add_transition")}
                </div>
              }
              options={targetOptions}
              onChange={handleAddTransition}
              maxHeight="md"
              buttonClassName="!px-3 !py-1.5 !text-13 w-full border border-dashed border-default bg-transparent hover:text-accent-primary"
            />
            {/* Kind chosen at create time (v1) */}
            <label className="flex items-center gap-1.5 text-12 text-secondary">
              <input
                type="checkbox"
                checked={createForbidden}
                onChange={(e) => setCreateForbidden(e.target.checked)}
                className="size-3.5"
              />
              <ShieldX className="size-3.5 text-danger-primary" />
              {t("project_settings.workflow.mark_forbidden")}
            </label>
          </div>
        )}
      </div>

      {/* Guardians */}
      <div className="mt-3 border-t border-subtle pt-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-13 font-medium text-primary">{t("project_settings.workflow.guardians")}</span>
          <span className="text-12 text-tertiary">{t("project_settings.workflow.guardians_help")}</span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {guardians.map((guardian) => {
            const details = getProjectMemberDetails(guardian.guardian, projectId);
            const member = details?.member;
            return (
              <div key={guardian.id} className="flex items-center gap-1 rounded-full bg-layer-1 px-2 py-0.5 text-13">
                <Avatar name={member?.display_name} src={getFileURL(member?.avatar_url ?? "")} size="sm" />
                <span>{member?.display_name ?? guardian.guardian}</span>
                <button
                  type="button"
                  onClick={() => handleRemoveGuardian(guardian.guardian)}
                  className="ml-0.5 rounded text-secondary transition-colors hover:text-danger-primary"
                >
                  <X className="size-3" />
                </button>
              </div>
            );
          })}

          {/* Add guardian */}
          {addingGuardian ? (
            <CustomSearchSelect
              value=""
              label={<span className="text-13 text-secondary">{t("project_settings.workflow.select_member")}</span>}
              options={guardianOptions as { value: string; query: string; content: React.ReactNode }[]}
              onChange={handleAddGuardian}
              maxHeight="md"
              buttonClassName="!px-2 !py-1 !text-13 bg-surface-1"
            />
          ) : (
            <button
              type="button"
              onClick={() => setAddingGuardian(true)}
              className="border-default hover:border-accent-primary flex items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-13 text-secondary transition-colors hover:text-accent-primary"
            >
              <Plus className="size-3" />
              {t("project_settings.workflow.add_guardian")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
});
