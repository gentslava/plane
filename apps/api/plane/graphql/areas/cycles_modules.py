# OVERLAY: mobile-graphql — "cycles_modules" area.
#
# Detail-level cycle/module fields for the native app, replicated from Plane's REST
# views so a self-hosted gateway answers the same shapes as Cloud:
#   - cycle / cycleIssues / cycleIssuesInformation / cycleIssueUserProperties
#   - module / moduleIssues / moduleIssuesInformation / moduleIssueUserProperties
#   - updateCycleUserProperties / updateModuleUserProperties
#   - triageStates
#
# Ground truth REST views:
#   apps/api/plane/app/views/cycle/base.py   (CycleViewSet.get_queryset status/issue
#       counts/assignee_ids; CycleUserPropertiesEndpoint get/patch)
#   apps/api/plane/app/views/cycle/issue.py  (CycleIssueViewSet.list)
#   apps/api/plane/app/views/module/base.py  (ModuleViewSet.get_queryset issue counts/
#       member_ids; ModuleUserPropertiesEndpoint get/patch)
#   apps/api/plane/app/views/module/issue.py (ModuleIssueViewSet.list)
#
# Module is self-contained: own QueryType/MutationType/ObjectType, shared helpers from
# resolvers.py. The global smart-fallback resolves camelCase->snake_case and FK->*_id,
# so only computed/aggregate/M2M-id/FK-as-object fields are bound here.
from ariadne import MutationType, ObjectType, QueryType
from django.db.models import Count
from django.utils import timezone

from plane.db.models import (
    Cycle,
    CycleIssue,
    CycleUserProperties,
    Issue,
    Module,
    ModuleIssue,
    ModuleMember,
    ModuleUserProperties,
    State,
    Workspace,
)
from plane.graphql.resolvers import (
    _apply_issue_filters,
    _ordered_issues,
    _page,
    _track_visit,
    _user,
)

query = QueryType()
mutation = MutationType()

cycle_type = ObjectType("CycleType")
module_type = ObjectType("ModuleType")

# State.group buckets used by Cloud's CycleType/ModuleType issue counters and by the
# IssuesInformation "active" view (REST: cycle/module get_queryset annotations).
_DONE_GROUPS = ["completed", "cancelled"]
_ACTIVE_GROUPS = ["unstarted", "started"]


# --- helpers --------------------------------------------------------------------------


def _cycle_issue_qs(slug, project_id, cycle_id):
    """Issues linked to a cycle — mirrors CycleIssueViewSet.list base queryset
    (issue_cycle join, excludes archived/draft via Issue.issue_objects manager)."""
    return Issue.issue_objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        issue_cycle__cycle_id=cycle_id,
        issue_cycle__deleted_at__isnull=True,
    ).distinct()


def _module_issue_qs(slug, project_id, module_id):
    """Issues linked to a module — mirrors ModuleIssueViewSet.list base queryset."""
    return Issue.issue_objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        issue_module__module_id=module_id,
        issue_module__deleted_at__isnull=True,
    ).distinct()


def _issues_information(qs, group_by):
    """IssuesInformationType: {all, active, backlog} each
    IssuesInformationObjectType {totalIssues, groupInfo}.

    REST analogue: the cycle/module issue list bundles a grouped count of the same
    queryset. We compute totalIssues per bucket and, when group_by is supplied, a
    groupInfo map {group_value: count} so the app can render grouped headers."""

    def _bucket(bucket_qs):
        total = bucket_qs.count()
        group_info = None
        if group_by:
            group_info = {}
            field = _GROUP_BY_FIELD.get(group_by, group_by)
            try:
                rows = (
                    bucket_qs.values(field)
                    .order_by(field)
                    .annotate(_cnt=Count("id", distinct=True))
                )
                for row in rows:
                    key = row.get(field)
                    group_info[str(key) if key is not None else "None"] = row["_cnt"]
            except Exception:
                group_info = {}
        return {"total_issues": total, "group_info": group_info}

    all_qs = qs
    active_qs = qs.filter(state__group__in=_ACTIVE_GROUPS)
    backlog_qs = qs.filter(state__group="backlog")
    return {
        "all": _bucket(all_qs),
        "active": _bucket(active_qs),
        "backlog": _bucket(backlog_qs),
    }


# group_by tokens the app sends -> ORM lookups (Cloud's issue grouping vocabulary).
_GROUP_BY_FIELD = {
    "state": "state_id",
    "state_detail.group": "state__group",
    "priority": "priority",
    "labels": "labels__id",
    "assignees": "assignees__id",
    "created_by": "created_by_id",
}


def _filtered_issues(qs, filters, order_by, cursor, type):
    """Apply the app's filter set + the cycle/module list `type` (all/active/backlog)
    and return the *PaginatorResponse shape."""
    qs = _apply_issue_filters(qs, filters)
    if type == "active":
        qs = qs.filter(state__group__in=_ACTIVE_GROUPS)
    elif type == "backlog":
        qs = qs.filter(state__group="backlog")
    return _page(_ordered_issues(qs, order_by), cursor)


def _user_property_payload(obj, *, entity_key, entity_id, slug):
    """Serialize a Cycle/Module UserProperties row into the SDL shape. JSON fields are
    non-null in the SDL; default to {} so the contract never breaks."""
    workspace_id = (
        obj.workspace_id
        if obj is not None
        else Workspace.objects.filter(slug=slug).values_list("id", flat=True).first()
    )
    payload = {
        "id": str(obj.id) if obj is not None else str(entity_id),
        "user": str(obj.user_id) if obj is not None else "",
        "project": str(obj.project_id) if obj is not None else "",
        "workspace": str(workspace_id) if workspace_id else "",
        "filters": (obj.filters if obj is not None else None) or {},
        "display_filters": (obj.display_filters if obj is not None else None) or {},
        "display_properties": (obj.display_properties if obj is not None else None) or {},
    }
    payload[entity_key] = str(entity_id)
    return payload


# --- Query: cycle detail / issues / information / user-properties ----------------------


@query.field("cycle")
def resolve_cycle(_, info, slug, project, cycle):
    # CycleType is non-null in the SDL, but a missing cycle must not crash the whole
    # query — return the row when present (computed CycleType fields are filled by the
    # cycle_type ObjectType below), else a minimal non-null-safe stub.
    user = _user(info)
    if user is None:
        return _cycle_stub(slug, project, cycle)
    item = Cycle.objects.filter(workspace__slug=slug, project_id=project, id=cycle).first()
    if item is None:
        return _cycle_stub(slug, project, cycle)
    _track_visit(user, slug, item.project_id, "cycle", item.id)
    return item


def _cycle_stub(slug, project, cycle):
    workspace_id = Workspace.objects.filter(slug=slug).values_list("id", flat=True).first()
    return {
        "id": str(cycle),
        "name": "",
        "project": str(project),
        "workspace": str(workspace_id) if workspace_id else "",
        "total_issues": 0,
        "completed_issues": 0,
        "is_favorite": False,
        "status": "DRAFT",
        "started_issues": 0,
        "unstarted_issues": 0,
        "assignees_count": 0,
    }


@query.field("cycleIssues")
def resolve_cycle_issues(_, info, slug, project, cycle, filters=None, orderBy="-created_at", cursor=None, type="all"):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = _cycle_issue_qs(slug, project, cycle)
    return _filtered_issues(qs, filters, orderBy, cursor, type)


@query.field("cycleIssuesInformation")
def resolve_cycle_issues_information(
    _, info, slug, project, cycle, filters=None, groupBy=None, orderBy="-created_at"
):
    user = _user(info)
    if user is None:
        return _issues_information(Issue.issue_objects.none(), groupBy)
    qs = _apply_issue_filters(_cycle_issue_qs(slug, project, cycle), filters)
    return _issues_information(qs, groupBy)


@query.field("cycleIssueUserProperties")
def resolve_cycle_issue_user_properties(_, info, slug, project, cycle):
    user = _user(info)
    if user is None:
        return _user_property_payload(None, entity_key="cycle", entity_id=cycle, slug=slug)
    # REST CycleUserPropertiesEndpoint.get uses get_or_create with model defaults.
    obj, _created = CycleUserProperties.objects.get_or_create(
        user=user,
        cycle_id=cycle,
        project_id=project,
        workspace__slug=slug,
        defaults={},
    )
    return _user_property_payload(obj, entity_key="cycle", entity_id=cycle, slug=slug)


# --- Query: module detail / issues / information / user-properties ---------------------


@query.field("module")
def resolve_module(_, info, slug, project, module):
    user = _user(info)
    if user is None:
        return _module_stub(slug, project, module)
    item = Module.objects.filter(workspace__slug=slug, project_id=project, id=module).first()
    if item is None:
        return _module_stub(slug, project, module)
    _track_visit(user, slug, item.project_id, "module", item.id)
    return item


def _module_stub(slug, project, module):
    workspace_id = Workspace.objects.filter(slug=slug).values_list("id", flat=True).first()
    return {
        "name": "",
        "status": "planned",
        "id": str(module),
        "project": str(project),
        "workspace": str(workspace_id) if workspace_id else "",
        "sort_order": 65535.0,
        "total_issues": 0,
        "completed_issues": 0,
        "is_favorite": False,
        "assignees_count": 0,
    }


@query.field("moduleIssues")
def resolve_module_issues(_, info, slug, project, module, filters=None, orderBy="-created_at", cursor=None, type="all"):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = _module_issue_qs(slug, project, module)
    return _filtered_issues(qs, filters, orderBy, cursor, type)


@query.field("moduleIssuesInformation")
def resolve_module_issues_information(
    _, info, slug, project, module, filters=None, groupBy=None, orderBy="-created_at"
):
    user = _user(info)
    if user is None:
        return _issues_information(Issue.issue_objects.none(), groupBy)
    qs = _apply_issue_filters(_module_issue_qs(slug, project, module), filters)
    return _issues_information(qs, groupBy)


@query.field("moduleIssueUserProperties")
def resolve_module_issue_user_properties(_, info, slug, project, module):
    user = _user(info)
    if user is None:
        return _user_property_payload(None, entity_key="module", entity_id=module, slug=slug)
    obj, _created = ModuleUserProperties.objects.get_or_create(
        user=user,
        module_id=module,
        project_id=project,
        workspace__slug=slug,
        defaults={},
    )
    return _user_property_payload(obj, entity_key="module", entity_id=module, slug=slug)


# --- Query: triageStates --------------------------------------------------------------


@query.field("triageStates")
def resolve_triage_states(_, info, slug, project):
    # The triage state(s) of a project — State rows flagged is_triage. Returns [] when
    # unauthenticated or none configured ([StateType!]! must stay non-null).
    user = _user(info)
    if user is None:
        return []
    return list(State.objects.filter(workspace__slug=slug, project_id=project, is_triage=True))


# --- Mutations: update cycle/module user properties -----------------------------------


@mutation.field("updateCycleUserProperties")
def resolve_update_cycle_user_properties(
    _, info, slug, project, cycle, filters, displayFilters, displayProperties
):
    # REST CycleUserPropertiesEndpoint.patch: upsert the row, persist the three JSON
    # blobs, return the serialized row.
    user = _user(info)
    if user is None:
        return _user_property_payload(None, entity_key="cycle", entity_id=cycle, slug=slug)
    obj, _created = CycleUserProperties.objects.get_or_create(
        user=user,
        cycle_id=cycle,
        project_id=project,
        workspace__slug=slug,
        defaults={},
    )
    obj.filters = filters if filters is not None else obj.filters
    obj.display_filters = displayFilters if displayFilters is not None else obj.display_filters
    obj.display_properties = displayProperties if displayProperties is not None else obj.display_properties
    obj.save(update_fields=["filters", "display_filters", "display_properties", "updated_at"])
    return _user_property_payload(obj, entity_key="cycle", entity_id=cycle, slug=slug)


@mutation.field("updateModuleUserProperties")
def resolve_update_module_user_properties(
    _, info, slug, project, module, filters, displayFilters, displayProperties
):
    user = _user(info)
    if user is None:
        return _user_property_payload(None, entity_key="module", entity_id=module, slug=slug)
    obj, _created = ModuleUserProperties.objects.get_or_create(
        user=user,
        module_id=module,
        project_id=project,
        workspace__slug=slug,
        defaults={},
    )
    obj.filters = filters if filters is not None else obj.filters
    obj.display_filters = displayFilters if displayFilters is not None else obj.display_filters
    obj.display_properties = displayProperties if displayProperties is not None else obj.display_properties
    obj.save(update_fields=["filters", "display_filters", "display_properties", "updated_at"])
    return _user_property_payload(obj, entity_key="module", entity_id=module, slug=slug)


# --- CycleType computed / aggregate / FK-as-object fields ------------------------------
#
# Plain scalar + FK-as-id fields (project, workspace, createdBy, ...) and JSON columns
# resolve via the smart fallback. Bound here: issue counters, status, assigneesCount,
# isFavorite/favoriteId, ownedBy (FK -> UserType object). These accept a real Cycle
# model OR a stub dict (from _cycle_stub); dict access wins when present.


def _cycle_issue_state_count(cycle, groups=None, group=None):
    qs = Issue.issue_objects.filter(
        issue_cycle__cycle_id=cycle.id,
        issue_cycle__deleted_at__isnull=True,
    )
    if groups is not None:
        qs = qs.filter(state__group__in=groups)
    elif group is not None:
        qs = qs.filter(state__group=group)
    return qs.distinct().count()


def _maybe_dict(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return None


@cycle_type.field("totalIssues")
def resolve_cycle_total_issues(cycle, info):
    v = _maybe_dict(cycle, "total_issues")
    if v is not None:
        return v
    return Issue.issue_objects.filter(
        issue_cycle__cycle_id=cycle.id, issue_cycle__deleted_at__isnull=True
    ).distinct().count()


@cycle_type.field("completedIssues")
def resolve_cycle_completed_issues(cycle, info):
    v = _maybe_dict(cycle, "completed_issues")
    if v is not None:
        return v
    return _cycle_issue_state_count(cycle, group="completed")


@cycle_type.field("startedIssues")
def resolve_cycle_started_issues(cycle, info):
    v = _maybe_dict(cycle, "started_issues")
    if v is not None:
        return v
    return _cycle_issue_state_count(cycle, group="started")


@cycle_type.field("unstartedIssues")
def resolve_cycle_unstarted_issues(cycle, info):
    v = _maybe_dict(cycle, "unstarted_issues")
    if v is not None:
        return v
    return _cycle_issue_state_count(cycle, group="unstarted")


@cycle_type.field("status")
def resolve_cycle_status(cycle, info):
    # Mirrors CycleViewSet.get_queryset status Case:
    #   start<=now<=end -> CURRENT; start>now -> UPCOMING; end<now -> COMPLETED;
    #   both null -> DRAFT.
    v = _maybe_dict(cycle, "status")
    if v is not None:
        return v
    now = timezone.now()
    start, end = cycle.start_date, cycle.end_date
    if start is None and end is None:
        return "DRAFT"
    if start is not None and end is not None and start <= now <= end:
        return "CURRENT"
    if start is not None and start > now:
        return "UPCOMING"
    if end is not None and end < now:
        return "COMPLETED"
    return "DRAFT"


@cycle_type.field("assigneesCount")
def resolve_cycle_assignees_count(cycle, info):
    v = _maybe_dict(cycle, "assignees_count")
    if v is not None:
        return v
    return (
        Issue.issue_objects.filter(
            issue_cycle__cycle_id=cycle.id, issue_cycle__deleted_at__isnull=True
        )
        .values("assignees__id")
        .exclude(assignees__id__isnull=True)
        .distinct()
        .count()
    )


@cycle_type.field("isFavorite")
def resolve_cycle_is_favorite(cycle, info):
    v = _maybe_dict(cycle, "is_favorite")
    if v is not None:
        return v
    user = _user(info)
    if user is None:
        return False
    from plane.db.models import UserFavorite

    return UserFavorite.objects.filter(
        user=user, entity_type="cycle", entity_identifier=cycle.id, deleted_at__isnull=True
    ).exists()


@cycle_type.field("favoriteId")
def resolve_cycle_favorite_id(cycle, info):
    if isinstance(cycle, dict):
        return cycle.get("favorite_id")
    user = _user(info)
    if user is None:
        return None
    from plane.db.models import UserFavorite

    fav = UserFavorite.objects.filter(
        user=user, entity_type="cycle", entity_identifier=cycle.id, deleted_at__isnull=True
    ).first()
    return str(fav.id) if fav else None


@cycle_type.field("ownedBy")
def resolve_cycle_owned_by(cycle, info):
    # FK -> UserType object (app selects sub-fields). Null-safe for the stub dict.
    if isinstance(cycle, dict):
        return None
    return cycle.owned_by if cycle.owned_by_id else None


# --- ModuleType computed / aggregate / M2M / FK-as-object fields -----------------------


def _module_issue_state_count(module, group=None):
    qs = Issue.issue_objects.filter(
        issue_module__module_id=module.id,
        issue_module__deleted_at__isnull=True,
    )
    if group is not None:
        qs = qs.filter(state__group=group)
    return qs.distinct().count()


@module_type.field("totalIssues")
def resolve_module_total_issues(module, info):
    v = _maybe_dict(module, "total_issues")
    if v is not None:
        return v
    return Issue.issue_objects.filter(
        issue_module__module_id=module.id, issue_module__deleted_at__isnull=True
    ).distinct().count()


@module_type.field("completedIssues")
def resolve_module_completed_issues(module, info):
    v = _maybe_dict(module, "completed_issues")
    if v is not None:
        return v
    return _module_issue_state_count(module, group="completed")


@module_type.field("members")
def resolve_module_members(module, info):
    # M2M -> list of member id strings (REST exposes member_ids). [ID!] (nullable list).
    if isinstance(module, dict):
        return module.get("members") or []
    return [
        str(pk)
        for pk in ModuleMember.objects.filter(
            module_id=module.id, deleted_at__isnull=True
        ).values_list("member_id", flat=True)
    ]


@module_type.field("assigneesCount")
def resolve_module_assignees_count(module, info):
    v = _maybe_dict(module, "assignees_count")
    if v is not None:
        return v
    return (
        Issue.issue_objects.filter(
            issue_module__module_id=module.id, issue_module__deleted_at__isnull=True
        )
        .values("assignees__id")
        .exclude(assignees__id__isnull=True)
        .distinct()
        .count()
    )


@module_type.field("isFavorite")
def resolve_module_is_favorite(module, info):
    v = _maybe_dict(module, "is_favorite")
    if v is not None:
        return v
    user = _user(info)
    if user is None:
        return False
    from plane.db.models import UserFavorite

    return UserFavorite.objects.filter(
        user=user, entity_type="module", entity_identifier=module.id, deleted_at__isnull=True
    ).exists()


@module_type.field("favoriteId")
def resolve_module_favorite_id(module, info):
    if isinstance(module, dict):
        return module.get("favorite_id")
    user = _user(info)
    if user is None:
        return None
    from plane.db.models import UserFavorite

    fav = UserFavorite.objects.filter(
        user=user, entity_type="module", entity_identifier=module.id, deleted_at__isnull=True
    ).first()
    return str(fav.id) if fav else None


@module_type.field("lead")
def resolve_module_lead(module, info):
    # FK -> UserType object (nullable). Null-safe for the stub dict.
    if isinstance(module, dict):
        return None
    return module.lead if module.lead_id else None


BINDABLES = [query, mutation, cycle_type, module_type]
