/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane ui
import { EpicIcon, WorkItemsIcon } from "@plane/propel/icons";
import { EIssueServiceType } from "@plane/types";
import { Breadcrumbs, CircularProgressIndicator, Header } from "@plane/ui";
// components
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { IssueDetailQuickActions } from "@/components/issues/issue-detail/issue-detail-quick-actions";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useProject } from "@/hooks/store/use-project";
import { useAppRouter } from "@/hooks/use-app-router";
// plane web imports
import { CommonProjectBreadcrumbs } from "@/components/breadcrumbs/common";

export const WorkItemDetailsHeader = observer(function WorkItemDetailsHeader() {
  // router
  const router = useAppRouter();
  const { workspaceSlug, workItem } = useParams();
  // store hooks
  const { getProjectById, loader } = useProject();
  const {
    issue: { getIssueById, getIssueIdByIdentifier },
  } = useIssueDetail();
  const {
    subIssues: { subIssuesByIssueId, stateDistributionByIssueId },
  } = useIssueDetail(EIssueServiceType.EPICS);
  // derived values
  const issueId = getIssueIdByIdentifier(workItem?.toString());
  const issueDetails = issueId ? getIssueById(issueId.toString()) : undefined;
  const projectId = issueDetails ? issueDetails?.project_id : undefined;
  const projectDetails = projectId ? getProjectById(projectId?.toString()) : undefined;
  const isEpic = issueDetails?.is_epic ?? false;
  // epic progress
  const subIssues = isEpic && issueId ? subIssuesByIssueId(issueId.toString()) : undefined;
  const distribution = isEpic && issueId ? stateDistributionByIssueId(issueId.toString()) : undefined;
  const completedCount = distribution?.completed?.length ?? 0;
  const totalCount = subIssues?.length ?? 0;
  const completionPercentage = totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0;

  if (!workspaceSlug || !projectId || !issueId) return null;
  return (
    <Header>
      <Header.LeftItem>
        <Breadcrumbs onBack={router.back} isLoading={loader === "init-loader"}>
          <CommonProjectBreadcrumbs workspaceSlug={workspaceSlug?.toString()} projectId={projectId?.toString()} />
          <Breadcrumbs.Item
            component={
              <BreadcrumbLink
                label={isEpic ? "Epics" : "Work Items"}
                href={
                  isEpic
                    ? `/${workspaceSlug}/projects/${projectId}/epics/`
                    : `/${workspaceSlug}/projects/${projectId}/issues/`
                }
                icon={
                  isEpic ? (
                    <EpicIcon className="h-4 w-4 text-tertiary" />
                  ) : (
                    <WorkItemsIcon className="h-4 w-4 text-tertiary" />
                  )
                }
              />
            }
          />
          <Breadcrumbs.Item
            component={
              <BreadcrumbLink
                label={projectDetails && issueDetails ? `${projectDetails.identifier}-${issueDetails.sequence_id}` : ""}
              />
            }
          />
        </Breadcrumbs>
      </Header.LeftItem>
      <Header.RightItem>
        {isEpic && totalCount > 0 && (
          <div className="text-xs mr-2 flex items-center gap-1.5 text-tertiary">
            <CircularProgressIndicator size={20} percentage={completionPercentage} strokeWidth={3} />
            <span>{completionPercentage}%</span>
          </div>
        )}
        {projectId && issueId && (
          <IssueDetailQuickActions
            workspaceSlug={workspaceSlug?.toString()}
            projectId={projectId?.toString()}
            issueId={issueId?.toString()}
          />
        )}
      </Header.RightItem>
    </Header>
  );
});
