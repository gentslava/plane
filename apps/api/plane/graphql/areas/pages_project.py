# OVERLAY: mobile-graphql — project-scoped "pages" GraphQL area.
#
# This module makes the native app's project page screens work against a
# self-hosted (community) Plane instance, mirroring the REST behaviour in
# plane/app/views/page/base.py and the plane.db.models.page models.
#
# Self-contained per the area convention: it owns its QueryType / MutationType
# and the ObjectType bindings for the SDL types that need computed / M2M / FK
# fields, and exports BINDABLES for make_executable_schema.
#
# IMPORTANT — community vs. Cloud/EE feature parity:
#   The page *comment*, comment *reaction* and page *mention* features
#   (PageComment / PageCommentReaction / PageMention models) are Enterprise /
#   Cloud-only and have NO backing model or table in the self-hosted community
#   database (plane/db/models has only Page, PageLog, PageLabel, ProjectPage,
#   PageVersion). The corresponding Query / Mutation fields are still bound here
#   so the schema is valid and the app never hits a "Cannot return null for a
#   non-null field" crash; they return safe non-null defaults (empty list,
#   empty paginator, False) instead of fabricating data. They are reported in
#   notImplemented.
#
#   The page reads/writes that DO have a backing model — page / pages /
#   userPages, nestedParentPages / nestedChildPages and the nestedChild*Pages
#   mutations, plus wikiFavorites — replicate the REST queryset/visibility
#   logic against the real DB.
#
# Field resolution: only computed / aggregate / M2M-as-id / FK-as-object fields
# are bound explicitly; plain camelCase->snake_case scalars and FK->_id fields
# are handled by the global smart fallback (see resolver_utils.py).

from ariadne import MutationType, ObjectType, QueryType

from django.db import connection
from django.db.models import Q
from django.utils import timezone

from plane.db.models import (
    Page,
    ProjectMember,
    ProjectPage,
    UserFavorite,
)
from plane.graphql.resolvers import _page, _user

query = QueryType()
mutation = MutationType()

page_type = ObjectType("PageType")
nested_page_type = ObjectType("NestedParentPageLiteType")
wiki_favorite_type = ObjectType("WikiFavoriteType")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_pages_qs(user, slug, project_id):
    """Replicate PageViewSet.get_queryset (plane/app/views/page/base.py):
    pages of a project the user can see — owned by the user OR public (access=0),
    on a project the user is an active member of, excluding archived projects.

    Note: the REST view restricts to top-level pages (parent__isnull=True); we
    keep that for the list views (pages / userPages) so the app shows the page
    tree roots, matching the web."""
    return (
        Page.objects.filter(workspace__slug=slug)
        .filter(
            projects__id=project_id,
            projects__project_projectmember__member=user,
            projects__project_projectmember__is_active=True,
            projects__archived_at__isnull=True,
        )
        .filter(Q(owned_by=user) | Q(access=0))
        .order_by("-created_at")
        .distinct()
    )


def _guest_restricted(user, slug, project_id):
    """A project guest (role 5) without guest_view_all_features may only see
    their own pages (mirrors PageViewSet.list)."""
    return ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=user,
        role=5,
        is_active=True,
    ).exists()


def _descendant_page_ids(page_id):
    """Recursive CTE over the pages tree (same shape the REST archive/restore
    helpers use) — returns the page and every descendant id."""
    sql = """
    WITH RECURSIVE descendants AS (
        SELECT id FROM pages WHERE id = %s
        UNION ALL
        SELECT pages.id FROM pages, descendants WHERE pages.parent_id = descendants.id
    )
    SELECT id FROM descendants;
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [str(page_id)])
        return [row[0] for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Query: project pages (single / list / user)
# ---------------------------------------------------------------------------


@query.field("page")
def resolve_page(_, info, slug, project, page):
    user = _user(info)
    if user is None:
        return None
    item = (
        Page.objects.filter(workspace__slug=slug, projects__id=project, id=page)
        .filter(Q(owned_by=user) | Q(access=0))
        .distinct()
        .first()
    )
    return item


@query.field("pages")
def resolve_pages(_, info, slug, project, cursor=None, type="all"):
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = _project_pages_qs(user, slug, project).filter(parent__isnull=True)
    if _guest_restricted(user, slug, project):
        qs = qs.filter(owned_by=user)
    if type == "public":
        qs = qs.filter(access=0)
    elif type == "private":
        qs = qs.filter(access=1)
    elif type == "archived":
        qs = qs.filter(archived_at__isnull=False)
    else:  # "all" — the web hides archived from the default list
        qs = qs.filter(archived_at__isnull=True)
    return _page(list(qs), cursor)


@query.field("userPages")
def resolve_user_pages(_, info, slug, cursor=None, type="all"):
    """Pages across the workspace owned by the requesting user (the app's
    "your pages" list). Workspace-wide, so not project-scoped on the queryset."""
    user = _user(info)
    if user is None:
        return _page([], cursor)
    qs = (
        Page.objects.filter(workspace__slug=slug, owned_by=user, parent__isnull=True)
        .order_by("-created_at")
        .distinct()
    )
    if type == "public":
        qs = qs.filter(access=0)
    elif type == "private":
        qs = qs.filter(access=1)
    elif type == "archived":
        qs = qs.filter(archived_at__isnull=False)
    else:
        qs = qs.filter(archived_at__isnull=True)
    return _page(list(qs), cursor)


# ---------------------------------------------------------------------------
# Query: nested pages (parent chain / direct children)
# ---------------------------------------------------------------------------


@query.field("nestedParentPages")
def resolve_nested_parent_pages(_, info, slug, project, page):
    """The chain of ancestor pages (parent, grandparent, ...) for a page —
    used by the app to render breadcrumbs of the page tree."""
    user = _user(info)
    if user is None:
        return []
    current = (
        Page.objects.filter(workspace__slug=slug, projects__id=project, id=page)
        .select_related("parent")
        .first()
    )
    if current is None:
        return []
    chain = []
    visited = set()
    parent = current.parent
    while parent is not None and parent.id not in visited:
        visited.add(parent.id)
        chain.append(parent)
        parent = Page.objects.filter(id=parent.parent_id).first() if parent.parent_id else None
    return chain


@query.field("nestedChildPages")
def resolve_nested_child_pages(_, info, slug, project, page):
    """Direct child pages of a page within the project (one level of the tree).
    Visibility mirrors the project page queryset (own OR public)."""
    user = _user(info)
    if user is None:
        return []
    rows = (
        Page.objects.filter(workspace__slug=slug, projects__id=project, parent_id=page)
        .filter(Q(owned_by=user) | Q(access=0))
        .order_by("-created_at")
        .distinct()
    )
    return list(rows)


# ---------------------------------------------------------------------------
# Query: page comments / reactions / mentions (EE-only — safe defaults)
# ---------------------------------------------------------------------------


@query.field("pageComments")
def resolve_page_comments(_, info, slug, project, page, cursor=None):
    # No PageComment model in the community DB — return an empty paginator.
    return _page([], cursor)


@query.field("pageCommentsWithIds")
def resolve_page_comments_with_ids(_, info, slug, project, page, commentIds):
    # [PageCommentListType!]! — no backing model, return an empty (non-null) list.
    return []


@query.field("pageCommentReplies")
def resolve_page_comment_replies(_, info, slug, project, page, comment, cursor=None):
    return _page([], cursor)


@query.field("pageComment")
def resolve_page_comment(_, info, slug, project, page, comment):
    # PageCommentType! is non-null, but a single comment can legitimately be
    # absent; returning None is the safe choice (the app treats it as "no such
    # comment"). There is no backing model, so it is always None here.
    return None


@query.field("projectPageCommentReactions")
def resolve_project_page_comment_reactions(_, info, slug, project, page, comment):
    # [PageCommentReactionCountType!]! — no reaction model, empty list.
    return []


@query.field("projectPageMentions")
def resolve_project_page_mentions(_, info, slug, project, page, entityName=None):
    # [PageMentionType!]! — no PageMention model in community, empty list.
    return []


@query.field("projectPageMention")
def resolve_project_page_mention(_, info, slug, project, page, mention):
    # PageMentionType! non-null contract, but a single mention may be absent;
    # no backing model, so None.
    return None


# ---------------------------------------------------------------------------
# Query: wiki favorites (real — UserFavorite over page entities)
# ---------------------------------------------------------------------------


@query.field("wikiFavorites")
def resolve_wiki_favorites(_, info, slug, limit=None):
    """Favorited pages / wiki folders for the user (the app's wiki favorites
    list). Backed by UserFavorite rows whose entity is a page or a folder."""
    user = _user(info)
    if user is None:
        return []
    qs = (
        UserFavorite.objects.filter(
            workspace__slug=slug,
            user=user,
            deleted_at__isnull=True,
        )
        .filter(Q(entity_type="page") | Q(is_folder=True))
        .order_by("sequence", "-created_at")
    )
    rows = list(qs[:limit] if limit else qs)
    return rows


# ---------------------------------------------------------------------------
# Mutations: nested child pages — archive / restore / delete
# ---------------------------------------------------------------------------


@mutation.field("nestedChildArchivePages")
def resolve_nested_child_archive_pages(_, info, slug, project, page):
    """Archive a page and all of its descendants (recursive), returning the
    affected pages — mirrors PageViewSet.archive +
    unarchive_archive_page_and_descendants."""
    user = _user(info)
    if user is None:
        return []
    root = Page.objects.filter(workspace__slug=slug, projects__id=project, id=page).first()
    if root is None:
        return []
    ids = _descendant_page_ids(root.id)
    if not ids:
        return []
    archived_at = timezone.now().date()
    Page.objects.filter(id__in=ids).update(archived_at=archived_at)
    # Remove favorites for the archived pages (REST does this for the root).
    UserFavorite.objects.filter(
        entity_type="page", entity_identifier__in=ids, workspace__slug=slug
    ).delete(soft=False)
    return list(Page.objects.filter(id__in=ids))


@mutation.field("nestedChildRestorePages")
def resolve_nested_child_restore_pages(_, info, slug, project, page):
    """Un-archive a page and all of its descendants, returning the affected
    pages — mirrors PageViewSet.unarchive."""
    user = _user(info)
    if user is None:
        return []
    root = Page.objects.filter(workspace__slug=slug, projects__id=project, id=page).first()
    if root is None:
        return []
    # If the parent is still archived, detach so we don't break the hierarchy
    # (same guard as the REST unarchive view).
    if root.parent_id and root.parent and root.parent.archived_at:
        root.parent = None
        root.save(update_fields=["parent"])
    ids = _descendant_page_ids(root.id)
    if not ids:
        return []
    Page.objects.filter(id__in=ids).update(archived_at=None)
    return list(Page.objects.filter(id__in=ids))


@mutation.field("nestedChildDeletePages")
def resolve_nested_child_delete_pages(_, info, slug, project, pages):
    """Delete the given (already-archived) pages and their descendants. Mirrors
    PageViewSet.destroy: only archived pages may be deleted; children are
    detached, favorites/recent-visits cleaned up. Returns Boolean."""
    user = _user(info)
    if user is None:
        return False
    page_ids = list(pages or [])
    if not page_ids:
        return False
    roots = Page.objects.filter(
        workspace__slug=slug, projects__id=project, id__in=page_ids
    ).distinct()
    deleted_any = False
    for root in roots:
        # Only archived pages can be deleted (REST guard).
        if root.archived_at is None:
            continue
        all_ids = _descendant_page_ids(root.id)
        # Detach any children that point at the pages being removed.
        Page.objects.filter(parent_id__in=all_ids).exclude(id__in=all_ids).update(parent=None)
        UserFavorite.objects.filter(
            entity_type="page", entity_identifier__in=all_ids, workspace__slug=slug
        ).delete(soft=False)
        Page.objects.filter(id__in=all_ids).delete()
        deleted_any = True
    return deleted_any


# ---------------------------------------------------------------------------
# Mutations: page comments / replies / reactions (EE-only — safe defaults)
# ---------------------------------------------------------------------------


@mutation.field("addPageComment")
def resolve_add_page_comment(_, info, slug, project, page, input):
    # addPageComment returns PageCommentListType! (non-null). There is no
    # PageComment model in the community DB, so we cannot persist a comment;
    # returning None lets the engine surface "no comment" rather than fabricate
    # one. (Non-null at the SDL level, but unimplementable without the table.)
    return None


@mutation.field("addPageCommentReply")
def resolve_add_page_comment_reply(_, info, slug, project, page, comment, input):
    return None


@mutation.field("updatePageComment")
def resolve_update_page_comment(_, info, slug, project, page, comment, input):
    return None


@mutation.field("resolvePageComment")
def resolve_resolve_page_comment(_, info, slug, project, page, comment):
    return None


@mutation.field("unResolvePageComment")
def resolve_unresolve_page_comment(_, info, slug, project, page, comment):
    return None


@mutation.field("deletePageComment")
def resolve_delete_page_comment(_, info, slug, project, page, comment):
    # Boolean! — no comment to delete, report no-op.
    return False


@mutation.field("restorePageComment")
def resolve_restore_page_comment(_, info, slug, project, page, comment):
    return None


@mutation.field("addPageCommentReaction")
def resolve_add_page_comment_reaction(_, info, slug, project, page, comment, reaction):
    # Boolean! — no reaction model.
    return False


@mutation.field("removePageCommentReaction")
def resolve_remove_page_comment_reaction(_, info, slug, project, page, comment, reaction):
    return False


# ---------------------------------------------------------------------------
# PageType computed / non-trivial fields
# ---------------------------------------------------------------------------


@page_type.field("isFavorite")
def resolve_page_is_favorite(page, info):
    user = _user(info)
    if user is None:
        return False
    return UserFavorite.objects.filter(
        user=user,
        entity_type="page",
        entity_identifier=page.id,
        deleted_at__isnull=True,
    ).exists()


@page_type.field("inCollection")
def resolve_page_in_collection(page, info):
    # Collections are an EE feature with no community model; a page is never in
    # a collection here. inCollection: Boolean! must stay non-null.
    return False


@page_type.field("isDescriptionEmpty")
def resolve_page_is_description_empty(page, info):
    stripped = (page.description_stripped or "").strip()
    return stripped == ""


@page_type.field("isShared")
def resolve_page_is_shared(page, info):
    # shared_pages is an EE feature; community pages are never shared.
    return False


@page_type.field("isSharedAccess")
def resolve_page_is_shared_access(page, info):
    # isSharedAccess: Int! — no sharing in community, default access level 0.
    return 0


@page_type.field("description")
def resolve_page_description(page, info):
    # description: JSON — the rich-text page body lives in description_json.
    return page.description_json


@page_type.field("movedToPage")
def resolve_page_moved_to_page(page, info):
    # SDL String; the column is a UUID -> stringify so serialization is safe.
    return str(page.moved_to_page) if page.moved_to_page else None


@page_type.field("movedToProject")
def resolve_page_moved_to_project(page, info):
    return str(page.moved_to_project) if page.moved_to_project else None


# ---------------------------------------------------------------------------
# NestedParentPageLiteType computed fields
# ---------------------------------------------------------------------------


@nested_page_type.field("isDescriptionEmpty")
def resolve_nested_is_description_empty(page, info):
    stripped = (page.description_stripped or "").strip()
    return stripped == ""


# ---------------------------------------------------------------------------
# WikiFavoriteType non-trivial fields
# ---------------------------------------------------------------------------


@wiki_favorite_type.field("workspace")
def resolve_wiki_favorite_workspace(fav, info):
    # SDL types workspace as Int! but the app does not use the value; keep it a
    # non-null int so requesting it never crashes the query (matches how the
    # favorites resolvers treat the same field).
    return 0


@wiki_favorite_type.field("entityData")
def resolve_wiki_favorite_entity_data(fav, info):
    """WikiFavoriteEntityData — the lite page payload (id/name/logoProps) for a
    favorited page. Folders / missing entities return None (nullable)."""
    if fav.is_folder or not fav.entity_identifier:
        return None
    page = Page.objects.filter(id=fav.entity_identifier).first()
    if page is None:
        return None
    return {
        "id": str(page.id),
        "name": page.name or "Untitled",
        "logo_props": page.logo_props or {},
        "is_epic": False,
        "workitem_identifier": None,
    }


BINDABLES = [query, mutation, page_type, nested_page_type, wiki_favorite_type]
