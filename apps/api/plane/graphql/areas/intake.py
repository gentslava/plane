# OVERLAY: mobile-graphql — resolvers for the "intake" (triage) domain.
#
# Intake = IntakeIssue: a work item submitted into a project's intake/triage queue
# before it is accepted into the regular board. The native app's intake screens read
# the queue, a single intake item, its activity/comments/attachments/stats, and write
# create/update/delete + comments/reactions/replies/attachments + status (accept /
# reject / snooze / mark-duplicate).
#
# GROUND TRUTH — replicates the REST behaviour in
#   plane/app/views/intake/base.py (IntakeIssueViewSet)
# and reuses the shared Issue infrastructure (IssueComment, IssueActivity, FileAsset
# with ISSUE_ATTACHMENT, CommentReaction) exactly as the web/REST intake screens do —
# an intake item's comments/activities/attachments are those of its underlying Issue.
#
# Conventions (see plane.graphql.resolvers / plane.graphql.mutations.work_items):
#   * Self-contained module: own QueryType / MutationType / ObjectType("<SDL type>").
#   * Auth via _user(info); empty = [] for lists, _page([], cursor) for paginators,
#     None for nullable singletons.
#   * The global smart fallback resolves camelCase->snake_case and FK->"<field>_id";
#     only computed / M2M-id / nested-FK-object fields are bound explicitly.
#   * Non-null SDL contract is never violated (real data or a safe non-null default).
import json
import uuid

from ariadne import MutationType, ObjectType, QueryType

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from plane.db.models import (
    CommentReaction,
    FileAsset,
    Intake,
    IntakeIssue,
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    IssueLink,
    IssueRelation,
    Project,
    State,
    StateGroup,
)
from plane.db.models.intake import IntakeIssueStatus, SourceType
from plane.graphql.resolvers import _member_project, _page, _user

query = QueryType()
mutation = MutationType()

intake_work_item_type = ObjectType("IntakeWorkItemType")
intake_activity_type = ObjectType("IntakeWorkItemPropertyActivityType")
intake_comment_type = ObjectType("IntakeWorkItemCommentActivityType")


# --- helpers -------------------------------------------------------------------


def _intake(slug, project_id):
    """The project's intake container (one per project; prefer the default)."""
    return (
        Intake.objects.filter(workspace__slug=slug, project_id=project_id)
        .order_by("-is_default", "created_at")
        .first()
    )


def _intake_issue_qs(slug, project_id):
    return IntakeIssue.objects.filter(
        workspace__slug=slug, project_id=project_id
    ).select_related("issue", "issue__project", "project", "workspace")


def _intake_issue(slug, project_id, intake_work_item):
    """An IntakeIssue keyed by the GraphQL `intakeWorkItem` argument.

    The app passes the underlying work-item (issue) id for intake screens, mirroring
    the REST `partial_update`/`retrieve` views which look intake items up by
    issue_id. Fall back to the IntakeIssue row id so either identifier resolves.
    """
    qs = _intake_issue_qs(slug, project_id)
    return (
        qs.filter(issue_id=intake_work_item).first()
        or qs.filter(id=intake_work_item).first()
    )


def _intake_issue_by_work_item(slug, project_id, work_item):
    return _intake_issue_qs(slug, project_id).filter(issue_id=work_item).first()


def _apply_intake_filters(qs, filters):
    """Honour the intake filter keys the app sends (status + the common issue facets).

    Mirrors the REST list view: a missing/empty status defaults to PENDING (-2), and
    `null` entries are dropped, so the default queue shows pending items only.
    """
    if not isinstance(filters, dict):
        filters = {}

    raw_status = filters.get("status")
    if raw_status is None:
        statuses = [IntakeIssueStatus.PENDING]
    else:
        values = raw_status if isinstance(raw_status, (list, tuple)) else [raw_status]
        statuses = []
        for value in values:
            if value in (None, "null"):
                continue
            try:
                statuses.append(int(value))
            except (TypeError, ValueError):
                continue
    if statuses:
        qs = qs.filter(status__in=statuses)

    if filters.get("priority"):
        qs = qs.filter(issue__priority__in=filters["priority"])
    if filters.get("state"):
        qs = qs.filter(issue__state_id__in=filters["state"])
    if filters.get("labels"):
        qs = qs.filter(issue__labels__id__in=filters["labels"])
    if filters.get("assignees"):
        qs = qs.filter(issue__assignees__id__in=filters["assignees"])
    if filters.get("created_by"):
        qs = qs.filter(created_by_id__in=filters["created_by"])
    return qs.distinct()


def _intake_order_by(order_by):
    # The app orders by the underlying issue's fields (REST default -issue__created_at).
    if not isinstance(order_by, str) or not order_by:
        return "-created_at"
    field = order_by
    desc = field.startswith("-")
    bare = field[1:] if desc else field
    if bare in ("created_at", "updated_at", "priority", "sort_order", "name", "sequence_id"):
        bare = f"issue__{bare}"
    return f"-{bare}" if desc else bare


# --- Query: queue / counts / single item ---------------------------------------


@query.field("intakeCount")
def resolve_intake_count(_, info, slug, project, filters=None):
    user = _user(info)
    if user is None:
        return {"total_intake_work_items": 0}
    qs = _apply_intake_filters(_intake_issue_qs(slug, project), filters)
    return {"total_intake_work_items": qs.count()}


@query.field("intakeWorkItems")
def resolve_intake_work_items(_, info, slug, project, filters=None, orderBy="-created_at", cursor=None):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = _apply_intake_filters(_intake_issue_qs(slug, project), filters)
    rows = list(qs.order_by(_intake_order_by(orderBy))[:500])
    return _page(rows, cursor)


@query.field("intakeWorkItem")
def resolve_intake_work_item(_, info, slug, project, intakeWorkItem):
    user = _user(info)
    if user is None:
        return None
    return _intake_issue(slug, project, intakeWorkItem)


@query.field("intakeWorkItemByWorkItem")
def resolve_intake_work_item_by_work_item(_, info, slug, project, workItem):
    user = _user(info)
    if user is None:
        return None
    return _intake_issue_by_work_item(slug, project, workItem)


@query.field("intakeSearch")
def resolve_intake_search(_, info, slug, project, search=None, cursor=None):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = _intake_issue_qs(slug, project)
    term = (search or "").strip()
    if term:
        qs = qs.filter(issue__name__icontains=term)
    rows = list(qs.order_by("-created_at")[:500])
    return _page(rows, cursor)


@query.field("intakeStats")
def resolve_intake_stats(_, info, slug, project, intakeWorkItem):
    # Aggregates over the underlying work item — same shape/source as issueStats.
    item = _intake_issue(slug, project, intakeWorkItem)
    if item is None:
        return {"attachments": 0, "relations": 0, "sub_work_items": 0, "links": 0}
    issue_id = item.issue_id
    from django.db.models import Q

    return {
        "attachments": FileAsset.objects.filter(
            issue_id=issue_id,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            is_deleted=False,
        ).count(),
        "relations": IssueRelation.objects.filter(
            Q(issue_id=issue_id) | Q(related_issue_id=issue_id)
        ).count(),
        "sub_work_items": Issue.objects.filter(parent_id=issue_id).count(),
        "links": IssueLink.objects.filter(issue_id=issue_id).count(),
    }


# --- Query: activity / comments / reactions / attachments ----------------------


@query.field("intakeWorkItemActivities")
def resolve_intake_work_item_activities(_, info, slug, project, intakeWorkItem):
    user = _user(info)
    if user is None:
        return []
    item = _intake_issue(slug, project, intakeWorkItem)
    if item is None:
        return []
    return list(
        IssueActivity.objects.filter(issue_id=item.issue_id, project_id=project)
        .select_related("actor")
        .order_by("created_at")
    )


@query.field("intakeWorkItemComments")
def resolve_intake_work_item_comments(_, info, slug, project, intakeWorkItem):
    user = _user(info)
    if user is None:
        return []
    item = _intake_issue(slug, project, intakeWorkItem)
    if item is None:
        return []
    return list(
        IssueComment.objects.filter(issue_id=item.issue_id, project_id=project)
        .select_related("actor")
        .order_by("created_at")
    )


@query.field("intakeWorkItemCommentReactions")
def resolve_intake_work_item_comment_reactions(_, info, slug, project, intakeWorkItem, comment):
    user = _user(info)
    if user is None:
        return []
    item = _intake_issue(slug, project, intakeWorkItem)
    if item is None:
        return []
    grouped = {}
    for reaction, actor_id in (
        CommentReaction.objects.filter(comment_id=comment, project_id=project)
        .values_list("reaction", "actor_id")
    ):
        grouped.setdefault(reaction, []).append(str(actor_id))
    return [{"reaction": r, "user_ids": ids} for r, ids in grouped.items()]


@query.field("intakeWorkItemAttachments")
def resolve_intake_work_item_attachments(_, info, slug, project, intakeWorkItem):
    user = _user(info)
    if user is None:
        return []
    item = _intake_issue(slug, project, intakeWorkItem)
    if item is None:
        return []
    return list(
        FileAsset.objects.filter(
            issue_id=item.issue_id,
            project_id=project,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            is_deleted=False,
        ).order_by("-created_at")
    )


@query.field("intakeWorkItemAttachment")
def resolve_intake_work_item_attachment(_, info, slug, project, intakeWorkItem, attachment):
    user = _user(info)
    if user is None:
        return None
    item = _intake_issue(slug, project, intakeWorkItem)
    if item is None:
        return None
    return FileAsset.objects.filter(
        id=attachment,
        issue_id=item.issue_id,
        project_id=project,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
    ).first()


# --- IntakeWorkItemType computed / nested fields --------------------------------


@intake_work_item_type.field("issue")
def resolve_intake_issue_object(intake_issue, info):
    # SDL: issue: IssuesType! — the app reads sub-fields, so return the related Issue
    # object (select_related already loaded it) and let IssuesType resolvers serialise.
    return intake_issue.issue


@intake_work_item_type.field("projectIdentifier")
def resolve_intake_project_identifier(intake_issue, info):
    project = intake_issue.project
    return project.identifier if project else None


@intake_work_item_type.field("intakeId")
def resolve_intake_id(intake_issue, info):
    return str(intake_issue.intake_id) if intake_issue.intake_id else None


@intake_work_item_type.field("archivedAt")
def resolve_intake_archived_at(intake_issue, info):
    # Surface the underlying work item's archive timestamp (IntakeIssue has none).
    issue = intake_issue.issue
    return issue.archived_at if issue else None


# --- IntakeWorkItemPropertyActivityType (IssueActivity) -------------------------


@intake_activity_type.field("actorDetails")
def resolve_activity_actor_details(activity, info):
    return activity.actor


@intake_activity_type.field("attachments")
def resolve_activity_attachments(activity, info):
    return [str(a) for a in (activity.attachments or [])]


@intake_activity_type.field("source")
def resolve_activity_source(activity, info):
    return None


@intake_activity_type.field("sourceEmail")
def resolve_activity_source_email(activity, info):
    return None


# --- IntakeWorkItemCommentActivityType (IssueComment) --------------------------


@intake_comment_type.field("actorDetails")
def resolve_comment_actor_details(comment, info):
    return comment.actor


@intake_comment_type.field("attachments")
def resolve_comment_attachments(comment, info):
    return [str(a) for a in (comment.attachments or [])]


@intake_comment_type.field("parent")
def resolve_comment_parent(comment, info):
    return str(comment.parent_id) if comment.parent_id else None


# --- Mutations: create / update / delete intake work item -----------------------


def _triage_state(slug, project):
    state = State.triage_objects.filter(project=project, workspace__slug=slug).first()
    if state is None:
        state = State.objects.create(
            name="Triage",
            group=StateGroup.TRIAGE.value,
            project=project,
            workspace=project.workspace,
            color="#4E5355",
            sequence=65000,
            default=False,
        )
    return state


def _set_assignees(issue, user_ids, user):
    IssueAssignee.objects.filter(issue=issue).delete()
    IssueAssignee.objects.bulk_create(
        [
            IssueAssignee(
                issue=issue,
                assignee_id=uid,
                project=issue.project,
                workspace=issue.workspace,
                created_by=user,
                updated_by=user,
            )
            for uid in user_ids
        ],
        ignore_conflicts=True,
    )


def _set_labels(issue, label_ids, user):
    IssueLabel.objects.filter(issue=issue).delete()
    IssueLabel.objects.bulk_create(
        [
            IssueLabel(
                issue=issue,
                label_id=lid,
                project=issue.project,
                workspace=issue.workspace,
                created_by=user,
                updated_by=user,
            )
            for lid in label_ids
        ],
        ignore_conflicts=True,
    )


def _log_activity(activity_type, requested_data, user, issue_id, project_id, intake_id, current_instance=None, notification=False):
    try:
        from plane.bgtasks.issue_activities_task import issue_activity

        issue_activity.delay(
            type=activity_type,
            requested_data=json.dumps(requested_data, cls=DjangoJSONEncoder),
            actor_id=str(user.id),
            issue_id=str(issue_id),
            project_id=str(project_id),
            current_instance=(
                json.dumps(current_instance, cls=DjangoJSONEncoder)
                if current_instance is not None
                else None
            ),
            epoch=int(timezone.now().timestamp()),
            notification=notification,
            origin=None,
            intake=str(intake_id),
        )
    except Exception:
        pass


@mutation.field("createIntakeWorkItem")
def resolve_create_intake_work_item(_, info, slug, project, workItemInput):
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    intake = _intake(slug, project)
    if intake is None:
        return None

    data = workItemInput or {}
    name = (data.get("name") or "").strip()
    if not name:
        return None
    priority = data.get("priority") or "none"
    if priority not in ("low", "medium", "high", "urgent", "none"):
        priority = "none"

    # Intake items land in the project's TRIAGE state.
    triage = _triage_state(slug, p)

    state = None
    state_id = data.get("state")
    if state_id:
        state = State.objects.filter(project=p, id=state_id).first()

    issue = Issue(
        name=name,
        project=p,
        workspace=p.workspace,
        priority=priority,
        description_html=data.get("descriptionHtml") or "<p></p>",
        target_date=(data.get("targetDate") or None),
        state=state or triage,
        created_by=user,
        updated_by=user,
    )
    issue.save()

    assignee_ids = data.get("assignees") or []
    if assignee_ids:
        _set_assignees(issue, assignee_ids, user)
    label_ids = data.get("labels") or []
    if label_ids:
        _set_labels(issue, label_ids, user)

    intake_issue = IntakeIssue.objects.create(
        intake_id=intake.id,
        project=p,
        workspace=p.workspace,
        issue=issue,
        source=SourceType.IN_APP,
        created_by=user,
        updated_by=user,
    )

    _log_activity(
        "issue.activity.created",
        {"name": name, "priority": priority},
        user,
        issue.id,
        p.id,
        intake_issue.id,
        notification=True,
    )

    # Reload with relations so the IssuesType resolvers serialise cleanly.
    return _intake_issue_qs(slug, project).filter(id=intake_issue.id).first() or intake_issue


@mutation.field("updateIntakeWorkItem")
def resolve_update_intake_work_item(_, info, slug, project, intakeWorkItem, workItemInput=None):
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return None
    issue = intake_issue.issue

    data = workItemInput or {}
    update_fields = ["updated_by", "updated_at"]
    if data.get("name") is not None:
        issue.name = data["name"]
        update_fields.append("name")
    if data.get("descriptionHtml") is not None:
        issue.description_html = data["descriptionHtml"]
        update_fields.append("description_html")
    if data.get("priority") is not None and data["priority"] in (
        "low", "medium", "high", "urgent", "none"
    ):
        issue.priority = data["priority"]
        update_fields.append("priority")
    if "targetDate" in data:
        issue.target_date = data.get("targetDate") or None
        update_fields.append("target_date")
    if data.get("state"):
        new_state = State.objects.filter(project=p, id=data["state"]).first()
        if new_state is not None:
            issue.state = new_state
            update_fields.append("state")
    issue.updated_by = user
    issue.save(update_fields=list(set(update_fields)))

    if data.get("assignees") is not None:
        _set_assignees(issue, data["assignees"], user)
    if data.get("labels") is not None:
        _set_labels(issue, data["labels"], user)

    _log_activity(
        "issue.activity.updated",
        {k: v for k, v in data.items()},
        user,
        issue.id,
        p.id,
        intake_issue.id,
        notification=True,
    )

    return _intake_issue_qs(slug, project).filter(id=intake_issue.id).first() or intake_issue


@mutation.field("deleteIntakeWorkItem")
def resolve_delete_intake_work_item(_, info, slug, project, intakeWorkItem):
    user = _user(info)
    if user is None:
        return False
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return False
    # REST deletes the underlying issue unless the item was accepted (status 1).
    if intake_issue.status in (
        IntakeIssueStatus.PENDING,
        IntakeIssueStatus.REJECTED,
        IntakeIssueStatus.SNOOZED,
        IntakeIssueStatus.DUPLICATE,
    ):
        Issue.objects.filter(id=intake_issue.issue_id).delete()
    intake_issue.delete()
    return True


@mutation.field("updateIntakeWorkItemStatus")
def resolve_update_intake_work_item_status(_, info, slug, project, intakeWorkItem, intakeWorkItemStatusInput):
    user = _user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return False

    data = intakeWorkItemStatusInput or {}
    current_instance = {
        "status": intake_issue.status,
        "snoozed_till": intake_issue.snoozed_till.isoformat() if intake_issue.snoozed_till else None,
        "duplicate_to": str(intake_issue.duplicate_to_id) if intake_issue.duplicate_to_id else None,
    }

    update_fields = ["updated_by", "updated_at"]
    new_status = data.get("status")
    if new_status is not None:
        intake_issue.status = int(new_status)
        update_fields.append("status")
    if "snoozed_till" in data or "snoozedTill" in data:
        intake_issue.snoozed_till = data.get("snoozed_till") or data.get("snoozedTill")
        update_fields.append("snoozed_till")
    if "duplicate_to" in data or "duplicateTo" in data:
        intake_issue.duplicate_to_id = data.get("duplicate_to") or data.get("duplicateTo")
        update_fields.append("duplicate_to")
    intake_issue.updated_by = user
    intake_issue.save(update_fields=list(set(update_fields)))

    # Accepting (status 1): move the issue out of TRIAGE into the default state.
    if new_status is not None and int(new_status) == IntakeIssueStatus.ACCEPTED:
        issue = intake_issue.issue
        if issue and issue.state and issue.state.group == StateGroup.TRIAGE.value:
            default_state = State.objects.filter(
                workspace=intake_issue.workspace, project=intake_issue.project, default=True
            ).first()
            if default_state:
                issue.state = default_state
                issue.updated_by = user
                issue.save(update_fields=["state", "updated_by", "updated_at"])

    _log_activity(
        "intake.activity.created",
        current_instance,
        user,
        intake_issue.issue_id,
        p.id,
        intake_issue.id,
        current_instance=current_instance,
        notification=False,
    )
    return True


# --- Mutations: comments / reactions / replies ----------------------------------


def _create_comment(user, project, issue_id, comment_html, parent=None):
    return IssueComment.objects.create(
        issue_id=issue_id,
        project=project,
        workspace=project.workspace,
        comment_html=comment_html or "<p></p>",
        actor=user,
        parent=parent,
        created_by=user,
        updated_by=user,
    )


@mutation.field("addIntakeWorkItemComment")
def resolve_add_intake_comment(_, info, slug, project, intakeWorkItem, commentInput):
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return None
    comment_html = (commentInput or {}).get("commentHtml")
    return _create_comment(user, p, intake_issue.issue_id, comment_html)


@mutation.field("deleteIntakeWorkItemComment")
def resolve_delete_intake_comment(_, info, slug, project, intakeWorkItem, comment):
    user = _user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return False
    IssueComment.objects.filter(
        project=p, issue_id=intake_issue.issue_id, id=comment
    ).delete()
    return True


@mutation.field("addIntakeWorkItemCommentReply")
def resolve_add_intake_comment_reply(_, info, slug, project, intakeWorkItem, comment, commentInput):
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return None
    parent = IssueComment.objects.filter(
        project=p, issue_id=intake_issue.issue_id, id=comment
    ).first()
    if parent is None:
        return None
    comment_html = (commentInput or {}).get("commentHtml")
    return _create_comment(user, p, intake_issue.issue_id, comment_html, parent=parent)


@mutation.field("deleteIntakeWorkItemCommentReply")
def resolve_delete_intake_comment_reply(_, info, slug, project, intakeWorkItem, comment, reply):
    user = _user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return False
    IssueComment.objects.filter(
        project=p, issue_id=intake_issue.issue_id, id=reply, parent_id=comment
    ).delete()
    return True


def _comment_reaction_payload(comment_id, reaction):
    user_ids = list(
        CommentReaction.objects.filter(comment_id=comment_id, reaction=reaction).values_list(
            "actor_id", flat=True
        )
    )
    return {"reaction": reaction, "user_ids": [str(uid) for uid in user_ids]}


@mutation.field("addIntakeWorkItemCommentReaction")
def resolve_add_intake_comment_reaction(_, info, slug, project, intakeWorkItem, comment, reactionInput):
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    comment_obj = IssueComment.objects.filter(project=p, id=comment).first()
    if comment_obj is None:
        return None
    reaction = (reactionInput or {}).get("reaction")
    if not reaction:
        return None
    CommentReaction.objects.get_or_create(
        comment=comment_obj,
        actor=user,
        reaction=reaction,
        project=p,
        defaults={"workspace": p.workspace, "created_by": user, "updated_by": user},
    )
    return _comment_reaction_payload(comment_obj.id, reaction)


@mutation.field("removeIntakeWorkItemCommentReaction")
def resolve_remove_intake_comment_reaction(_, info, slug, project, intakeWorkItem, comment, reactionInput):
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    comment_obj = IssueComment.objects.filter(project=p, id=comment).first()
    if comment_obj is None:
        return None
    reaction = (reactionInput or {}).get("reaction")
    if not reaction:
        return None
    CommentReaction.objects.filter(comment=comment_obj, actor=user, reaction=reaction).delete()
    return _comment_reaction_payload(comment_obj.id, reaction)


# --- Mutations: attachments -----------------------------------------------------


@mutation.field("createIntakeWorkItemAttachment")
def resolve_create_intake_attachment(_, info, slug, project, intakeWorkItem, attachmentInput):
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return None

    data = attachmentInput or {}
    name = data.get("name") or "unnamed"
    file_type = data.get("type") or "application/octet-stream"
    size_limit = int(data.get("size") or 0)

    try:
        from plane.utils.path_validator import sanitize_filename

        safe_name = sanitize_filename(name) or "unnamed"
    except Exception:
        safe_name = name

    asset_key = f"{p.workspace.id}/{uuid.uuid4().hex}-{safe_name}"
    asset = FileAsset.objects.create(
        attributes={"name": safe_name, "type": file_type, "size": size_limit},
        asset=asset_key,
        size=size_limit,
        workspace=p.workspace,
        project=p,
        issue_id=intake_issue.issue_id,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        entity_identifier=str(intake_issue.issue_id),
        created_by=user,
    )

    upload_data = {}
    try:
        from plane.settings.storage import S3Storage

        storage = S3Storage(request=info.context)
        upload_data = storage.generate_presigned_post(
            object_name=asset_key, file_type=file_type, file_size=size_limit
        )
    except Exception:
        upload_data = {}

    return {
        "upload_data": upload_data,
        "attachment_id": str(asset.id),
        "asset_url": asset.asset_url,
    }


@mutation.field("updateIntakeWorkItemAttachment")
def resolve_update_intake_attachment(_, info, slug, project, intakeWorkItem, attachment, attachmentInput):
    user = _user(info)
    if user is None:
        return None
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return None
    asset = FileAsset.objects.filter(
        workspace__slug=slug, project_id=project, issue_id=intake_issue.issue_id, id=attachment
    ).first()
    if asset is None:
        return None
    asset.is_uploaded = True
    attributes = (attachmentInput or {}).get("attributes")
    if attributes is not None:
        asset.attributes = attributes
    asset.updated_by = user
    asset.save(update_fields=["is_uploaded", "attributes", "updated_by", "updated_at"])
    return asset


@mutation.field("deleteIntakeWorkItemAttachment")
def resolve_delete_intake_attachment(_, info, slug, project, intakeWorkItem, attachment):
    user = _user(info)
    if user is None:
        return False
    intake_issue = _intake_issue(slug, project, intakeWorkItem)
    if intake_issue is None:
        return False
    asset = FileAsset.objects.filter(
        workspace__slug=slug, project_id=project, issue_id=intake_issue.issue_id, id=attachment
    ).first()
    if asset is None:
        return False
    asset.is_deleted = True
    asset.deleted_at = timezone.now()
    asset.save(update_fields=["is_deleted", "deleted_at"])
    return True


BINDABLES = [query, mutation, intake_work_item_type, intake_activity_type, intake_comment_type]
