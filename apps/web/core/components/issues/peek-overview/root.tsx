/* eslint-disable no-shadow */
/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState, useMemo, useCallback } from "react";
import { observer } from "mobx-react";
import { usePathname } from "next/navigation";
// Plane imports
import useSWR from "swr";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setPromiseToast, setToast } from "@plane/propel/toast";
import type { IWorkItemPeekOverview, TIssue } from "@plane/types";
import { EIssueServiceType, EIssuesStoreType } from "@plane/types";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useIssues } from "@/hooks/store/use-issues";
import { useUserPermissions } from "@/hooks/store/user";
import { useWorkflow } from "@/hooks/store/use-workflow";
import { useIssueStoreType } from "@/hooks/use-issue-layout-store";
import { useWorkItemProperties } from "@/plane-web/hooks/use-issue-properties";
// workflow
import { WorkflowBlockerModal } from "@/plane-web/components/workflow";
// local imports
import type { TIssueOperations } from "../issue-detail";
import { IssueView } from "./view";

export const IssuePeekOverview = observer(function IssuePeekOverview(props: IWorkItemPeekOverview) {
  const {
    embedIssue = false,
    embedRemoveCurrentNotification,
    is_draft = false,
    storeType: issueStoreFromProps,
  } = props;
  const { t } = useTranslation();
  // router
  const pathname = usePathname();
  // store hook
  const { allowPermissions } = useUserPermissions();

  const {
    issues: { restoreIssue },
  } = useIssues(EIssuesStoreType.ARCHIVED);
  const issueStoreType = useIssueStoreType();
  const _pageStoreType = issueStoreFromProps ?? issueStoreType;
  // Call both stores unconditionally (React hooks rule).
  // Determine service type from the peeked issue, not page context.
  const issueDetailStore = useIssueDetail(EIssueServiceType.ISSUES);
  const epicDetailStore = useIssueDetail(EIssueServiceType.EPICS);
  // peekIssue could be set on either store depending on context.
  // Which store it was set on tells us whether the source was an epic list or a work items list.
  const issueStorePeek = issueDetailStore.peekIssue;
  const epicStorePeek = epicDetailStore.peekIssue;
  const peekIssue = issueStorePeek ?? epicStorePeek;
  const peekSourceIsEpicStore = !issueStorePeek && !!epicStorePeek;
  // Check if peeked issue is an epic (it may already be in the shared store)
  const peekIssueObj = peekIssue?.issueId ? issueDetailStore.issue.getIssueById(peekIssue.issueId) : undefined;
  // Prefer explicit is_epic from issue data; fall back to which store originated the peek.
  // Never fall back to page context (storeType) — that leaks epic context to child work items.
  const isPeekEpic = peekIssueObj?.is_epic ?? peekSourceIsEpicStore;
  const activeDetailStore = isPeekEpic ? epicDetailStore : issueDetailStore;
  const {
    issue: { fetchIssue },
    fetchActivities,
  } = activeDetailStore;
  // Use the correct issues store for the peeked item, not the page context.
  // When peeking a child work item from an epic page, storeType is EPIC but
  // the child must go through PROJECT store (hitting /issues/, not /epics/).
  const peekStoreType = isPeekEpic ? EIssuesStoreType.EPIC : EIssuesStoreType.PROJECT;
  const { issues } = useIssues(peekStoreType);

  useWorkItemProperties(
    peekIssue?.projectId,
    peekIssue?.workspaceSlug,
    peekIssue?.issueId,
    isPeekEpic ? EIssueServiceType.EPICS : EIssueServiceType.ISSUES
  );
  // workflow blocked-transition dialog — routed through the shared store; the
  // dialog is rendered below (always, regardless of whether a work item is peeked)
  const { tryOpenBlockerFromError } = useWorkflow();
  // state
  const [error, setError] = useState(false);

  const removeRoutePeekId = useCallback(() => {
    // Clear both stores to prevent stale peekIssue on the other store
    issueDetailStore.setPeekIssue(undefined);
    epicDetailStore.setPeekIssue(undefined);
    if (embedIssue) embedRemoveCurrentNotification?.();
  }, [embedIssue, embedRemoveCurrentNotification, issueDetailStore, epicDetailStore]);

  const issueOperations: TIssueOperations = useMemo(
    () => ({
      fetch: async (workspaceSlug: string, projectId: string, issueId: string) => {
        try {
          setError(false);
          await fetchIssue(workspaceSlug, projectId, issueId);
        } catch (error) {
          setError(true);
          console.error("Error fetching the parent issue", error);
        }
      },
      update: async (workspaceSlug: string, projectId: string, issueId: string, data: Partial<TIssue>) => {
        if (issues?.updateIssue) {
          await issues
            .updateIssue(workspaceSlug, projectId, issueId, data)
            .then(async () => {
              fetchActivities(workspaceSlug, projectId, issueId);
              return;
            })
            .catch((updateError: unknown) => {
              if (
                !tryOpenBlockerFromError(updateError, {
                  workspaceSlug,
                  projectId,
                  issueId,
                  toState: (data.state_id as string) ?? "",
                })
              ) {
                setToast({
                  title: t("toast.error"),
                  type: TOAST_TYPE.ERROR,
                  message: t("entity.update.failed", { entity: t("issue.label", { count: 1 }) }),
                });
              }
            });
        }
      },
      remove: async (workspaceSlug: string, projectId: string, issueId: string) => {
        try {
          return issues?.removeIssue(workspaceSlug, projectId, issueId).then(() => {
            removeRoutePeekId();
            return;
          });
        } catch (_error) {
          setToast({
            title: t("toast.error"),
            type: TOAST_TYPE.ERROR,
            message: t("entity.delete.failed", { entity: t("issue.label", { count: 1 }) }),
          });
        }
      },
      archive: async (workspaceSlug: string, projectId: string, issueId: string) => {
        try {
          if (!issues?.archiveIssue) return;
          await issues.archiveIssue(workspaceSlug, projectId, issueId);
        } catch (error) {
          console.error("Error archiving the issue", error);
        }
      },
      restore: async (workspaceSlug: string, projectId: string, issueId: string) => {
        try {
          await restoreIssue(workspaceSlug, projectId, issueId);
          setToast({
            type: TOAST_TYPE.SUCCESS,
            title: t("issue.restore.success.title"),
            message: t("issue.restore.success.message"),
          });
        } catch (_error) {
          setToast({
            type: TOAST_TYPE.ERROR,
            title: t("toast.error"),
            message: t("issue.restore.failed.message"),
          });
        }
      },
      addCycleToIssue: async (workspaceSlug: string, projectId: string, cycleId: string, issueId: string) => {
        try {
          await issues.addCycleToIssue(workspaceSlug, projectId, cycleId, issueId);
          fetchActivities(workspaceSlug, projectId, issueId);
        } catch (_error) {
          setToast({
            type: TOAST_TYPE.ERROR,
            title: t("toast.error"),
            message: t("issue.add.cycle.failed"),
          });
        }
      },
      addIssueToCycle: async (workspaceSlug: string, projectId: string, cycleId: string, issueIds: string[]) => {
        try {
          await issues.addIssueToCycle(workspaceSlug, projectId, cycleId, issueIds);
        } catch (_error) {
          setToast({
            type: TOAST_TYPE.ERROR,
            title: t("toast.error"),
            message: t("issue.add.cycle.failed"),
          });
        }
      },
      removeIssueFromCycle: async (workspaceSlug: string, projectId: string, cycleId: string, issueId: string) => {
        try {
          const removeFromCyclePromise = issues.removeIssueFromCycle(workspaceSlug, projectId, cycleId, issueId);
          setPromiseToast(removeFromCyclePromise, {
            loading: t("issue.remove.cycle.loading"),
            success: {
              title: t("toast.success"),
              message: () => t("issue.remove.cycle.success"),
            },
            error: {
              title: t("toast.error"),
              message: () => t("issue.remove.cycle.failed"),
            },
          });
          await removeFromCyclePromise;
          fetchActivities(workspaceSlug, projectId, issueId);
        } catch (error) {
          console.error("Error removing issue from cycle", error);
        }
      },
      changeModulesInIssue: async (
        workspaceSlug: string,
        projectId: string,
        issueId: string,
        addModuleIds: string[],
        removeModuleIds: string[]
      ) => {
        const promise = await issues.changeModulesInIssue(
          workspaceSlug,
          projectId,
          issueId,
          addModuleIds,
          removeModuleIds
        );
        fetchActivities(workspaceSlug, projectId, issueId);
        return promise;
      },
      removeIssueFromModule: async (workspaceSlug: string, projectId: string, moduleId: string, issueId: string) => {
        try {
          const removeFromModulePromise = issues.removeIssuesFromModule(workspaceSlug, projectId, moduleId, [issueId]);
          setPromiseToast(removeFromModulePromise, {
            loading: t("issue.remove.module.loading"),
            success: {
              title: t("toast.success"),
              message: () => t("issue.remove.module.success"),
            },
            error: {
              title: t("toast.error"),
              message: () => t("issue.remove.module.failed"),
            },
          });
          await removeFromModulePromise;
          fetchActivities(workspaceSlug, projectId, issueId);
        } catch (error) {
          console.error("Error removing issue from module", error);
        }
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fetchIssue, is_draft, issues, fetchActivities, pathname, removeRoutePeekId, restoreIssue, tryOpenBlockerFromError]
  );

  const { isLoading } = useSWR(
    ["peek-issue", peekIssue?.workspaceSlug, peekIssue?.projectId, peekIssue?.issueId],
    () => peekIssue && issueOperations.fetch(peekIssue.workspaceSlug, peekIssue.projectId, peekIssue.issueId),
    {
      revalidateIfStale: false,
      revalidateOnFocus: false,
      revalidateOnReconnect: false,
    }
  );

  // Check if issue is editable, based on user role
  const isEditable = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT,
    peekIssue?.workspaceSlug,
    peekIssue?.projectId
  );

  return (
    <>
      {peekIssue?.workspaceSlug && peekIssue?.projectId && peekIssue?.issueId && (
        <IssueView
          workspaceSlug={peekIssue.workspaceSlug}
          projectId={peekIssue.projectId}
          issueId={peekIssue.issueId}
          isLoading={isLoading}
          isError={error}
          is_archived={!!peekIssue.isArchived}
          disabled={!isEditable}
          embedIssue={embedIssue}
          embedRemoveCurrentNotification={embedRemoveCurrentNotification}
          issueOperations={issueOperations}
        />
      )}
      {/* Workflow blocker dialog — store-driven, mounted regardless of whether a
          work item is peeked. IssuePeekOverview is rendered by every issue layout
          (and the full issue page), so board drag-n-drop blocks have a host to
          render the dialog even when no peek is open. The peek detector gates on
          its open-state. */}
      <WorkflowBlockerModal />
    </>
  );
});
