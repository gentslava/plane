/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { ArrowRight } from "lucide-react";
// i18n
import { useTranslation } from "@plane/i18n";
// plane propel
import { Avatar } from "@plane/propel/avatar";
import { Button } from "@plane/propel/button";
import { setToast, TOAST_TYPE } from "@plane/propel/toast";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useMember } from "@/hooks/store/use-member";
import { useProjectState } from "@/hooks/store/use-project-state";
import { useUser } from "@/hooks/store/user";
import { useWorkflow } from "@/hooks/store/use-workflow";

type Props = {
  workspaceSlug: string;
  projectId: string;
  issueId: string;
};

/**
 * On-item panel for a pending workflow approval request. Renders nothing unless
 * the issue has a pending request. The requester sees a "Cancel request" action;
 * every other project member sees "Approve" / "Reject" (Reject reveals an optional
 * reason field). Frontend approver-resolution is best-effort — the server is the
 * authority and returns 403 if the caller can't decide, surfaced here as a toast.
 */
export const PendingApprovalPanel = observer(function PendingApprovalPanel(props: Props) {
  const { workspaceSlug, projectId, issueId } = props;
  // hooks
  const { t } = useTranslation();
  const { getPendingRequest, fetchApprovalRequests, decideApprovalRequest } = useWorkflow();
  const { getStateById } = useProjectState();
  const { getUserDetails } = useMember();
  const { data: currentUser } = useUser();
  const { fetchIssue } = useIssueDetail();
  // local state
  const [showRejectReason, setShowRejectReason] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [submitting, setSubmitting] = useState<"approve" | "reject" | "cancel" | null>(null);

  // Load the issue's approval requests on mount / when the issue changes.
  useEffect(() => {
    fetchApprovalRequests(workspaceSlug, projectId, issueId).catch(() => {
      // Silent: a missing/forbidden requests list just means no panel.
    });
  }, [fetchApprovalRequests, workspaceSlug, projectId, issueId]);

  const request = getPendingRequest(issueId);
  if (!request) return null;

  const fromState = getStateById(request.from_state);
  const toState = getStateById(request.to_state);
  const requestedBy = getUserDetails(request.requested_by);
  const requestedByName = requestedBy?.display_name ?? requestedBy?.email ?? request.requested_by;
  const isRequester = !!currentUser?.id && request.requested_by === currentUser.id;

  const handleDecision = async (decision: "approve" | "reject" | "cancel") => {
    setSubmitting(decision);
    try {
      await decideApprovalRequest(
        workspaceSlug,
        projectId,
        issueId,
        request.id,
        decision,
        decision === "reject" ? { reason: rejectReason.trim() || undefined } : undefined
      );
      // An approved decision applies the move — refetch the issue so the new state
      // reflects. Always refetch requests so this panel updates (or disappears).
      await Promise.all([
        fetchIssue(workspaceSlug, projectId, issueId).catch(() => {}),
        fetchApprovalRequests(workspaceSlug, projectId, issueId).catch(() => {}),
      ]);
      const successMessage =
        decision === "approve"
          ? t("project_settings.workflow.toast.request_approved")
          : decision === "reject"
            ? t("project_settings.workflow.toast.request_rejected")
            : t("project_settings.workflow.toast.request_cancelled");
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("toast.success"),
        message: successMessage,
      });
      setShowRejectReason(false);
      setRejectReason("");
    } catch (error) {
      // Server enforces approver authority — a 403 (or anything else) means the
      // decision didn't apply. Refetch so the panel reflects the real state.
      fetchApprovalRequests(workspaceSlug, projectId, issueId).catch(() => {});
      // The service rejects with the backend error body, whose 403 message lives
      // under `error` (e.g. "You are not permitted to decide this request.").
      const message =
        (error as Record<string, unknown> | null)?.error?.toString() ??
        t("project_settings.workflow.toast.decision_failed");
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("toast.error"),
        message,
      });
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <div className="rounded-md border border-subtle bg-warning-subtle px-3 py-2.5 text-13">
      {/* Header: pending badge + requested-by */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="rounded-full bg-warning-subtle px-2 py-0.5 text-11 font-medium text-warning-primary">
          {t("project_settings.workflow.pending_approval")}
        </span>
        <div className="flex items-center gap-1.5">
          <span className="text-11 text-secondary">{t("project_settings.workflow.requested_by")}:</span>
          <Avatar src={requestedBy?.avatar_url ?? undefined} name={requestedByName} size="sm" showTooltip={false} />
          <span className="text-12 text-secondary">{requestedByName}</span>
        </div>
      </div>

      {/* Requested move: from → to */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {fromState ? (
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 flex-shrink-0 rounded-full" style={{ backgroundColor: fromState.color }} />
            <span className="font-medium text-primary">{fromState.name}</span>
          </span>
        ) : (
          <span className="text-tertiary italic">{request.from_state}</span>
        )}
        <ArrowRight className="size-3.5 flex-shrink-0 text-secondary" />
        {toState ? (
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 flex-shrink-0 rounded-full" style={{ backgroundColor: toState.color }} />
            <span className="font-medium text-primary">{toState.name}</span>
          </span>
        ) : (
          <span className="text-tertiary italic">{request.to_state}</span>
        )}
      </div>

      {/* Optional comment */}
      {request.comment && <p className="mt-2 text-12 text-secondary">{request.comment}</p>}

      {/* Reject reason field */}
      {!isRequester && showRejectReason && (
        <textarea
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
          rows={2}
          placeholder={t("project_settings.workflow.reject")}
          className="focus:border-accent-primary mt-2 w-full resize-none rounded-md border border-subtle-1 bg-surface-1 px-2 py-1.5 text-12 text-primary outline-none"
        />
      )}

      {/* Actions */}
      <div className="mt-2.5 flex items-center justify-end gap-2">
        {isRequester ? (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => handleDecision("cancel")}
            loading={submitting === "cancel"}
            disabled={!!submitting}
          >
            {t("project_settings.workflow.cancel_request")}
          </Button>
        ) : (
          <>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                if (showRejectReason) handleDecision("reject");
                else setShowRejectReason(true);
              }}
              loading={submitting === "reject"}
              disabled={!!submitting}
            >
              {t("project_settings.workflow.reject")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => handleDecision("approve")}
              loading={submitting === "approve"}
              disabled={!!submitting}
            >
              {t("project_settings.workflow.approve")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
});
