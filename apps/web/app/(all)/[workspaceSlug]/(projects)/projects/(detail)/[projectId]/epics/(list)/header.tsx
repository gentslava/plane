/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

"use client";

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { EpicIcon } from "@plane/propel/icons";
import { Tooltip } from "@plane/propel/tooltip";
import { Button } from "@plane/propel/button";
import { EIssuesStoreType } from "@plane/types";
import { Breadcrumbs, Header } from "@plane/ui";
// components
import { BreadcrumbLink } from "@/components/common/breadcrumb-link";
import { CountChip } from "@/components/common/count-chip";
// hooks
import { useIssues } from "@/hooks/store/use-issues";
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
import { usePlatformOS } from "@/hooks/use-platform-os";
// plane web imports
import { CommonProjectBreadcrumbs } from "@/components/breadcrumbs/common";
import { CreateUpdateEpicModal } from "@/components/epic-modal/modal";

export const ProjectEpicsHeader = observer(function ProjectEpicsHeader() {
  const router = useAppRouter();
  const { workspaceSlug, projectId } = useParams();
  const [isCreateEpicModalOpen, setIsCreateEpicModalOpen] = useState(false);
  // store hooks
  const {
    issues: { getGroupIssueCount },
  } = useIssues(EIssuesStoreType.EPIC);
  const { loader } = useProject();
  const { allowPermissions } = useUserPermissions();
  const { isMobile } = usePlatformOS();

  const epicsCount = getGroupIssueCount(undefined, undefined, false);
  const canUserCreateEpic = allowPermissions(
    [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
    EUserPermissionsLevel.PROJECT
  );

  return (
    <>
      <Header>
        <Header.LeftItem>
          <div className="flex items-center gap-2.5">
            <Breadcrumbs onBack={() => router.back()} isLoading={loader === "init-loader"} className="flex-grow-0">
              <CommonProjectBreadcrumbs workspaceSlug={workspaceSlug?.toString()} projectId={projectId?.toString()} />
              <Breadcrumbs.Item
                component={
                  <BreadcrumbLink
                    label="Epics"
                    href={`/${workspaceSlug}/projects/${projectId}/epics/`}
                    icon={<EpicIcon className="h-4 w-4 text-tertiary" />}
                    isLast
                  />
                }
                isLast
              />
            </Breadcrumbs>
            {epicsCount && epicsCount > 0 ? (
              <Tooltip
                isMobile={isMobile}
                tooltipContent={`${epicsCount} epic${epicsCount > 1 ? "s" : ""}`}
                position="bottom"
              >
                <CountChip count={epicsCount} />
              </Tooltip>
            ) : null}
          </div>
        </Header.LeftItem>
        <Header.RightItem>
          {canUserCreateEpic && (
            <Button variant="primary" size="lg" onClick={() => setIsCreateEpicModalOpen(true)}>
              Add Epic
            </Button>
          )}
        </Header.RightItem>
      </Header>
      <CreateUpdateEpicModal isOpen={isCreateEpicModalOpen} onClose={() => setIsCreateEpicModalOpen(false)} />
    </>
  );
});
