/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { Plus, Workflow, X } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { setPromiseToast } from "@plane/propel/toast";
import { Avatar, CustomSearchSelect, ToggleSwitch } from "@plane/ui";
// helpers
import { getFileURL } from "@plane/utils";
// components
import { SettingsControlItem } from "@/components/settings/control-item";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useProjectState } from "@/hooks/store/use-project-state";
import { useWorkflow } from "@/hooks/store/use-workflow";
// local imports
import { WorkflowStateCard } from "./workflow-state-card";

type Props = {
  workspaceSlug: string;
  projectId: string;
};

export const WorkflowSettingsRoot = observer(function WorkflowSettingsRoot(props: Props) {
  const { workspaceSlug, projectId } = props;
  const [addingApprover, setAddingApprover] = useState(false);
  // hooks
  const { t } = useTranslation();
  const {
    fetchWorkflow,
    fetchTransitions,
    fetchStateConfigs,
    fetchProjectApprovers,
    toggleWorkflow,
    addProjectApprover,
    removeProjectApprover,
    workflowByProject,
    projectApprovers,
  } = useWorkflow();
  const { getProjectStates, fetchProjectStates } = useProjectState();
  const {
    project: { getProjectMemberDetails, projectMemberIds },
  } = useMember();

  // Fetch data on mount
  useEffect(() => {
    if (!workspaceSlug || !projectId) return;
    fetchWorkflow(workspaceSlug, projectId);
    fetchTransitions(workspaceSlug, projectId);
    fetchStateConfigs(workspaceSlug, projectId);
    fetchProjectApprovers(workspaceSlug, projectId);
    fetchProjectStates(workspaceSlug, projectId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceSlug, projectId]);

  const workflow = workflowByProject[projectId];
  const isEnabled = workflow?.is_enabled ?? false;

  const projectStates = getProjectStates(projectId) ?? [];

  // Project-level approvers
  const approvers = projectApprovers[projectId] ?? [];
  const approverIds = new Set(approvers.map((a) => a.approver));

  // Member options for the approver picker — exclude members already added as approvers
  const approverOptions =
    projectMemberIds
      ?.map((userId) => {
        const details = getProjectMemberDetails(userId, projectId);
        if (!details?.member) return null;
        if (approverIds.has(userId)) return null;
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

  const handleToggleWorkflow = (value: boolean) => {
    const promise = toggleWorkflow(workspaceSlug, projectId, value);
    setPromiseToast(promise, {
      loading: value ? t("project_settings.workflow.toast.enabling") : t("project_settings.workflow.toast.disabling"),
      success: {
        title: t("toast.success"),
        message: () =>
          value ? t("project_settings.workflow.toast.enabled") : t("project_settings.workflow.toast.disabled"),
      },
      error: {
        title: t("toast.error"),
        message: () => t("project_settings.workflow.toast.update_workflow_failed"),
      },
    });
  };

  const handleAddApprover = (userId: string) => {
    setAddingApprover(false);
    const promise = addProjectApprover(workspaceSlug, projectId, userId);
    setPromiseToast(promise, {
      loading: t("project_settings.workflow.toast.adding_approver"),
      success: { title: t("toast.success"), message: () => t("project_settings.workflow.toast.approver_added") },
      error: { title: t("toast.error"), message: () => t("project_settings.workflow.toast.add_approver_failed") },
    });
  };

  const handleRemoveApprover = (userId: string) => {
    const promise = removeProjectApprover(workspaceSlug, projectId, userId);
    setPromiseToast(promise, {
      loading: t("project_settings.workflow.toast.removing_approver"),
      success: { title: t("toast.success"), message: () => t("project_settings.workflow.toast.approver_removed") },
      error: { title: t("toast.error"), message: () => t("project_settings.workflow.toast.remove_approver_failed") },
    });
  };

  return (
    <div className="flex flex-col gap-6">
      {/* Enable/disable workflow toggle */}
      <div className="flex flex-col gap-4 border-b border-subtle py-2">
        <div className="flex items-center gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-sm bg-layer-2">
            <Workflow className="size-4 shrink-0 text-primary" />
          </div>
          <SettingsControlItem
            title={t("project_settings.workflow.enable")}
            description={t("project_settings.workflow.description")}
            control={<ToggleSwitch value={isEnabled} onChange={handleToggleWorkflow} size="sm" />}
          />
        </div>
      </div>

      {/* Project approvers + state cards — only visible when workflow is enabled */}
      {isEnabled && (
        <>
          {/* Project approvers */}
          <div className="flex flex-col gap-2 border-b border-subtle pb-4">
            <div className="flex flex-col gap-0.5">
              <span className="text-13 font-medium text-primary">
                {t("project_settings.workflow.project_approvers")}
              </span>
              <span className="text-12 text-tertiary">{t("project_settings.workflow.project_approvers_help")}</span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {approvers.length === 0 ? (
                <span className="text-13 text-tertiary italic">
                  {t("project_settings.workflow.any_member_approves")}
                </span>
              ) : (
                approvers.map((approver) => {
                  const details = getProjectMemberDetails(approver.approver, projectId);
                  const member = details?.member;
                  return (
                    <div
                      key={approver.id}
                      className="flex items-center gap-1 rounded-full bg-layer-1 px-2 py-0.5 text-13"
                    >
                      <Avatar name={member?.display_name} src={getFileURL(member?.avatar_url ?? "")} size="sm" />
                      <span>{member?.display_name ?? approver.approver}</span>
                      <button
                        type="button"
                        onClick={() => handleRemoveApprover(approver.approver)}
                        className="ml-0.5 rounded text-secondary transition-colors hover:text-danger-primary"
                      >
                        <X className="size-3" />
                      </button>
                    </div>
                  );
                })
              )}

              {/* Add approver */}
              {addingApprover ? (
                <CustomSearchSelect
                  value=""
                  label={<span className="text-13 text-secondary">{t("project_settings.workflow.select_member")}</span>}
                  options={approverOptions as { value: string; query: string; content: React.ReactNode }[]}
                  onChange={handleAddApprover}
                  maxHeight="md"
                  buttonClassName="!px-2 !py-1 !text-13 bg-surface-1"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setAddingApprover(true)}
                  className="border-default hover:border-accent-primary flex items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-13 text-secondary transition-colors hover:text-accent-primary"
                >
                  <Plus className="size-3" />
                  {t("project_settings.workflow.project_approvers")}
                </button>
              )}
            </div>
          </div>

          {/* State cards */}
          {projectStates.length > 0 && (
            <div className="flex flex-col gap-3">
              {projectStates.map((state) => (
                <WorkflowStateCard
                  key={state.id}
                  workspaceSlug={workspaceSlug}
                  projectId={projectId}
                  state={state}
                  projectStates={projectStates}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
});
