# OVERLAY: mobile-graphql — resolvers for the native app's entry flow.
#
# Implemented operations (enough for the app to clear the "unable to get your
# info" screen and list workspaces): versionCheck, user, profile, workspaces,
# workspaceFeatures, featureFlag, workspaceLicense, tours, timezoneList, yourWork
# and the updateProfile / updateLastWorkspace mutations.
#
# Field-level resolution is camelCase->snake_case via snake_case_fallback_resolvers
# (see schema.py); only computed/aggregate fields get explicit resolvers here.
from datetime import datetime, timedelta

import pytz
from ariadne import MutationType, ObjectType, QueryType
from django.db.models import BooleanField, Case, Exists, OuterRef, Q, When
from django.utils import timezone

from plane.db.models import (
    Cycle,
    CycleIssue,
    Device,
    FileAsset,
    IntakeIssue,
    Issue,
    IssueActivity,
    IssueAssignee,
    IssueComment,
    IssueLink,
    IssueRelation,
    IssueSubscriber,
    Label,
    Module,
    ModuleIssue,
    Notification,
    Page,
    Profile,
    Project,
    ProjectMember,
    State,
    Sticky,
    UserFavorite,
    UserRecentVisit,
    Workspace,
    WorkspaceMember,
)
from plane.bgtasks.recent_visited_task import recent_visited_task

query = QueryType()
mutation = MutationType()

workspace_type = ObjectType("WorkspaceType")
user_type = ObjectType("UserType")
profile_type = ObjectType("ProfileType")
project_type = ObjectType("ProjectType")
issues_type = ObjectType("IssuesType")
issue_activity_type = ObjectType("IssuePropertyActivityType")
recent_visit_type = ObjectType("UserRecentVisitType")
user_favorite_type = ObjectType("UserFavoriteType")
favorite_type = ObjectType("FavoriteType")
issue_comment_type = ObjectType("IssueCommentActivityType")
notification_type = ObjectType("NotificationType")


def _track_visit(user, slug, project_id, entity_name, entity_id):
    if user is None or not project_id:
        return
    try:
        recent_visited_task.delay(
            entity_name=entity_name,
            entity_identifier=str(entity_id),
            user_id=str(user.id),
            project_id=str(project_id),
            slug=slug,
        )
    except Exception:
        pass

_DONE_GROUPS = ["completed", "cancelled"]

# All FeatureFlag fields default to off for a self-managed community instance.
FEATURE_FLAGS = [
    "oidc_saml_auth", "home_advanced", "inbox_stacking", "workspace_active_cycles",
    "customers", "initiatives", "teamspaces", "epics", "project_templates",
    "page_templates", "project_templates_publish", "view_access_private", "view_lock",
    "view_publish", "project_overview", "project_grouping", "project_updates",
    "bulk_ops_one", "bulk_ops_pro", "cycle_progress_charts", "issue_types",
    "issue_worklog", "workitem_templates", "work_item_conversion", "copy_work_item",
    "estimate_with_time", "time_estimates", "workflows", "intake_settings",
    "intake_email", "intake_form", "link_pages", "collaboration_cursor",
    "editor_ai_ops", "page_issue_embeds", "page_publish", "move_pages", "nested_pages",
    "workspace_pages", "shared_pages", "editor_attachments", "editor_mathematics",
    "editor_external_embeds", "page_comments", "editor_advanced_mentions",
    "editor_copy_block_link", "editor_video_attachments", "silo", "silo_importers",
    "flatfile_importer", "jira_importer", "jira_issue_types_importer",
    "jira_server_importer", "jira_server_issue_types_importer", "linear_importer",
    "linear_teams_importer", "asana_importer", "asana_issue_properties_importer",
    "clickup_importer", "clickup_issue_properties_importer", "notion_importer",
    "silo_integrations", "github_integration", "gitlab_integration", "slack_integration",
    "file_size_limit_pro", "timeline_dependency", "pi_chat", "pi_dedupe", "pi_converse",
    "pi_file_uploads", "ai_chat", "ai_dedupe", "ai_converse", "ai_file_uploads",
    "ai_pages_blocks", "ai_pages_summary", "advanced_search",
]


def _user(info):
    user = getattr(info.context, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def _page(results, cursor=None):
    """*PaginatorResponse with valid Plane cursors ("limit:page:is_prev") AND real
    offset slicing.

    The app follows nextCursor for infinite scroll; if every page returns the full
    list (offset ignored) it never reaches an empty page and loops forever (the
    cold-start "infinite loader"). Slice by the requested page so page 1+ is empty."""
    limit, page = 100, 0
    if isinstance(cursor, str) and cursor.count(":") == 2:
        try:
            raw_limit, raw_page, _ = cursor.split(":")
            limit, page = int(raw_limit), int(raw_page)
        except (ValueError, TypeError):
            limit, page = 100, 0
    if limit <= 0:
        limit = 100
    page = max(page, 0)
    offset = page * limit
    window = results[offset:offset + limit]
    has_more = len(results) > offset + limit
    # Match Cloud exactly: nextCursor is null on the last page (a non-null value makes
    # the app treat the cached stickies/list as an unfinished pagination and never
    # render it on a cold start).
    return {
        "prev_cursor": f"{limit}:{page - 1}:0",
        "cursor": f"{limit}:{page}:0",
        "next_cursor": f"{limit}:{page + 1}:0" if has_more else None,
        "prev_page_results": page > 0,
        "next_page_results": has_more,
        "count": len(window),
        "total_count": len(results),
        "results": window,
    }


def _empty_paginator():
    """Shape shared by every *PaginatorResponse type in the Cloud SDL."""
    return _page([])


# --- Query: version / user / profile -------------------------------------------------


@query.field("versionCheck")
def resolve_version_check(_, info, platform, isInternal=False):
    # The mobile app stores minSupportedBackendVersion as SelfHostedVersionCubit.
    # minSupportedSelfHostedVersion and parses it as a "vX.Y.Z" string (Cloud returns
    # e.g. "v1.12.0"). A bare "0.1.0" fails that parse, leaving the value null, so the
    # app can never confirm the backend is supported and silently gates the home
    # dashboard. Keep the "v" prefix and a very low floor so we are always supported;
    # never force an update.
    return {
        "version": "2.1.0",
        "min_supported_version": "1.0.0",
        "url": None,
        "force_update": False,
        "min_supported_backend_version": "v0.0.1",
    }


@query.field("user")
def resolve_user(_, info):
    return _user(info)


@query.field("profile")
def resolve_profile(_, info):
    user = _user(info)
    if user is None:
        return None
    profile, _created = Profile.objects.get_or_create(user=user)
    return profile


def _device_info_payload(device):
    # DeviceInformationType has non-null user/deviceId/pushToken/deviceType.
    if device is None:
        return None
    return {
        "user": str(device.user_id),
        "device_id": device.device_id or "",
        "device_type": device.device_type or Device.DeviceType.ANDROID,
        "push_token": device.push_token or "",
        "is_active": device.is_active,
    }


@query.field("userInformation")
def resolve_user_information(_, info, deviceId=None):
    # Entry-flow root field (operation userInformationAndWorkspacesQuery):
    # bundles the current user, profile and last-visited workspace.
    user = _user(info)
    if user is None:
        return None
    profile, _created = Profile.objects.get_or_create(user=user)
    workspace = None
    if profile.last_workspace_id:
        workspace = Workspace.objects.filter(id=profile.last_workspace_id, deleted_at__isnull=True).first()
        if workspace is not None:
            member = WorkspaceMember.objects.filter(workspace=workspace, member=user, is_active=True).first()
            if member is not None:
                workspace._gql_role = member.role
    # deviceInfo must be non-null once we know the device, exactly like Cloud's
    # steady state. While it stays null the app treats the device as unsynced and
    # re-syncs the home from scratch on every cold start — which drops the cached
    # home dashboard (stickies / yourWork, neither of which is refetched on a cold
    # start) and leaves a skeleton. The app registers a device only once and never
    # re-fires deviceInformation when the server reports null, so we lazily persist
    # the device here (first userInformation call carrying a deviceId) to break the
    # churn; the deviceInformation mutation keeps it up to date afterwards.
    device = None
    if deviceId:
        device, _ = Device.objects.get_or_create(
            user=user,
            device_id=deviceId,
            defaults={"device_type": Device.DeviceType.ANDROID, "is_active": True},
        )
    return {
        "user": user,
        "profile": profile,
        "workspace": workspace,
        "device_info": _device_info_payload(device),
    }


@query.field("tours")
def resolve_tours(_, info):
    user = _user(info)
    completed = False
    if user is not None:
        profile = Profile.objects.filter(user=user).first()
        completed = bool(profile and profile.is_tour_completed)
    return {"home_tour": completed}


@query.field("timezoneList")
def resolve_timezone_list(_, info):
    # Mirror Cloud's richer shape (raw IANA in `value`, a friendly city/region in
    # `label`, an offset-bearing search string in `query`), sorted by UTC offset
    # then name — the app's timezone picker relies on this for search + ordering.
    ref = datetime(2024, 1, 15)
    rows = []
    for tz in pytz.common_timezones:
        try:
            off = pytz.timezone(tz).utcoffset(ref) or timedelta(0)
        except Exception:
            off = timedelta(0)
        mins = int(off.total_seconds() // 60)
        sign = "+" if mins >= 0 else "-"
        hh, mm = divmod(abs(mins), 60)
        tag = f"{sign}{hh:02d}:{mm:02d}"
        city = tz.split("/")[-1].replace("_", " ")
        rows.append(
            {
                "value": tz,
                "label": city,
                "query": f"{tz} {city}, GMT{tag}, UTC{tag}",
                "_off": mins,
            }
        )
    rows.sort(key=lambda r: (r["_off"], r["value"]))
    for r in rows:
        r.pop("_off")
    return rows


# --- UserType computed fields ---------------------------------------------------------


@user_type.field("id")
def resolve_user_id(user, info):
    return str(user.id)


@user_type.field("avatarUrl")
def resolve_user_avatar_url(user, info):
    return user.avatar_url


@user_type.field("coverImageUrl")
def resolve_user_cover_image_url(user, info):
    return user.cover_image_url


# --- ProfileType id / FK fields (UUID -> str; FK -> *_id) ------------------------------


@profile_type.field("id")
def resolve_profile_id(profile, info):
    return str(profile.id)


# profile.isMobileOnboarded resolves via the smart fallback to the real
# profile.is_mobile_onboarded column (dynamic): the native app gates the home content
# (Recent activity + Stickies) on it, sets it true when the user finishes the mobile
# onboarding (updateProfileV2 / UpdateMobileOnboardedMutation persist the flag).


@profile_type.field("user")
def resolve_profile_user(profile, info):
    return str(profile.user_id)


@profile_type.field("lastWorkspaceId")
def resolve_profile_last_workspace_id(profile, info):
    return str(profile.last_workspace_id) if profile.last_workspace_id else None


# --- Query: workspaces ----------------------------------------------------------------


@query.field("workspaces")
def resolve_workspaces(_, info):
    user = _user(info)
    if user is None:
        return []
    members = (
        WorkspaceMember.objects.filter(member=user, is_active=True)
        .select_related("workspace")
        .order_by("workspace__name")
    )
    result = []
    for member in members:
        ws = member.workspace
        if ws.deleted_at is not None:
            continue
        ws._gql_role = member.role
        result.append(ws)
    return result


@workspace_type.field("role")
def resolve_workspace_role(ws, info):
    role = getattr(ws, "_gql_role", None)
    if role is not None:
        return role
    user = _user(info)
    if user is None:
        return None
    member = WorkspaceMember.objects.filter(workspace=ws, member=user, is_active=True).first()
    return member.role if member else None


@workspace_type.field("owner")
def resolve_workspace_owner(ws, info):
    return str(ws.owner_id)


@workspace_type.field("logoUrl")
def resolve_workspace_logo_url(ws, info):
    return ws.logo_url


# --- Query: per-workspace features / license / home counts ----------------------------


@query.field("workspaceFeatures")
def resolve_workspace_features(_, info, slug):
    return {
        "is_project_grouping_enabled": False,
        "is_initiative_enabled": False,
        "is_teams_enabled": False,
        "is_customer_enabled": False,
    }


@query.field("featureFlag")
def resolve_feature_flag(_, info, slug):
    return {flag: False for flag in FEATURE_FLAGS}


@query.field("workspaceLicense")
def resolve_workspace_license(_, info, slug):
    # Community / self-managed: no paid subscription, no trial banners.
    return {
        "is_cancelled": False,
        "purchased_seats": 0,
        "current_period_end_date": None,
        "interval": None,
        "product": "FREE",
        "is_offline_payment": False,
        "trial_end_date": None,
        "has_activated_free_trial": False,
        "has_added_payment_method": False,
        "subscription": None,
        "is_self_managed": True,
        "is_on_trial": False,
        "is_trial_allowed": False,
        "remaining_trial_days": 0,
        "has_upgraded": False,
        "show_payment_button": False,
        "show_trial_banner": False,
        "free_seats": 0,
        "occupied_seats": 0,
        "show_seats_banner": False,
        "current_period_start_date": None,
        "is_trial_ended": False,
        "billable_members": 0,
        "is_free_member_count_exceeded": False,
        "can_delete_workspace": True,
        "show_verification_failed_banner": False,
    }


@query.field("yourWork")
def resolve_your_work(_, info, slug):
    user = _user(info)
    if user is None:
        return {"projects": 0, "issues": 0, "pages": 0}
    try:
        project_ids = list(
            Project.objects.filter(
                workspace__slug=slug,
                project_projectmember__member=user,
                project_projectmember__is_active=True,
            )
            .distinct()
            .values_list("id", flat=True)
        )
        issues = (
            Issue.objects.filter(workspace__slug=slug, project_id__in=project_ids, assignees=user)
            .distinct()
            .count()
        )
        pages = Page.objects.filter(workspace__slug=slug, owned_by=user).count()
        return {"projects": len(project_ids), "issues": issues, "pages": pages}
    except Exception:
        return {"projects": 0, "issues": 0, "pages": 0}


# --- Home screen: catch-up feed / favorites / recents / notifications -----------------


def _entity_lite(entity_type, eid, fallback_name=None):
    """Resolve a favorited/visited entity id into the lite {id,name,logoProps,...} shape."""
    if not eid:
        return {"id": None, "name": fallback_name, "logo_props": {}, "is_epic": False, "workitem_identifier": None}
    et = entity_type or ""
    if et == "project":
        item = Project.objects.filter(id=eid).first()
        if item is not None:
            return {"id": str(item.id), "name": item.name, "logo_props": item.logo_props, "is_epic": False, "workitem_identifier": None}
    elif et in ("issue", "work_item"):
        item = Issue.objects.filter(id=eid).select_related("project", "type").first()
        if item is not None:
            identifier = f"{item.project.identifier}-{item.sequence_id}" if item.project_id else None
            is_epic = bool(item.type_id and item.type and item.type.is_epic)
            return {"id": str(item.id), "name": item.name, "logo_props": {}, "is_epic": is_epic, "workitem_identifier": identifier}
    elif et == "page":
        item = Page.objects.filter(id=eid).first()
        if item is not None:
            return {"id": str(item.id), "name": item.name or "Untitled", "logo_props": item.logo_props, "is_epic": False, "workitem_identifier": None}
    elif et == "cycle":
        item = Cycle.objects.filter(id=eid).first()
        if item is not None:
            return {"id": str(item.id), "name": item.name, "logo_props": item.logo_props, "is_epic": False, "workitem_identifier": None}
    elif et == "module":
        item = Module.objects.filter(id=eid).first()
        if item is not None:
            return {"id": str(item.id), "name": item.name, "logo_props": item.logo_props, "is_epic": False, "workitem_identifier": None}
    return {"id": None, "name": fallback_name, "logo_props": {}, "is_epic": False, "workitem_identifier": None}


def _catchup_activity_type(n):
    # CatchUpActivityTypeEnum is {COMMENT, ACTIVITY}. A notification triggered by a
    # comment activity has data.issue_activity.field == "comment"; everything else
    # (state/assignee/... changes, mentions) is a generic ACTIVITY. Returning the
    # raw Notification.entity_name ("issue") is not a valid enum member and crashes
    # the whole CatchUpQuery on serialization.
    field = ((n.data or {}).get("issue_activity") or {}).get("field")
    return "COMMENT" if field == "comment" else "ACTIVITY"


@query.field("catchUps")
def resolve_catch_ups(_, info, slug):
    # Home "catch-up" feed = the user's unread notifications, grouped by the
    # work item they belong to (the app shows one card per item with a count).
    user = _user(info)
    if user is None:
        return []
    notifs = list(
        Notification.objects.filter(
            workspace__slug=slug, receiver=user, read_at__isnull=True, archived_at__isnull=True
        ).order_by("created_at")
    )
    groups = {}
    for n in notifs:
        g = groups.get(n.entity_identifier)
        if g is None:
            g = groups[n.entity_identifier] = {
                "id": str(n.entity_identifier) if n.entity_identifier else None,
                "project_id": str(n.project_id) if n.project_id else None,
                "type": "WORK_ITEM",
                "count": 0,
                "_first": n,
                "_last": n,
            }
        g["count"] += 1
        g["_last"] = n
    out = []
    for g in groups.values():
        first, last = g.pop("_first"), g.pop("_last")
        work_item = None
        if g["id"]:
            item = Issue.objects.filter(id=g["id"]).select_related("project", "type").first()
            if item is not None:
                intake = IntakeIssue.objects.filter(issue_id=item.id).first()
                is_epic = bool(item.type_id and item.type and item.type.is_epic)
                # CatchUpTypeEnum is {WORK_ITEM, INTAKE, EPIC} (was hardcoded WORK_ITEM).
                g["type"] = "EPIC" if is_epic else ("INTAKE" if intake else "WORK_ITEM")
                work_item = {
                    "id": str(item.id),
                    "name": item.name,
                    "project_identifier": item.project.identifier if item.project_id else None,
                    "sequence_id": item.sequence_id,
                    "intake_id": str(intake.id) if intake else None,
                }
        g["work_item"] = work_item
        g["first_unread"] = {"id": str(first.id), "type": _catchup_activity_type(first)}
        g["last_unread"] = {"id": str(last.id), "type": _catchup_activity_type(last)}
        out.append(g)
    return out


def _favorites_qs(user, slug, limit):
    qs = UserFavorite.objects.filter(
        workspace__slug=slug, user=user, deleted_at__isnull=True
    ).order_by("sequence", "-created_at")
    return list(qs[:limit] if limit else qs[:50])


@query.field("userFavorites")
def resolve_user_favorites(_, info, slug, limit=None):
    user = _user(info)
    return _favorites_qs(user, slug, limit) if user is not None else []


@query.field("favorites")
def resolve_favorites(_, info, slug, limit=None):
    user = _user(info)
    return _favorites_qs(user, slug, limit) if user is not None else []


def _favorite_project_details(fav, info):
    # projectDetails is ProjectLiteType! (non-null): a project favorite IS the
    # project; for other entities use the parent project. Never return None.
    pid = fav.project_id or (fav.entity_identifier if fav.entity_type == "project" else None)
    if pid:
        project = Project.objects.filter(id=pid).first()
        if project is not None:
            return project
    return {"id": None, "name": fav.name, "identifier": None, "logo_props": {}}


def _favorite_entity_data(fav, info):
    return _entity_lite(fav.entity_type, fav.entity_identifier, fav.name)


for _ft in (user_favorite_type, favorite_type):
    _ft.set_field("projectDetails", _favorite_project_details)
    _ft.set_field("entityData", _favorite_entity_data)
    # workspace is typed Int! in the schema but unused by the app; keep it
    # non-null so requesting it never crashes the whole query.
    _ft.set_field("workspace", lambda obj, info: 0)


@query.field("userRecentVisit")
def resolve_user_recent_visit(_, info, slug, limit=None):
    user = _user(info)
    if user is None:
        return []
    qs = UserRecentVisit.objects.filter(workspace__slug=slug, user=user).order_by("-visited_at")
    return list(qs[:limit] if limit else qs[:20])


@recent_visit_type.field("projectDetails")
def resolve_recent_project_details(visit, info):
    return Project.objects.filter(id=visit.project_id).first() if visit.project_id else None


@recent_visit_type.field("entityData")
def resolve_recent_entity_data(visit, info):
    # Same lite shape as favorites; _entity_lite computes is_epic (issue.type.is_epic)
    # and real logo_props for page/cycle/module. Return null when the entity is gone.
    if not visit.entity_identifier:
        return None
    data = _entity_lite(visit.entity_name, visit.entity_identifier)
    return data if data.get("id") else None


@query.field("notificationCount")
def resolve_notification_count(_, info):
    user = _user(info)
    if user is None:
        return {"unread": 0, "mentioned": 0, "workspaces": []}
    base = Notification.objects.filter(
        receiver=user, read_at__isnull=True, archived_at__isnull=True
    ).select_related("workspace")
    workspaces = {}
    for n in base:
        w = n.workspace
        row = workspaces.get(w.id)
        if row is None:
            row = workspaces[w.id] = {"id": str(w.id), "slug": w.slug, "name": w.name, "unread": 0, "mentioned": 0}
        row["unread"] += 1
        if "mentioned" in (n.sender or ""):
            row["mentioned"] += 1
    total = sum(r["unread"] for r in workspaces.values())
    total_mentioned = sum(r["mentioned"] for r in workspaces.values())
    return {"unread": total, "mentioned": total_mentioned, "workspaces": list(workspaces.values())}


@query.field("stickies")
def resolve_stickies(_, info, slug, cursor=None):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    rows = list(
        Sticky.objects.filter(workspace__slug=slug, owner=user).order_by("sort_order", "-created_at")[:200]
    )
    return _page(rows, cursor)


# --- Workspace members / states (board scaffolding) -----------------------------------


@query.field("workspaceMembers")
def resolve_workspace_members(_, info, slug):
    user = _user(info)
    if user is None:
        return []
    return list(
        WorkspaceMember.objects.filter(workspace__slug=slug, is_active=True).select_related("member")
    )


@query.field("workspaceStates")
def resolve_workspace_states(_, info, slug):
    user = _user(info)
    if user is None:
        return []
    return list(State.objects.filter(workspace__slug=slug))


@query.field("workspaceLabels")
def resolve_workspace_labels(_, info, slug):
    # Workspace-wide labels (every project's labels). The project-scoped
    # ``labels(slug, project)`` variant exists; only this one was never bound,
    # so it silently returned [] via the safe-default engine.
    user = _user(info)
    if user is None:
        return []
    return list(Label.objects.filter(workspace__slug=slug))


# --- Projects (list) + ProjectType computed fields ------------------------------------


def _member_projects(user, slug):
    return (
        Project.objects.filter(
            workspace__slug=slug,
            project_projectmember__member=user,
            project_projectmember__is_active=True,
        )
        .distinct()
        .order_by("name")
    )


@query.field("projects")
def resolve_projects(_, info, slug, cursor=None, **kwargs):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    return _page(list(_member_projects(user, slug)), cursor)


@query.field("allProjects")
def resolve_all_projects(_, info, slug, type="all"):
    user = _user(info)
    if user is None:
        return []
    return list(_member_projects(user, slug))


@query.field("project")
def resolve_project(_, info, slug, project):
    user = _user(info)
    if user is None:
        return None
    item = Project.objects.filter(workspace__slug=slug, id=project).first()
    if item is not None:
        _track_visit(user, slug, item.id, "project", item.id)
    return item


@query.field("userProjectRoles")
def resolve_user_project_roles(_, info, slug, project):
    user = _user(info)
    role = None
    if user is not None:
        member = ProjectMember.objects.filter(project_id=project, member=user, is_active=True).first()
        role = member.role if member else None
    return {"project_id": str(project), "role": role, "project_role": role, "teamspace_role": None}


@query.field("projectFeatures")
def resolve_project_features(_, info, slug, project):
    p = Project.objects.filter(workspace__slug=slug, id=project).first()
    return {
        "module_view": p.module_view if p else False,
        "cycle_view": p.cycle_view if p else False,
        "issue_views_view": p.issue_views_view if p else False,
        "page_view": p.page_view if p else True,
        "intake_view": p.intake_view if p else False,
        "guest_view_all_features": p.guest_view_all_features if p else False,
        "is_project_updates_enabled": False,
        "is_epic_enabled": False,
        "is_workflow_enabled": False,
    }


@project_type.field("workspace")
def resolve_project_workspace(project, info):
    return str(project.workspace_id)


@project_type.field("isMember")
def resolve_project_is_member(project, info):
    user = _user(info)
    return bool(user) and ProjectMember.objects.filter(project=project, member=user, is_active=True).exists()


@project_type.field("isFavorite")
def resolve_project_is_favorite(project, info):
    user = _user(info)
    if user is None:
        return False
    return UserFavorite.objects.filter(
        user=user, entity_type="project", entity_identifier=project.id, deleted_at__isnull=True
    ).exists()


@project_type.field("role")
def resolve_project_role(project, info):
    user = _user(info)
    if user is None:
        return None
    member = ProjectMember.objects.filter(project=project, member=user, is_active=True).first()
    return member.role if member else None


@project_type.field("totalMembers")
def resolve_project_total_members(project, info):
    return ProjectMember.objects.filter(project=project, is_active=True).count()


@project_type.field("totalIssues")
def resolve_project_total_issues(project, info):
    return Issue.objects.filter(project=project).count()


@project_type.field("totalActiveIssues")
def resolve_project_total_active_issues(project, info):
    return Issue.objects.filter(project=project).exclude(state__group__in=_DONE_GROUPS).count()


# --- Work items (board) ---------------------------------------------------------------


def _paged(results, cursor=None):
    return _page(results, cursor)


def _apply_issue_filters(qs, filters):
    # The board fetches one query per column with {state:[id]}; honour the active
    # filter keys so issues land in the right column (not under every column).
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


def _ordered_issues(qs, order_by):
    return list(qs.order_by(order_by if isinstance(order_by, str) and order_by else "-created_at")[:500])


@query.field("workspaceIssues")
def resolve_workspace_issues(_, info, slug, orderBy="-created_at", filters=None, cursor=None, **kwargs):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = Issue.objects.filter(
        workspace__slug=slug,
        project__project_projectmember__member=user,
        project__project_projectmember__is_active=True,
    ).distinct()
    qs = _apply_issue_filters(qs, filters)
    return _paged(_ordered_issues(qs, orderBy), cursor)


@query.field("issues")
def resolve_issues(_, info, slug, project, orderBy="-created_at", filters=None, cursor=None, **kwargs):
    qs = Issue.objects.filter(workspace__slug=slug, project_id=project)
    qs = _apply_issue_filters(qs, filters)
    return _paged(_ordered_issues(qs, orderBy), cursor)


@query.field("issue")
def resolve_issue(_, info, slug, project, issue):
    item = Issue.objects.filter(workspace__slug=slug, project_id=project, id=issue).first()
    if item is not None:
        _track_visit(_user(info), slug, item.project_id, "issue", item.id)
    return item


@query.field("issuePropertyActivities")
def resolve_issue_property_activities(_, info, slug, project, issue):
    return list(
        IssueActivity.objects.filter(issue_id=issue, project_id=project)
        .select_related("actor")
        .order_by("created_at")
    )


@issue_activity_type.field("attachments")
def resolve_activity_attachments(activity, info):
    return []


@issue_activity_type.field("actorDetails")
def resolve_activity_actor_details(activity, info):
    return activity.actor


@query.field("labels")
def resolve_labels(_, info, slug, project):
    return list(Label.objects.filter(workspace__slug=slug, project_id=project))


@query.field("states")
def resolve_states(_, info, slug, project):
    return list(State.objects.filter(workspace__slug=slug, project_id=project))


@query.field("projectMembers")
def resolve_project_members(_, info, slug, project):
    return list(
        ProjectMember.objects.filter(project_id=project, is_active=True).select_related("member")
    )


@query.field("cycles")
def resolve_cycles(_, info, slug, project, ids=None):
    qs = Cycle.objects.filter(workspace__slug=slug, project_id=project)
    if ids:
        qs = qs.filter(id__in=ids)
    return list(qs)


@query.field("modules")
def resolve_modules(_, info, slug, project, cursor=None, **kwargs):
    return _paged(list(Module.objects.filter(workspace__slug=slug, project_id=project)), cursor)


# IssuesType: many-to-many id lists, through-model ids and computed fields. Plain
# FK-as-id fields (state, project, workspace, parent, type, estimatePoint) are handled
# by the smart resolver.


@issues_type.field("assignees")
def resolve_issue_assignees(issue, info):
    return [str(pk) for pk in issue.assignees.values_list("id", flat=True)]


@issues_type.field("labels")
def resolve_issue_labels(issue, info):
    return [str(pk) for pk in issue.labels.values_list("id", flat=True)]


@issues_type.field("modules")
def resolve_issue_modules(issue, info):
    return [str(pk) for pk in ModuleIssue.objects.filter(issue=issue).values_list("module_id", flat=True)]


@issues_type.field("cycle")
def resolve_issue_cycle(issue, info):
    link = CycleIssue.objects.filter(issue=issue).first()
    return str(link.cycle_id) if link else None


@issues_type.field("description")
def resolve_issue_description(issue, info):
    return None


@issues_type.field("descriptionJson")
def resolve_issue_description_json(issue, info):
    # The work-item editor renders from this rich-text JSON; the column exists,
    # so returning None left every work-item body blank in the detail view.
    return issue.description_json


@issues_type.field("projectIdentifier")
def resolve_issue_project_identifier(issue, info):
    return issue.project.identifier if issue.project_id else None


@issues_type.field("isEpic")
def resolve_issue_is_epic(issue, info):
    return bool(issue.type_id and issue.type and issue.type.is_epic)


@issues_type.field("parentProjectId")
def resolve_issue_parent_project_id(issue, info):
    return str(issue.parent.project_id) if issue.parent_id else None


@issues_type.field("parentProjectIdentifier")
def resolve_issue_parent_project_identifier(issue, info):
    return issue.parent.project.identifier if issue.parent_id else None


@issues_type.field("parentIsEpic")
def resolve_issue_parent_is_epic(issue, info):
    if not issue.parent_id:
        return False
    parent = issue.parent
    return bool(parent and parent.type_id and parent.type and parent.type.is_epic)


@issues_type.field("analytics")
def resolve_issue_analytics(issue, info):
    return {"backlog": 0, "unstarted": 0, "started": 0, "completed": 0, "cancelled": 0}


# --- Notifications / search / initiatives count ---------------------------------------


@query.field("notifications")
def resolve_notifications(
    _, info, slug, cursor=None, read=None, type=None, snoozed=False, archived=False, mentioned=None, **kwargs
):
    """Notification inbox — mirrors the REST list view (plane/app/views/notification/base.py)
    so the app's inbox matches the web. Was a stub returning an empty page, which is
    why the app showed no notifications while the web did."""
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = (
        Notification.objects.filter(workspace__slug=slug, receiver=user, entity_name="issue")
        .select_related("workspace", "project", "triggered_by")
        .annotate(
            is_mentioned_notification=Case(
                When(sender__icontains="mentioned", then=True),
                default=False,
                output_field=BooleanField(),
            )
        )
        .order_by("snoozed_till", "-created_at")
    )
    if snoozed:
        qs = qs.filter(Q(snoozed_till__lt=timezone.now()) | Q(snoozed_till__isnull=False))
    else:
        qs = qs.filter(Q(snoozed_till__gte=timezone.now()) | Q(snoozed_till__isnull=True))
    qs = qs.filter(archived_at__isnull=False) if archived else qs.filter(archived_at__isnull=True)
    if read == "false":
        qs = qs.filter(read_at__isnull=True)
    elif read == "true":
        qs = qs.filter(read_at__isnull=False)
    # mentioned defaults to excluding mention notifications (separate "mentioned" tab).
    if mentioned:
        qs = qs.filter(sender__icontains="mentioned")
    else:
        qs = qs.exclude(sender__icontains="mentioned")
    types = type if isinstance(type, list) else [t for t in str(type or "all").split(",") if t]
    q_filters = Q()
    if "subscribed" in types:
        sub_ids = (
            IssueSubscriber.objects.filter(workspace__slug=slug, subscriber_id=user.id)
            .annotate(created=Exists(Issue.objects.filter(created_by=user, pk=OuterRef("issue_id"))))
            .annotate(assigned=Exists(IssueAssignee.objects.filter(pk=OuterRef("issue_id"), assignee=user)))
            .filter(created=False, assigned=False)
            .values_list("issue_id", flat=True)
        )
        q_filters |= Q(entity_identifier__in=sub_ids)
    if "assigned" in types:
        a_ids = IssueAssignee.objects.filter(workspace__slug=slug, assignee_id=user.id).values_list(
            "issue_id", flat=True
        )
        q_filters |= Q(entity_identifier__in=a_ids)
    if "created" in types:
        if WorkspaceMember.objects.filter(
            workspace__slug=slug, member=user, role__lt=15, is_active=True
        ).exists():
            return _page([], cursor)
        c_ids = Issue.objects.filter(workspace__slug=slug, created_by=user).values_list("pk", flat=True)
        q_filters |= Q(entity_identifier__in=c_ids)
    return _page(list(qs.filter(q_filters)), cursor)


# NotificationType has non-null isIntakeIssue/isEpic (Boolean!) and data (JSON!) that are
# NOT columns on the Notification model (the REST list annotates them per-row). Without
# these the smart fallback returns None and the whole NotificationsQuery fails the non-null
# check — the app's notification list then shows a load error.
@notification_type.field("data")
def resolve_notification_data(n, info):
    return n.data if n.data is not None else {}


@notification_type.field("isIntakeIssue")
def resolve_notification_is_intake_issue(n, info):
    return bool(n.entity_identifier) and IntakeIssue.objects.filter(issue_id=n.entity_identifier).exists()


@notification_type.field("isEpic")
def resolve_notification_is_epic(n, info):
    if not n.entity_identifier:
        return False
    issue = Issue.objects.filter(id=n.entity_identifier).select_related("type").first()
    return bool(issue and issue.type_id and issue.type and issue.type.is_epic)


@notification_type.field("intakeId")
def resolve_notification_intake_id(n, info):
    if not n.entity_identifier:
        return None
    intake = IntakeIssue.objects.filter(issue_id=n.entity_identifier).first()
    return str(intake.id) if intake else None


@query.field("globalSearch")
def resolve_global_search(_, info, slug, query=None, **kwargs):
    empty = {"projects": [], "issues": [], "modules": [], "cycles": [], "pages": [], "epics": []}
    user = _user(info)
    term = (query or "").strip()
    if user is None or not term:
        return empty
    proj_qs = _member_projects(user, slug)
    project_ids = list(proj_qs.values_list("id", flat=True))
    return {
        "projects": list(proj_qs.filter(Q(name__icontains=term) | Q(identifier__icontains=term))[:20]),
        "issues": list(
            Issue.objects.filter(project_id__in=project_ids, name__icontains=term).order_by("-created_at")[:20]
        ),
        "cycles": list(Cycle.objects.filter(project_id__in=project_ids, name__icontains=term)[:20]),
        "modules": list(Module.objects.filter(project_id__in=project_ids, name__icontains=term)[:20]),
        "pages": [],
        "epics": [],
    }


@query.field("issueStats")
def resolve_issue_stats(_, info, slug, project, issue):
    return {
        "attachments": FileAsset.objects.filter(issue_id=issue, is_deleted=False).count(),
        "relations": IssueRelation.objects.filter(Q(issue_id=issue) | Q(related_issue_id=issue)).count(),
        "sub_work_items": Issue.objects.filter(parent_id=issue).count(),
        "links": IssueLink.objects.filter(issue_id=issue).count(),
        "pages": 0,
    }


@query.field("issueCommentActivities")
def resolve_issue_comment_activities(_, info, slug, project, issue):
    # Read side of comments. The write side (addIssueComment/replies/reactions)
    # was implemented but this was never bound, so the app could post comments
    # yet never read them back.
    user = _user(info)
    if user is None:
        return []
    return list(
        IssueComment.objects.filter(project_id=project, issue_id=issue)
        .select_related("actor")
        .order_by("created_at")
    )


@issue_comment_type.field("actorDetails")
def resolve_comment_actor_details(comment, info):
    return comment.actor


@issue_comment_type.field("attachments")
def resolve_comment_attachments(comment, info):
    # No attachments column on IssueComment; the field is [String!]! so it must
    # never be None.
    return []


@query.field("issueLink")
def resolve_issue_link(_, info, slug, project, issue):
    user = _user(info)
    if user is None:
        return []
    return list(IssueLink.objects.filter(project_id=project, issue_id=issue).order_by("created_at"))


@query.field("subIssues")
def resolve_sub_issues(_, info, slug, project, issue, cursor=None):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    rows = list(
        Issue.objects.filter(parent_id=issue, project_id=project)
        .select_related("project")
        .order_by("sequence_id")[:200]
    )
    return _page(rows, cursor)


@query.field("initiativesCount")
def resolve_initiatives_count(_, info, slug):
    return {"total_count": 0, "total_count_by_lead": 0}


@query.field("issueUserProperties")
def resolve_issue_user_properties(_, info, slug, project):
    # Per-user view config for a project's work items. We have no stored prefs for
    # the mobile app yet, so return sane defaults (non-null ids + empty JSON props).
    user = _user(info)
    workspace_id = Workspace.objects.filter(slug=slug).values_list("id", flat=True).first()
    return {
        "id": str(project),
        "user": str(user.id) if user is not None else "",
        "project": str(project),
        "workspace": str(workspace_id) if workspace_id else "",
        "filters": {},
        "display_filters": {"layout": "list", "group_by": "state", "order_by": "-created_at"},
        "display_properties": {},
    }


# --- Mutations: profile / last workspace ----------------------------------------------


@mutation.field("updateProfile")
def resolve_update_profile(_, info, mobileTimezoneAutoSet=False):
    user = _user(info)
    if user is None:
        return None
    profile, _created = Profile.objects.get_or_create(user=user)
    profile.mobile_timezone_auto_set = mobileTimezoneAutoSet
    profile.save(update_fields=["mobile_timezone_auto_set"])
    return profile


@mutation.field("deviceInformation")
def resolve_device_information(_, info, deviceId, deviceType, pushToken, isActive=True):
    user = _user(info)
    if user is None:
        return {
            "user": "",
            "device_id": deviceId,
            "device_type": deviceType,
            "push_token": pushToken,
            "is_active": isActive,
        }
    # Persist (upsert) the device so userInformation.deviceInfo stops being null on
    # the next cold start — that is what tells the app the device is already synced
    # and keeps it from wiping the cached home dashboard.
    device, _created = Device.objects.update_or_create(
        user=user,
        device_id=deviceId,
        defaults={
            "device_type": deviceType,
            "push_token": pushToken,
            "is_active": isActive,
        },
    )
    return _device_info_payload(device)


@mutation.field("updateLastWorkspace")
def resolve_update_last_workspace(_, info, workspace):
    user = _user(info)
    if user is None:
        return False
    profile, _created = Profile.objects.get_or_create(user=user)
    profile.last_workspace_id = workspace
    profile.save(update_fields=["last_workspace_id"])
    return True


RESOLVERS = [
    query,
    mutation,
    workspace_type,
    user_type,
    profile_type,
    project_type,
    issues_type,
    issue_activity_type,
    recent_visit_type,
    user_favorite_type,
    favorite_type,
    issue_comment_type,
    notification_type,
]
