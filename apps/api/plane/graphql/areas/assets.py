# OVERLAY: mobile-graphql — GraphQL area "assets".
#
# Self-contained ariadne module replicating Plane's REST presigned-URL asset flow
# (S3/MinIO direct upload) so the native app's asset screens work against a
# self-hosted gateway exactly as against Cloud.
#
# REST ground truth: plane/app/views/asset/v2.py
#   * UserAssetsV2Endpoint        -> createUserAsset / updateUserAsset / deleteUserAsset
#   * WorkspaceFileAssetEndpoint  -> createWorkspaceAsset / updateWorkspaceAsset /
#                                    deleteWorkspaceAsset + workspaceAsset (signed GET)
#   * ProjectAssetEndpoint        -> createProjectAsset / updateProjectAsset /
#                                    deleteProjectAsset + projectAsset (signed GET)
#   * ProjectBulkAssetEndpoint    -> updateProjectAssetEntity / updateWorkspaceAssetEntity
# Model: plane.db.models.FileAsset (db_table "file_assets").
#
# Flow (mirrors REST):
#   create*  -> create a FileAsset row, return a presigned POST (AssetPresignedUrlResponseType).
#   update*  -> mark is_uploaded, persist attributes, wire the asset into its owning
#               entity (entity_asset_save), return Boolean.
#   delete*  -> soft-delete (is_deleted/deleted_at), unwire the entity, return Boolean.
#   *Entity  -> bulk-attach a set of asset ids to one entity (cover/description), Boolean.
#   workspaceAsset/projectAsset queries -> signed GET URL string for an uploaded asset.
#
# Field resolution is camelCase->snake_case via the global smart-fallback resolver;
# only the root query/mutation fields are bound here. AssetPresignedUrlResponseType is
# returned as a dict whose snake_case keys (upload_data, asset_id, asset_url) map to the
# SDL camelCase fields by the same fallback, so no ObjectType binding is needed.
import uuid

from ariadne import MutationType, ObjectType, QueryType
from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone

from plane.db.models import FileAsset, Project, Workspace
from plane.graphql.resolvers import _user

query = QueryType()
mutation = MutationType()


ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "image/jpg", "image/gif"]


def _size_limit(size):
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = settings.FILE_SIZE_LIMIT
    return min(settings.FILE_SIZE_LIMIT, size)


def _safe_name(name):
    from plane.utils.path_validator import sanitize_filename

    return sanitize_filename(name) or "unnamed"


def _presigned_response(asset, file_type, size_limit, info):
    """AssetPresignedUrlResponseType: {uploadData!, assetId!, assetUrl}.

    uploadData is non-null in the SDL, so fall back to an empty dict if the storage
    backend is unreachable (the client can retry the upload step) rather than crash.
    """
    upload_data = None
    try:
        from plane.settings.storage import S3Storage

        storage = S3Storage(request=info.context)
        upload_data = storage.generate_presigned_post(
            object_name=asset.asset.name, file_type=file_type, file_size=size_limit
        )
    except Exception:
        upload_data = {}
    if upload_data is None:
        upload_data = {}
    return {
        "upload_data": upload_data,
        "asset_id": str(asset.id),
        "asset_url": asset.asset_url,
    }


def _entity_id_field(entity_type, entity_id):
    """Translate an entity_type + identifier into the FK kwargs FileAsset.create wants.

    Mirrors get_entity_id_field across the REST asset endpoints."""
    if not entity_id:
        return {}
    et = FileAsset.EntityTypeContext
    if entity_type == et.WORKSPACE_LOGO:
        return {"workspace_id": entity_id}
    if entity_type == et.PROJECT_COVER:
        return {"project_id": entity_id}
    if entity_type in (et.USER_AVATAR, et.USER_COVER):
        return {"user_id": entity_id}
    if entity_type in (et.ISSUE_ATTACHMENT, et.ISSUE_DESCRIPTION, et.DRAFT_ISSUE_ATTACHMENT):
        return {"issue_id": entity_id}
    if entity_type == et.DRAFT_ISSUE_DESCRIPTION:
        return {"draft_issue_id": entity_id}
    if entity_type == et.PAGE_DESCRIPTION:
        return {"page_id": entity_id}
    if entity_type == et.COMMENT_DESCRIPTION:
        return {"comment_id": entity_id}
    return {}


def _asset_soft_delete(asset_id):
    asset = FileAsset.objects.filter(id=asset_id).first()
    if asset is None:
        return
    asset.is_deleted = True
    asset.deleted_at = timezone.now()
    asset.save(update_fields=["is_deleted", "deleted_at"])


def _entity_asset_save(asset_id, entity_type, asset):
    """On upload-complete, wire the asset into its owning entity (cover/logo).

    Mirrors entity_asset_save in WorkspaceFileAssetEndpoint / ProjectAssetEndpoint."""
    et = FileAsset.EntityTypeContext
    if entity_type == et.WORKSPACE_LOGO:
        workspace = Workspace.objects.filter(id=asset.workspace_id).first()
        if workspace is None:
            return
        if workspace.logo_asset_id:
            _asset_soft_delete(workspace.logo_asset_id)
        workspace.logo = ""
        workspace.logo_asset_id = asset_id
        workspace.save()
    elif entity_type == et.PROJECT_COVER:
        project = Project.objects.filter(id=asset.project_id).first()
        if project is None:
            return
        if project.cover_image_asset_id:
            _asset_soft_delete(project.cover_image_asset_id)
        project.cover_image = ""
        project.cover_image_asset_id = asset_id
        project.save()


def _entity_asset_delete(entity_type, asset):
    """On delete, unwire the asset from its owning entity. Mirrors entity_asset_delete."""
    et = FileAsset.EntityTypeContext
    if entity_type == et.WORKSPACE_LOGO:
        workspace = Workspace.objects.filter(id=asset.workspace_id).first()
        if workspace is None:
            return
        workspace.logo_asset_id = None
        workspace.save()
    elif entity_type == et.PROJECT_COVER:
        project = Project.objects.filter(id=asset.project_id).first()
        if project is None:
            return
        project.cover_image_asset_id = None
        project.save()


def _signed_get_url(asset, info):
    """Signed GET URL for an uploaded asset; empty string keeps the String! contract."""
    if asset is None or not asset.is_uploaded:
        return ""
    try:
        from plane.settings.storage import S3Storage

        storage = S3Storage(request=info.context)
        url = storage.generate_presigned_url(
            object_name=asset.asset.name,
            disposition="attachment",
            filename=(asset.attributes or {}).get("name"),
        )
        return url or ""
    except Exception:
        return ""


# --- Queries: signed GET URL for an uploaded asset ------------------------------------


@query.field("workspaceAsset")
def resolve_workspace_asset(_, info, slug, assetId):
    # REST: WorkspaceFileAssetEndpoint.get — returns a signed URL for an uploaded
    # workspace-scoped asset (the app follows it to download/preview). String!.
    user = _user(info)
    if user is None:
        return ""
    asset = FileAsset.objects.filter(id=assetId, workspace__slug=slug, is_uploaded=True).first()
    return _signed_get_url(asset, info)


@query.field("projectAsset")
def resolve_project_asset(_, info, slug, project, assetId):
    # REST: ProjectAssetEndpoint.get — signed URL for an uploaded project-scoped asset.
    user = _user(info)
    if user is None:
        return ""
    asset = FileAsset.objects.filter(
        id=assetId, workspace__slug=slug, project_id=project, is_uploaded=True
    ).first()
    return _signed_get_url(asset, info)


# --- Mutations: create (presigned POST) -----------------------------------------------


@mutation.field("createUserAsset")
def resolve_create_user_asset(_, info, name, type, size, entityType):
    # REST: UserAssetsV2Endpoint.post. User avatar/cover live under "user-<uuid>-name".
    user = _user(info)
    if user is None:
        return {"upload_data": {}, "asset_id": "", "asset_url": None}
    safe_name = _safe_name(name)
    file_type = type if type in ALLOWED_TYPES else "image/jpeg"
    size_limit = _size_limit(size)
    asset_key = f"{uuid.uuid4().hex}-{safe_name}"
    asset = FileAsset.objects.create(
        attributes={"name": safe_name, "type": file_type, "size": size_limit},
        asset=asset_key,
        size=size_limit,
        user=user,
        created_by=user,
        entity_type=entityType,
    )
    return _presigned_response(asset, file_type, size_limit, info)


@mutation.field("createWorkspaceAsset")
def resolve_create_workspace_asset(_, info, slug, name, type, size, entityType, entityIdentifier=None):
    # REST: WorkspaceFileAssetEndpoint.post.
    user = _user(info)
    if user is None:
        return {"upload_data": {}, "asset_id": "", "asset_url": None}
    workspace = Workspace.objects.filter(slug=slug).first()
    if workspace is None:
        return {"upload_data": {}, "asset_id": "", "asset_url": None}
    safe_name = _safe_name(name)
    file_type = type if type in ALLOWED_TYPES else "image/jpeg"
    size_limit = _size_limit(size)
    asset_key = f"{workspace.id}/{uuid.uuid4().hex}-{safe_name}"
    asset = FileAsset.objects.create(
        attributes={"name": safe_name, "type": file_type, "size": size_limit},
        asset=asset_key,
        size=size_limit,
        workspace=workspace,
        created_by=user,
        entity_type=entityType,
        **_entity_id_field(entityType, entityIdentifier),
    )
    return _presigned_response(asset, file_type, size_limit, info)


@mutation.field("createProjectAsset")
def resolve_create_project_asset(_, info, slug, project, name, type, size, entityType, entityIdentifier=None):
    # REST: ProjectAssetEndpoint.post.
    user = _user(info)
    if user is None:
        return {"upload_data": {}, "asset_id": "", "asset_url": None}
    workspace = Workspace.objects.filter(slug=slug).first()
    if workspace is None:
        return {"upload_data": {}, "asset_id": "", "asset_url": None}
    safe_name = _safe_name(name)
    file_type = type if type in ALLOWED_TYPES else "image/jpeg"
    size_limit = _size_limit(size)
    asset_key = f"{workspace.id}/{uuid.uuid4().hex}-{safe_name}"
    extra = _entity_id_field(entityType, entityIdentifier)
    extra["project_id"] = project
    asset = FileAsset.objects.create(
        attributes={"name": safe_name, "type": file_type, "size": size_limit},
        asset=asset_key,
        size=size_limit,
        workspace=workspace,
        created_by=user,
        entity_type=entityType,
        **extra,
    )
    return _presigned_response(asset, file_type, size_limit, info)


# --- Mutations: update (mark uploaded + persist attributes + wire entity) --------------


@mutation.field("updateUserAsset")
def resolve_update_user_asset(_, info, assetId, attributes=None):
    # REST: UserAssetsV2Endpoint.patch.
    user = _user(info)
    if user is None:
        return False
    asset = FileAsset.objects.filter(id=assetId, user_id=user.id).first()
    if asset is None:
        return False
    asset.is_uploaded = True
    _entity_asset_save(asset_id=assetId, entity_type=asset.entity_type, asset=asset)
    if attributes is not None:
        asset.attributes = attributes
    asset.updated_by = user
    asset.save(update_fields=["is_uploaded", "attributes", "updated_by", "updated_at"])
    return True


@mutation.field("updateWorkspaceAsset")
def resolve_update_workspace_asset(_, info, slug, assetId, attributes=None):
    # REST: WorkspaceFileAssetEndpoint.patch.
    user = _user(info)
    if user is None:
        return False
    asset = FileAsset.objects.filter(id=assetId, workspace__slug=slug).first()
    if asset is None:
        return False
    asset.is_uploaded = True
    _entity_asset_save(asset_id=assetId, entity_type=asset.entity_type, asset=asset)
    if attributes is not None:
        asset.attributes = attributes
    asset.updated_by = user
    asset.save(update_fields=["is_uploaded", "attributes", "updated_by", "updated_at"])
    return True


@mutation.field("updateProjectAsset")
def resolve_update_project_asset(_, info, slug, project, assetId, attributes=None):
    # REST: ProjectAssetEndpoint.patch (no entity wire-up there beyond attributes).
    user = _user(info)
    if user is None:
        return False
    asset = FileAsset.objects.filter(id=assetId, workspace__slug=slug, project_id=project).first()
    if asset is None:
        return False
    asset.is_uploaded = True
    if attributes is not None:
        asset.attributes = attributes
    asset.updated_by = user
    asset.save(update_fields=["is_uploaded", "attributes", "updated_by", "updated_at"])
    return True


@mutation.field("updateWorkspaceAssetEntity")
def resolve_update_workspace_asset_entity(_, info, slug, entityId, assetIds):
    # REST: ProjectBulkAssetEndpoint.post — bulk-attach uploaded assets to one entity,
    # dispatching by the first asset's entity_type (workspace-scoped, no project).
    return _bulk_attach(info, slug, None, entityId, assetIds)


@mutation.field("updateProjectAssetEntity")
def resolve_update_project_asset_entity(_, info, slug, project, entityId, assetIds):
    # REST: ProjectBulkAssetEndpoint.post — project-scoped variant.
    return _bulk_attach(info, slug, project, entityId, assetIds)


def _bulk_attach(info, slug, project, entity_id, asset_ids):
    user = _user(info)
    if user is None or not asset_ids:
        return False
    assets = FileAsset.objects.filter(id__in=asset_ids, workspace__slug=slug)
    if project is not None:
        assets = assets.filter(project_id=project)
    asset = assets.first()
    if asset is None:
        return False
    et = FileAsset.EntityTypeContext
    entity_type = asset.entity_type
    if entity_type == et.PROJECT_COVER:
        if project is not None:
            assets.update(project_id=project)
        for a in assets:
            proj = Project.objects.filter(id=a.project_id or project).first()
            if proj is not None:
                proj.cover_image_asset_id = a.id
                proj.save()
    elif entity_type in (et.ISSUE_DESCRIPTION, et.ISSUE_ATTACHMENT):
        try:
            assets.update(issue_id=entity_id, project_id=project)
        except IntegrityError:
            pass
    elif entity_type == et.COMMENT_DESCRIPTION:
        try:
            assets.update(comment_id=entity_id)
        except IntegrityError:
            pass
    elif entity_type == et.PAGE_DESCRIPTION:
        assets.update(page_id=entity_id)
    elif entity_type == et.DRAFT_ISSUE_DESCRIPTION:
        try:
            assets.update(draft_issue_id=entity_id)
        except IntegrityError:
            pass
    return True


# --- Mutations: delete (soft-delete + unwire entity) ----------------------------------


@mutation.field("deleteUserAsset")
def resolve_delete_user_asset(_, info, assetId):
    # REST: UserAssetsV2Endpoint.delete.
    user = _user(info)
    if user is None:
        return False
    asset = FileAsset.objects.filter(id=assetId, user_id=user.id).first()
    if asset is None:
        return False
    asset.is_deleted = True
    asset.deleted_at = timezone.now()
    _entity_asset_delete(entity_type=asset.entity_type, asset=asset)
    asset.save(update_fields=["is_deleted", "deleted_at"])
    return True


@mutation.field("deleteWorkspaceAsset")
def resolve_delete_workspace_asset(_, info, slug, assetId):
    # REST: WorkspaceFileAssetEndpoint.delete.
    user = _user(info)
    if user is None:
        return False
    asset = FileAsset.objects.filter(id=assetId, workspace__slug=slug).first()
    if asset is None:
        return False
    asset.is_deleted = True
    asset.deleted_at = timezone.now()
    _entity_asset_delete(entity_type=asset.entity_type, asset=asset)
    asset.save(update_fields=["is_deleted", "deleted_at"])
    return True


@mutation.field("deleteProjectAsset")
def resolve_delete_project_asset(_, info, slug, project, assetId):
    # REST: ProjectAssetEndpoint.delete (soft-delete only; no entity unwire there).
    user = _user(info)
    if user is None:
        return False
    asset = FileAsset.objects.filter(id=assetId, workspace__slug=slug, project_id=project).first()
    if asset is None:
        return False
    asset.is_deleted = True
    asset.deleted_at = timezone.now()
    asset.save(update_fields=["is_deleted", "deleted_at"])
    return True


# AssetPresignedUrlResponseType has no computed fields: its dict keys (snake_case)
# map to the SDL camelCase via the global fallback. Declared (empty) to document the
# return type and keep the area self-contained should computed fields be added later.
asset_presigned_type = ObjectType("AssetPresignedUrlResponseType")


BINDABLES = [query, mutation, asset_presigned_type]
