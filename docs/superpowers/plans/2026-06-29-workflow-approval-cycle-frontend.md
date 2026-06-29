# Workflow Approval Cycle — Frontend (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Re-align the workflow frontend to the new backend model (Phase 1, branch `feature/workflow-approval`): on-path moves are free, off-path moves open a "Request approval" flow, forbidden moves are blocked; reviewers move from transitions to **state guardians** + a project approver list; and add the approval-request UI (request / approve / reject / cancel + a pending badge).

**Architecture:** MobX store + service mirror the new REST contract (state guardians, project approvers, transition `kind`, `ApprovalRequest` lifecycle). The settings UI drops per-transition approvers and gains per-state guardians + project approvers + forbidden marking. The reactive 403 dialog catches the new codes (`WORKFLOW_TRANSITION_NEEDS_APPROVAL` / `WORKFLOW_TRANSITION_FORBIDDEN`) and, for the approval case, offers "Request approval". A pending-approval panel on the work item drives approve/reject/cancel.

**Tech Stack:** React Router 7 (NOT Next.js — `next/*` are Vite compat-shims), MobX (`mobx-react` observer, `mobx-utils` computedFn), TypeScript. **No unit tests on the web** (no jest/vitest). Per-task gate = `cd apps/web && pnpm check:types` (tsc) + `pnpm exec oxlint <files>`. Browser verification on the live dev stand is a final manual pass (drag uses `@atlaskit/pragmatic-drag-and-drop`; playwright can't drive it).

**Conventions:**

- Branch `feature/workflow-approval` (do not branch).
- Net-new TS errors only: the baseline has ~hundreds of pre-existing `@plane/*` module-resolution tsc errors — judge each file by whether IT introduces new errors, not the global count.
- Commit on a green typecheck+lint for the touched files. End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## New backend contract (what the frontend talks to)

REST (all under `/api/workspaces/<slug>/projects/<projectId>`):

- `GET/PATCH /workflow/` — `{ is_enabled }` (unchanged).
- `GET /workflow-states/`, `PATCH /workflow-states/<state_id>/` — `{ allow_issue_creation }` (unchanged).
- `GET /workflow-transitions/` — rows now `{ id, state, transition_state, kind }` (kind ∈ `allowed`/`forbidden`; **no `approvers`**).
- `POST /workflow-transitions/` — body `{ state, transition_state, kind }` (kind default `allowed`).
- `DELETE /workflow-transitions/<id>/`.
- **NEW** `GET/POST /workflow-states/<state_id>/guardians/` (POST `{ guardian_id }`), `DELETE /workflow-states/<state_id>/guardians/<guardian_id>/`.
- **NEW** `GET/POST /workflow/approvers/` (POST `{ approver_id }`), `DELETE /workflow/approvers/<approver_id>/`.
- **REMOVED** the per-transition approver endpoints.
- **NEW** approval lifecycle: `GET/POST /work-items/<issue_id>/approval-requests/` (POST `{ to_state, comment? }` → 201), `POST /work-items/<issue_id>/approval-requests/<pk>/<approve|reject|cancel>/` (reject body `{ reason? }`).

Error contract (caught on 403 from issue updates):

- `error_code: "WORKFLOW_TRANSITION_NEEDS_APPROVAL"` + `{ approvers: string[], any_member: boolean, from_state, to_state }` → offer Request approval.
- `error_code: "WORKFLOW_TRANSITION_FORBIDDEN"` + `{ from_state, to_state }` → informational "not permitted".
- (Creation still `WORKFLOW_CREATION_BLOCKED`, 400 — unchanged.)

---

## File structure

**Modify:**

- `packages/types/src/workflow.ts` — `TWorkflowTransition` (drop `approvers`, add `kind`); new `TWorkflowStateGuardian`, `TWorkflowProjectApprover`, `TApprovalRequest`.
- `apps/web/core/services/project/workflow.service.ts` — drop transition-approver methods; add guardian / project-approver / transition-kind / approval-request methods.
- `apps/web/core/store/workflow.store.ts` — drop transition-approver actions + `getAllowedReviewers`; transitions carry `kind`; add guardians/project-approvers observables + CRUD; add `getTransitionKind`; rework `isTransitionAllowed` + the blocker state to the new contract (carry issue/to_state); add the approval-request slice.
- `apps/web/ce/components/projects/settings/workflows/workflow-state-card.tsx` — add Guardians section; pass through.
- `apps/web/ce/components/projects/settings/workflows/transition-row.tsx` — remove approvers section; add `kind` (allowed/forbidden) badge/toggle.
- `apps/web/ce/components/projects/settings/workflows/root.tsx` — add a project-level Approvers section.
- `apps/web/ce/components/workflow/workflow-blocker-modal.tsx` — new codes; Request-approval action for NEEDS_APPROVAL; forbidden message.
- `apps/web/ce/components/workflow/use-workflow-drag-n-drop.ts` — block only `forbidden`; let off-path attempt (→ request modal).
- `apps/web/core/components/issues/issue-detail/sidebar.tsx`, `apps/web/core/components/issues/peek-overview/root.tsx`, `apps/web/core/hooks/use-group-dragndrop.ts` — pass `{ issueId, toState }` context into the blocker so it can POST a request.
- `packages/i18n/src/locales/*/project-settings.json` (×19) — add the new keys.

**Create:**

- `apps/web/ce/components/workflow/pending-approval-panel.tsx` — on-item panel (request info + Approve/Reject/Cancel + badge).
- (optional) `apps/web/ce/components/projects/settings/workflows/state-guardians.tsx` — extracted guardians sub-component if the state card grows large.

---

## Task 1: Types + Service

**Files:** `packages/types/src/workflow.ts`, `apps/web/core/services/project/workflow.service.ts`

- [ ] **Step 1: Update types**

In `packages/types/src/workflow.ts`:

```typescript
export type TWorkflowTransition = {
  id: string;
  state: string; // from-state id
  transition_state: string; // to-state id
  kind: "allowed" | "forbidden";
};

export type TWorkflowStateGuardian = { id: string; state: string; guardian: string };
export type TWorkflowProjectApprover = { id: string; approver: string };

export type TApprovalRequestStatus = "pending" | "approved" | "rejected" | "cancelled";
export type TApprovalRequest = {
  id: string;
  work_item: string;
  from_state: string;
  to_state: string;
  requested_by: string;
  comment: string | null;
  status: TApprovalRequestStatus;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  created_at: string;
};
```

Remove `approvers` from `TWorkflowTransition`.

- [ ] **Step 2: Rewrite the service**

In `apps/web/core/services/project/workflow.service.ts`: keep `getWorkflow/updateWorkflow/getStateConfigs/updateStateConfig/getTransitions/deleteTransition`. Change `createTransition` to accept `kind`. **Delete** `addApprover`/`removeApprover`. Add:

```typescript
async createTransition(workspaceSlug, projectId, data: { state: string; transition_state: string; kind?: "allowed" | "forbidden" }) {
  return this.post(`${this.base(workspaceSlug, projectId)}/workflow-transitions/`, data).then(r => r?.data).catch(e => { throw e?.response?.data; });
}
// state guardians
async getStateGuardians(workspaceSlug, projectId, stateId): Promise<TWorkflowStateGuardian[]> {
  return this.get(`${this.base(workspaceSlug, projectId)}/workflow-states/${stateId}/guardians/`).then(r => r?.data).catch(e => { throw e?.response?.data; });
}
async addStateGuardian(workspaceSlug, projectId, stateId, guardianId: string) {
  return this.post(`${this.base(workspaceSlug, projectId)}/workflow-states/${stateId}/guardians/`, { guardian_id: guardianId }).then(r => r?.data).catch(e => { throw e?.response?.data; });
}
async removeStateGuardian(workspaceSlug, projectId, stateId, guardianId: string) {
  return this.delete(`${this.base(workspaceSlug, projectId)}/workflow-states/${stateId}/guardians/${guardianId}/`).then(r => r?.data).catch(e => { throw e?.response?.data; });
}
// project approvers
async getProjectApprovers(workspaceSlug, projectId): Promise<TWorkflowProjectApprover[]> { /* GET /workflow/approvers/ */ }
async addProjectApprover(workspaceSlug, projectId, approverId: string) { /* POST /workflow/approvers/ { approver_id } */ }
async removeProjectApprover(workspaceSlug, projectId, approverId: string) { /* DELETE /workflow/approvers/<approverId>/ */ }
// approval requests
async getApprovalRequests(workspaceSlug, projectId, issueId): Promise<TApprovalRequest[]> { /* GET /work-items/<issueId>/approval-requests/ */ }
async createApprovalRequest(workspaceSlug, projectId, issueId, data: { to_state: string; comment?: string }): Promise<TApprovalRequest> { /* POST */ }
async decideApprovalRequest(workspaceSlug, projectId, issueId, requestId, action: "approve" | "reject" | "cancel", data?: { reason?: string }): Promise<TApprovalRequest> { /* POST /.../<requestId>/<action>/ */ }
```

(Fill the elided bodies mirroring the shown ones — same `.then(r=>r?.data).catch(e=>{throw e?.response?.data})` shape.)

- [ ] **Step 3: Gate** — `cd apps/web && pnpm check:types` (no NEW errors in these two files) + `pnpm exec oxlint core/services/project/workflow.service.ts`. Commit.

---

## Task 2: Store

**Files:** `apps/web/core/store/workflow.store.ts`

- [ ] **Step 1: Transitions carry `kind`; drop dead approver logic**

- Remove `addApprover`/`removeApprover` actions and `getAllowedReviewers` computed (dead under the new model).
- `createTransition` signature gains `kind` and passes it through.
- Add `getTransitionKind = computedFn((projectId, fromStateId, toStateId): "allowed" | "forbidden" | "off_path" => { ... })` — find the transition row; return its `kind`; if none → `"off_path"`.
- Rework `isTransitionAllowed(projectId, fromStateId, toStateId, userId)`: workflow disabled → true; same state → true; `getTransitionKind` → `forbidden` → false; `allowed` → true; `off_path` → **also true** (off-path is no longer a hard client-side block — it is requestable; the drag hook handles "forbidden vs attempt"). Keep the method for callers but its only `false` case is now `forbidden`.

- [ ] **Step 2: Guardians + project approvers slice**

Add observables `guardiansByState: Record<string, TWorkflowStateGuardian[]>` and `projectApprovers: Record<string, TWorkflowProjectApprover[]>` (keyed by projectId), with `fetchStateGuardians/addStateGuardian/removeStateGuardian` and `fetchProjectApprovers/addProjectApprover/removeProjectApprover` actions — mirror the existing optimistic+revert pattern of `addApprover`/`removeApprover` you are removing (so the code style is unchanged), but against the new service methods and observables. Register new observables/actions in `makeObservable`.

- [ ] **Step 3: Rework the reactive blocker state to the new contract**

The blocker dialog must support BOTH the approval case and the forbidden case, and (for approval) needs the issue + target so it can POST a request. Replace the blocker slice:

```typescript
// observables
blocker: {
  open: boolean;
  kind: "needs_approval" | "forbidden" | null;
  reviewers: string[];     // resolved approvers (empty => any member)
  anyMember: boolean;
  workspaceSlug: string; projectId: string; issueId: string; toState: string;  // for POSTing a request
} = { open: false, kind: null, reviewers: [], anyMember: false, workspaceSlug: "", projectId: "", issueId: "", toState: "" };

closeBlocker = () => { this.blocker = { ...this.blocker, open: false, kind: null }; };

// Returns true if it handled a workflow 403 (and opened the dialog); else false.
tryOpenBlockerFromError = (error, ctx: { workspaceSlug: string; projectId: string; issueId: string; toState: string }): boolean => {
  const err = error as Record<string, unknown> | null;
  const code = err?.error_code;
  if (code === "WORKFLOW_TRANSITION_NEEDS_APPROVAL") {
    this.blocker = { open: true, kind: "needs_approval",
      reviewers: Array.isArray(err?.approvers) ? (err.approvers as string[]) : [],
      anyMember: !!err?.any_member, ...ctx };
    return true;
  }
  if (code === "WORKFLOW_TRANSITION_FORBIDDEN") {
    this.blocker = { open: true, kind: "forbidden", reviewers: [], anyMember: false, ...ctx };
    return true;
  }
  return false;
};

// Called by the modal's "Request approval" button:
requestApprovalFromBlocker = async (comment?: string): Promise<void> => {
  const { workspaceSlug, projectId, issueId, toState } = this.blocker;
  await this.workflowService.createApprovalRequest(workspaceSlug, projectId, issueId, { to_state: toState, comment });
  this.closeBlocker();
};
```

> Note: every caller of `tryOpenBlockerFromError` (3 sites, Task 4) must now pass the `ctx` object. Update its signature accordingly.

- [ ] **Step 4: Approval-request slice (for the on-item panel)**

Add `approvalRequestsByIssue: Record<string, TApprovalRequest[]>` + `fetchApprovalRequests(ws, projectId, issueId)` and `decideApprovalRequest(ws, projectId, issueId, requestId, action, data?)` (calls the service, updates the issue's list). A computed `getPendingRequest = computedFn((issueId) => (this.approvalRequestsByIssue[issueId] ?? []).find(r => r.status === "pending"))`.

- [ ] **Step 5: Gate** — typecheck (no new errors in the store) + `pnpm exec oxlint core/store/workflow.store.ts`. Also update the `IWorkflowStore` interface to match. Commit.

---

## Task 3: Settings redesign (guardians / project approvers / forbidden)

**Files:** the three `ce/components/projects/settings/workflows/*` + i18n.

- [ ] **Step 1: i18n keys** (add to `packages/i18n/src/locales/en/project-settings.json` `workflow` block, then mirror to all 19 locales — English placeholder is fine for non-en, matching the existing convention):
      `guardians: "Guardians"`, `add_guardian: "Add guardian"`, `guardians_help: "People who approve off-path moves into this status"`, `project_approvers: "Project approvers"`, `project_approvers_help: "Fallback approvers for any off-path move"`, `forbidden: "Forbidden"`, `allowed: "Allowed"`, `mark_forbidden: "Mark forbidden"`, `request_approval: "Request approval"`, `approval_requested: "Approval requested"`, `pending_approval: "Pending approval"`, `needs_approval_message: "This move needs approval. Request it from:"`, `transition_forbidden_message: "This status transition is not allowed by the project workflow."`, `approve: "Approve"`, `reject: "Reject"`, `cancel_request: "Cancel request"`, `requested_by: "Requested by"`, `any_member_approves: "Any project member can approve"`.
      Remove now-unused `approvers`/`allowed_reviewers`/`any_member`/`transition_blocked` only if nothing else references them (grep first; if unsure, leave them — unused keys are harmless).

- [ ] **Step 2: `transition-row.tsx` — drop approvers, show/set `kind`**

Remove the entire "Approvers:" section (chips, add-approver picker, `addApprover`/`removeApprover`, member-detail reads). The row now shows the target state, a `kind` indicator (Allowed / Forbidden), a control to toggle a transition's kind (or — simpler for v1 — set kind at creation time only and show a read-only badge + delete), and the delete button. If toggling kind in place is more than a small change, v1 = create-time choice + read-only badge; note the limitation in the PR.

- [ ] **Step 3: `workflow-state-card.tsx` — add Guardians section**

Below the transitions list, add a "Guardians" block: a chip list of current guardians for THIS state (from `guardiansByState[state.id]`, resolve names via `useMember`) with remove buttons, and an "Add guardian" member picker (reuse the same `CustomSearchSelect`/member-picker the old approver UI used — see git history of `transition-row.tsx`) → `addStateGuardian(ws, projectId, state.id, userId)` / `removeStateGuardian(...)`. Fetch guardians for visible states (call `fetchStateGuardians` in the card's mount effect, or batch in `root.tsx`). When the "Add transition" button creates a transition, allow choosing kind (allowed default; an option/checkbox to create a forbidden one) — pass `kind` to `createTransition`.

- [ ] **Step 4: `root.tsx` — project approvers section + fetch**

Add a top-level "Project approvers" block (chip list + member picker → `addProjectApprover`/`removeProjectApprover`), and `fetchProjectApprovers` + per-state `fetchStateGuardians` in the mount effect.

- [ ] **Step 5: Gate** — typecheck + oxlint on the three components + the i18n JSON is valid. Commit. (Browser verification of the settings UI is part of the final manual pass.)

---

## Task 4: Blocker → Approval dialog + 403 catch sites + drag

**Files:** `ce/components/workflow/workflow-blocker-modal.tsx`, the 3 catch sites, `ce/components/workflow/use-workflow-drag-n-drop.ts`.

- [ ] **Step 1: Modal — two modes + Request approval**

`workflow-blocker-modal.tsx` (observer, reads `useWorkflow().blocker` + `closeBlocker` + `requestApprovalFromBlocker`):

- `blocker.kind === "forbidden"` → heading + `transition_forbidden_message`, just a Close button.
- `blocker.kind === "needs_approval"` → heading + `needs_approval_message`; if `blocker.reviewers.length` → list them (avatars/names via `useMember`), else `any_member_approves`; an optional comment textarea; a primary **Request approval** button → `await requestApprovalFromBlocker(comment)` then toast success; plus Close.
- Keep using `ModalCore` (same styling). Drop the old `transition_blocked`/`transition_not_allowed`/`allowed_reviewers` wiring.

- [ ] **Step 2: Update the 3 catch sites to pass context**

In `sidebar.tsx` (`handleStateChange`), `peek-overview/root.tsx` (`issueOperations.update`), and `use-group-dragndrop.ts` (`updateIssueOnDrop`): change `tryOpenBlockerFromError(error)` → `tryOpenBlockerFromError(error, { workspaceSlug, projectId, issueId, toState })`. The target state id is the `state_id` being set (available at each call site — it's the value being assigned). For the drag site, `data` carries the new `state_id`.

- [ ] **Step 3: Drag hook — block only forbidden**

In `use-workflow-drag-n-drop.ts`, `handleWorkFlowState(source, dest)`: set `isWorkflowDropDisabled = (getTransitionKind(projectId, source, dest) === "forbidden")` (hard block only forbidden). Off-path is NOT preemptively blocked — the drop attempts, gets a 403 NEEDS_APPROVAL, and the modal offers Request approval (the card reverts via the optimistic rollback, already in place). Keep `workflowDisabledSource` for the red overlay only on forbidden.

- [ ] **Step 4: Gate** — typecheck + oxlint on all touched files. Commit.

---

## Task 5: On-item pending-approval panel + badge

**Files:** create `ce/components/workflow/pending-approval-panel.tsx`; mount it on the work item; (optional) a small badge.

- [ ] **Step 1: Panel component**

`PendingApprovalPanel` (observer, props `{ workspaceSlug, projectId, issueId }`): on mount `fetchApprovalRequests`. Read `getPendingRequest(issueId)`. If none → render nothing. If pending → show "Pending approval" badge, the requested move (`from_state → to_state` via `useProjectState`), `requested_by` (via `useMember`), and the comment. Actions:

- **Cancel** — shown to the requester (`request.requested_by === currentUser.id`) → `decideApprovalRequest(..., "cancel")`.
- **Approve / Reject** — shown to a resolved approver. The frontend can't perfectly resolve approvers, so show Approve/Reject to project members who are NOT the requester (the server enforces authority and returns 403 otherwise — surface that as a toast). Reject opens a small reason input. On success, refetch the issue (the approve applies the move) + the requests.

- [ ] **Step 2: Mount it**

Render `<PendingApprovalPanel ... />` in the work-item detail sidebar (`core/components/issues/issue-detail/sidebar.tsx`) and the peek properties (`core/components/issues/peek-overview/properties.tsx`) near the State row, so both surfaces show the pending state.

- [ ] **Step 3: Gate** — typecheck + oxlint. Commit.

---

## Final manual verification (live dev stand — not automatable)

Run the web on :3000 + api on :8000 (api must be migrated to `iw_006`). Hard-reload (HMR can leave stale listeners). Verify, with a member (non-admin) and an admin:

1. **Settings:** enable workflow; add an `allowed` transition (free), a `forbidden` transition, a state guardian, a project approver. Persist across reload.
2. **On-path free:** move a card along an `allowed` transition → succeeds, no dialog.
3. **Forbidden:** drag/select a `forbidden` move → blocked (preemptive red on drag / dialog with "not allowed" on select).
4. **Off-path → request:** move a card off-path → dialog "needs approval" with the resolved approvers (or "any member") + Request approval → creates a request; the card shows "Pending approval".
5. **Approve:** as a guardian (≠ requester), Approve from the panel → the card moves; requester sees the decision. **Reject** → card stays. **Cancel** as requester → request gone.
6. **Notifications:** approver gets an in-app notification on request; requester on decision.

---

## Self-review (done while writing)

- **Spec coverage:** types/service/store (T1–T2), settings redesign incl. guardians/project-approvers/forbidden (T3), reactive dialog + Request approval + new codes + drag (T4), on-item approve/reject/cancel + badge (T5). The whole new contract is consumed.
- **Known follow-ups for the implementer:** confirm the exact member-picker component used by the old approver UI (reuse it for guardians/project-approvers) — see git history of `transition-row.tsx`; the in-place transition `kind` toggle may be deferred to create-time-only for v1 (flag it); frontend approver-resolution for showing Approve/Reject is best-effort (server is the authority).
- **Type consistency:** `getTransitionKind` returns `"allowed"|"forbidden"|"off_path"` and is the single source for both drag and `isTransitionAllowed`; `tryOpenBlockerFromError(error, ctx)` ctx shape is identical at all 3 call sites and matches the store's `blocker` fields.
