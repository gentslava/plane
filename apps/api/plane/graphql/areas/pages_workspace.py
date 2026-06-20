# OVERLAY: mobile-graphql — GraphQL area "pages_workspace".
#
# Workspace-scoped page surface the native app drives against a self-hosted
# Plane gateway, mirrored against the REST ground truth in
#   plane/app/views/page/iw_workspace_page.py   (WorkspacePageViewSet)
#   plane/app/views/page/base.py                (PageViewSet — shared page logic)
# and the Page / PageLog / ProjectPage models in plane/db/models/page.py.
#
# Self-contained ariadne module: it owns its QueryType / MutationType and the
# ObjectType bindings for the SDL return types that need computed / M2M / nested
# fields, then exposes BINDABLES for make_executable_schema aggregation.
#
# Binding contract (see resolver_utils.install_smart_fallback):
#   * The global smart fallback already resolves camelCase->snake_case scalars and
#     FK->`<field>_id`. We bind ONLY root Query/Mutation fields and the genuinely
#     computed / aggregate / M2M-as-id / nested-object fields.
#   * Mutation/Query fields bound here are NOT bound anywhere else. In particular
#     planning.py already owns createPage/updatePage/batchCreatePages and the
#     lock/unlock/archive/unarchive WorkspacePage mutations, so they are NOT
#     re-bound here (re-binding a field crashes schema assembly).
#
# Data gap (intentional non-null safe defaults, see NOT-IMPLEMENTED note below):
# this server's db layer has NO PageComment / PageCommentReaction / PageMention
# models, so every page-comment and page-mention field returns an empty/None
# safe default. Pages are linked to work items / epics through PageLog rows
# (entity_name="issue", entity_identifier=<work_item_id>), which is the embed
# mechanism this server actually ships — that is what work-item / epic page
# fields read and what addWorkItemPage / deleteWorkItemPage write.

import base64
import uuid

from ariadne import MutationType, ObjectType, QueryType

from django.db.models import Q
from django.utils import timezone

from plane.db.models import (
    Page,
    PageLog,
    ProjectPage,
    UserFavorite,
    Workspace,
)
from plane.graphql.resolvers import _page, _user

query = QueryType()
mutation = MutationType()


# --- helpers -------------------------------------------------------------------------


def _accessible_workspace_pages(user, slug):
    """Workspace-level (is_global=True) pages the user may see.

    Mirrors WorkspacePageViewSet.get_queryset: global pages in the workspace,
    top-level (parent is null), owned by the user OR public (access=0).
    """
    return (
        Page.objects.filter(workspace__slug=slug, is_global=True)
        .filter(parent__isnull=True)
        .filter(Q(owned_by=user) | Q(access=0))
        .select_related("workspace", "owned_by")
        .order_by("-created_at")
        .distinct()
    )


def _single_workspace_page(user, slug, page_id):
    """A single workspace page (any level) the user may see."""
    return (
        Page.objects.filter(workspace__slug=slug, is_global=True, id=page_id)
        .filter(Q(owned_by=user) | Q(access=0))
        .select_related("workspace", "owned_by")
        .first()
    )


def _filter_by_type(qs, type):
    """The `type` arg the app sends: all / public / private / archived."""
    if type == "public":
        return qs.filter(access=0, archived_at__isnull=True)
    if type == "private":
        return qs.filter(access=1, archived_at__isnull=True)
    if type == "archived":
        return qs.filter(archived_at__isnull=False)
    # "all" — active (non-archived) pages, matching the Cloud default list.
    return qs.filter(archived_at__isnull=True)


def _pages_embedding_work_item(slug, work_item_id):
    """Pages that embed a given work item / epic.

    This server links pages to issues through PageLog rows
    (entity_name="issue", entity_identifier=<issue id>), set when the editor
    embeds a work item. The work-item "Pages" tab is the reverse of that link.
    """
    page_ids = (
        PageLog.objects.filter(
            workspace__slug=slug,
            entity_name="issue",
            entity_identifier=work_item_id,
        )
        .values_list("page_id", flat=True)
        .distinct()
    )
    return (
        Page.objects.filter(workspace__slug=slug, id__in=list(page_ids))
        .select_related("workspace", "owned_by")
        .order_by("-created_at")
        .distinct()
    )


# --- Query: workspace pages ----------------------------------------------------------


@query.field("workspacePages")
def resolve_workspace_pages(_, info, slug, cursor=None, type="all"):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = _filter_by_type(_accessible_workspace_pages(user, slug), type)
    return _page(list(qs), cursor)


@query.field("workspacePage")
def resolve_workspace_page(_, info, slug, page):
    user = _user(info)
    if user is None:
        return None
    return _single_workspace_page(user, slug, page)


@query.field("workspaceNestedParentPages")
def resolve_workspace_nested_parent_pages(_, info, slug, page):
    """Ancestor chain (parents) of a workspace page, closest parent first."""
    user = _user(info)
    if user is None:
        return []
    current = (
        Page.objects.filter(workspace__slug=slug, is_global=True, id=page)
        .select_related("parent")
        .first()
    )
    if current is None:
        return []
    ancestors = []
    seen = set()
    node = current.parent
    while node is not None and node.id not in seen:
        seen.add(node.id)
        ancestors.append(node)
        node = node.parent
    return ancestors


@query.field("workspaceNestedChildPages")
def resolve_workspace_nested_child_pages(_, info, slug, page):
    """Direct children of a workspace page."""
    user = _user(info)
    if user is None:
        return []
    return list(
        Page.objects.filter(
            workspace__slug=slug, is_global=True, parent_id=page
        )
        .select_related("workspace", "owned_by")
        .order_by("sort_order", "-created_at")
        .distinct()
    )


# --- Query: workspace page comments --------------------------------------------------
# No PageComment / PageCommentReaction model in this server's db layer; every
# comment field returns a safe empty/None default (see NOT-IMPLEMENTED note).


@query.field("workspacePageComments")
def resolve_workspace_page_comments(_, info, slug, page, cursor=None):
    return _page([], cursor)


@query.field("workspacePageCommentsWithIds")
def resolve_workspace_page_comments_with_ids(_, info, slug, page, commentIds):
    return []


@query.field("workspacePageCommentReplies")
def resolve_workspace_page_comment_replies(
    _, info, slug, page, comment, cursor=None
):
    return _page([], cursor)


@query.field("workspacePageComment")
def resolve_workspace_page_comment(_, info, slug, page, comment):
    # SDL: PageCommentType! — no backing model, so there is no comment to return.
    return None


@query.field("workspacePageCommentReactions")
def resolve_workspace_page_comment_reactions(_, info, slug, page, comment):
    return []


# --- Query: workspace page mentions --------------------------------------------------
# No PageMention model; safe empty/None defaults.


@query.field("workspacePageMentions")
def resolve_workspace_page_mentions(_, info, slug, page, entityName=None):
    return []


@query.field("workspacePageMention")
def resolve_workspace_page_mention(_, info, slug, page, mention):
    # SDL: PageMentionType! — no backing model, so there is no mention to return.
    return None


# --- Query: work item / epic pages ---------------------------------------------------


@query.field("workItemPages")
def resolve_work_item_pages(_, info, slug, project, workItem):
    user = _user(info)
    if user is None:
        return []
    return list(_pages_embedding_work_item(slug, workItem))


@query.field("workItemPage")
def resolve_work_item_page(_, info, slug, project, workItem, page):
    user = _user(info)
    if user is None:
        return None
    return _pages_embedding_work_item(slug, workItem).filter(id=page).first()


@query.field("searchWorkItemPages")
def resolve_search_work_item_pages(
    _, info, slug, project, workItem, search=None, isGlobal=False
):
    user = _user(info)
    if user is None:
        return []
    if isGlobal:
        qs = (
            Page.objects.filter(workspace__slug=slug)
            .filter(Q(owned_by=user) | Q(access=0))
            .select_related("workspace", "owned_by")
            .order_by("-created_at")
            .distinct()
        )
    else:
        qs = _pages_embedding_work_item(slug, workItem)
    if search:
        qs = qs.filter(
            Q(name__icontains=search) | Q(description_stripped__icontains=search)
        )
    return list(qs)


@query.field("searchEpicPages")
def resolve_search_epic_pages(
    _, info, slug, project, epic, search=None, isGlobal=False
):
    # An epic is an Issue (IssueType.is_epic=True); the page<->epic embed link is
    # the same PageLog mechanism as work items.
    user = _user(info)
    if user is None:
        return []
    if isGlobal:
        qs = (
            Page.objects.filter(workspace__slug=slug)
            .filter(Q(owned_by=user) | Q(access=0))
            .select_related("workspace", "owned_by")
            .order_by("-created_at")
            .distinct()
        )
    else:
        qs = _pages_embedding_work_item(slug, epic)
    if search:
        qs = qs.filter(
            Q(name__icontains=search) | Q(description_stripped__icontains=search)
        )
    return list(qs)


# --- Mutation: workspace pages -------------------------------------------------------


@mutation.field("createWorkspacePage")
def resolve_create_workspace_page(
    _,
    info,
    slug,
    name="",
    descriptionHtml="",
    logoProps=None,
    access=0,
    descriptionBinary=None,
):
    """Mirror WorkspacePageViewSet.create: a workspace-level (is_global=True) page."""
    user = _user(info)
    if user is None:
        return None
    workspace = Workspace.objects.filter(slug=slug).first()
    if workspace is None:
        return None
    binary = None
    if descriptionBinary:
        try:
            binary = base64.b64decode(descriptionBinary)
        except (ValueError, TypeError):
            binary = None
    page = Page.objects.create(
        workspace=workspace,
        owned_by=user,
        is_global=True,
        name=name or "",
        description_html=descriptionHtml or "<p></p>",
        description_binary=binary,
        logo_props=logoProps or {},
        access=access if access is not None else 0,
        created_by=user,
        updated_by=user,
    )
    return page


@mutation.field("updateWorkspacePage")
def resolve_update_workspace_page(
    _,
    info,
    slug,
    id,
    name=None,
    descriptionHtml=None,
    logoProps=None,
    access=None,
):
    """Mirror WorkspacePageViewSet.partial_update."""
    user = _user(info)
    if user is None:
        return None
    page = Page.objects.filter(
        workspace__slug=slug, is_global=True, id=id
    ).first()
    if page is None:
        return None
    if page.is_locked:
        return page
    # Access may only change when the requester owns the page.
    if (
        access is not None
        and access != page.access
        and page.owned_by_id != user.id
    ):
        access = None
    if name is not None:
        page.name = name
    if descriptionHtml is not None:
        page.description_html = descriptionHtml
    if logoProps is not None:
        page.logo_props = logoProps
    if access is not None:
        page.access = access
    page.updated_by = user
    page.save()
    return page


# --- Mutation: work item pages -------------------------------------------------------


@mutation.field("addWorkItemPage")
def resolve_add_work_item_page(_, info, slug, project, workItem, pageIds):
    """Embed pages into a work item: create the PageLog link rows, then return
    the (now) linked pages as WorkItemPageType."""
    user = _user(info)
    if user is None:
        return []
    workspace = Workspace.objects.filter(slug=slug).first()
    if workspace is None:
        return []
    for raw_page_id in pageIds or []:
        page = Page.objects.filter(
            workspace__slug=slug, id=raw_page_id
        ).first()
        if page is None:
            continue
        PageLog.objects.get_or_create(
            page=page,
            entity_name="issue",
            entity_identifier=workItem,
            defaults={
                "workspace": workspace,
                "transaction": uuid.uuid4(),
                "created_by": user,
                "updated_by": user,
            },
        )
    return list(_pages_embedding_work_item(slug, workItem))


@mutation.field("deleteWorkItemPage")
def resolve_delete_work_item_page(_, info, slug, project, workItem, pageIds):
    """Remove the page<->work-item embed links."""
    user = _user(info)
    if user is None:
        return False
    PageLog.objects.filter(
        workspace__slug=slug,
        entity_name="issue",
        entity_identifier=workItem,
        page_id__in=list(pageIds or []),
    ).delete()
    return True


# --- Mutation: workspace nested child page archive / restore / delete ----------------


def _descendant_page_ids(slug, root_id):
    """All descendants (inclusive) of a workspace page via the parent chain."""
    collected = []
    frontier = [root_id]
    seen = set()
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        collected.append(current)
        children = list(
            Page.objects.filter(
                workspace__slug=slug, is_global=True, parent_id=current
            ).values_list("id", flat=True)
        )
        frontier.extend(children)
    return collected


@mutation.field("workspaceNestedChildArchivePages")
def resolve_workspace_nested_child_archive_pages(_, info, slug, page):
    """Archive a workspace page and all of its descendants; return the descendants."""
    user = _user(info)
    if user is None:
        return []
    ids = _descendant_page_ids(slug, page)
    now = timezone.now().date()
    Page.objects.filter(
        workspace__slug=slug, is_global=True, id__in=ids
    ).update(archived_at=now)
    return list(
        Page.objects.filter(workspace__slug=slug, is_global=True, id__in=ids)
        .select_related("workspace", "owned_by")
        .order_by("sort_order", "-created_at")
    )


@mutation.field("workspaceNestedChildRestorePages")
def resolve_workspace_nested_child_restore_pages(_, info, slug, page):
    """Unarchive a workspace page and all of its descendants; return the descendants."""
    user = _user(info)
    if user is None:
        return []
    ids = _descendant_page_ids(slug, page)
    Page.objects.filter(
        workspace__slug=slug, is_global=True, id__in=ids
    ).update(archived_at=None)
    return list(
        Page.objects.filter(workspace__slug=slug, is_global=True, id__in=ids)
        .select_related("workspace", "owned_by")
        .order_by("sort_order", "-created_at")
    )


@mutation.field("workspaceNestedChildDeletePages")
def resolve_workspace_nested_child_delete_pages(_, info, slug, pages):
    """Delete the given workspace pages (and detach their direct children)."""
    user = _user(info)
    if user is None:
        return False
    page_ids = list(pages or [])
    if not page_ids:
        return True
    # Detach children so the parent FK does not cascade-delete an unintended tree.
    Page.objects.filter(
        workspace__slug=slug, is_global=True, parent_id__in=page_ids
    ).update(parent=None)
    Page.objects.filter(
        workspace__slug=slug, is_global=True, id__in=page_ids
    ).delete()
    # Clean up favourites pointing at the deleted pages.
    UserFavorite.objects.filter(
        workspace__slug=slug,
        entity_type="page",
        entity_identifier__in=[str(pid) for pid in page_ids],
    ).delete()
    return True


# --- ObjectType: PageType ------------------------------------------------------------
# Computed / M2M / binary fields the smart fallback cannot serialise correctly.
# (planning.py binds no PageType fields, so these bindings are unique.)

page_type = ObjectType("PageType")


@page_type.field("isFavorite")
def resolve_page_is_favorite(page, info):
    user = _user(info)
    if user is None:
        return False
    return UserFavorite.objects.filter(
        user=user,
        entity_type="page",
        entity_identifier=str(page.id),
        workspace_id=page.workspace_id,
    ).exists()


@page_type.field("projects")
def resolve_page_projects(page, info):
    return [
        str(pk)
        for pk in ProjectPage.objects.filter(page_id=page.id).values_list(
            "project_id", flat=True
        )
    ]


@page_type.field("isDescriptionEmpty")
def resolve_page_is_description_empty(page, info):
    stripped = (page.description_stripped or "").strip()
    return stripped == ""


@page_type.field("inCollection")
def resolve_page_in_collection(page, info):
    # No page-collection model in this server's db layer.
    return False


@page_type.field("isShared")
def resolve_page_is_shared(page, info):
    # No page-sharing model; a page is "shared" in Cloud terms only via projects.
    return False


@page_type.field("isSharedAccess")
def resolve_page_is_shared_access(page, info):
    # SDL types this as Int! (access level of a share). Default to the page access.
    return page.access if page.access is not None else 0


@page_type.field("descriptionBinary")
def resolve_page_description_binary(page, info):
    raw = page.description_binary
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(raw)).decode("ascii")
    return str(raw)


# --- ObjectType: NestedParentPageLiteType --------------------------------------------

nested_parent_page_lite_type = ObjectType("NestedParentPageLiteType")


@nested_parent_page_lite_type.field("projects")
def resolve_nested_page_projects(page, info):
    return [
        str(pk)
        for pk in ProjectPage.objects.filter(page_id=page.id).values_list(
            "project_id", flat=True
        )
    ]


@nested_parent_page_lite_type.field("isDescriptionEmpty")
def resolve_nested_page_is_description_empty(page, info):
    stripped = (page.description_stripped or "").strip()
    return stripped == ""


# --- ObjectType: WorkItemPageType / EpicPageType -------------------------------------
# Small projections of Page (id/name/logoProps/isGlobal/access). The smart
# fallback resolves every field directly off the Page model, so no explicit
# field bindings are needed; the ObjectType is declared for completeness /
# future computed fields without changing behaviour.

work_item_page_type = ObjectType("WorkItemPageType")
epic_page_type = ObjectType("EpicPageType")


BINDABLES = [
    query,
    mutation,
    page_type,
    nested_parent_page_lite_type,
    work_item_page_type,
    epic_page_type,
]
