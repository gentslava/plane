/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { EmptyStateDetailed } from "@plane/propel/empty-state";
import { EUserProjectRoles } from "@plane/types";
// components
import { CreateUpdateEpicModal } from "@/components/epic-modal/modal";
// hooks
import { useUserPermissions } from "@/hooks/store/user";

export const ProjectEpicsEmptyState = observer(function ProjectEpicsEmptyState() {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  // router
  const { projectId: routerProjectId } = useParams();
  const projectId = routerProjectId ? routerProjectId.toString() : undefined;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { allowPermissions } = useUserPermissions();

  const canPerformActions = allowPermissions(
    [EUserProjectRoles.ADMIN, EUserProjectRoles.MEMBER],
    EUserPermissionsLevel.PROJECT
  );

  return (
    <div className="relative h-full w-full overflow-y-auto">
      <CreateUpdateEpicModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        isProjectSelectionDisabled={!!projectId}
      />
      <EmptyStateDetailed
        assetKey="epic"
        title={t("project_empty_state.epics.title")}
        description={t("project_empty_state.epics.description")}
        actions={[
          {
            label: t("project_empty_state.epics.cta_primary"),
            onClick: () => setIsCreateModalOpen(true),
            disabled: !canPerformActions,
            variant: "primary",
          },
        ]}
      />
    </div>
  );
});
