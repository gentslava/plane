# OVERLAY: mobile-graphql — "issue_extras" area: work-item extras that the native
# app needs to make a self-hosted gateway behave like Plane Cloud.
#
# Implemented Query fields (every field of this area):
#   issueAttachment            -> REST IssueAttachmentEndpoint.get
#   issueAttachmentDetail      -> REST IssueAttachmentEndpoint (single file asset)
#   issueRelation              -> REST IssueRelationViewSet.list
#   issueShortenedMetaInfo     -> resolve "<IDENT>-<seq>" to project/work-item ids
#   issueTypes                 -> IssueType.objects (workspace scoped)
#   issuesInformation          -> grouped totals (all/active/backlog) for a project
#   workspaceIssuesInformation -> grouped totals across the whole workspace
#   issuesSearch               -> REST IssueSearchEndpoint.get (work-item picker)
#   recentIssues               -> UserRecentVisit (entity_name=issue) -> Issue list
#   workItemCommentReactions   -> REST CommentReactionViewSet (grouped by reaction)
#   estimatePoints             -> REST ProjectEstimatePointEndpoint.get
#
# Module is self-contained: own QueryType + ObjectType bindings, exposes BINDABLES.
# Field-level scalar/FK resolution is handled by the global smart fallback
# (camelCase -> snake_case, FK -> <field>_id). We only bind: root query fields,
# computed/aggregate fields, M2M-as-id-lists and FK-as-nested-object fields.
#
# NOTE: IssuesType is bound in plane.graphql.resolvers (recentIssues returns plain
# Issue model instances, so its fields resolve via that existing binding + the smart
# fallback). We deliberately do NOT re-bind ObjectType("IssuesType") here to avoid a
# duplicate-resolver collision at make_executable_schema time.
from ariadne import ObjectType, QueryType

from django.db.models import Q

from plane.db.models import (
    CommentReaction,
    EstimatePoint,
    FileAsset,
    IntakeIssue,
    Issue,
    IssueRelation,
    IssueType,
    Project,
    UserRecentVisit,
)
from plane.graphql.resolvers import _member_project, _user

query = QueryType()

file_asset_type = ObjectType("FileAssetType")
issue_relation_type = ObjectType("IssueRelationType")
relation_work_item_type = ObjectType("WorkItemRelationWorkItemType")
issues_information_type = ObjectType("IssuesInformationType")
issues_information_object_type = ObjectType("IssuesInformationObjectType")
issue_shortened_meta_type = ObjectType("IssueShortenedMetaInfo")
comment_reaction_type = ObjectType("CommentReactionType")
issue_types_type = ObjectType("IssueTypesType")
estimate_point_type = ObjectType("EstimatePointType")
issue_lite_type = ObjectType("IssueLiteType")


# State groups that count as "active" (work in progress) vs "backlog".
_ACTIVE_GROUPS = ["unstarted", "started", "completed", "cancelled"]
_BACKLOG_GROUPS = ["backlog"]


# --- Attachments -------------------------------------------------------------------


@query.field("issueAttachment")
def resolve_issue_attachment(_, info, slug, project, issue):
    # Mirror REST IssueAttachmentEndpoint.get: every issue-attachment file asset for
    # the work item, scoped to the workspace/project.
    user = _user(info)
    if user is None:
        return []
    return list(
        FileAsset.objects.filter(
            workspace__slug=slug,
            project_id=project,
            issue_id=issue,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            is_deleted=False,
        ).order_by("-created_at")
    )


@query.field("issueAttachmentDetail")
def resolve_issue_attachment_detail(_, info, slug, project, issue, attachment):
    # Single file asset; FileAssetType is non-null in the SDL, but the only sane
    # value when the asset is missing is None (the field itself is FileAssetType!,
    # so a missing row surfaces as a query error rather than a silent wrong asset).
    user = _user(info)
    if user is None:
        return None
    return FileAsset.objects.filter(
        workspace__slug=slug, project_id=project, issue_id=issue, id=attachment
    ).first()


@file_asset_type.field("assetUrl")
def resolve_file_asset_url(asset, info):
    # FileAsset.asset_url is a model @property (entity-type aware download path).
    try:
        return asset.asset_url
    except Exception:
        return None


# --- Relations ---------------------------------------------------------------------


def _relation_work_item_payload(item):
    return {
        "id": str(item.id),
        "name": item.name,
        "priority": item.priority or "none",
        "sequence_id": item.sequence_id,
        "project": str(item.project_id),
        "project_identifier": (item.project.identifier if item.project_id else ""),
        "state": str(item.state_id) if item.state_id else "",
        "_assignee_ids": [str(pk) for pk in item.assignees.values_list("id", flat=True)],
        "_is_epic": bool(item.type_id and item.type and item.type.is_epic),
    }


def _empty_relation():
    return {
        "blocking": [],
        "blocked_by": [],
        "duplicate": [],
        "relates_to": [],
        "start_after": [],
        "start_before": [],
        "finish_after": [],
        "finish_before": [],
        "implements": [],
        "implemented_by": [],
    }


@query.field("issueRelation")
def resolve_issue_relation(_, info, slug, project, issue):
    # Mirror REST IssueRelationViewSet.list: split the work item's relations into the
    # 8 (+2 epic) buckets the app renders. The REST view derives "blocking" from the
    # inverse "blocked_by" rows, "start_after"/"finish_after" from the inverse
    # "start_before"/"finish_before"; we replicate that mapping exactly.
    user = _user(info)
    if user is None:
        return _empty_relation()

    relations = (
        IssueRelation.objects.filter(Q(issue_id=issue) | Q(related_issue_id=issue))
        .filter(workspace__slug=slug)
        .distinct()
    )

    blocking_ids = list(
        relations.filter(relation_type="blocked_by", related_issue_id=issue).values_list("issue_id", flat=True)
    )
    blocked_by_ids = list(
        relations.filter(relation_type="blocked_by", issue_id=issue).values_list("related_issue_id", flat=True)
    )
    duplicate_ids = list(
        relations.filter(issue_id=issue, relation_type="duplicate").values_list("related_issue_id", flat=True)
    ) + list(
        relations.filter(related_issue_id=issue, relation_type="duplicate").values_list("issue_id", flat=True)
    )
    relates_to_ids = list(
        relations.filter(issue_id=issue, relation_type="relates_to").values_list("related_issue_id", flat=True)
    ) + list(
        relations.filter(related_issue_id=issue, relation_type="relates_to").values_list("issue_id", flat=True)
    )
    start_after_ids = list(
        relations.filter(relation_type="start_before", related_issue_id=issue).values_list("issue_id", flat=True)
    )
    start_before_ids = list(
        relations.filter(relation_type="start_before", issue_id=issue).values_list("related_issue_id", flat=True)
    )
    finish_after_ids = list(
        relations.filter(relation_type="finish_before", related_issue_id=issue).values_list("issue_id", flat=True)
    )
    finish_before_ids = list(
        relations.filter(relation_type="finish_before", issue_id=issue).values_list("related_issue_id", flat=True)
    )

    def _items(ids):
        if not ids:
            return []
        qs = (
            Issue.objects.filter(id__in=ids)
            .select_related("project", "state", "type")
            .prefetch_related("assignees")
        )
        return [_relation_work_item_payload(item) for item in qs]

    return {
        "blocking": _items(blocking_ids),
        "blocked_by": _items(blocked_by_ids),
        "duplicate": _items(duplicate_ids),
        "relates_to": _items(relates_to_ids),
        "start_after": _items(start_after_ids),
        "start_before": _items(start_before_ids),
        "finish_after": _items(finish_after_ids),
        "finish_before": _items(finish_before_ids),
        # IMPLEMENTS / IMPLEMENTED_BY exist in the relation enum but are an EE-only
        # relation type; community has no such rows, so an empty list is correct.
        "implements": [],
        "implemented_by": [],
    }


@relation_work_item_type.field("assignees")
def resolve_relation_assignees(item, info):
    return item.get("_assignee_ids", [])


@relation_work_item_type.field("isEpic")
def resolve_relation_is_epic(item, info):
    return bool(item.get("_is_epic"))


@relation_work_item_type.field("analytics")
def resolve_relation_analytics(item, info):
    # EpicAnalyticsType! — non-null, but per-relation epic analytics are not part of
    # the REST relation payload; return a zeroed (non-null) shape.
    return {"backlog": 0, "unstarted": 0, "started": 0, "completed": 0, "cancelled": 0}


# --- Shortened meta info -----------------------------------------------------------


@query.field("issueShortenedMetaInfo")
def resolve_issue_shortened_meta_info(_, info, slug, workItemIdentifier):
    # Resolve a human identifier ("PROJ-123") into the project/work-item ids plus the
    # epic/intake flags the app uses for deep links. IssueShortenedMetaInfo is
    # non-null, so we always return a populated shape (empty ids when unresolved).
    empty = {"project": "", "work_item": "", "is_epic": False, "is_intake": False, "intake_id": None}
    user = _user(info)
    if user is None or not workItemIdentifier or "-" not in workItemIdentifier:
        return empty
    identifier, _, seq = workItemIdentifier.rpartition("-")
    try:
        sequence_id = int(seq)
    except (TypeError, ValueError):
        return empty
    item = (
        Issue.objects.filter(
            workspace__slug=slug,
            project__identifier__iexact=identifier,
            sequence_id=sequence_id,
        )
        .select_related("type")
        .first()
    )
    if item is None:
        return empty
    intake = IntakeIssue.objects.filter(issue_id=item.id).first()
    return {
        "project": str(item.project_id),
        "work_item": str(item.id),
        "is_epic": bool(item.type_id and item.type and item.type.is_epic),
        "is_intake": intake is not None,
        "intake_id": str(intake.id) if intake else None,
    }


@query.field("workspaceWorkItemMention")
def resolve_workspace_work_item_mention(_, info, slug, workitem):
    # Resolve a single work item to its @-mention card (WorkItemMentionType!, non-null).
    # Returns a fully-populated dict so the smart fallback maps every camelCase field
    # to a key; an unresolved id yields an empty-but-populated shape (never None, which
    # would crash the whole query on the non-null contract — same guard as
    # issueShortenedMetaInfo).
    empty = {
        "id": "", "name": "", "sequence_id": 0, "project_id": "", "type_id": None,
        "project_identifier": "", "state_group": "", "state_name": "",
        "archived_at": None, "is_epic": False,
    }
    user = _user(info)
    if user is None or not workitem:
        return empty
    item = (
        Issue.objects.filter(workspace__slug=slug, pk=workitem)
        .select_related("project", "state", "type")
        .first()
    )
    if item is None:
        return empty
    return {
        "id": str(item.id),
        "name": item.name,
        "sequence_id": item.sequence_id,
        "project_id": str(item.project_id) if item.project_id else "",
        "type_id": str(item.type_id) if item.type_id else None,
        "project_identifier": item.project.identifier if item.project_id else "",
        "state_group": item.state.group if item.state_id else "",
        "state_name": item.state.name if item.state_id else "",
        "archived_at": item.archived_at,
        "is_epic": bool(item.type_id and item.type and item.type.is_epic),
    }


# --- Issue types -------------------------------------------------------------------


@query.field("issueTypes")
def resolve_issue_types(_, info, slug):
    user = _user(info)
    if user is None:
        return []
    return list(IssueType.objects.filter(workspace__slug=slug).order_by("level", "name"))


@issue_types_type.field("level")
def resolve_issue_type_level(issue_type, info):
    # SDL level is Int!; the model column is a FloatField.
    try:
        return int(issue_type.level or 0)
    except (TypeError, ValueError):
        return 0


# --- Issues information (grouped totals) -------------------------------------------


def _information_buckets(qs, group_by):
    """Build the all/active/backlog IssuesInformationObjectType buckets.

    active = states whose group is unstarted/started/completed/cancelled.
    backlog = states whose group is backlog.
    groupInfo is an optional per-group count map when groupBy is supplied.
    """
    all_qs = qs
    active_qs = qs.filter(state__group__in=_ACTIVE_GROUPS)
    backlog_qs = qs.filter(state__group__in=_BACKLOG_GROUPS)

    def _bucket(bucket_qs):
        group_info = None
        if group_by:
            group_info = {}
            field = _GROUP_BY_FIELDS.get(group_by)
            if field:
                rows = bucket_qs.values_list(field, flat=True)
                for value in rows:
                    key = str(value) if value is not None else "None"
                    group_info[key] = group_info.get(key, 0) + 1
        return {"total_issues": bucket_qs.count(), "group_info": group_info}

    return {
        "all": _bucket(all_qs),
        "active": _bucket(active_qs),
        "backlog": _bucket(backlog_qs),
    }


# Supported groupBy keys -> queryable field (FK/scalar columns). Unknown keys yield
# an empty groupInfo rather than a crash.
_GROUP_BY_FIELDS = {
    "state": "state_id",
    "state_id": "state_id",
    "priority": "priority",
    "project": "project_id",
    "project_id": "project_id",
    "created_by": "created_by_id",
    "assignees": "assignees__id",
    "labels": "labels__id",
    "cycle": "issue_cycle__cycle_id",
    "module": "issue_module__module_id",
    "target_date": "target_date",
    "start_date": "start_date",
}


def _apply_information_filters(qs, filters):
    if not isinstance(filters, dict):
        return qs
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
    return qs.distinct()


@query.field("issuesInformation")
def resolve_issues_information(_, info, slug, project, filters=None, groupBy=None, orderBy="-created_at"):
    user = _user(info)
    if user is None:
        return {"all": None, "active": None, "backlog": None}
    qs = Issue.issue_objects.filter(workspace__slug=slug, project_id=project)
    qs = _apply_information_filters(qs, filters)
    return _information_buckets(qs, groupBy)


@query.field("workspaceIssuesInformation")
def resolve_workspace_issues_information(_, info, slug, filters=None, groupBy=None, orderBy="-created_at"):
    user = _user(info)
    if user is None:
        return {"all": None, "active": None, "backlog": None}
    qs = Issue.issue_objects.filter(
        workspace__slug=slug,
        project__project_projectmember__member=user,
        project__project_projectmember__is_active=True,
    ).distinct()
    qs = _apply_information_filters(qs, filters)
    return _information_buckets(qs, groupBy)


# --- Issues search (work-item picker) ----------------------------------------------


def _search_issues(qs, search):
    import re

    fields = ["name", "sequence_id", "project__identifier"]
    q = Q()
    for field in fields:
        if field == "sequence_id" and len(search) <= 20:
            for sequence_id in re.findall(r"\b\d+\b", search):
                q |= Q(sequence_id=sequence_id)
        else:
            q |= Q(**{f"{field}__icontains": search})
    return qs.filter(q).distinct()


def _issue_lite_payload(item):
    return {
        "id": str(item.id),
        "name": item.name,
        "sequence_id": item.sequence_id,
        "workspace": str(item.workspace_id),
        "project": str(item.project_id),
        "project_identifier": item.project.identifier if item.project_id else None,
        "_is_epic": bool(item.type_id and item.type and item.type.is_epic),
    }


@query.field("issuesSearch")
def resolve_issues_search(
    _,
    info,
    slug,
    project=None,
    issue=None,
    cursor=None,
    search=None,
    relationType=False,
    subIssues=False,
    isEpicRelated=False,
    isIntakeRelated=False,
    relationTypeValue=None,
):
    # Mirror REST IssueSearchEndpoint.get: search the user's joined work items, with
    # the picker's exclusion modes (relation -> drop already-related items;
    # subIssues -> root items only excluding the current/parent item).
    user = _user(info)
    from plane.graphql.resolvers import _page

    if user is None:
        return _page([], cursor)

    qs = Issue.issue_objects.filter(
        workspace__slug=slug,
        project__project_projectmember__member=user,
        project__project_projectmember__is_active=True,
        project__archived_at__isnull=True,
    )
    if project:
        qs = qs.filter(project_id=project)

    term = (search or "").strip()
    if term:
        qs = _search_issues(qs, term)

    if isEpicRelated:
        qs = qs.filter(type__is_epic=True)

    if relationType and issue:
        related_ids = (
            IssueRelation.objects.filter(Q(related_issue_id=issue) | Q(issue_id=issue))
            .values_list("issue_id", "related_issue_id")
            .distinct()
        )
        exclude_ids = {item for pair in related_ids for item in pair}
        exclude_ids.add(issue)
        qs = qs.exclude(pk__in=exclude_ids)

    if subIssues and issue:
        current = Issue.issue_objects.filter(pk=issue).first()
        if current is not None:
            qs = qs.filter(~Q(pk=issue), parent__isnull=True).exclude(type__is_epic=True)
            if current.parent_id:
                qs = qs.filter(~Q(pk=current.parent_id))

    if isIntakeRelated:
        intake_ids = IntakeIssue.objects.filter(workspace__slug=slug).values_list("issue_id", flat=True)
        qs = qs.exclude(pk__in=intake_ids)

    rows = [
        _issue_lite_payload(item)
        for item in qs.select_related("project", "type").order_by("-created_at")[:100]
    ]
    return _page(rows, cursor)


@issue_lite_type.field("isEpic")
def resolve_issue_lite_is_epic(item, info):
    return bool(item.get("_is_epic"))


# --- Recent issues -----------------------------------------------------------------


@query.field("recentIssues")
def resolve_recent_issues(_, info, slug):
    # The "recent work items" rail: the user's recently visited issues (UserRecentVisit
    # rows with entity_name=issue), resolved back to live Issue rows in visit order.
    user = _user(info)
    if user is None:
        return []
    visits = list(
        UserRecentVisit.objects.filter(
            workspace__slug=slug, user=user, entity_name="issue"
        ).order_by("-visited_at")[:50]
    )
    issue_ids = [v.entity_identifier for v in visits if v.entity_identifier]
    if not issue_ids:
        return []
    issues = {
        str(item.id): item
        for item in Issue.objects.filter(id__in=issue_ids).select_related("project", "type", "state")
    }
    ordered = [issues[str(iid)] for iid in issue_ids if str(iid) in issues]
    return ordered


# --- Work item comment reactions ---------------------------------------------------


@query.field("workItemCommentReactions")
def resolve_work_item_comment_reactions(_, info, slug, project, workItem, comment):
    # CommentReactionType is {reaction, userIds}: group the comment's reactions by
    # emoji and collect the actors. Mirror REST CommentReactionViewSet scope.
    user = _user(info)
    if user is None:
        return []
    rows = CommentReaction.objects.filter(
        workspace__slug=slug, project_id=project, comment_id=comment
    ).order_by("-created_at")
    grouped = {}
    for r in rows:
        bucket = grouped.setdefault(r.reaction, [])
        bucket.append(str(r.actor_id))
    return [{"reaction": reaction, "user_ids": user_ids} for reaction, user_ids in grouped.items()]


# --- Estimate points ---------------------------------------------------------------


@query.field("estimatePoints")
def resolve_estimate_points(_, info, slug, project):
    # Mirror REST ProjectEstimatePointEndpoint.get: the points of the project's active
    # estimate (empty when the project has no estimate linked).
    user = _user(info)
    if user is None:
        return []
    proj = _member_project(info, slug, project)
    if proj is None or proj.estimate_id is None:
        return []
    return list(
        EstimatePoint.objects.filter(
            estimate_id=proj.estimate_id, project_id=project, workspace__slug=slug
        ).order_by("key")
    )


BINDABLES = [
    query,
    file_asset_type,
    issue_relation_type,
    relation_work_item_type,
    issues_information_type,
    issues_information_object_type,
    issue_shortened_meta_type,
    comment_reaction_type,
    issue_types_type,
    estimate_point_type,
    issue_lite_type,
]
