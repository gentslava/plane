/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { EIssueServiceType } from "@plane/types";
import type { IProjectIssues } from "../project/issue.store";
import { ProjectIssues } from "../project/issue.store";
import type { IIssueRootStore } from "../root.store";
import type { IProjectEpicsFilter } from "./filter.store";

export type IProjectEpics = IProjectIssues;

export class ProjectEpics extends ProjectIssues implements IProjectEpics {
  constructor(rootStore: IIssueRootStore, issueFilterStore: IProjectEpicsFilter) {
    super(rootStore, issueFilterStore, EIssueServiceType.EPICS);
  }
}
