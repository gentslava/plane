# OVERLAY: mobile-graphql — mutations for the workspace / project / sticky /
# favorites / user domain.
#
# Conventions (see overlay/features/mobile-graphql/SUBAGENT-BRIEF.md):
#   * ariadne MutationType; field names match the Cloud SDL exactly (camelCase).
#   * Object-input args arrive as dicts keyed by camelCase
#     (e.g. stickyData.get("descriptionHtml")).
#   * Return the Django model instance for object types — the smart fallback
#     resolver serialises every field (camelCase -> snake_case, FK -> *_id).
#   * Boolean / delete mutations return True/False.
#
# updateProfile, updateLastWorkspace and deviceInformation already live in
# resolvers.py — they are intentionally NOT redefined here.
from ariadne import MutationType

from django.db import IntegrityError

from plane.db.models import (
    DEFAULT_STATES,
    Notification,
    Profile,
    Project,
    ProjectMember,
    ProjectMemberInvite,
    ProjectUserProperty,
    State,
    Sticky,
    UserFavorite,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberInvite,
)

mutation = MutationType()

# Project / workspace admin role.
_ADMIN_ROLE = 20
_MEMBER_ROLE = 15


def _user(info):
    user = getattr(info.context, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def _workspace(slug):
    return Workspace.objects.filter(slug=slug, deleted_at__isnull=True).first()


# --- Stickies -------------------------------------------------------------------------


@mutation.field("createSticky")
def resolve_create_sticky(_, info, slug, stickyData):
    user = _user(info)
    if user is None:
        return None
    workspace = _workspace(slug)
    if workspace is None:
        return None
    data = stickyData or {}
    sticky = Sticky.objects.create(
        workspace=workspace,
        owner=user,
        name=data.get("name") or "",
        description_html=data.get("descriptionHtml") or "<p></p>",
        background_color=data.get("backgroundColor") or "",
        created_by=user,
        updated_by=user,
    )
    return sticky


@mutation.field("updateSticky")
def resolve_update_sticky(_, info, slug, sticky, stickyData):
    user = _user(info)
    if user is None:
        return None
    instance = Sticky.objects.filter(id=sticky, workspace__slug=slug, owner=user).first()
    if instance is None:
        return None
    data = stickyData or {}
    update_fields = []
    if "name" in data and data.get("name") is not None:
        instance.name = data.get("name")
        update_fields.append("name")
    if "descriptionHtml" in data and data.get("descriptionHtml") is not None:
        instance.description_html = data.get("descriptionHtml")
        update_fields.append("description_html")
    if "backgroundColor" in data and data.get("backgroundColor") is not None:
        instance.background_color = data.get("backgroundColor")
        update_fields.append("background_color")
    instance.updated_by = user
    update_fields.append("updated_by")
    # description_stripped is recomputed in Sticky.save(); let it run fully.
    instance.save()
    return instance


@mutation.field("deleteStickies")
def resolve_delete_stickies(_, info, slug, stickies):
    user = _user(info)
    if user is None:
        return False
    Sticky.objects.filter(id__in=stickies, workspace__slug=slug, owner=user).delete()
    return True


# --- Projects -------------------------------------------------------------------------


@mutation.field("createProject")
def resolve_create_project(
    _,
    info,
    slug,
    name,
    identifier,
    description="",
    network=2,
    coverImage=None,
    projectLead=None,
    logoProps=None,
    pageView=True,
    moduleView=True,
    cycleView=True,
    issueViewsView=True,
):
    user = _user(info)
    if user is None:
        return None
    workspace = _workspace(slug)
    if workspace is None:
        return None

    project = Project.objects.create(
        workspace=workspace,
        name=name,
        identifier=(identifier or "").upper(),
        description=description or "",
        network=network if network is not None else 2,
        cover_image=coverImage,
        project_lead_id=projectLead,
        logo_props=logoProps or {},
        page_view=pageView,
        module_view=moduleView,
        cycle_view=cycleView,
        issue_views_view=issueViewsView,
        created_by=user,
        updated_by=user,
    )

    # Default workflow states (mirrors app/views/project/base.py).
    State.objects.bulk_create(
        [
            State(
                name=state["name"],
                color=state["color"],
                project=project,
                sequence=state["sequence"],
                workspace=workspace,
                group=state["group"],
                default=state.get("default", False),
                created_by=user,
                updated_by=user,
            )
            for state in DEFAULT_STATES
        ]
    )

    # Creator becomes a project admin.
    ProjectMember.objects.create(
        project=project,
        member=user,
        workspace=workspace,
        role=_ADMIN_ROLE,
        is_active=True,
        created_by=user,
        updated_by=user,
    )

    # Optionally add the nominated lead as admin.
    if projectLead and str(projectLead) != str(user.id):
        ProjectMember.objects.get_or_create(
            project=project,
            member_id=projectLead,
            workspace=workspace,
            defaults={
                "role": _ADMIN_ROLE,
                "is_active": True,
                "created_by": user,
                "updated_by": user,
            },
        )

    return project


@mutation.field("updateProject")
def resolve_update_project(
    _,
    info,
    id,
    slug,
    name=None,
    identifier=None,
    description=None,
    network=None,
    logoProps=None,
    pageView=None,
    moduleView=None,
    cycleView=None,
    issueViewsView=None,
    coverImage=None,
):
    user = _user(info)
    if user is None:
        return None
    project = Project.objects.filter(id=id, workspace__slug=slug).first()
    if project is None:
        return None

    update_fields = []

    def _set(attr, value):
        setattr(project, attr, value)
        update_fields.append(attr)

    if name is not None:
        _set("name", name)
    if identifier is not None:
        _set("identifier", identifier.upper())
    if description is not None:
        _set("description", description)
    if network is not None:
        _set("network", network)
    if logoProps is not None:
        _set("logo_props", logoProps)
    if pageView is not None:
        _set("page_view", pageView)
    if moduleView is not None:
        _set("module_view", moduleView)
    if cycleView is not None:
        _set("cycle_view", cycleView)
    if issueViewsView is not None:
        _set("issue_views_view", issueViewsView)
    if coverImage is not None:
        _set("cover_image", coverImage)

    if update_fields:
        project.updated_by = user
        update_fields.append("updated_by")
        project.save(update_fields=update_fields)
    return project


@mutation.field("deleteProject")
def resolve_delete_project(_, info, id):
    user = _user(info)
    if user is None:
        return False
    project = Project.objects.filter(id=id).first()
    if project is None:
        return False
    project.delete()
    return True


@mutation.field("joinProject")
def resolve_join_project(_, info, slug, project):
    user = _user(info)
    if user is None:
        return False
    target = Project.objects.filter(id=project, workspace__slug=slug).first()
    if target is None:
        return False
    member, created = ProjectMember.objects.get_or_create(
        project=target,
        member=user,
        workspace=target.workspace,
        defaults={
            "role": _MEMBER_ROLE,
            "is_active": True,
            "created_by": user,
            "updated_by": user,
        },
    )
    if not created and not member.is_active:
        member.is_active = True
        member.save(update_fields=["is_active"])
    return True


@mutation.field("inviteProjectMembers")
def resolve_invite_project_members(_, info, slug, project, emails):
    user = _user(info)
    if user is None:
        return False
    target = Project.objects.filter(id=project, workspace__slug=slug).first()
    if target is None:
        return False
    # `emails` is JSON: either ["a@b.com"] or [{"email": ..., "role": ...}].
    if not isinstance(emails, (list, tuple)):
        return False
    for entry in emails:
        if isinstance(entry, dict):
            email = entry.get("email")
            role = entry.get("role", _MEMBER_ROLE)
        else:
            email = entry
            role = _MEMBER_ROLE
        if not email:
            continue
        try:
            ProjectMemberInvite.objects.get_or_create(
                project=target,
                workspace=target.workspace,
                email=email,
                defaults={
                    "role": role,
                    "created_by": user,
                    "updated_by": user,
                },
            )
        except IntegrityError:
            continue
    return True


# --- Workspaces -----------------------------------------------------------------------


@mutation.field("createWorkspace")
def resolve_create_workspace(_, info, workspaceInput):
    user = _user(info)
    if user is None:
        return None
    data = workspaceInput or {}
    name = data.get("name")
    slug = data.get("slug")
    if not name or not slug:
        return None
    try:
        workspace = Workspace.objects.create(
            name=name,
            slug=slug,
            owner=user,
            organization_size=data.get("organizationSize"),
            created_by=user,
            updated_by=user,
        )
    except IntegrityError:
        return None
    # Creator joins as workspace admin.
    WorkspaceMember.objects.create(
        workspace=workspace,
        member=user,
        role=_ADMIN_ROLE,
        is_active=True,
        created_by=user,
        updated_by=user,
    )
    workspace._gql_role = _ADMIN_ROLE
    return workspace


@mutation.field("updateWorkspace")
def resolve_update_workspace(_, info, slug, workspaceInput):
    user = _user(info)
    if user is None:
        return None
    workspace = _workspace(slug)
    if workspace is None:
        return None
    data = workspaceInput or {}
    update_fields = []
    if data.get("name") is not None:
        workspace.name = data.get("name")
        update_fields.append("name")
    if data.get("organizationSize") is not None:
        workspace.organization_size = data.get("organizationSize")
        update_fields.append("organization_size")
    if update_fields:
        workspace.updated_by = user
        update_fields.append("updated_by")
        workspace.save(update_fields=update_fields)
    return workspace


@mutation.field("workspaceSlugCheck")
def resolve_workspace_slug_check(_, info, workspaceSlugInput):
    data = workspaceSlugInput or {}
    slug = data.get("slug")
    if not slug:
        return False
    # Returns True when the slug is available (not already taken).
    return not Workspace.objects.filter(slug=slug).exists()


# --- Favorites ------------------------------------------------------------------------


def _add_favorite(info, slug, entity_type, entity_identifier, project_id=None):
    user = _user(info)
    if user is None:
        return False
    workspace = _workspace(slug)
    if workspace is None:
        return False
    existing = UserFavorite.objects.filter(
        workspace=workspace,
        user=user,
        entity_type=entity_type,
        entity_identifier=entity_identifier,
    ).first()
    if existing is not None:
        return True
    try:
        UserFavorite.objects.create(
            workspace=workspace,
            user=user,
            entity_type=entity_type,
            entity_identifier=entity_identifier,
            project_id=project_id,
            created_by=user,
            updated_by=user,
        )
    except IntegrityError:
        return True
    return True


def _remove_favorite(info, slug, entity_type, entity_identifier):
    user = _user(info)
    if user is None:
        return False
    UserFavorite.objects.filter(
        workspace__slug=slug,
        user=user,
        entity_type=entity_type,
        entity_identifier=entity_identifier,
    ).delete()
    return True


@mutation.field("favoriteProject")
def resolve_favorite_project(_, info, slug, project):
    return _add_favorite(info, slug, "project", project, project_id=project)


@mutation.field("unFavoriteProject")
def resolve_unfavorite_project(_, info, slug, project):
    return _remove_favorite(info, slug, "project", project)


@mutation.field("favoriteCycle")
def resolve_favorite_cycle(_, info, slug, project, cycle):
    return _add_favorite(info, slug, "cycle", cycle, project_id=project)


@mutation.field("unFavoriteCycle")
def resolve_unfavorite_cycle(_, info, slug, project, cycle):
    return _remove_favorite(info, slug, "cycle", cycle)


@mutation.field("favoriteModule")
def resolve_favorite_module(_, info, slug, project, module):
    return _add_favorite(info, slug, "module", module, project_id=project)


@mutation.field("unFavoriteModule")
def resolve_unfavorite_module(_, info, slug, project, module):
    return _remove_favorite(info, slug, "module", module)


@mutation.field("favoritePage")
def resolve_favorite_page(_, info, slug, project, page):
    return _add_favorite(info, slug, "page", page, project_id=project)


@mutation.field("unFavoritePage")
def resolve_unfavorite_page(_, info, slug, project, page):
    return _remove_favorite(info, slug, "page", page)


@mutation.field("createUserFavorite")
def resolve_create_user_favorite(_, info, slug, entityIdentifier, entityType, project=None):
    return _add_favorite(info, slug, entityType, entityIdentifier, project_id=project)


@mutation.field("deleteUserFavorite")
def resolve_delete_user_favorite(_, info, slug, favorite):
    user = _user(info)
    if user is None:
        return False
    UserFavorite.objects.filter(id=favorite, workspace__slug=slug, user=user).delete()
    return True


# --- User / profile -------------------------------------------------------------------


@mutation.field("completeTour")
def resolve_complete_tour(_, info, input):
    user = _user(info)
    if user is None:
        return {"home_tour": False}
    data = input or {}
    home_tour = data.get("homeTour")
    profile, _created = Profile.objects.get_or_create(user=user)
    if home_tour is not None:
        profile.is_tour_completed = bool(home_tour)
        product_tour = profile.product_tour or {}
        if isinstance(product_tour, dict):
            product_tour["home"] = bool(home_tour)
            profile.product_tour = product_tour
        profile.save(update_fields=["is_tour_completed", "product_tour"])
    return {"home_tour": bool(profile.is_tour_completed)}


@mutation.field("setPassword")
def resolve_set_password(_, info, passwordInput):
    user = _user(info)
    if user is None:
        return False
    data = passwordInput or {}
    password = data.get("password")
    if not password:
        return False
    user.set_password(password)
    user.is_password_autoset = False
    user.save(update_fields=["password", "is_password_autoset"])
    return True


@mutation.field("updateUser")
def resolve_update_user(
    _,
    info,
    firstName=None,
    lastName=None,
    displayName=None,
    userTimezone=None,
    coverImage=None,
):
    user = _user(info)
    if user is None:
        return None
    update_fields = []
    if firstName is not None:
        user.first_name = firstName
        update_fields.append("first_name")
    if lastName is not None:
        user.last_name = lastName
        update_fields.append("last_name")
    if displayName is not None:
        user.display_name = displayName
        update_fields.append("display_name")
    if userTimezone is not None:
        user.user_timezone = userTimezone
        update_fields.append("user_timezone")
    if coverImage is not None:
        user.cover_image = coverImage
        update_fields.append("cover_image")
    if update_fields:
        user.save(update_fields=update_fields)
    return user


@mutation.field("updateProfileV2")
def resolve_update_profile_v2(_, info, profileInput):
    user = _user(info)
    if user is None:
        return None
    profile, _created = Profile.objects.get_or_create(user=user)
    data = profileInput or {}
    update_fields = []
    if data.get("mobileTimezoneAutoSet") is not None:
        profile.mobile_timezone_auto_set = bool(data.get("mobileTimezoneAutoSet"))
        update_fields.append("mobile_timezone_auto_set")
    if data.get("isMobileOnboarded") is not None:
        profile.is_mobile_onboarded = bool(data.get("isMobileOnboarded"))
        update_fields.append("is_mobile_onboarded")
    if data.get("mobileOnboardingStep") is not None:
        profile.mobile_onboarding_step = data.get("mobileOnboardingStep")
        update_fields.append("mobile_onboarding_step")
    if update_fields:
        profile.save(update_fields=update_fields)
    return profile


@mutation.field("updateUserProperties")
def resolve_update_user_properties(_, info, slug, project, filters, displayFilters, displayProperties):
    user = _user(info)
    if user is None:
        return None
    workspace = _workspace(slug)
    if workspace is None:
        return None
    prop, _created = ProjectUserProperty.objects.get_or_create(
        workspace=workspace,
        project_id=project,
        user=user,
        defaults={"created_by": user, "updated_by": user},
    )
    prop.filters = filters if filters is not None else {}
    prop.display_filters = displayFilters if displayFilters is not None else {}
    prop.display_properties = displayProperties if displayProperties is not None else {}
    prop.updated_by = user
    prop.save(update_fields=["filters", "display_filters", "display_properties", "updated_by"])
    # IssueUserPropertyType shape: id/user/project/workspace + the three JSON blobs.
    return {
        "id": str(prop.id),
        "user": str(user.id),
        "project": str(project),
        "workspace": str(workspace.id),
        "filters": prop.filters,
        "display_filters": prop.display_filters,
        "display_properties": prop.display_properties,
    }


# --- Notifications --------------------------------------------------------------------


@mutation.field("readNotification")
def resolve_read_notification(_, info, slug, notification):
    from django.utils import timezone

    user = _user(info)
    if user is None:
        return False
    instance = Notification.objects.filter(
        id=notification, workspace__slug=slug, receiver=user
    ).first()
    if instance is None:
        return False
    if instance.read_at is None:
        instance.read_at = timezone.now()
        instance.save(update_fields=["read_at"])
    return True


@mutation.field("markAllReadNotification")
def resolve_mark_all_read_notification(_, info, slug):
    from django.utils import timezone

    user = _user(info)
    if user is None:
        return False
    Notification.objects.filter(
        workspace__slug=slug, receiver=user, read_at__isnull=True
    ).update(read_at=timezone.now())
    return True


@mutation.field("catchUpMarkAsRead")
def resolve_catch_up_mark_as_read(_, info, slug, typeId, type="WORK_ITEM"):
    from django.utils import timezone

    user = _user(info)
    if user is None:
        return False
    # Catch-up entries map onto notifications keyed by the source entity id.
    Notification.objects.filter(
        workspace__slug=slug,
        receiver=user,
        entity_identifier=typeId,
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    return True


@mutation.field("catchUpMarkAllAsRead")
def resolve_catch_up_mark_all_as_read(_, info, slug):
    from django.utils import timezone

    user = _user(info)
    if user is None:
        return False
    Notification.objects.filter(
        workspace__slug=slug, receiver=user, read_at__isnull=True
    ).update(read_at=timezone.now())
    return True


# --- Workspace invites ----------------------------------------------------------------


@mutation.field("joinUserWorkspaceInvites")
def resolve_join_user_workspace_invites(_, info, invitationIds):
    from django.utils import timezone

    user = _user(info)
    if user is None:
        return False
    invites = WorkspaceMemberInvite.objects.filter(
        id__in=invitationIds, email=user.email
    )
    for invite in invites:
        member, created = WorkspaceMember.objects.get_or_create(
            workspace=invite.workspace,
            member=user,
            defaults={
                "role": invite.role,
                "is_active": True,
                "created_by": user,
                "updated_by": user,
            },
        )
        if not created and not member.is_active:
            member.is_active = True
            member.save(update_fields=["is_active"])
        invite.accepted = True
        invite.responded_at = timezone.now()
        invite.save(update_fields=["accepted", "responded_at"])
    return True


BINDABLES = [mutation]
