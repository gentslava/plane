# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Defense-in-depth: a pre_save safety net that blocks disallowed transitions
on any direct Issue.save().

Actor resolution order:
  1. Explicit ``workflow_actor_context`` context variable (set by callers that
     want fine-grained control or run outside an HTTP request, e.g. tests).
  2. ``crum.get_current_user()`` — set automatically by
     ``crum.CurrentRequestUserMiddleware`` for every HTTP request (REST and
     GraphQL alike), so the net is **active on all HTTP issue-saves**.

If neither source provides an authenticated user the net is dormant — this
covers management commands, migrations, and test fixtures that bypass HTTP.

The net only has real effect when the project's workflow is **enabled**.
``enforce_transition`` → ``guard.evaluate_transition`` returns ALLOW
immediately when the workflow is disabled, so the cost is a single
EXISTS query per save on the vast majority of projects where workflow is
off, and there are no false-positive blocks.
"""

import contextvars
from contextlib import contextmanager

from crum import get_current_user
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from plane.db.models import ApprovalRequest, Issue
from .enforcement import enforce_transition

_actor_var = contextvars.ContextVar("workflow_actor", default=None)
_bypass_var = contextvars.ContextVar("workflow_bypass", default=False)


@contextmanager
def workflow_actor_context(actor):
    """Bind the acting user so the pre_save net can evaluate transitions.

    Takes priority over the crum request-user for the duration of the block.
    """
    token = _actor_var.set(actor)
    try:
        yield
    finally:
        _actor_var.reset(token)


@contextmanager
def workflow_bypass():
    """Suppress the pre_save guard net for an already-approved server action.

    Used by the apply-on-approve path: the transition is still off-path, so the
    net would re-block it; the approval decision already authorized the move.
    """
    token = _bypass_var.set(True)
    try:
        yield
    finally:
        _bypass_var.reset(token)


@receiver(pre_save, sender=Issue, dispatch_uid="workflow.guard_issue_transition")
def guard_issue_transition(sender, instance, update_fields=None, **kwargs):
    # An already-approved server move runs inside workflow_bypass(); the
    # approval decision authorized it, so the net must not re-block it.
    if _bypass_var.get():
        return
    # Resolve actor: explicit context first, then HTTP request user via crum.
    actor = _actor_var.get()
    if actor is None:
        actor = get_current_user()
    if actor is None or not getattr(actor, "is_authenticated", False):
        return
    if instance.pk is None or instance.state_id is None:
        return
    # Skip the DB round-trip for saves that cannot change the state.
    if update_fields is not None and "state" not in update_fields and "state_id" not in update_fields:
        return
    previous = Issue.objects.filter(pk=instance.pk).values_list("state_id", flat=True).first()
    if previous is None or previous == instance.state_id:
        return
    enforce_transition(instance.project_id, previous, instance.state_id, actor)


@receiver(post_save, sender=Issue, dispatch_uid="workflow.auto_cancel_pending_requests")
def auto_cancel_pending_requests(sender, instance, **kwargs):
    """Cancel PENDING requests made moot once the work item has moved elsewhere.

    A request whose ``to_state`` equals the item's *current* state is being
    fulfilled — ``apply_approved_request`` saves the issue (firing this receiver)
    while the request is still PENDING, then marks it APPROVED — so it is
    ``.exclude``-d here and never cancelled. Only PENDING requests whose target
    differs from where the card actually landed (the card moved another way) are
    stale and get CANCELLED.
    """
    ApprovalRequest.objects.filter(
        work_item_id=instance.id, status=ApprovalRequest.PENDING
    ).exclude(to_state_id=instance.state_id).update(status=ApprovalRequest.CANCELLED)
