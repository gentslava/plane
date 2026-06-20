# OVERLAY: mobile-graphql — mutations for the planning domain.
#
# Covers pages, epics (issues whose IssueType.is_epic=True), epic links,
# epic comments and epic-comment reactions. Returns Django model instances for
# object types (the smart_default_resolver serialises them) and booleans for
# deletions.
#
# NOTE on page comments: there is no PageComment model in this server's db
# layer, so the page-comment / page-comment-reaction mutations are intentionally
# left unbound. install_safe_query_defaults gives them a safe empty value and the
# app keeps working. See the final report for details.
from ariadne import MutationType, ObjectType

from plane.db.models import (
    CommentReaction,
    Issue,
    IssueComment,
    IssueLink,
    IssueType,
    Label,
    Page,
    ProjectPage,
    State,
    Workspace,
)
from plane.graphql.context import get_user

mutation = MutationType()

# EpicType is an Issue (type.is_epic=True). Its assignees/labels are exposed as
# [ID!] and analytics as a non-null aggregate, so a raw Issue would not serialise
# safely through the smart fallback. Bind those fields explicitly.
epic_type = ObjectType("EpicType")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _workspace(slug):
    return Workspace.objects.filter(slug=slug).first()


def _epic_issue_type(workspace):
    """The workspace's epic IssueType, mirroring app/views/issue/iw_epic.py."""
    return IssueType.objects.filter(workspace=workspace, is_epic=True).first()


def _epic_qs(slug, project):
    return Issue.objects.filter(
        workspace__slug=slug, project_id=project, type__is_epic=True
    )


# --------------------------------------------------------------------------- #
# EpicType field resolvers (returned Issue -> EpicType)
# --------------------------------------------------------------------------- #
@epic_type.field("assignees")
def resolve_epic_assignees(epic, info):
    return [str(pk) for pk in epic.assignees.values_list("id", flat=True)]


@epic_type.field("labels")
def resolve_epic_labels(epic, info):
    return [str(pk) for pk in epic.labels.values_list("id", flat=True)]


@epic_type.field("description")
def resolve_epic_description(epic, info):
    return None


@epic_type.field("analytics")
def resolve_epic_analytics(epic, info):
    return {
        "backlog": 0,
        "unstarted": 0,
        "started": 0,
        "completed": 0,
        "cancelled": 0,
    }


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@mutation.field("createPage")
def resolve_create_page(
    _,
    info,
    slug,
    project,
    name,
    descriptionHtml="",
    logoProps=None,
    access=2,
    descriptionBinary=None,
):
    user = get_user(info)
    workspace = _workspace(slug)
    if user is None or workspace is None:
        return None

    page = Page.objects.create(
        workspace=workspace,
        owned_by=user,
        name=name,
        description_html=descriptionHtml or "<p></p>",
        access=access,
        logo_props=logoProps or {},
        created_by=user,
        updated_by=user,
    )
    # Pages link to projects through the ProjectPage through-model.
    ProjectPage.objects.create(
        workspace=workspace,
        project_id=project,
        page=page,
        created_by=user,
        updated_by=user,
    )
    return page


@mutation.field("updatePage")
def resolve_update_page(
    _,
    info,
    slug,
    project,
    id,
    name=None,
    descriptionHtml=None,
    logoProps=None,
    access=None,
):
    user = get_user(info)
    page = Page.objects.filter(
        workspace__slug=slug, project_pages__project_id=project, id=id
    ).first()
    if page is None:
        return None

    if name is not None:
        page.name = name
    if descriptionHtml is not None:
        page.description_html = descriptionHtml
    if logoProps is not None:
        page.logo_props = logoProps
    if access is not None:
        page.access = access
    if user is not None:
        page.updated_by = user
    page.save()
    return page


@mutation.field("batchCreatePages")
def resolve_batch_create_pages(_, info, slug, project, pages):
    """Create several pages at once. Returns Void (None)."""
    user = get_user(info)
    workspace = _workspace(slug)
    if user is None or workspace is None:
        return None

    for page_input in pages or []:
        page = Page.objects.create(
            workspace=workspace,
            owned_by=user,
            name=page_input.get("name") or "",
            description_html=page_input.get("descriptionHtml") or "<p></p>",
            access=page_input.get("access", 2),
            logo_props=page_input.get("logoProps") or {},
            created_by=user,
            updated_by=user,
        )
        ProjectPage.objects.create(
            workspace=workspace,
            project_id=project,
            page=page,
            created_by=user,
            updated_by=user,
        )
    return None


@mutation.field("archiveWorkspacePage")
def resolve_archive_workspace_page(_, info, slug, pageId):
    from django.utils import timezone

    user = get_user(info)
    page = Page.objects.filter(workspace__slug=slug, id=pageId).first()
    if page is None:
        return None
    page.archived_at = timezone.now().date()
    if user is not None:
        page.updated_by = user
    page.save()
    return page


@mutation.field("unarchiveWorkspacePage")
def resolve_unarchive_workspace_page(_, info, slug, pageId):
    user = get_user(info)
    page = Page.objects.filter(workspace__slug=slug, id=pageId).first()
    if page is None:
        return None
    page.archived_at = None
    if user is not None:
        page.updated_by = user
    page.save()
    return page


@mutation.field("lockWorkspacePage")
def resolve_lock_workspace_page(_, info, slug, pageId, cascade=False):
    user = get_user(info)
    page = Page.objects.filter(workspace__slug=slug, id=pageId).first()
    if page is None:
        return None
    page.is_locked = True
    if user is not None:
        page.updated_by = user
    page.save()
    return page


@mutation.field("unlockWorkspacePage")
def resolve_unlock_workspace_page(_, info, slug, pageId, cascade=False):
    user = get_user(info)
    page = Page.objects.filter(workspace__slug=slug, id=pageId).first()
    if page is None:
        return None
    page.is_locked = False
    if user is not None:
        page.updated_by = user
    page.save()
    return page


# --------------------------------------------------------------------------- #
# Epics (Issue with IssueType.is_epic=True)
# --------------------------------------------------------------------------- #
def _apply_epic_m2m(epic, epic_input, project):
    """Set assignees / labels from an epic input dict (lists of ids)."""
    assignees = epic_input.get("assignees")
    if assignees is not None:
        epic.assignees.set(assignees)
    labels = epic_input.get("labels")
    if labels is not None:
        epic.labels.set(
            list(Label.objects.filter(project_id=project, id__in=labels))
        )


@mutation.field("createEpic")
def resolve_create_epic(_, info, slug, project, epicInput):
    user = get_user(info)
    workspace = _workspace(slug)
    if user is None or workspace is None:
        return None

    epic_type_obj = _epic_issue_type(workspace)

    state_id = epicInput.get("state")
    state = None
    if state_id:
        state = State.objects.filter(project_id=project, id=state_id).first()

    epic = Issue.objects.create(
        workspace=workspace,
        project_id=project,
        type=epic_type_obj,
        name=epicInput.get("name") or "",
        description_html=epicInput.get("descriptionHtml") or "<p></p>",
        priority=epicInput.get("priority") or "none",
        start_date=epicInput.get("startDate"),
        target_date=epicInput.get("targetDate"),
        state=state,
        created_by=user,
        updated_by=user,
    )
    _apply_epic_m2m(epic, epicInput, project)
    return epic


@mutation.field("updateEpic")
def resolve_update_epic(_, info, slug, project, epic, epicInput=None):
    user = get_user(info)
    epic_obj = _epic_qs(slug, project).filter(id=epic).first()
    if epic_obj is None:
        return None
    if not epicInput:
        return epic_obj

    if epicInput.get("name") is not None:
        epic_obj.name = epicInput["name"]
    if epicInput.get("descriptionHtml") is not None:
        epic_obj.description_html = epicInput["descriptionHtml"]
    if epicInput.get("priority") is not None:
        epic_obj.priority = epicInput["priority"]
    if "startDate" in epicInput:
        epic_obj.start_date = epicInput.get("startDate")
    if "targetDate" in epicInput:
        epic_obj.target_date = epicInput.get("targetDate")
    if epicInput.get("state") is not None:
        state = State.objects.filter(
            project_id=project, id=epicInput["state"]
        ).first()
        if state is not None:
            epic_obj.state = state
    if user is not None:
        epic_obj.updated_by = user
    epic_obj.save()
    _apply_epic_m2m(epic_obj, epicInput, project)
    return epic_obj


@mutation.field("deleteEpic")
def resolve_delete_epic(_, info, slug, project, epic):
    epic_obj = _epic_qs(slug, project).filter(id=epic).first()
    if epic_obj is None:
        return False
    epic_obj.delete()
    return True


# --------------------------------------------------------------------------- #
# Epic links (IssueLink)
# --------------------------------------------------------------------------- #
@mutation.field("createEpicLink")
def resolve_create_epic_link(_, info, slug, project, epic, linkInput):
    user = get_user(info)
    epic_obj = _epic_qs(slug, project).filter(id=epic).first()
    if epic_obj is None:
        return None
    link = IssueLink.objects.create(
        project_id=project,
        issue=epic_obj,
        url=linkInput.get("url") or "",
        title=linkInput.get("title"),
        created_by=user,
        updated_by=user,
    )
    return link


@mutation.field("updateEpicLink")
def resolve_update_epic_link(_, info, slug, project, epic, link, linkInput):
    user = get_user(info)
    link_obj = IssueLink.objects.filter(
        project_id=project, issue_id=epic, id=link
    ).first()
    if link_obj is None:
        return None
    if linkInput.get("url") is not None:
        link_obj.url = linkInput["url"]
    if linkInput.get("title") is not None:
        link_obj.title = linkInput["title"]
    if user is not None:
        link_obj.updated_by = user
    link_obj.save()
    return link_obj


@mutation.field("deleteEpicLink")
def resolve_delete_epic_link(_, info, slug, project, epic, link):
    link_obj = IssueLink.objects.filter(
        project_id=project, issue_id=epic, id=link
    ).first()
    if link_obj is None:
        return False
    link_obj.delete()
    return True


# --------------------------------------------------------------------------- #
# Epic comments (IssueComment) and reactions (CommentReaction)
# --------------------------------------------------------------------------- #
@mutation.field("addEpicComment")
def resolve_add_epic_comment(_, info, slug, project, epic, commentInput):
    user = get_user(info)
    epic_obj = _epic_qs(slug, project).filter(id=epic).first()
    if epic_obj is None:
        return None
    comment = IssueComment.objects.create(
        project_id=project,
        issue=epic_obj,
        actor=user,
        comment_html=commentInput.get("commentHtml") or "<p></p>",
        created_by=user,
        updated_by=user,
    )
    return comment


@mutation.field("deleteEpicComment")
def resolve_delete_epic_comment(_, info, slug, project, epic, comment):
    comment_obj = IssueComment.objects.filter(
        project_id=project, issue_id=epic, id=comment
    ).first()
    if comment_obj is None:
        return False
    comment_obj.delete()
    return True


def _comment_reaction_payload(comment, reaction):
    """Aggregate the CommentReactionType payload: {reaction, userIds}."""
    user_ids = [
        str(pk)
        for pk in CommentReaction.objects.filter(
            comment_id=comment, reaction=reaction
        ).values_list("actor_id", flat=True)
    ]
    return {"reaction": reaction, "user_ids": user_ids}


@mutation.field("addEpicCommentReaction")
def resolve_add_epic_comment_reaction(
    _, info, slug, project, epic, comment, reactionInput
):
    user = get_user(info)
    reaction = reactionInput.get("reaction")
    comment_obj = IssueComment.objects.filter(
        project_id=project, issue_id=epic, id=comment
    ).first()
    if user is None or comment_obj is None or not reaction:
        return {"reaction": reaction or "", "user_ids": []}
    CommentReaction.objects.get_or_create(
        project_id=project,
        comment=comment_obj,
        actor=user,
        reaction=reaction,
        defaults={"created_by": user, "updated_by": user},
    )
    return _comment_reaction_payload(comment, reaction)


@mutation.field("removeEpicCommentReaction")
def resolve_remove_epic_comment_reaction(
    _, info, slug, project, epic, comment, reactionInput
):
    user = get_user(info)
    reaction = reactionInput.get("reaction")
    if user is None or not reaction:
        return {"reaction": reaction or "", "user_ids": []}
    CommentReaction.objects.filter(
        project_id=project, comment_id=comment, actor=user, reaction=reaction
    ).delete()
    return _comment_reaction_payload(comment, reaction)


BINDABLES = [mutation, epic_type]
