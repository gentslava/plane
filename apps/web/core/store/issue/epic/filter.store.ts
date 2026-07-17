/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TIssueGroupByOptions } from "@plane/types";
import type { IProjectIssuesFilter } from "../project/filter.store";
import { ProjectIssuesFilter } from "../project/filter.store";
import type { IIssueRootStore } from "../root.store";

export type IProjectEpicsFilter = IProjectIssuesFilter;

/**
 * Epics always group by state_detail.group (5 simplified columns:
 * Backlog, Todo, In Progress, Done, Cancelled) instead of individual
 * project states. This remaps both saved preferences and defaults.
 */
const EPIC_GROUP_BY_REMAP: Partial<Record<string, TIssueGroupByOptions>> = {
  state: "state_detail.group",
};

export class ProjectEpicsFilter extends ProjectIssuesFilter implements IProjectEpicsFilter {
  constructor(rootStore: IIssueRootStore) {
    super(rootStore);
    this.rootIssueStore = rootStore;
  }

  getIssueFilters(projectId: string) {
    const filters = super.getIssueFilters(projectId);
    if (!filters) return filters;

    const groupBy = filters.displayFilters?.group_by;
    if (groupBy && EPIC_GROUP_BY_REMAP[groupBy]) {
      return {
        ...filters,
        displayFilters: {
          ...filters.displayFilters,
          group_by: EPIC_GROUP_BY_REMAP[groupBy],
        },
      };
    }

    return filters;
  }

  protected getDefaultKanbanGroupBy(): TIssueGroupByOptions {
    return "state_detail.group";
  }
}
