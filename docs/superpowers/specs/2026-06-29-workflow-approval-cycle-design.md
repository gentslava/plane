# Workflow Approval Cycle — Design

**Date:** 2026-06-29
**Branch:** `feature/workflow-approval`
**Status:** Design (approved in brainstorm; pending spec review)

## Relationship to the managed-transitions base

The base feature ("managed transitions", spec `2026-06-25-workflow-approval-design.md`)
is committed on `feature/workflow-approval` (`a91710e652`, `84f2d4fa63`) and **not yet
merged** to `plus`. This document **revises the enforcement model** and **adds the
approval cycle** on top of it. Because the base is unmerged, the model change is made
as a forward migration (`iw_006`), not a rewrite of `iw_005`.

What changes versus the base:

|                       | Base (managed transitions)                         | This design (approval cycle)                        |
| --------------------- | -------------------------------------------------- | --------------------------------------------------- |
| Unconfigured move     | **blocked** (strict allowlist)                     | **free** (workflow does not manage it)              |
| Configured transition | allowed; `approvers` restrict _who may perform it_ | the **free** happy-path graph; no approvers on it   |
| Off-path move         | (n/a — blocked)                                    | **needs approval** (request → approve → apply)      |
| Approver placement    | per transition (`WorkflowTransitionApprover`)      | **per target state** (guardians) + project fallback |
| Hard forbid           | implicit (just don't list it)                      | **explicit** mark (cannot even request)             |

## Philosophy

Kanban is a value stream (`Backlog → Dev → QA → Review → Done`). The workflow should:

- keep the **happy path frictionless** — moving along a defined edge is free;
- make **deviations accountable, not impossible** — skipping/jumping requires a
  conscious sign-off from someone responsible, rather than being silently allowed or
  dead-ended;
- reserve **hard blocks** for the few real rules (e.g. never reopen `Done → Backlog`).

## Enforcement model — how a card may move

For a move `from_state → to_state` in a project with workflow enabled:

| Move classification                                                 | Behaviour                                                                          |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| **On-path** — `(from, to)` is an allowed edge in the workflow graph | **Free**: anyone moves directly, no approval.                                      |
| **Off-path** — not an allowed edge, not forbidden                   | **Needs approval**: requester initiates → an approver approves → the move applies. |
| **Forbidden** — `(from, to)` explicitly marked forbidden            | **Blocked**: cannot be done or requested.                                          |

Workflow disabled → everything is free (early ALLOW), unchanged from the base.

### Who approves an off-path move (approver resolution)

The approver is the **guardian of the target state** ("who lets a card into this
column"). One approver group regardless of how many states were skipped; the target
guardian sees where the card came from and accepts responsibility for what it skipped.
(The alternative "guardian of each skipped state" is more precise but needs path
computation and multi-approval — **deferred**, see Open questions.)

Resolution is a fallback chain so the flexible (per-state) and the simple (central PM)
setups coexist through one mechanism:

1. **Target state has guardians** → those people approve.
2. **else project-level fallback approvers** are set → those people approve.
3. **else** → any **other** project member (four-eyes; the requester can never approve
   their own request).

An actor who is already an approver for the move performs it **directly** (no request —
approving yourself is pointless).

## Data model

`ProjectWorkflow(is_enabled)` and `WorkflowStateConfig(allow_issue_creation)` are
unchanged from the base.

Revised / new (migration `iw_006`):

| Model                             | Purpose                                                                                                                                                                                                                                                                                 |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `WorkflowTransition`              | Gains a `kind`: `ALLOWED` (free edge) \| `FORBIDDEN` (hard block). Off-path = **no row**. The `approvers` relation is removed (guardians move to states).                                                                                                                               |
| `WorkflowStateGuardian` _(new)_   | `(project, state, user)`. People who approve off-path entry **into** `state`. No rows for a state = fall through the resolution chain. Soft-delete-aware unique `(state, user)`.                                                                                                        |
| `WorkflowProjectApprover` _(new)_ | `(project, user)`. Project-level fallback approvers (the "PM watches the board" setup).                                                                                                                                                                                                 |
| `ApprovalRequest` _(new)_         | A pending off-path move. Lightweight — **not** a work item. Fields: `work_item`, `project`, `workspace`, `from_state`, `to_state`, `requested_by`, `comment` (optional), `status` (`PENDING\|APPROVED\|REJECTED\|CANCELLED`), `decided_by`, `decided_at`, `decision_reason` (optional). |

`WorkflowTransitionApprover` (base) is dropped by `iw_006`.

> Migration note (fork convention `iw_*`): the dev stand already has `iw_005` applied,
> so `iw_006` evolves the schema forward. Do not rewrite `iw_005`.

## Approval request lifecycle

```
PENDING ──approve──▶ APPROVED   (server applies the transition; requester notified)
   │     ──reject───▶ REJECTED   (requester notified, optional reason; card stays)
   │     ──cancel───▶ CANCELLED  (requester withdraws)
   └─────auto────────▶ CANCELLED  (card moved another way / config invalidated)
```

- **One active (`PENDING`) request per work item** at a time. A second request while one
  is pending is rejected with a clear error.
- While `PENDING`, the **card stays in `from_state`** and shows a "pending approval"
  badge. The transition is applied **only** on approval (no optimistic move-then-revert).
- **Auto-cancel** a pending request when its premise no longer holds: the card was moved
  another way (on-path, or directly by a guardian), or the `to_state`/`from_state` config
  changed such that the request is moot (e.g. `to_state` became forbidden).
- On **approve**, the server applies `work_item.state_id = to_state` as an
  already-approved action (bypassing the guard) and records `decided_by/at`.

## Visibility

Two distinct senses, by the principle _"you see the actionable request only if you can
act on it"_:

- **The "pending approval" badge** on the work item — visible to anyone who can see the
  work item (it is only a status indicator).
- **The request itself + Approve/Reject** — visible/actionable only to:
  - the **requester** (sees their request, may cancel);
  - the **resolved approvers** for the move (guardians → project approvers → any other
    member).

So with target-state guardians or a project approver list, the actionable circle stays
narrow and predictable; it is broad ("any member") only when the resolution falls
through to four-eyes, which is exactly the case where any member genuinely can approve.

## Surface (lightweight — no separate "Approvals" page)

Because the actionable circle is narrow, reuse existing Plane mechanics:

- **In-app notifications**: approvers notified on a new request; requester notified on the
  decision.
- **On the work item**: a "pending approval" panel showing the requested move, requester,
  and comment, with **Approve / Reject** (optional reason) for approvers and **Cancel**
  for the requester.
- The **requester entry point**: when a direct off-path move is refused, the work-item UI
  offers **"Request approval"** (with optional comment) instead of a dead-end error.

A dedicated project-wide "Approvals" inbox is **out of scope** for v1; it can be added
later if volume warrants.

## Enforcement integration

`guard.evaluate_transition` returns one of `ALLOW | NEEDS_APPROVAL | BLOCK` (the
`NEEDS_APPROVAL` action is already reserved in `guard.py`):

```
workflow disabled                         → ALLOW
(from,to) is FORBIDDEN                     → BLOCK   (WORKFLOW_TRANSITION_FORBIDDEN)
(from,to) is ALLOWED (free edge)           → ALLOW
actor is a resolved approver for the move  → ALLOW   (acts directly)
otherwise (off-path, actor not approver)   → NEEDS_APPROVAL (carries resolved approvers)
```

- A **direct** off-path move by a non-approver does **not** mutate state; the API returns
  a structured `NEEDS_APPROVAL` response (HTTP 403, `error_code:
"WORKFLOW_TRANSITION_NEEDS_APPROVAL"`, with the target state's resolved approvers and a
  hint to use the request endpoint). The frontend turns this into "Request approval"
  rather than an error toast.
- Creating a request goes through a dedicated endpoint (`ApprovalRequest` POST); applying
  an approved request is a server action that bypasses the guard.
- **Coverage discipline (carried from the base):** every state-write path must surface
  the same decision contract — REST app create/partial_update, draft→issue, public API,
  and the **GraphQL gateway**. The coverage registry test is extended for the new codes.

## Testing

- **`guard.evaluate_transition` unit matrix**: disabled; on-path free; forbidden; actor is
  guardian (direct); actor not approver (needs approval) with each resolution tier
  (state guardian / project approver / four-eyes); requester-cannot-self-approve.
- **Contract tests** (Docker, `iw_006 --create-db`): request create (happy + duplicate
  pending rejected + forbidden rejected), approve → card moved + request `APPROVED`,
  reject → card unchanged, cancel, auto-cancel on independent move, permissions (only
  resolved approvers may approve; only requester may cancel).
- **Coverage registry**: assert all write paths emit `NEEDS_APPROVAL`/forbidden
  consistently, including GraphQL.
- **Signal safety net**: direct `issue.save()` of an off-path move by a non-approver is
  still caught.

## Suggested phasing (for the implementation plan)

1. **Backend model + enforcement + request lifecycle** — `iw_006`, guard rewrite,
   `ApprovalRequest` + endpoints (request/approve/reject/cancel), apply-on-approve,
   notifications wiring, tests. Note: this changes the error contract (new
   `WORKFLOW_TRANSITION_NEEDS_APPROVAL` / `…_FORBIDDEN` codes replace the base's
   `…_BLOCKED`), so the existing blocker dialog stops matching off-path moves until
   step 2 — acceptable for a phased rollout (the server still blocks the move).
2. **Frontend** — settings redesign (per-state guardians, project fallback approvers,
   free-edge graph, forbidden marks); work-item pending-approval panel +
   Request/Approve/Reject/Cancel; notification surfacing.
3. **GraphQL / mobile** — surface `NEEDS_APPROVAL` on the mobile path and (optionally)
   request/approve via GraphQL.

## Phase 3 detail — GraphQL approval cycle (A+B)

Phase 3 surfaces the approval cycle on the GraphQL gateway (used by the native
mobile app, which we do **not** patch and which only knows the Cloud SDL). Two
complementary parts, both chosen by the product owner:

- **A — API parity.** Expose the full request lifecycle as GraphQL operations so
  any GraphQL client (future native features, custom integrations, tooling) can
  drive it, mirroring the REST endpoints exactly.
- **B — native-friendly auto-request.** Because the native app cannot render a
  "Request approval" button, an off-path **transition** attempt on the gateway
  auto-creates the approval request and returns an informative error, so the act
  of trying to move the card _is_ the request. Approvers act on the web.

**Single source of truth.** The request-create and decide (approve/reject/cancel)
logic moves out of the REST views into `plane/workflow/service.py`
(`create_approval_request`, `decide_approval_request`, raising a domain
`ApprovalError(code, message, status)`); REST views and GraphQL resolvers both
call it. This is a behaviour-preserving extraction guarded by the existing
`test_approval_requests_app.py` contract tests.

**A — schema (`schema.graphql`), resolvers (`graphql/areas/workflow_approval.py`):**

```graphql
type WorkflowApprovalRequestType {
  id: ID!  workspace: ID!  project: ID!  workItem: ID!
  fromState: ID!  toState: ID!  requestedBy: ID!  comment: String
  status: String!  decidedBy: ID  decidedAt: DateTime  decisionReason: String
  createdAt: DateTime!  updatedAt: DateTime!
}
# Mutation
requestWorkflowApproval(slug, project, workItem, toState, comment): WorkflowApprovalRequestType!
approveWorkflowRequest(slug, project, request, reason): WorkflowApprovalRequestType!
rejectWorkflowRequest(slug, project, request, reason): WorkflowApprovalRequestType!
cancelWorkflowRequest(slug, project, request): WorkflowApprovalRequestType!
# Query — visibility = requester OR resolved approver; default status PENDING
workflowApprovalRequests(slug, project, workItem, status): [WorkflowApprovalRequestType!]!
```

FK fields resolve through explicit `str(obj.*_id)` resolvers (not smart fallback).

**B — `graphql/workflow.py` helper `guard_transition_or_request(project_id,
from_state_id, to_state_id, actor, work_item)`**, used in `updateIssueV2` and
`updateEpic` in place of `guard_transition`:

```
ALLOW          -> return (move proceeds)
FORBIDDEN      -> raise GraphQLError(decision.code)         (hard block, as today)
NEEDS_APPROVAL -> create_approval_request(...)              (or reuse the pending one)
                  raise GraphQLError(code="WORKFLOW_APPROVAL_REQUESTED",
                                     extensions={requestId, context})
```

Creation paths (`createIssueV2`/`createEpic`) keep `guard_creation` — a creation
block is a hard error, there is no "request to create". The new code
`WORKFLOW_APPROVAL_REQUESTED` lets a client distinguish "a request was just filed
for you" from REST's `WORKFLOW_TRANSITION_NEEDS_APPROVAL` ("you must request").

**Why this is safe transactionally.** The gateway runs with no `ATOMIC_REQUESTS`
and no `transaction.atomic` around `/graphql/` (sync resolvers, autocommit), so
the auto-created request commits immediately and the subsequent `GraphQLError`
does **not** roll it back. (Verified in the codebase; if a transaction wrapper is
ever added, B must switch to a durable write or a non-error response.)

**Accepted asymmetry.** REST off-path → structured `NEEDS_APPROVAL` → web shows an
explicit "Request approval" button; GraphQL off-path → auto-files the request.
Same logical action, client-appropriate behaviour. GraphQL error message strings
bypass the web i18n layer (the native app uses its own locale), matching how the
existing guard messages already behave — server-side error localisation is out of
scope.

**Tests** (Docker, direct-resolver pattern from `test_workflow_graphql.py`): the
four mutations (happy + permission paths), the query visibility filter, and B on
`updateIssueV2`/`updateEpic` (off-path → request persisted + `WORKFLOW_APPROVAL_REQUESTED`

- state unchanged; forbidden → error, no request; resolved approver → direct move,
  no request; second attempt → "already pending"). The two existing
  `test_update_*_transition_blocked` tests are updated to the B contract, and the
  coverage registry (`test_workflow_coverage.py`) learns the new code.

## Open questions / deferred

- **Guardian of the skipped state(s)** as an alternative/added approver policy (more
  precise, but needs skipped-path computation and multi-approval). Deferred; target-state
  guardian is v1.
- **Multi-approver policies** (quorum / N-of-M). v1: a single approval applies the move.
- **GraphQL request/approve** parity vs. read-only "needs approval" surfacing on mobile
  (phasing within step 3).
- **Settings UX** for the three concepts on one page (free-edge graph vs. state guardians
  vs. forbidden) — to be detailed in the plan; keep the card-list style, no custom canvas.
