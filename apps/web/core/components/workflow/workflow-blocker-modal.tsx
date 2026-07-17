/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
// i18n
import { useTranslation } from "@plane/i18n";
// plane propel
import { Avatar } from "@plane/propel/avatar";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
// plane ui
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useWorkflow } from "@/hooks/store/use-workflow";

/**
 * Explains a blocked state transition. Open/close state and the blocker payload
 * come from the workflow store, so a single mounted instance (in the peek overview,
 * which every issue layout renders) serves every write path — board drag-n-drop,
 * peek, full issue page — and the peek outside-click detector can gate on
 * `blocker.open`.
 *
 * Two modes off `blocker.kind`:
 * - "forbidden": the workflow disallows this move for everyone — just an explanation.
 * - "needs_approval": the move is requestable — list the reviewers (or "any member")
 *   and offer a "Request approval" action that POSTs the request.
 */
export const WorkflowBlockerModal = observer(function WorkflowBlockerModal() {
  const { t } = useTranslation();
  const { getUserDetails } = useMember();
  const { blocker, closeBlocker, requestApprovalFromBlocker } = useWorkflow();

  const [comment, setComment] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Reset the shared flag when the host unmounts — e.g. the user navigates away
  // with the dialog still open — so it never re-appears on the next work item.
  useEffect(() => () => closeBlocker(), [closeBlocker]);

  // Clear the comment whenever the dialog opens for a fresh transition.
  useEffect(() => {
    if (blocker.open) setComment("");
  }, [blocker.open]);

  const handleRequestApproval = async () => {
    setIsSubmitting(true);
    try {
      await requestApprovalFromBlocker(comment.trim() || undefined);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("toast.success"),
        message: t("project_settings.workflow.toast.approval_requested"),
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message: t("project_settings.workflow.toast.request_approval_failed"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={blocker.open} handleClose={closeBlocker} position={EModalPosition.CENTER} width={EModalWidth.LG}>
      <div className="px-5 py-4">
        <h3 className="text-18 font-medium 2xl:text-20">{t("project_settings.workflow.heading")}</h3>

        {blocker.kind === "forbidden" ? (
          <>
            <p className="mt-3 text-13 text-secondary">{t("project_settings.workflow.transition_forbidden_message")}</p>
            <div className="mt-4 flex justify-end">
              <Button variant="secondary" size="lg" onClick={closeBlocker}>
                {t("close")}
              </Button>
            </div>
          </>
        ) : (
          <>
            {blocker.reviewers.length > 0 ? (
              // Specific reviewers: the "...request it from:" line leads into the list.
              <>
                <p className="mt-3 text-13 text-secondary">{t("project_settings.workflow.needs_approval_message")}</p>
                <ul className="mt-4 space-y-2">
                  {blocker.reviewers.map((userId) => {
                    const userDetails = getUserDetails(userId);
                    const displayName = userDetails?.display_name ?? userDetails?.email ?? userId;
                    return (
                      <li key={userId} className="flex items-center gap-2">
                        <Avatar
                          src={userDetails?.avatar_url ?? undefined}
                          name={displayName}
                          size="sm"
                          showTooltip={false}
                        />
                        <span className="text-13 text-secondary">{displayName}</span>
                      </li>
                    );
                  })}
                </ul>
              </>
            ) : (
              // No specific reviewers: a standalone sentence — no dangling "request it from:" colon.
              <p className="mt-3 text-13 text-secondary">{t("project_settings.workflow.any_member_approves")}</p>
            )}

            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
              className="focus:border-accent-primary mt-4 w-full resize-none rounded-md border border-subtle-1 bg-surface-1 px-3 py-2 text-13 text-primary outline-none"
            />

            <div className="mt-4 flex justify-end gap-2">
              <Button variant="secondary" size="lg" onClick={closeBlocker} disabled={isSubmitting}>
                {t("close")}
              </Button>
              <Button
                variant="primary"
                size="lg"
                onClick={handleRequestApproval}
                loading={isSubmitting}
                disabled={isSubmitting}
              >
                {t("project_settings.workflow.request_approval")}
              </Button>
            </div>
          </>
        )}
      </div>
    </ModalCore>
  );
});
