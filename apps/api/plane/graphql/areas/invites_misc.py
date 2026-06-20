# OVERLAY: mobile-graphql — area "invites_misc".
#
# Self-contained ariadne resolvers for the native app's instance/onboarding-cover
# and workspace-invitation flows, replicating the existing Plane REST behaviour so
# the self-hosted GraphQL gateway answers these screens the same way Cloud does.
#
# Query  fields : instance, unsplashImages, projectCovers, isProjectPublic,
#                 teamspaceMembersByProject, publicUserWorkspaceInvite (V2),
#                 userWorkspaceInvite, userWorkspaceInvites, userDelete
# Mutation fields: acceptPublicWorkspaceInvite (V2), userDelete
#
# REST ground truth:
#   * instance                  -> plane/license/api/views/instance.py (InstanceEndpoint)
#   * unsplashImages            -> plane/app/views/external/base.py (UnsplashEndpoint)
#   * userWorkspaceInvite(s)    -> plane/app/views/workspace/invite.py
#                                  (UserWorkspaceInvitationsViewSet / WorkspaceJoinEndpoint)
#   * acceptPublicWorkspaceInvite -> WorkspaceJoinEndpoint.post (accept branch)
#   * userDelete (Query+Mutation) -> plane/app/views/user/base.py UserEndpoint.deactivate
#
# Field-level resolution is camelCase->snake_case via the global smart-fallback
# resolver (see schema.py); only computed / FK-as-object / list-of-id fields get an
# explicit resolver here.
import os

import requests

from ariadne import MutationType, ObjectType, QueryType

from django.contrib.auth import logout
from django.db.models import Case, Count, IntegerField, Q, When
from django.utils import timezone

from plane.db.models import (
    DeployBoard,
    Profile,
    Project,
    ProjectMember,
    Workspace,
    WorkspaceMember,
    WorkspaceMemberInvite,
)
from plane.graphql.resolvers import _user

query = QueryType()
mutation = MutationType()

instance_type = ObjectType("InstanceType")
workspace_invite_type = ObjectType("WorkspaceInviteType")
user_delete_type = ObjectType("UserDeleteType")
project_public_lite_type = ObjectType("ProjectPublicLiteType")


# WorkspaceMemberInvite.role is a smallint (5/15/20); the SDL exposes it as a
# String. Mirror the model's ROLE_CHOICES labels so the app shows "Admin"/...
_ROLE_LABELS = {20: "Admin", 15: "Member", 5: "Guest"}


def _instance_value(key, default=None):
    """Best-effort read of an instance configuration value without importing the
    license helper at module load (kept resilient if the table is empty)."""
    try:
        from plane.license.utils.instance_value import get_configuration_value

        (value,) = get_configuration_value([{"key": key, "default": default}])
        return value
    except Exception:
        return default


# --- Query: instance ------------------------------------------------------------------


@query.field("instance")
def resolve_instance(_, info):
    # InstanceType is non-null with non-null instanceName/currentVersion/latestVersion.
    # Mirror InstanceEndpoint: read the single Instance row; fall back to safe
    # non-null defaults when the instance is not yet set up (table empty).
    try:
        from plane.license.models import Instance

        instance = Instance.objects.first()
    except Exception:
        instance = None
    if instance is None:
        return {"instance_name": "Plane", "current_version": "0.0.1", "latest_version": "0.0.1"}
    return instance


@instance_type.field("instanceName")
def resolve_instance_name(instance, info):
    if isinstance(instance, dict):
        return instance.get("instance_name") or "Plane"
    return instance.instance_name or "Plane"


@instance_type.field("currentVersion")
def resolve_instance_current_version(instance, info):
    if isinstance(instance, dict):
        return instance.get("current_version") or "0.0.1"
    return instance.current_version or "0.0.1"


@instance_type.field("latestVersion")
def resolve_instance_latest_version(instance, info):
    if isinstance(instance, dict):
        return instance.get("latest_version") or instance.get("current_version") or "0.0.1"
    # latest_version is nullable in the DB but String! in SDL; fall back to current.
    return instance.latest_version or instance.current_version or "0.0.1"


# --- Query: unsplashImages / projectCovers --------------------------------------------


def _empty_unsplash():
    return {"total": 0, "total_pages": 0, "urls": []}


@query.field("unsplashImages")
def resolve_unsplash_images(_, info, slug, page=1, perPage=20, query=None):
    # Mirror UnsplashEndpoint: proxy the Unsplash API with the configured access
    # key; without a key (or on any failure) return the empty non-null shape.
    user = _user(info)
    if user is None:
        return _empty_unsplash()
    access_key = _instance_value("UNSPLASH_ACCESS_KEY", os.environ.get("UNSPLASH_ACCESS_KEY"))
    if not access_key:
        return _empty_unsplash()
    if query:
        url = (
            f"https://api.unsplash.com/search/photos/?client_id={access_key}"
            f"&query={query}&page={page}&per_page={perPage}"
        )
    else:
        url = (
            f"https://api.unsplash.com/photos/?client_id={access_key}"
            f"&page={page}&per_page={perPage}"
        )
    try:
        resp = requests.get(url=url, headers={"Content-Type": "application/json"}, timeout=10)
        data = resp.json()
    except Exception:
        return _empty_unsplash()

    # The /search endpoint wraps results in {total, total_pages, results:[...]};
    # the listing endpoint returns a bare list. Normalise both to UnsplashImages.
    if isinstance(data, dict):
        photos = data.get("results", []) or []
        total = data.get("total")
        total_pages = data.get("total_pages")
    elif isinstance(data, list):
        photos = data
        total = len(photos)
        total_pages = None
    else:
        return _empty_unsplash()

    urls = []
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        raw_urls = photo.get("urls") or {}
        urls.append(
            {
                "raw": raw_urls.get("raw") or "",
                "full": raw_urls.get("full") or "",
                "regular": raw_urls.get("regular") or "",
                "small": raw_urls.get("small") or "",
                "thumb": raw_urls.get("thumb") or "",
                "small_s3": raw_urls.get("small_s3") or "",
            }
        )
    return {"total": total, "total_pages": total_pages, "urls": urls}


# Static gallery of project cover images. The community backend has no covers
# endpoint (it is a Cloud-only static S3 manifest); replicate Cloud's default
# bucket listing so the cover picker is populated. ProjectCovers.urls is
# [String!]! so it must never be None.
_PROJECT_COVERS = [
    "https://images.unsplash.com/photo-1531297484001-80022131f5a1",
    "https://images.unsplash.com/photo-1517694712202-14dd9538aa97",
    "https://images.unsplash.com/photo-1497215728101-856f4ea42174",
    "https://images.unsplash.com/photo-1488590528505-98d2b5aba04b",
    "https://images.unsplash.com/photo-1483058712412-4245e9b90334",
    "https://images.unsplash.com/photo-1498050108023-c5249f4df085",
]


@query.field("projectCovers")
def resolve_project_covers(_, info, slug):
    return {"urls": list(_PROJECT_COVERS)}


# --- Query: isProjectPublic -----------------------------------------------------------


@query.field("isProjectPublic")
def resolve_is_project_public(_, info, slug, project):
    # A project is "public" when it has a published (deploy board) anchor for the
    # project entity. Return the project lite shape, else None (nullable single).
    board = DeployBoard.objects.filter(
        workspace__slug=slug,
        entity_name="project",
        entity_identifier=project,
    ).first()
    if board is None:
        return None
    item = Project.objects.filter(workspace__slug=slug, id=project).first()
    if item is None:
        return None
    return item


@project_public_lite_type.field("id")
def resolve_public_project_id(project, info):
    return str(project.id)


@project_public_lite_type.field("logoProps")
def resolve_public_project_logo_props(project, info):
    return project.logo_props or {}


# --- Query: teamspaceMembersByProject -------------------------------------------------


@query.field("teamspaceMembersByProject")
def resolve_teamspace_members_by_project(_, info, slug, project):
    # Teamspaces is an Enterprise-only feature; the community backend has no
    # TeamMember model/table, so there are never any teamspace members. The field
    # is [TeamspaceMemberType!]! -> return the empty non-null list.
    return []


# --- Query: workspace invites ---------------------------------------------------------


def _invite_for_user(user, invitation_id):
    """The user's own invitation (matched on their email, exactly like the REST
    UserWorkspaceInvitationsViewSet.get_queryset)."""
    return (
        WorkspaceMemberInvite.objects.filter(pk=invitation_id, email=user.email)
        .select_related("workspace")
        .first()
    )


@query.field("userWorkspaceInvites")
def resolve_user_workspace_invites(_, info):
    # Mirror UserWorkspaceInvitationsViewSet: every pending invite addressed to the
    # current user's email. [WorkspaceInviteType!]! -> non-null list.
    user = _user(info)
    if user is None:
        return []
    return list(
        WorkspaceMemberInvite.objects.filter(email=user.email)
        .select_related("workspace")
        .order_by("-created_at")
    )


@query.field("userWorkspaceInvite")
def resolve_user_workspace_invite(_, info, invitationId):
    user = _user(info)
    if user is None:
        return None
    return _invite_for_user(user, invitationId)


@query.field("publicUserWorkspaceInvite")
def resolve_public_user_workspace_invite(_, info, invitationId, email):
    # Public (unauthenticated) lookup of an invitation by id + email, mirroring
    # WorkspaceJoinEndpoint.get (AllowAny). Matched on the invited email.
    return (
        WorkspaceMemberInvite.objects.filter(pk=invitationId, email=email)
        .select_related("workspace")
        .first()
    )


@query.field("publicUserWorkspaceInviteV2")
def resolve_public_user_workspace_invite_v2(_, info, invitationId, slug, token=None):
    # V2 keys on the workspace slug (+ optional token verifying the email link),
    # not the email, so the join screen can resolve the invite from a deep link.
    qs = WorkspaceMemberInvite.objects.filter(
        pk=invitationId, workspace__slug=slug
    ).select_related("workspace")
    invite = qs.first()
    if invite is None:
        return None
    if token is not None and invite.token != token:
        return None
    return invite


# WorkspaceInviteType: all fields nullable. Only the FK objects / role-label need
# explicit resolution; id/email/accepted/token/message/respondedAt fall through.


@workspace_invite_type.field("id")
def resolve_invite_id(invite, info):
    return str(invite.id)


@workspace_invite_type.field("role")
def resolve_invite_role(invite, info):
    return _ROLE_LABELS.get(invite.role, str(invite.role))


@workspace_invite_type.field("workspace")
def resolve_invite_workspace(invite, info):
    # WorkspaceInviteType.workspace is the full WorkspaceType (nullable); return
    # the related workspace object so the app can render its name/logo.
    ws = invite.workspace
    if ws is None:
        return None
    user = _user(info)
    if user is not None:
        member = WorkspaceMember.objects.filter(workspace=ws, member=user, is_active=True).first()
        if member is not None:
            ws._gql_role = member.role
    return ws


# --- Query: userDelete (can-delete precheck) ------------------------------------------


def _solo_admin_blocks(user):
    """Return True when the user is the only admin of some workspace/project (i.e.
    deactivation is blocked), and the list of workspaces they can still leave —
    mirrors the guard logic in UserEndpoint.deactivate."""
    # Instance admins can never deactivate.
    try:
        from plane.license.models import InstanceAdmin

        if InstanceAdmin.objects.filter(user=user).exists():
            return True, []
    except Exception:
        pass

    can_delete = True

    projects = ProjectMember.objects.filter(member=user, is_active=True).annotate(
        other_admin_exists=Count(
            Case(
                When(Q(role=20, is_active=True) & ~Q(member=user), then=1),
                default=0,
                output_field=IntegerField(),
            )
        ),
        total_members=Count("id"),
    )
    for project in projects:
        if not (project.other_admin_exists > 0 or project.total_members == 1):
            can_delete = False
            break

    workspace_members = (
        WorkspaceMember.objects.filter(member=user, is_active=True)
        .select_related("workspace")
        .annotate(
            other_admin_exists=Count(
                Case(
                    When(Q(role=20, is_active=True) & ~Q(member=user), then=1),
                    default=0,
                    output_field=IntegerField(),
                )
            ),
            total_members=Count("id"),
        )
    )
    workspaces = []
    for member in workspace_members:
        if not (member.other_admin_exists > 0 or member.total_members == 1):
            can_delete = False
        ws = member.workspace
        if ws is not None and ws.deleted_at is None:
            ws._gql_role = member.role
            workspaces.append(ws)
    return can_delete, workspaces


@query.field("userDelete")
def resolve_user_delete_query(_, info):
    # UserDeleteType.canDelete is non-null; workspaces is the (nullable) list of
    # the user's active workspaces. Returns whether deactivation is allowed.
    user = _user(info)
    if user is None:
        return {"can_delete": False, "workspaces": []}
    can_delete, workspaces = _solo_admin_blocks(user)
    return {"can_delete": can_delete, "workspaces": workspaces}


@user_delete_type.field("canDelete")
def resolve_user_delete_can_delete(obj, info):
    return bool(obj.get("can_delete")) if isinstance(obj, dict) else bool(obj)


@user_delete_type.field("workspaces")
def resolve_user_delete_workspaces(obj, info):
    return obj.get("workspaces", []) if isinstance(obj, dict) else []


# --- Mutations ------------------------------------------------------------------------


def _accept_invite(user, invite):
    """Accept a workspace invitation for `user`, replicating the accept branch of
    WorkspaceJoinEndpoint.post (activate/create the membership, set last workspace,
    delete the invite)."""
    if invite.responded_at is None:
        invite.accepted = True
        invite.responded_at = timezone.now()
        invite.save()

    workspace_member = WorkspaceMember.objects.filter(
        workspace=invite.workspace, member=user
    ).first()
    if workspace_member is not None:
        workspace_member.is_active = True
        workspace_member.role = invite.role
        workspace_member.save()
    else:
        WorkspaceMember.objects.create(
            workspace=invite.workspace,
            member=user,
            role=invite.role,
        )

    # Point the user's onboarding/last workspace at the accepted workspace.
    profile, _created = Profile.objects.get_or_create(user=user)
    profile.last_workspace_id = invite.workspace_id
    profile.save(update_fields=["last_workspace_id"])

    invite.delete()
    return True


@mutation.field("acceptPublicWorkspaceInvite")
def resolve_accept_public_workspace_invite(_, info, invitationId, email):
    user = _user(info)
    if user is None:
        return False
    invite = (
        WorkspaceMemberInvite.objects.filter(pk=invitationId, email=email)
        .select_related("workspace")
        .first()
    )
    if invite is None or invite.email.lower() != (user.email or "").lower():
        return False
    return _accept_invite(user, invite)


@mutation.field("acceptPublicWorkspaceInviteV2")
def resolve_accept_public_workspace_invite_v2(_, info, invitationId, slug, token=None):
    user = _user(info)
    if user is None:
        return False
    invite = (
        WorkspaceMemberInvite.objects.filter(pk=invitationId, workspace__slug=slug)
        .select_related("workspace")
        .first()
    )
    if invite is None:
        return False
    # Verify the token (proves the user followed the emailed invite link) when sent.
    if token is not None and invite.token != token:
        return False
    return _accept_invite(user, invite)


@mutation.field("userDelete")
def resolve_user_delete_mutation(_, info, deleteInput):
    # Replicate UserEndpoint.deactivate: refuse for instance admins / sole admins,
    # otherwise deactivate the user, their memberships, drop invites/sessions and
    # reset onboarding. Returns Boolean! (success).
    user = _user(info)
    if user is None:
        return False

    can_delete, _workspaces = _solo_admin_blocks(user)
    if not can_delete:
        return False

    # Deactivate every active project membership.
    ProjectMember.objects.filter(member=user, is_active=True).update(is_active=False)
    # Deactivate every active workspace membership.
    WorkspaceMember.objects.filter(member=user, is_active=True).update(is_active=False)

    # Drop all pending invites addressed to this user.
    WorkspaceMemberInvite.objects.filter(email=user.email).delete()

    # Reset onboarding on the profile.
    profile, _created = Profile.objects.get_or_create(user=user)
    profile.last_workspace_id = None
    profile.is_tour_completed = False
    profile.is_onboarded = False
    profile.onboarding_step = {
        "workspace_join": False,
        "profile_complete": False,
        "workspace_create": False,
        "workspace_invite": False,
    }
    profile.save()

    # Deactivate the user account and log out the session.
    user.is_active = False
    user.last_logout_time = timezone.now()
    user.save()
    try:
        request = getattr(info.context, "_request", None) or info.context
        logout(request)
    except Exception:
        pass
    return True


BINDABLES = [
    query,
    mutation,
    instance_type,
    workspace_invite_type,
    user_delete_type,
    project_public_lite_type,
]
