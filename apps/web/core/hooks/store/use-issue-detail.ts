/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useContext } from "react";
import type { TIssueServiceType } from "@plane/types";
import { EIssueServiceType, EIssuesStoreType } from "@plane/types";
// mobx store
import { StoreContext } from "@/lib/store-context";
// hooks
import { IssuesStoreContext } from "@/hooks/use-issue-layout-store";
// types
import type { IIssueDetail } from "@/store/issue/issue-details/root.store";

export const useIssueDetail = (serviceType?: TIssueServiceType): IIssueDetail => {
  const context = useContext(StoreContext);
  const storeType = useContext(IssuesStoreContext);

  if (context === undefined) throw new Error("useIssueDetail must be used within StoreProvider");

  // If explicit service type provided, use it
  if (serviceType !== undefined) {
    return serviceType === EIssueServiceType.EPICS ? context.issue.epicDetail : context.issue.issueDetail;
  }

  // Otherwise, auto-detect from the IssuesStoreContext (set by epic/project pages)
  if (storeType === EIssuesStoreType.EPIC) return context.issue.epicDetail;

  return context.issue.issueDetail;
};
