# OVERLAY: mobile-graphql — resolver module for the "initiatives" area.
#
# GROUND TRUTH NOTE (important):
# ------------------------------------------------------------------------------
# "Initiatives" is a Plane Enterprise (EE) feature. In THIS self-hosted community
# codebase there is **no Initiative model, table or REST view** to replicate
# (verified: `grep -ri Initiative` over plane/db/models, plane/app/views and the
# whole plane/ python tree returns no model/view — only the SDL contract, the
# feature-flag constant and this overlay). The Cloud GraphQL SDL still advertises
# the whole "initiatives" surface, and the captured schema.graphql is the
# authoritative contract the native app introspects against, so every Query and
# Mutation field of this area MUST be bound and MUST honour the SDL non-null
# contract — otherwise make_executable_schema / the smart-default engine could
# leave a non-null field returning null and crash the WHOLE schema build or a
# query at runtime.
#
# Because there is no backing storage:
#   * read lists / paginators       -> empty (auth-gated) — [] or _page([], cursor)
#   * read single non-null objects  -> a safe, fully-formed non-null stub that
#                                      matches the SDL shape (real ids where we can
#                                      derive them, e.g. workspace id from slug)
#   * boolean mutations             -> False (nothing was persisted; non-null kept)
#   * object-returning mutations    -> a safe non-null stub of the SDL return type
#   * reaction-list mutations       -> []
#
# The app's initiative screens are additionally gated off on a community instance
# (featureFlag.initiatives == False and workspaceFeatures.isInitiativeEnabled ==
# False, see plane/graphql/resolvers.py), so these safe defaults are never user
# visible; they exist purely to keep the introspected schema buildable and every
# field non-null-safe. Each field is recorded in `notImplemented` of the run
# report with this reason. If/when the EE Initiative models land, swap the stub
# bodies for real querysets — the bindings already match the SDL exactly.
#
# Self-contained module: own QueryType / MutationType / ObjectType(s); exposes
# BINDABLES. Auth via plane.graphql.resolvers._user; pagination via _page.
from ariadne import MutationType, ObjectType, QueryType

from plane.db.models import Workspace
from plane.graphql.resolvers import _page, _user

query = QueryType()
mutation = MutationType()

# ObjectType for InitiativeType — guarantees the non-null nested aggregate fields
# (entityUpdates / progress) and the non-null enum (state) are always present even
# if a future source object omits them; the smart fallback handles plain scalar /
# FK-id fields from the returned dict.
initiative_type = ObjectType("InitiativeType")
initiative_progress_type = ObjectType("InitiativeProgressType")


# --- shared safe-default builders -----------------------------------------------


def _workspace_id(slug):
    """Real workspace id for the slug (keeps `workspace: ID!` fields honest), or ""."""
    wid = Workspace.objects.filter(slug=slug).values_list("id", flat=True).first()
    return str(wid) if wid else ""


def _empty_analytics():
    # InitiativeProgressAnalyticsType / EpicAnalyticsType: all nullable Ints.
    return {
        "backlog": 0,
        "unstarted": 0,
        "started": 0,
        "completed": 0,
        "cancelled": 0,
    }


def _empty_entity_updates():
    # InitiativeEpicEntityUpdateType: offTrack/atRisk/onTrack are Int! (non-null).
    return {"off_track": 0, "at_risk": 0, "on_track": 0}


def _empty_progress():
    # InitiativeProgressType: analytics non-null, projects/epics non-null lists.
    return {"analytics": _empty_analytics(), "projects": [], "epics": []}


def _initiative_stub(slug, initiative_id=None):
    """Safe non-null InitiativeType stub matching the SDL exactly."""
    wid = _workspace_id(slug)
    return {
        "id": str(initiative_id) if initiative_id else "",
        "name": "",
        "description": None,
        "description_html": None,
        "description_stripped": None,
        "description_binary": None,
        "start_date": None,
        "end_date": None,
        "logo_props": {},
        "state": "DRAFT",  # InitiativeState! — DRAFT is the SDL's first member.
        "workspace": wid,
        "lead": None,
        "labels": [],
        "created_by": None,
        "updated_by": None,
        "archived_at": None,
        "created_at": None,
        "updated_at": None,
        "projects": [],
        "epics": [],
        "entity_updates": _empty_entity_updates(),
        "progress": _empty_progress(),
    }


def _label_stub(slug, label_id=None):
    return {
        "id": str(label_id) if label_id else "",
        "name": "",
        "description": None,
        "color": "#000000",  # color: String! (non-null)
        "sort_order": 0.0,
        "workspace": _workspace_id(slug),
        "created_by": None,
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
    }


def _comment_stub(slug, initiative_id=None, comment_id=None):
    return {
        "id": str(comment_id) if comment_id else "",
        "comment_stripped": "",
        "comment_json": {},
        "comment_html": "",
        "access": "INTERNAL",
        "external_source": None,
        "external_id": None,
        "edited_at": None,
        "actor": None,
        "actor_details": None,
        "workspace": _workspace_id(slug),
        "initiative": str(initiative_id) if initiative_id else "",
        "created_by": None,
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
    }


def _link_stub(slug, initiative_id=None, link_id=None):
    return {
        "id": str(link_id) if link_id else "",
        "url": "",
        "title": None,
        "metadata": {},
        "workspace": _workspace_id(slug),
        "initiative": str(initiative_id) if initiative_id else "",
        "created_by": None,
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
    }


def _user_property_stub(slug):
    wid = _workspace_id(slug)
    user = None
    return {
        "id": wid or "",
        "filters": {},
        "display_filters": {},
        "display_properties": {},
        "workspace": wid,
        "user": str(user.id) if user is not None else "",
        "created_by": None,
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
    }


def _attachment_stub(slug, initiative_id=None, attachment_id=None):
    # InitiativeAttachmentType: attributes JSON!, asset String!, size Float!,
    # entityType FileAssetEntityType!, isUploaded/isArchived/isDeleted Boolean!.
    return {
        "id": str(attachment_id) if attachment_id else "",
        "attributes": {},
        "asset": "",
        "size": 0.0,
        "entity_type": "INITIATIVE_ATTACHMENT",
        "entity_identifier": str(initiative_id) if initiative_id else None,
        "storage_metadata": {},
        "is_uploaded": False,
        "is_archived": False,
        "is_deleted": False,
        "external_id": None,
        "external_source": None,
        "deleted_at": None,
        "user": None,
        "workspace": _workspace_id(slug),
        "project": None,
        "draft_issue": None,
        "issue": None,
        "comment": None,
        "page": None,
        "asset_url": None,
        "created_by": None,
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
    }


# --- InitiativeType non-null nested fields (never null regardless of source) -----


@initiative_type.field("state")
def resolve_initiative_state(obj, info):
    if isinstance(obj, dict):
        return obj.get("state") or "DRAFT"
    return getattr(obj, "state", None) or "DRAFT"


@initiative_type.field("entityUpdates")
def resolve_initiative_entity_updates(obj, info):
    if isinstance(obj, dict) and obj.get("entity_updates"):
        return obj["entity_updates"]
    return _empty_entity_updates()


@initiative_type.field("progress")
def resolve_initiative_progress(obj, info):
    if isinstance(obj, dict) and obj.get("progress"):
        return obj["progress"]
    return _empty_progress()


@initiative_progress_type.field("analytics")
def resolve_initiative_progress_analytics(obj, info):
    if isinstance(obj, dict) and obj.get("analytics"):
        return obj["analytics"]
    return _empty_analytics()


# --- Queries --------------------------------------------------------------------


@query.field("initiativeLabels")
def resolve_initiative_labels(_, info, slug):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeLabel")
def resolve_initiative_label(_, info, slug, label):
    user = _user(info)
    if user is None:
        return _label_stub(slug, label)
    return _label_stub(slug, label)


@query.field("initiativesCount")
def resolve_initiatives_count(_, info, slug):
    return {"total_count": 0, "total_count_by_lead": 0}


@query.field("initiativeInformation")
def resolve_initiative_information(_, info, slug):
    # InitiativeInformationType: createdBy / lead are nullable [String!].
    return {"created_by": [], "lead": []}


@query.field("initiatives")
def resolve_initiatives(_, info, slug, filters=None, groupBy=None, orderBy="-updated_at", cursor=None):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    return _page([], cursor)


@query.field("initiative")
def resolve_initiative(_, info, slug, initiative):
    user = _user(info)
    if user is None:
        return _initiative_stub(slug, initiative)
    return _initiative_stub(slug, initiative)


@query.field("initiativeActivities")
def resolve_initiative_activities(_, info, slug, initiative):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeReactions")
def resolve_initiative_reactions(_, info, slug, initiative):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeComments")
def resolve_initiative_comments(_, info, slug, initiative):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeComment")
def resolve_initiative_comment(_, info, slug, initiative, comment):
    user = _user(info)
    if user is None:
        return _comment_stub(slug, initiative, comment)
    return _comment_stub(slug, initiative, comment)


@query.field("initiativeCommentReactions")
def resolve_initiative_comment_reactions(_, info, slug, initiative, comment):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeUserProperty")
def resolve_initiative_user_property(_, info, slug):
    return _user_property_stub(slug)


@query.field("initiativeLinks")
def resolve_initiative_links(_, info, slug, initiative):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeLink")
def resolve_initiative_link(_, info, slug, initiative, link):
    user = _user(info)
    if user is None:
        return _link_stub(slug, initiative, link)
    return _link_stub(slug, initiative, link)


@query.field("initiativeProjectsSearch")
def resolve_initiative_projects_search(_, info, slug, initiative, input):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeProjects")
def resolve_initiative_projects(_, info, slug, initiative):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeEpicsSearch")
def resolve_initiative_epics_search(_, info, slug, initiative, input):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeEpics")
def resolve_initiative_epics(_, info, slug, initiative):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeAttachments")
def resolve_initiative_attachments(_, info, slug, initiative):
    user = _user(info)
    if user is None:
        return []
    return []


@query.field("initiativeAttachment")
def resolve_initiative_attachment(_, info, slug, initiative, attachment):
    user = _user(info)
    if user is None:
        return _attachment_stub(slug, initiative, attachment)
    return _attachment_stub(slug, initiative, attachment)


@query.field("initiativeAttachmentPresignedUrl")
def resolve_initiative_attachment_presigned_url(_, info, slug, initiative, attachment):
    # String! — no asset storage row to presign; return "" (non-null kept).
    return ""


@query.field("archivedInitiatives")
def resolve_archived_initiatives(_, info, slug, filters=None, orderBy="-updated_at", cursor=None):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    return _page([], cursor)


# --- Mutations ------------------------------------------------------------------


@mutation.field("createInitiativeLabel")
def resolve_create_initiative_label(_, info, slug, input):
    return _label_stub(slug)


@mutation.field("updateInitiativeLabel")
def resolve_update_initiative_label(_, info, slug, label, input):
    return _label_stub(slug, label)


@mutation.field("deleteInitiativeLabel")
def resolve_delete_initiative_label(_, info, slug, label):
    return False


@mutation.field("createInitiative")
def resolve_create_initiative(_, info, slug, input):
    return _initiative_stub(slug)


@mutation.field("updateInitiative")
def resolve_update_initiative(_, info, slug, initiative, input):
    return _initiative_stub(slug, initiative)


@mutation.field("deleteInitiative")
def resolve_delete_initiative(_, info, slug, initiative):
    return False


@mutation.field("createInitiativeReaction")
def resolve_create_initiative_reaction(_, info, slug, initiative, input):
    return []


@mutation.field("deleteInitiativeReaction")
def resolve_delete_initiative_reaction(_, info, slug, initiative, input):
    return []


@mutation.field("createInitiativeComment")
def resolve_create_initiative_comment(_, info, slug, initiative, input):
    return _comment_stub(slug, initiative)


@mutation.field("updateInitiativeComment")
def resolve_update_initiative_comment(_, info, slug, initiative, comment, input):
    return _comment_stub(slug, initiative, comment)


@mutation.field("deleteInitiativeComment")
def resolve_delete_initiative_comment(_, info, slug, initiative, comment):
    return False


@mutation.field("createInitiativeCommentReaction")
def resolve_create_initiative_comment_reaction(_, info, slug, initiative, comment, input):
    return []


@mutation.field("deleteInitiativeCommentReaction")
def resolve_delete_initiative_comment_reaction(_, info, slug, initiative, comment, input):
    return []


@mutation.field("updateInitiativeUserProperty")
def resolve_update_initiative_user_property(_, info, slug, input):
    return _user_property_stub(slug)


@mutation.field("createInitiativeLink")
def resolve_create_initiative_link(_, info, slug, initiative, input):
    return _link_stub(slug, initiative)


@mutation.field("updateInitiativeLink")
def resolve_update_initiative_link(_, info, slug, initiative, link, input):
    return _link_stub(slug, initiative, link)


@mutation.field("deleteInitiativeLink")
def resolve_delete_initiative_link(_, info, slug, initiative, link):
    return False


@mutation.field("createInitiativeProject")
def resolve_create_initiative_project(_, info, slug, initiative, input):
    return []


@mutation.field("deleteInitiativeProject")
def resolve_delete_initiative_project(_, info, slug, initiative, input):
    return []


@mutation.field("createInitiativeEpic")
def resolve_create_initiative_epic(_, info, slug, initiative, input):
    return []


@mutation.field("deleteInitiativeEpic")
def resolve_delete_initiative_epic(_, info, slug, initiative, input):
    return []


@mutation.field("createInitiativeAttachment")
def resolve_create_initiative_attachment(_, info, slug, initiative, input):
    # InitiativeAttachmentPresignedUrlResponseType: uploadData JSON!, attachmentId
    # String!, assetUrl String (nullable). No storage row created.
    return {"upload_data": {}, "attachment_id": "", "asset_url": None}


@mutation.field("updateInitiativeAttachment")
def resolve_update_initiative_attachment(_, info, slug, initiative, attachment, input):
    return _attachment_stub(slug, initiative, attachment)


@mutation.field("deleteInitiativeAttachment")
def resolve_delete_initiative_attachment(_, info, slug, initiative, attachment):
    return False


@mutation.field("archiveInitiatives")
def resolve_archive_initiatives(_, info, slug, input):
    return False


@mutation.field("restoreInitiatives")
def resolve_restore_initiatives(_, info, slug, input):
    return False


BINDABLES = [query, mutation, initiative_type, initiative_progress_type]
