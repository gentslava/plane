# OVERLAY: mobile-graphql — resolvers for the "epics" area.
#
# An Epic is an Issue whose work-item-type has is_epic=True (see
# plane/app/views/issue/iw_epic.py: Issue.issue_objects.filter(type__is_epic=True)).
# Every read/write here therefore reuses the ordinary Issue machinery (Issue,
# IssueLink, IssueComment, IssueRelation, IssueActivity, FileAsset, CommentReaction)
# scoped to the epic-typed issue, mirroring the REST IwEpicViewSet / IssueViewSet.
#
# Self-contained module: own QueryType/MutationType plus ObjectType bindings only for
# computed / aggregate / M2M / FK-as-object fields. Plain scalar and FK-as-id fields
# resolve through the global smart fallback (camelCase->snake_case, FK-><field>_id).
import json
import uuid

from ariadne import MutationType, ObjectType, QueryType

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count, Q
from django.utils import timezone

from plane.bgtasks.issue_activities_task import issue_activity
from plane.db.models import (
    CommentReaction,
    FileAsset,
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    IssueLink,
    IssueRelation,
    IssueType,
    PageLog,
    Project,
    State,
    Workspace,
)
from plane.graphql.resolvers import _member_project, _page, _user

query = QueryType()
mutation = MutationType()

# ObjectType bindings for SDL types that need computed / aggregate / M2M / FK-object
# fields. (IssuesType is already bound globally in resolvers.py — do NOT re-bind it,
# epicWorkItems just returns Issue instances and reuses that binding.)
epic_type = ObjectType("EpicType")
epic_link_type = ObjectType("EpicLinkType")
epic_activity_type = ObjectType("EpicPropertyActivityType")
epic_comment_type = ObjectType("EpicCommentActivityType")

_DONE_GROUPS = ["completed", "cancelled"]

# Forward/reverse mapping for issue relations (mirrors IssueRelationChoices pairs in
# plane/db/models/issue.py).
_RELATION_REVERSE = {
    "blocked_by": "blocking",
    "blocking": "blocked_by",
    "relates_to": "relates_to",
    "duplicate": "duplicate",
    "start_before": "start_after",
    "start_after": "start_before",
    "finish_before": "finish_after",
    "finish_after": "finish_before",
    "implemented_by": "implements",
    "implements": "implemented_by",
}

# EpicRelationType / WorkItemRelationWorkItemType: the SDL groups every relation by
# its (camelCase) bucket. Map the stored relation_type to the bucket as seen from the
# epic, mirroring Plane's issue-relation view semantics.
_RELATION_BUCKETS = {
    "blocking": "blocking",
    "blocked_by": "blockedBy",
    "duplicate": "duplicate",
    "relates_to": "relatesTo",
    "start_after": "startAfter",
    "start_before": "startBefore",
    "finish_after": "finishAfter",
    "finish_before": "finishBefore",
    "implements": "implements",
    "implemented_by": "implementedBy",
}


# --- helpers ------------------------------------------------------------------------


def _epic(slug, project_id, epic_id):
    """The epic (an Issue with type.is_epic=True) scoped to slug+project, or None."""
    return Issue.issue_objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        type__is_epic=True,
        id=epic_id,
    ).first()


def _epic_type_for_workspace(slug):
    workspace = Workspace.objects.filter(slug=slug).first()
    if workspace is None:
        return None
    return IssueType.objects.filter(workspace=workspace, is_epic=True).first()


def _to_date(value):
    if not value:
        return None
    if isinstance(value, str):
        return value[:10]
    return value


def _set_epic_assignees(issue, user_ids, user):
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


def _set_epic_labels(issue, label_ids, user):
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


def _log_epic_activity(issue, user, requested, current_instance, activity_type):
    try:
        issue_activity.delay(
            type=activity_type,
            requested_data=json.dumps(requested, cls=DjangoJSONEncoder),
            actor_id=str(user.id),
            issue_id=str(issue.id),
            project_id=str(issue.project_id),
            current_instance=(
                json.dumps(current_instance, cls=DjangoJSONEncoder)
                if current_instance is not None
                else None
            ),
            epoch=int(timezone.now().timestamp()),
            notification=False,
            origin=None,
        )
    except Exception:
        pass


def _epic_snapshot(issue):
    return {
        "name": issue.name,
        "description_html": issue.description_html,
        "priority": issue.priority,
        "state": str(issue.state_id) if issue.state_id else None,
        "start_date": issue.start_date.isoformat() if issue.start_date else None,
        "target_date": issue.target_date.isoformat() if issue.target_date else None,
        "assignees": [str(x) for x in issue.assignees.values_list("id", flat=True)],
        "labels": [str(x) for x in issue.labels.values_list("id", flat=True)],
    }


_ACTIVITY_KEY = {
    "descriptionHtml": "description_html",
    "startDate": "start_date",
    "targetDate": "target_date",
}


def _activity_requested(data):
    out = {}
    for key, value in data.items():
        mapped = _ACTIVITY_KEY.get(key, key)
        if mapped in ("start_date", "target_date"):
            value = _to_date(value)
        out[mapped] = value
    return out


def _empty_relations():
    return {
        "blocking": [],
        "blockedBy": [],
        "duplicate": [],
        "relatesTo": [],
        "startAfter": [],
        "startBefore": [],
        "finishAfter": [],
        "finishBefore": [],
        "implements": [],
        "implementedBy": [],
    }


def _relation_work_item(issue):
    # WorkItemRelationWorkItemType: id/name/priority/sequenceId/project/
    # projectIdentifier/state/assignees/isEpic/analytics.
    is_epic = bool(issue.type_id and issue.type and issue.type.is_epic)
    return {
        "id": str(issue.id),
        "name": issue.name,
        "priority": issue.priority or "none",
        "sequence_id": issue.sequence_id,
        "project": str(issue.project_id) if issue.project_id else None,
        "project_identifier": issue.project.identifier if issue.project_id else "",
        "state": str(issue.state_id) if issue.state_id else None,
        "assignees": [str(pk) for pk in issue.assignees.values_list("id", flat=True)],
        "is_epic": is_epic,
        "analytics": _zero_analytics(),
    }


def _zero_analytics():
    return {"backlog": 0, "unstarted": 0, "started": 0, "completed": 0, "cancelled": 0}


def _epic_analytics(epic_id):
    """Aggregate analytics for an epic's child work items (state-group counts).
    Mirrors IwEpicViewSet.analytics' state_group breakdown."""
    children = Issue.issue_objects.filter(parent_id=epic_id)
    counts = dict(
        children.values_list("state__group")
        .annotate(count=Count("id"))
        .values_list("state__group", "count")
    )
    return {
        "backlog": counts.get("backlog", 0),
        "unstarted": counts.get("unstarted", 0),
        "started": counts.get("started", 0),
        "completed": counts.get("completed", 0),
        "cancelled": counts.get("cancelled", 0),
    }


# --- Query: user property / count / list / detail -----------------------------------


@query.field("epicUserProperty")
def resolve_epic_user_property(_, info, slug, project):
    # Per-user view config for a project's epics. There is no stored-prefs model for
    # the mobile gateway (same as issueUserProperties in resolvers.py): return sane
    # non-null defaults. EpicUserPropertyType fields are all nullable except id.
    user = _user(info)
    workspace_id = Workspace.objects.filter(slug=slug).values_list("id", flat=True).first()
    return {
        "id": str(project),
        "filters": {},
        "display_filters": {"layout": "list", "group_by": "state", "order_by": "-created_at"},
        "display_properties": {},
        "workspace": str(workspace_id) if workspace_id else None,
        "project": str(project),
        "user": str(user.id) if user is not None else None,
        "created_by": str(user.id) if user is not None else None,
        "updated_by": str(user.id) if user is not None else None,
        "created_at": None,
        "updated_at": None,
    }


@query.field("epicCount")
def resolve_epic_count(_, info, slug, project):
    user = _user(info)
    if user is None:
        return {"total_epics": 0}
    total = Issue.issue_objects.filter(
        workspace__slug=slug, project_id=project, type__is_epic=True
    ).count()
    return {"total_epics": total}


@query.field("epics")
def resolve_epics(_, info, slug, project, filters=None, orderBy="-created_at", cursor=None):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = Issue.issue_objects.filter(
        workspace__slug=slug, project_id=project, type__is_epic=True
    )
    if isinstance(filters, dict):
        if filters.get("state"):
            qs = qs.filter(state_id__in=filters["state"])
        if filters.get("priority"):
            qs = qs.filter(priority__in=filters["priority"])
        if filters.get("labels"):
            qs = qs.filter(labels__id__in=filters["labels"])
        if filters.get("assignees"):
            qs = qs.filter(assignees__id__in=filters["assignees"])
        if filters.get("created_by"):
            qs = qs.filter(created_by_id__in=filters["created_by"])
        qs = qs.distinct()
    order = orderBy if isinstance(orderBy, str) and orderBy else "-created_at"
    rows = list(qs.order_by(order)[:500])
    return _page(rows, cursor)


@query.field("epic")
def resolve_epic(_, info, slug, project, epic):
    user = _user(info)
    if user is None:
        return None
    return _epic(slug, project, epic)


# --- EpicType computed fields (Issue instance backing) ------------------------------


@epic_type.field("labels")
def resolve_epic_labels(issue, info):
    return [str(pk) for pk in issue.labels.values_list("id", flat=True)]


@epic_type.field("assignees")
def resolve_epic_assignees(issue, info):
    return [str(pk) for pk in issue.assignees.values_list("id", flat=True)]


@epic_type.field("projectIdentifier")
def resolve_epic_project_identifier(issue, info):
    return issue.project.identifier if issue.project_id else None


@epic_type.field("analytics")
def resolve_epic_type_analytics(issue, info):
    return _epic_analytics(issue.id)


# --- Query: links -------------------------------------------------------------------


@query.field("epicLinks")
def resolve_epic_links(_, info, slug, project, epic):
    user = _user(info)
    if user is None:
        return []
    return list(IssueLink.objects.filter(project_id=project, issue_id=epic).order_by("created_at"))


@query.field("epicLink")
def resolve_epic_link(_, info, slug, project, epic, link):
    user = _user(info)
    if user is None:
        return None
    return IssueLink.objects.filter(project_id=project, issue_id=epic, id=link).first()


@epic_link_type.field("epic")
def resolve_epic_link_epic(link, info):
    # SDL field is `epic: ID!`; the backing IssueLink stores it as issue_id.
    return str(link.issue_id)


# --- Query: attachments -------------------------------------------------------------


@query.field("epicAttachments")
def resolve_epic_attachments(_, info, slug, project, epic):
    user = _user(info)
    if user is None:
        return []
    return list(
        FileAsset.objects.filter(
            workspace__slug=slug,
            project_id=project,
            issue_id=epic,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            is_deleted=False,
        ).order_by("created_at")
    )


@query.field("epicAttachment")
def resolve_epic_attachment(_, info, slug, project, epic, attachment):
    user = _user(info)
    if user is None:
        return None
    return FileAsset.objects.filter(
        workspace__slug=slug, project_id=project, issue_id=epic, id=attachment, is_deleted=False
    ).first()


# --- Query: child work items --------------------------------------------------------


@query.field("epicWorkItems")
def resolve_epic_work_items(_, info, slug, project, epic, cursor=None):
    # Child work items of the epic (Issue.parent == epic). Returns IssuesType, which
    # is bound globally in resolvers.py, so plain Issue instances are enough.
    user = _user(info)
    if user is None:
        return _page([], cursor)
    rows = list(
        Issue.issue_objects.filter(project_id=project, parent_id=epic)
        .select_related("project")
        .order_by("sequence_id")[:500]
    )
    return _page(rows, cursor)


# --- Query: relations ---------------------------------------------------------------


@query.field("epicRelation")
def resolve_epic_relation(_, info, slug, project, epic):
    user = _user(info)
    buckets = _empty_relations()
    if user is None:
        return buckets
    # Relations stored from the epic's side: relation_type as-is.
    for rel in (
        IssueRelation.objects.filter(issue_id=epic)
        .select_related("related_issue", "related_issue__project", "related_issue__type")
    ):
        bucket = _RELATION_BUCKETS.get(rel.relation_type)
        if bucket:
            buckets[bucket].append(_relation_work_item(rel.related_issue))
    # Relations stored from the other side pointing at the epic: use the reverse type.
    for rel in (
        IssueRelation.objects.filter(related_issue_id=epic)
        .select_related("issue", "issue__project", "issue__type")
    ):
        reverse = _RELATION_REVERSE.get(rel.relation_type, rel.relation_type)
        bucket = _RELATION_BUCKETS.get(reverse)
        if bucket:
            buckets[bucket].append(_relation_work_item(rel.issue))
    return buckets


# --- Query: activities --------------------------------------------------------------


@query.field("epicActivities")
def resolve_epic_activities(_, info, slug, project, epic):
    user = _user(info)
    if user is None:
        return []
    return list(
        IssueActivity.objects.filter(issue_id=epic, project_id=project)
        .select_related("actor")
        .order_by("created_at")
    )


@epic_activity_type.field("actorDetails")
def resolve_epic_activity_actor_details(activity, info):
    return activity.actor


# --- Query: comments ----------------------------------------------------------------


@query.field("epicComments")
def resolve_epic_comments(_, info, slug, project, epic):
    user = _user(info)
    if user is None:
        return []
    # Top-level comments (replies are nested under their parent), mirroring the Cloud
    # comment list which keys replies off `parent`.
    return list(
        IssueComment.objects.filter(project_id=project, issue_id=epic, parent__isnull=True)
        .select_related("actor")
        .order_by("created_at")
    )


@epic_comment_type.field("actorDetails")
def resolve_epic_comment_actor_details(comment, info):
    return comment.actor


@epic_comment_type.field("attachments")
def resolve_epic_comment_attachments(comment, info):
    # SDL: attachments [String!]! — IssueComment.attachments is an ArrayField(URLField).
    return [str(a) for a in (comment.attachments or [])]


# --- Query: comment reactions -------------------------------------------------------


@query.field("epicCommentReactions")
def resolve_epic_comment_reactions(_, info, slug, project, epic, comment):
    user = _user(info)
    if user is None:
        return []
    rows = CommentReaction.objects.filter(
        project_id=project, comment_id=comment
    ).values_list("reaction", "actor_id")
    grouped = {}
    for reaction, actor_id in rows:
        grouped.setdefault(reaction, []).append(str(actor_id))
    return [{"reaction": r, "user_ids": ids} for r, ids in grouped.items()]


# --- Query: stats -------------------------------------------------------------------


@query.field("epicStats")
def resolve_epic_stats(_, info, slug, project, epic):
    return {
        "attachments": FileAsset.objects.filter(
            issue_id=epic,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            is_deleted=False,
        ).count(),
        "relations": IssueRelation.objects.filter(
            Q(issue_id=epic) | Q(related_issue_id=epic)
        ).count(),
        "sub_work_items": Issue.issue_objects.filter(parent_id=epic).count(),
        "links": IssueLink.objects.filter(issue_id=epic).count(),
        "pages": _epic_page_ids(epic).count(),
    }


# --- Query: pages -------------------------------------------------------------------


def _epic_page_ids(epic_id):
    # Pages reference a work item via PageLog(entity_name="issue") — see
    # plane/app/views/page/base.py. An epic is just an issue, so its linked pages are
    # the pages whose PageLog points at the epic id.
    return PageLog.objects.filter(entity_name="issue", entity_identifier=epic_id).values_list(
        "page_id", flat=True
    )


def _epic_page_payload(page):
    # EpicPageType { id:String!, name:String!, logoProps:JSON!, isGlobal:Boolean!, access:Int! }
    return {
        "id": str(page.id),
        "name": page.name or "Untitled",
        "logo_props": page.logo_props or {},
        "is_global": bool(page.is_global),
        "access": page.access if page.access is not None else 0,
    }


@query.field("epicPages")
def resolve_epic_pages(_, info, slug, project, epic):
    from plane.db.models import Page

    user = _user(info)
    if user is None:
        return []
    page_ids = list(_epic_page_ids(epic))
    if not page_ids:
        return []
    pages = Page.objects.filter(id__in=page_ids, workspace__slug=slug)
    return [_epic_page_payload(p) for p in pages]


@query.field("epicPage")
def resolve_epic_page(_, info, slug, project, epic, page):
    from plane.db.models import Page

    user = _user(info)
    if user is None:
        return None
    page_obj = Page.objects.filter(id=page, workspace__slug=slug).first()
    if page_obj is None:
        return None
    return _epic_page_payload(page_obj)


@query.field("searchEpicPages")
def resolve_search_epic_pages(_, info, slug, project, epic, search=None, isGlobal=False):
    """Search pages linkable to the epic. isGlobal=True searches all workspace pages
    (the page picker); otherwise searches the epic's already-linked pages."""
    from plane.db.models import Page

    user = _user(info)
    if user is None:
        return []
    if isGlobal:
        qs = Page.objects.filter(workspace__slug=slug, projects__id=project)
    else:
        page_ids = list(_epic_page_ids(epic))
        if not page_ids:
            return []
        qs = Page.objects.filter(id__in=page_ids, workspace__slug=slug)
    term = (search or "").strip()
    if term:
        qs = qs.filter(name__icontains=term)
    return [_epic_page_payload(p) for p in qs.distinct()[:50]]


# --- Mutations: user property -------------------------------------------------------


@mutation.field("updateEpicUserProperties")
def resolve_update_epic_user_properties(_, info, slug, project, userProperties):
    # No stored-prefs model exists for the gateway; echo the submitted values back in
    # the EpicUserPropertyType shape so the client round-trips successfully.
    user = _user(info)
    data = userProperties or {}
    workspace_id = Workspace.objects.filter(slug=slug).values_list("id", flat=True).first()
    return {
        "id": str(project),
        "filters": data.get("filters") or {},
        "display_filters": data.get("displayFilters") or {},
        "display_properties": data.get("displayProperties") or {},
        "workspace": str(workspace_id) if workspace_id else None,
        "project": str(project),
        "user": str(user.id) if user is not None else None,
        "created_by": str(user.id) if user is not None else None,
        "updated_by": str(user.id) if user is not None else None,
        "created_at": None,
        "updated_at": None,
    }


# --- Mutations: epic create / update / delete ---------------------------------------


@mutation.field("createEpic")
def resolve_create_epic(_, info, slug, project, epicInput):
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    epic_type_obj = _epic_type_for_workspace(slug)

    data = epicInput or {}
    state = None
    if data.get("state"):
        state = State.objects.filter(project=p, id=data["state"]).first()

    issue = Issue(
        name=data.get("name") or "",
        project=p,
        workspace=p.workspace,
        priority=data.get("priority") or "none",
        description_html=data.get("descriptionHtml") or "<p></p>",
        start_date=_to_date(data.get("startDate")),
        target_date=_to_date(data.get("targetDate")),
        state=state,
        type=epic_type_obj,
        created_by=user,
        updated_by=user,
    )
    issue.save()

    if data.get("assignees"):
        _set_epic_assignees(issue, data["assignees"], user)
    if data.get("labels"):
        _set_epic_labels(issue, data["labels"], user)

    _log_epic_activity(issue, user, _activity_requested(data), None, "epic.activity.created")
    issue.refresh_from_db()
    return issue


@mutation.field("updateEpic")
def resolve_update_epic(_, info, slug, project, epic, epicInput=None):
    user = _user(info)
    if user is None:
        return None
    issue = _epic(slug, project, epic)
    if issue is None:
        return None
    p = issue.project

    data = epicInput or {}
    before = _epic_snapshot(issue)
    update_fields = ["updated_by", "updated_at"]

    if "name" in data and data.get("name") is not None:
        issue.name = data["name"]
        update_fields.append("name")
    if "descriptionHtml" in data and data.get("descriptionHtml") is not None:
        issue.description_html = data["descriptionHtml"]
        update_fields.append("description_html")
    if "priority" in data and data.get("priority") is not None:
        issue.priority = data["priority"]
        update_fields.append("priority")
    if "startDate" in data:
        issue.start_date = _to_date(data.get("startDate"))
        update_fields.append("start_date")
    if "targetDate" in data:
        issue.target_date = _to_date(data.get("targetDate"))
        update_fields.append("target_date")
    if "state" in data and data.get("state"):
        new_state = State.objects.filter(project=p, id=data["state"]).first()
        if new_state is not None:
            issue.state = new_state
            update_fields.append("state")

    issue.updated_by = user
    issue.save(update_fields=list(set(update_fields)))

    if "assignees" in data and data.get("assignees") is not None:
        _set_epic_assignees(issue, data["assignees"], user)
    if "labels" in data and data.get("labels") is not None:
        _set_epic_labels(issue, data["labels"], user)

    _log_epic_activity(issue, user, _activity_requested(data), before, "epic.activity.updated")
    issue.refresh_from_db()
    return issue


@mutation.field("deleteEpic")
def resolve_delete_epic(_, info, slug, project, epic):
    user = _user(info)
    if user is None:
        return False
    issue = _epic(slug, project, epic)
    if issue is None:
        return False
    issue.delete()
    return True


# --- Mutations: epic links ----------------------------------------------------------


@mutation.field("createEpicLink")
def resolve_create_epic_link(_, info, slug, project, epic, linkInput):
    user = _user(info)
    if user is None:
        return None
    issue = _epic(slug, project, epic)
    if issue is None:
        return None
    data = linkInput or {}
    return IssueLink.objects.create(
        issue=issue,
        project=issue.project,
        workspace=issue.workspace,
        url=data.get("url") or "",
        title=data.get("title") or "",
        created_by=user,
        updated_by=user,
    )


@mutation.field("updateEpicLink")
def resolve_update_epic_link(_, info, slug, project, epic, link, linkInput):
    user = _user(info)
    if user is None:
        return None
    link_obj = IssueLink.objects.filter(project_id=project, issue_id=epic, id=link).first()
    if link_obj is None:
        return None
    data = linkInput or {}
    update_fields = ["updated_by", "updated_at"]
    if data.get("url") is not None:
        link_obj.url = data["url"]
        update_fields.append("url")
    if data.get("title") is not None:
        link_obj.title = data["title"]
        update_fields.append("title")
    link_obj.updated_by = user
    link_obj.save(update_fields=list(set(update_fields)))
    return link_obj


@mutation.field("deleteEpicLink")
def resolve_delete_epic_link(_, info, slug, project, epic, link):
    user = _user(info)
    if user is None:
        return False
    IssueLink.objects.filter(project_id=project, issue_id=epic, id=link).delete()
    return True


# --- Mutations: epic attachments ----------------------------------------------------


@mutation.field("createEpicAttachment")
def resolve_create_epic_attachment(_, info, slug, project, epic, attachmentInput):
    """Create the FileAsset row + presigned POST for direct upload. Returns
    EpicAttachmentPresignedUrlResponseType { uploadData, attachmentId, assetUrl }."""
    user = _user(info)
    if user is None:
        return None
    issue = _epic(slug, project, epic)
    if issue is None:
        return None

    from plane.utils.path_validator import sanitize_filename

    data = attachmentInput or {}
    safe_name = sanitize_filename(data.get("name") or "") or "unnamed"
    file_type = data.get("type") or "application/octet-stream"
    size_limit = int(data.get("size") or 0)
    asset_key = f"{issue.workspace_id}/{uuid.uuid4().hex}-{safe_name}"

    asset = FileAsset.objects.create(
        attributes={"name": safe_name, "type": file_type, "size": size_limit},
        asset=asset_key,
        size=size_limit,
        workspace=issue.workspace,
        project=issue.project,
        issue=issue,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        entity_identifier=str(issue.id),
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


@mutation.field("updateEpicAttachment")
def resolve_update_epic_attachment(_, info, slug, project, epic, attachment, attachmentInput):
    user = _user(info)
    if user is None:
        return None
    asset = FileAsset.objects.filter(
        workspace__slug=slug, project_id=project, issue_id=epic, id=attachment
    ).first()
    if asset is None:
        return None
    asset.is_uploaded = True
    data = attachmentInput or {}
    if data.get("attributes") is not None:
        asset.attributes = data["attributes"]
    asset.updated_by = user
    asset.save(update_fields=["is_uploaded", "attributes", "updated_by", "updated_at"])
    return asset


@mutation.field("deleteEpicAttachment")
def resolve_delete_epic_attachment(_, info, slug, project, epic, attachment):
    user = _user(info)
    if user is None:
        return False
    asset = FileAsset.objects.filter(
        workspace__slug=slug, project_id=project, issue_id=epic, id=attachment
    ).first()
    if asset is None:
        return False
    asset.is_deleted = True
    asset.deleted_at = timezone.now()
    asset.save(update_fields=["is_deleted", "deleted_at"])
    return True


# --- Mutations: epic work items -----------------------------------------------------


@mutation.field("createEpicWorkItem")
def resolve_create_epic_work_item(_, info, slug, project, epic, issueInput):
    """Create a child work item nested under the epic. Returns IssuesType."""
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    parent = _epic(slug, project, epic)
    if p is None or parent is None:
        return None

    data = issueInput or {}
    state = None
    if data.get("state"):
        state = State.objects.filter(project=p, id=data["state"]).first()

    issue = Issue(
        name=data.get("name") or "",
        project=p,
        workspace=p.workspace,
        priority=data.get("priority") or "none",
        description_html=data.get("descriptionHtml") or "<p></p>",
        start_date=_to_date(data.get("startDate")),
        target_date=_to_date(data.get("targetDate")),
        state=state,
        parent=parent,
        estimate_point_id=data.get("estimatePoint"),
        created_by=user,
        updated_by=user,
    )
    issue.save()

    if data.get("assignees"):
        _set_epic_assignees(issue, data["assignees"], user)
    if data.get("labels"):
        _set_epic_labels(issue, data["labels"], user)

    _log_epic_activity(issue, user, _activity_requested(data), None, "issue.activity.created")
    issue.refresh_from_db()
    return issue


@mutation.field("addExistingWorkItems")
def resolve_add_existing_work_items(_, info, slug, project, epic, workItemIds):
    """Nest existing work items under the epic."""
    user = _user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    epic_issue = _epic(slug, project, epic)
    if p is None or epic_issue is None:
        return False
    Issue.objects.filter(project=p, id__in=workItemIds or []).update(parent=epic_issue, updated_by=user)
    return True


@mutation.field("addEpicWorkItemRelation")
def resolve_add_epic_work_item_relation(_, info, slug, project, epic, relationType, relatedWorkItemIds):
    user = _user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    epic_issue = _epic(slug, project, epic)
    if p is None or epic_issue is None:
        return False
    reverse_type = _RELATION_REVERSE.get(relationType, relationType)
    for related_id in relatedWorkItemIds or []:
        if not Issue.objects.filter(project=p, id=related_id).exists():
            continue
        IssueRelation.objects.get_or_create(
            issue_id=related_id,
            related_issue=epic_issue,
            project=p,
            defaults={
                "relation_type": reverse_type,
                "workspace": p.workspace,
                "created_by": user,
                "updated_by": user,
            },
        )
    return True


# --- Mutations: epic comments -------------------------------------------------------


def _create_epic_comment(user, issue, comment_html, parent=None):
    return IssueComment.objects.create(
        issue=issue,
        project=issue.project,
        workspace=issue.workspace,
        comment_html=comment_html or "<p></p>",
        actor=user,
        parent=parent,
        created_by=user,
        updated_by=user,
    )


@mutation.field("addEpicComment")
def resolve_add_epic_comment(_, info, slug, project, epic, commentInput):
    user = _user(info)
    if user is None:
        return None
    issue = _epic(slug, project, epic)
    if issue is None:
        return None
    comment_html = (commentInput or {}).get("commentHtml")
    return _create_epic_comment(user, issue, comment_html)


@mutation.field("deleteEpicComment")
def resolve_delete_epic_comment(_, info, slug, project, epic, comment):
    user = _user(info)
    if user is None:
        return False
    IssueComment.objects.filter(project_id=project, issue_id=epic, id=comment).delete()
    return True


@mutation.field("addEpicCommentReply")
def resolve_add_epic_comment_reply(_, info, slug, project, epic, comment, commentInput):
    user = _user(info)
    if user is None:
        return None
    issue = _epic(slug, project, epic)
    if issue is None:
        return None
    parent = IssueComment.objects.filter(project_id=project, issue=issue, id=comment).first()
    if parent is None:
        return None
    comment_html = (commentInput or {}).get("commentHtml")
    return _create_epic_comment(user, issue, comment_html, parent=parent)


@mutation.field("deleteEpicCommentReply")
def resolve_delete_epic_comment_reply(_, info, slug, project, epic, comment, reply):
    user = _user(info)
    if user is None:
        return False
    IssueComment.objects.filter(
        project_id=project, issue_id=epic, id=reply, parent_id=comment
    ).delete()
    return True


# --- Mutations: epic comment reactions ----------------------------------------------


def _comment_reaction_payload(comment_id, reaction):
    user_ids = list(
        CommentReaction.objects.filter(comment_id=comment_id, reaction=reaction).values_list(
            "actor_id", flat=True
        )
    )
    return {"reaction": reaction, "user_ids": [str(uid) for uid in user_ids]}


@mutation.field("addEpicCommentReaction")
def resolve_add_epic_comment_reaction(_, info, slug, project, epic, comment, reactionInput):
    user = _user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    comment_obj = IssueComment.objects.filter(project_id=project, issue_id=epic, id=comment).first()
    if p is None or comment_obj is None:
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


@mutation.field("removeEpicCommentReaction")
def resolve_remove_epic_comment_reaction(_, info, slug, project, epic, comment, reactionInput):
    user = _user(info)
    if user is None:
        return None
    comment_obj = IssueComment.objects.filter(project_id=project, issue_id=epic, id=comment).first()
    if comment_obj is None:
        return None
    reaction = (reactionInput or {}).get("reaction")
    if not reaction:
        return None
    CommentReaction.objects.filter(comment=comment_obj, actor=user, reaction=reaction).delete()
    return _comment_reaction_payload(comment_obj.id, reaction)


# --- Mutations: epic pages ----------------------------------------------------------


@mutation.field("addEpicPage")
def resolve_add_epic_page(_, info, slug, project, epic, pageIds):
    """Link existing pages to the epic via PageLog(entity_name="issue"). Returns the
    epic's pages in EpicPageType shape."""
    from plane.db.models import Page

    user = _user(info)
    if user is None:
        return []
    issue = _epic(slug, project, epic)
    if issue is None:
        return []
    for page_id in pageIds or []:
        page = Page.objects.filter(id=page_id, workspace__slug=slug).first()
        if page is None:
            continue
        PageLog.objects.get_or_create(
            page_id=page_id,
            entity_identifier=issue.id,
            entity_name="issue",
            workspace=issue.workspace,
            defaults={"created_by": user, "updated_by": user},
        )
    page_ids = list(_epic_page_ids(epic))
    pages = Page.objects.filter(id__in=page_ids, workspace__slug=slug)
    return [_epic_page_payload(p) for p in pages]


@mutation.field("deleteEpicPage")
def resolve_delete_epic_page(_, info, slug, project, epic, pageIds):
    user = _user(info)
    if user is None:
        return False
    PageLog.objects.filter(
        entity_name="issue", entity_identifier=epic, page_id__in=pageIds or []
    ).delete()
    return True


BINDABLES = [
    query,
    mutation,
    epic_type,
    epic_link_type,
    epic_activity_type,
    epic_comment_type,
]
