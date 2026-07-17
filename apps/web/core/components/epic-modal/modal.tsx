/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React from "react";
import { observer } from "mobx-react";
import { EIssuesStoreType } from "@plane/types";
import type { TIssue } from "@plane/types";
import { IssueModalProvider } from "@/components/issues/issue-modal/provider";
import { CreateUpdateIssueModalBase } from "@/components/issues/issue-modal/base";

export interface EpicModalProps {
  data?: Partial<TIssue>;
  isOpen: boolean;
  onClose: () => void;
  beforeFormSubmit?: () => Promise<void>;
  onSubmit?: (res: TIssue) => Promise<void>;
  fetchIssueDetails?: boolean;
  primaryButtonText?: {
    default: string;
    loading: string;
  };
  isProjectSelectionDisabled?: boolean;
}

export const CreateUpdateEpicModal = observer(function CreateUpdateEpicModal(props: EpicModalProps) {
  const {
    isOpen,
    onClose,
    data,
    onSubmit,
    beforeFormSubmit,
    fetchIssueDetails,
    primaryButtonText,
    isProjectSelectionDisabled,
  } = props;

  if (!isOpen) return null;

  const dataForPreload = { ...data, is_epic: true };

  return (
    <IssueModalProvider dataForPreload={dataForPreload}>
      <CreateUpdateIssueModalBase
        data={dataForPreload}
        isOpen={isOpen}
        onClose={onClose}
        onSubmit={onSubmit}
        beforeFormSubmit={beforeFormSubmit}
        fetchIssueDetails={fetchIssueDetails}
        primaryButtonText={primaryButtonText}
        isProjectSelectionDisabled={isProjectSelectionDisabled}
        storeType={EIssuesStoreType.EPIC}
        modalTitle={data?.id ? "Update epic" : "Create epic"}
      />
    </IssueModalProvider>
  );
});
