# OVERLAY: mobile-graphql — Mutations for the "work items / issues" domain.
#
# Implements the Mutation fields the native app uses to create and manage work
# items (issues), their sub-issues, cycle/module membership, comments, comment
# reactions/replies, links, relations, subscriptions and attachments.
#
# Conventions (see overlay/features/mobile-graphql/SUBAGENT-BRIEF.md):
#   * ariadne MutationType; resolver arg names are camelCase, exactly as the SDL.
#   * Input objects (e.g. issueInput) arrive as dicts with camelCase keys.
#   * Object-returning mutations return the Django model instance; the engine's
#     smart fallback resolver serialises fields (camelCase->snake_case, FK->_id).
#   * Boolean mutations return True/False.
#   * The current user comes from plane.graphql.context.get_user(info); when there
#     is no authenticated user we fail safe (False / None) rather than crash.
#
# The Issue model's save() already assigns sequence_id atomically (advisory lock +
# IssueSequence) and fills the default project state when none is provided, so
# createIssueV2 only needs to set the audit fields and the M2M relations.
import json
import uuid

from ariadne import MutationType

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from plane.bgtasks.issue_activities_task import issue_activity

from plane.db.models import (
    CommentReaction,
    Cycle,
    CycleIssue,
    FileAsset,
    Issue,
    IssueAssignee,
    IssueComment,
    IssueLabel,
    IssueLink,
    IssueRelation,
    IssueSubscriber,
    Module,
    ModuleIssue,
    Project,
    State,
)
from plane.graphql.context import get_user
from plane.graphql.resolvers import _member_project


def _to_date(value):
    """The app sends start/target dates as ISO DateTime; the model wants a date."""
    if not value:
        return None
    if isinstance(value, str):
        return value[:10]
    return value


def _set_issue_assignees(issue, user_ids, user):
    # The IssueAssignee/IssueLabel through models are ProjectBaseModel (project +
    # workspace are NOT NULL), so .set() would violate the constraint — create the
    # rows explicitly instead.
    IssueAssignee.objects.filter(issue=issue).delete()
    IssueAssignee.objects.bulk_create(
        [
            IssueAssignee(
                issue=issue,
                assignee_id=uid,
                project=issue.project,
                workspace=issue.workspace,
                created_by=user,
                updated_by=user,
            )
            for uid in user_ids
        ],
        ignore_conflicts=True,
    )


# GraphQL input key -> the key names the issue_activity diff task reads.
_ACTIVITY_KEY = {
    "descriptionHtml": "description_html",
    "startDate": "start_date",
    "targetDate": "target_date",
    "estimatePoint": "estimate_point",
}


def _activity_requested(data):
    out = {}
    for key, value in data.items():
        mapped = _ACTIVITY_KEY.get(key, key)
        if mapped in ("start_date", "target_date"):
            value = _to_date(value)
        out[mapped] = value
    return out


def _issue_snapshot(issue):
    return {
        "name": issue.name,
        "description_html": issue.description_html,
        "priority": issue.priority,
        "state": str(issue.state_id) if issue.state_id else None,
        "parent": str(issue.parent_id) if issue.parent_id else None,
        "start_date": issue.start_date.isoformat() if issue.start_date else None,
        "target_date": issue.target_date.isoformat() if issue.target_date else None,
        "estimate_point": str(issue.estimate_point_id) if issue.estimate_point_id else None,
        "assignees": [str(x) for x in issue.assignees.values_list("id", flat=True)],
        "labels": [str(x) for x in issue.labels.values_list("id", flat=True)],
    }


def _log_issue_activity(issue, user, requested, current_instance, activity_type):
    # The async task computes the diff (current vs requested) and writes the
    # IssueActivity feed entries (who changed state/priority/assignees/dates/...).
    try:
        issue_activity.delay(
            type=activity_type,
            requested_data=json.dumps(requested, cls=DjangoJSONEncoder),
            actor_id=str(user.id),
            issue_id=str(issue.id),
            project_id=str(issue.project_id),
            current_instance=(json.dumps(current_instance, cls=DjangoJSONEncoder) if current_instance is not None else None),
            epoch=int(timezone.now().timestamp()),
            notification=False,
            origin=None,
        )
    except Exception:
        pass


def _set_issue_labels(issue, label_ids, user):
    IssueLabel.objects.filter(issue=issue).delete()
    IssueLabel.objects.bulk_create(
        [
            IssueLabel(
                issue=issue,
                label_id=lid,
                project=issue.project,
                workspace=issue.workspace,
                created_by=user,
                updated_by=user,
            )
            for lid in label_ids
        ],
        ignore_conflicts=True,
    )

mutation = MutationType()


# --- helpers -------------------------------------------------------------------


def _issue(project, issue_id):
    """Issue scoped to a project, or None."""
    if project is None:
        return None
    return Issue.objects.filter(project=project, id=issue_id).first()


# Forward/reverse mapping for issue relations (mirrors IssueRelationChoices pairs).
_RELATION_REVERSE = {
    "blocked_by": "blocking",
    "blocking": "blocked_by",
    "relates_to": "relates_to",
    "duplicate": "duplicate",
    "start_before": "start_after",
    "start_after": "start_before",
    "finish_before": "finish_after",
    "finish_after": "finish_before",
    "implemented_by": "implements",
    "implements": "implemented_by",
}


# --- Work item create / update / delete ----------------------------------------


@mutation.field("createIssueV2")
def resolve_create_issue_v2(_, info, slug, project, issueInput):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None

    data = issueInput or {}

    state = None
    state_id = data.get("state")
    if state_id:
        state = State.objects.filter(project=p, id=state_id).first()

    parent = None
    parent_id = data.get("parent")
    if parent_id:
        parent = Issue.objects.filter(project=p, id=parent_id).first()

    issue = Issue(
        name=data.get("name") or "",
        project=p,
        workspace=p.workspace,
        priority=data.get("priority") or "none",
        description_html=data.get("descriptionHtml") or "<p></p>",
        start_date=_to_date(data.get("startDate")),
        target_date=_to_date(data.get("targetDate")),
        state=state,
        parent=parent,
        estimate_point_id=data.get("estimatePoint"),
        created_by=user,
        updated_by=user,
    )
    # Issue.save() assigns sequence_id atomically and fills the default state.
    issue.save()

    # M2M relations are set after the instance exists (through models need project).
    assignee_ids = data.get("assignees") or []
    if assignee_ids:
        _set_issue_assignees(issue, assignee_ids, user)
    label_ids = data.get("labels") or []
    if label_ids:
        _set_issue_labels(issue, label_ids, user)

    # Optional cycle membership.
    cycle_id = data.get("cycleId")
    if cycle_id and Cycle.objects.filter(project=p, id=cycle_id).exists():
        CycleIssue.objects.update_or_create(
            issue=issue,
            project=p,
            defaults={
                "cycle_id": cycle_id,
                "workspace": p.workspace,
                "created_by": user,
                "updated_by": user,
            },
        )

    # Optional module membership.
    module_ids = data.get("moduleIds") or []
    for module_id in module_ids:
        if Module.objects.filter(project=p, id=module_id).exists():
            ModuleIssue.objects.get_or_create(
                issue=issue,
                module_id=module_id,
                project=p,
                defaults={
                    "workspace": p.workspace,
                    "created_by": user,
                    "updated_by": user,
                },
            )

    _log_issue_activity(issue, user, _activity_requested(data), None, "issue.activity.created")
    return issue


@mutation.field("updateIssueV2")
def resolve_update_issue_v2(_, info, slug, project, id, issueInput=None):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    issue = _issue(p, id)
    if issue is None:
        return None

    data = issueInput or {}
    before = _issue_snapshot(issue)  # old values for the activity diff
    update_fields = ["updated_by", "updated_at"]

    if "name" in data and data.get("name") is not None:
        issue.name = data["name"]
        update_fields.append("name")
    if "descriptionHtml" in data and data.get("descriptionHtml") is not None:
        issue.description_html = data["descriptionHtml"]
        update_fields.append("description_html")
    if "priority" in data and data.get("priority") is not None:
        issue.priority = data["priority"]
        update_fields.append("priority")
    if "startDate" in data:
        issue.start_date = _to_date(data.get("startDate"))
        update_fields.append("start_date")
    if "targetDate" in data:
        issue.target_date = _to_date(data.get("targetDate"))
        update_fields.append("target_date")
    if "state" in data and data.get("state"):
        new_state = State.objects.filter(project=p, id=data["state"]).first()
        if new_state is not None:
            issue.state = new_state
            update_fields.append("state")
    if "parent" in data:
        parent_id = data.get("parent")
        issue.parent = Issue.objects.filter(project=p, id=parent_id).first() if parent_id else None
        update_fields.append("parent")
    if "estimatePoint" in data:
        issue.estimate_point_id = data.get("estimatePoint")
        update_fields.append("estimate_point")

    issue.updated_by = user
    issue.save(update_fields=list(set(update_fields)))

    if "assignees" in data and data.get("assignees") is not None:
        _set_issue_assignees(issue, data["assignees"], user)
    if "labels" in data and data.get("labels") is not None:
        _set_issue_labels(issue, data["labels"], user)

    _log_issue_activity(issue, user, _activity_requested(data), before, "issue.activity.updated")
    issue.refresh_from_db()
    return issue


@mutation.field("deleteWorkItem")
def resolve_delete_work_item(_, info, slug, project, workItem):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    issue = _issue(p, workItem)
    if issue is None:
        return False
    issue.delete()
    return True


@mutation.field("archiveWorkItem")
def resolve_archive_work_item(_, info, slug, project, workItem):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    issue = _issue(p, workItem)
    if issue is None:
        return False
    issue.archived_at = timezone.now().date()
    issue.updated_by = user
    issue.save(update_fields=["archived_at", "updated_by", "updated_at"])
    return True


@mutation.field("unarchiveWorkItem")
def resolve_unarchive_work_item(_, info, slug, project, workItem):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    issue = _issue(p, workItem)
    if issue is None:
        return False
    issue.archived_at = None
    issue.updated_by = user
    issue.save(update_fields=["archived_at", "updated_by", "updated_at"])
    return True


# --- Sub-issues ----------------------------------------------------------------


@mutation.field("createSubIssue")
def resolve_create_sub_issue(_, info, slug, project, parentIssueId, subIssueIds):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    parent = _issue(p, parentIssueId)
    if parent is None:
        return False
    Issue.objects.filter(project=p, id__in=subIssueIds or []).update(parent=parent, updated_by=user)
    return True


@mutation.field("removeSubIssue")
def resolve_remove_sub_issue(_, info, slug, project, parentIssueId, subIssueId):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    Issue.objects.filter(project=p, id=subIssueId, parent_id=parentIssueId).update(parent=None, updated_by=user)
    return True


@mutation.field("addExistingWorkItems")
def resolve_add_existing_work_items(_, info, slug, project, epic, workItemIds):
    """Nest existing work items under an epic (an Issue whose type is 'Epic')."""
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    epic_issue = _issue(p, epic)
    if epic_issue is None:
        return False
    Issue.objects.filter(project=p, id__in=workItemIds or []).update(parent=epic_issue, updated_by=user)
    return True


# --- Cycle / Module membership -------------------------------------------------


@mutation.field("issueCycle")
def resolve_issue_cycle(_, info, slug, project, issue, cycle=None):
    """Set (or clear, when cycle is null) the cycle of a single work item."""
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    issue_obj = _issue(p, issue)
    if issue_obj is None:
        return False

    # An issue belongs to at most one cycle.
    CycleIssue.objects.filter(issue=issue_obj, project=p).delete()
    if cycle and Cycle.objects.filter(project=p, id=cycle).exists():
        CycleIssue.objects.create(
            issue=issue_obj,
            cycle_id=cycle,
            project=p,
            workspace=p.workspace,
            created_by=user,
            updated_by=user,
        )
    return True


@mutation.field("issueModules")
def resolve_issue_modules(_, info, slug, project, issue, modules):
    """Replace the module membership of a single work item with `modules`."""
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    issue_obj = _issue(p, issue)
    if issue_obj is None:
        return False

    module_ids = set(str(m) for m in (modules or []))
    existing = set(
        str(m) for m in ModuleIssue.objects.filter(issue=issue_obj, project=p).values_list("module_id", flat=True)
    )
    # Remove modules no longer present.
    ModuleIssue.objects.filter(issue=issue_obj, project=p).exclude(module_id__in=module_ids).delete()
    # Add new ones.
    for module_id in module_ids - existing:
        if Module.objects.filter(project=p, id=module_id).exists():
            ModuleIssue.objects.get_or_create(
                issue=issue_obj,
                module_id=module_id,
                project=p,
                defaults={
                    "workspace": p.workspace,
                    "created_by": user,
                    "updated_by": user,
                },
            )
    return True


@mutation.field("createCycleIssue")
def resolve_create_cycle_issue(_, info, slug, project, cycle, issues):
    """Add a set of work items to a cycle. `issues` is a JSON list of issue ids."""
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None or not Cycle.objects.filter(project=p, id=cycle).exists():
        return False

    issue_ids = issues if isinstance(issues, (list, tuple)) else [issues]
    for issue_id in issue_ids:
        if not Issue.objects.filter(project=p, id=issue_id).exists():
            continue
        # An issue is in one cycle at a time.
        CycleIssue.objects.filter(issue_id=issue_id, project=p).delete()
        CycleIssue.objects.create(
            issue_id=issue_id,
            cycle_id=cycle,
            project=p,
            workspace=p.workspace,
            created_by=user,
            updated_by=user,
        )
    return True


@mutation.field("deleteCycleIssue")
def resolve_delete_cycle_issue(_, info, slug, project, cycle, issue):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    CycleIssue.objects.filter(project=p, cycle_id=cycle, issue_id=issue).delete()
    return True


@mutation.field("createModuleIssues")
def resolve_create_module_issues(_, info, slug, project, module, issues):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None or not Module.objects.filter(project=p, id=module).exists():
        return False

    for issue_id in issues or []:
        if not Issue.objects.filter(project=p, id=issue_id).exists():
            continue
        ModuleIssue.objects.get_or_create(
            issue_id=issue_id,
            module_id=module,
            project=p,
            defaults={
                "workspace": p.workspace,
                "created_by": user,
                "updated_by": user,
            },
        )
    return True


@mutation.field("deleteModuleIssue")
def resolve_delete_module_issue(_, info, slug, project, module, issue):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    ModuleIssue.objects.filter(project=p, module_id=module, issue_id=issue).delete()
    return True


# --- Comments ------------------------------------------------------------------


def _create_comment(user, project, issue_obj, comment_html, parent=None):
    return IssueComment.objects.create(
        issue=issue_obj,
        project=project,
        workspace=project.workspace,
        comment_html=comment_html or "<p></p>",
        actor=user,
        parent=parent,
        created_by=user,
        updated_by=user,
    )


@mutation.field("addIssueComment")
def resolve_add_issue_comment(_, info, slug, project, issue, commentHtml):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    issue_obj = _issue(p, issue)
    if issue_obj is None:
        return False
    _create_comment(user, p, issue_obj, commentHtml)
    return True


@mutation.field("addIssueCommentV2")
def resolve_add_issue_comment_v2(_, info, slug, project, issue, commentHtml):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    issue_obj = _issue(p, issue)
    if issue_obj is None:
        return None
    return _create_comment(user, p, issue_obj, commentHtml)


@mutation.field("deleteWorkItemComment")
def resolve_delete_work_item_comment(_, info, slug, project, workItem, comment):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    IssueComment.objects.filter(project=p, issue_id=workItem, id=comment).delete()
    return True


@mutation.field("addWorkItemCommentReply")
def resolve_add_work_item_comment_reply(_, info, slug, project, workItem, comment, commentHtml):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    issue_obj = _issue(p, workItem)
    if issue_obj is None:
        return None
    parent = IssueComment.objects.filter(project=p, issue=issue_obj, id=comment).first()
    if parent is None:
        return None
    return _create_comment(user, p, issue_obj, commentHtml, parent=parent)


@mutation.field("deleteWorkItemCommentReply")
def resolve_delete_work_item_comment_reply(_, info, slug, project, workItem, comment, reply):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    IssueComment.objects.filter(project=p, issue_id=workItem, id=reply, parent_id=comment).delete()
    return True


# --- Comment reactions ---------------------------------------------------------


def _comment_reaction_payload(comment, reaction):
    """Shape for CommentReactionType { reaction, userIds }."""
    user_ids = list(
        CommentReaction.objects.filter(comment=comment, reaction=reaction).values_list("actor_id", flat=True)
    )
    return {"reaction": reaction, "user_ids": [str(uid) for uid in user_ids]}


@mutation.field("addWorkItemCommentReaction")
def resolve_add_work_item_comment_reaction(_, info, slug, project, workItem, comment, reactionInput):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    comment_obj = IssueComment.objects.filter(project=p, issue_id=workItem, id=comment).first()
    if comment_obj is None:
        return None
    reaction = (reactionInput or {}).get("reaction")
    if not reaction:
        return None
    CommentReaction.objects.get_or_create(
        comment=comment_obj,
        actor=user,
        reaction=reaction,
        project=p,
        defaults={"workspace": p.workspace, "created_by": user, "updated_by": user},
    )
    return _comment_reaction_payload(comment_obj, reaction)


@mutation.field("removeWorkItemCommentReaction")
def resolve_remove_work_item_comment_reaction(_, info, slug, project, workItem, comment, reactionInput):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    comment_obj = IssueComment.objects.filter(project=p, issue_id=workItem, id=comment).first()
    if comment_obj is None:
        return None
    reaction = (reactionInput or {}).get("reaction")
    if not reaction:
        return None
    CommentReaction.objects.filter(comment=comment_obj, actor=user, reaction=reaction).delete()
    return _comment_reaction_payload(comment_obj, reaction)


# --- Links ---------------------------------------------------------------------


@mutation.field("createIssueLink")
def resolve_create_issue_link(_, info, slug, project, issue, url, title=""):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    issue_obj = _issue(p, issue)
    if issue_obj is None:
        return None
    return IssueLink.objects.create(
        issue=issue_obj,
        project=p,
        workspace=p.workspace,
        url=url,
        title=title or "",
        created_by=user,
        updated_by=user,
    )


@mutation.field("updateIssueLink")
def resolve_update_issue_link(_, info, slug, project, issue, link, title=None, url=None):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    link_obj = IssueLink.objects.filter(project=p, issue_id=issue, id=link).first()
    if link_obj is None:
        return None
    update_fields = ["updated_by", "updated_at"]
    if url is not None:
        link_obj.url = url
        update_fields.append("url")
    if title is not None:
        link_obj.title = title
        update_fields.append("title")
    link_obj.updated_by = user
    link_obj.save(update_fields=list(set(update_fields)))
    return link_obj


@mutation.field("removeIssueLink")
def resolve_remove_issue_link(_, info, slug, project, issue, link):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    IssueLink.objects.filter(project=p, issue_id=issue, id=link).delete()
    return True


# --- Relations -----------------------------------------------------------------


@mutation.field("addIssueRelation")
def resolve_add_issue_relation(_, info, slug, project, issue, relationType, relatedIssueIds):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    issue_obj = _issue(p, issue)
    if issue_obj is None:
        return False

    reverse_type = _RELATION_REVERSE.get(relationType, relationType)
    for related_id in relatedIssueIds or []:
        if not Issue.objects.filter(project=p, id=related_id).exists():
            continue
        # Store the relation from the related issue's perspective with the reverse
        # type, mirroring Plane's relation view semantics.
        IssueRelation.objects.get_or_create(
            issue_id=related_id,
            related_issue=issue_obj,
            project=p,
            defaults={
                "relation_type": reverse_type,
                "workspace": p.workspace,
                "created_by": user,
                "updated_by": user,
            },
        )
    return True


# --- Subscriptions -------------------------------------------------------------


@mutation.field("subscribeIssue")
def resolve_subscribe_issue(_, info, slug, project, issue):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    issue_obj = _issue(p, issue)
    if issue_obj is None:
        return False
    IssueSubscriber.objects.get_or_create(
        issue=issue_obj,
        subscriber=user,
        project=p,
        defaults={"workspace": p.workspace, "created_by": user, "updated_by": user},
    )
    return True


@mutation.field("unSubscribeIssue")
def resolve_unsubscribe_issue(_, info, slug, project, issue):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    IssueSubscriber.objects.filter(project=p, issue_id=issue, subscriber=user).delete()
    return True


# --- Attachments ---------------------------------------------------------------


@mutation.field("createIssueAttachment")
def resolve_create_issue_attachment(_, info, slug, project, issue, name, type, size):
    """Create the FileAsset row and return a presigned POST for direct upload.

    Returns IssueAttachmentPresignedUrlResponseType { uploadData, attachmentId, assetUrl }.
    """
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    issue_obj = _issue(p, issue)
    if issue_obj is None:
        return None

    from plane.utils.path_validator import sanitize_filename

    safe_name = sanitize_filename(name) or "unnamed"
    size_limit = int(size)
    asset_key = f"{p.workspace.id}/{uuid.uuid4().hex}-{safe_name}"

    asset = FileAsset.objects.create(
        attributes={"name": safe_name, "type": type, "size": size_limit},
        asset=asset_key,
        size=size_limit,
        workspace=p.workspace,
        project=p,
        issue=issue_obj,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        entity_identifier=str(issue_obj.id),
        created_by=user,
    )

    # Presigned POST against the configured S3/MinIO backend. info.context is the
    # Django request, so the storage builds a host-correct endpoint.
    upload_data = None
    try:
        from plane.settings.storage import S3Storage

        storage = S3Storage(request=info.context)
        upload_data = storage.generate_presigned_post(
            object_name=asset_key, file_type=type, file_size=size_limit
        )
    except Exception:
        # If storage is unreachable in this environment, still return the asset id
        # and an empty upload_data so the client can retry the upload step.
        upload_data = {}

    return {
        "upload_data": upload_data,
        "attachment_id": str(asset.id),
        "asset_url": asset.asset_url,
    }


@mutation.field("updateIssueAttachment")
def resolve_update_issue_attachment(_, info, slug, project, issue, attachment, attributes=None):
    """Mark the upload complete and persist attributes; returns the FileAsset."""
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    asset = FileAsset.objects.filter(
        workspace__slug=slug, project=p, issue_id=issue, id=attachment
    ).first()
    if asset is None:
        return None
    asset.is_uploaded = True
    if attributes is not None:
        asset.attributes = attributes
    asset.updated_by = user
    asset.save(update_fields=["is_uploaded", "attributes", "updated_by", "updated_at"])
    return asset


@mutation.field("deleteIssueAttachment")
def resolve_delete_issue_attachment(_, info, slug, project, issue, attachment):
    user = get_user(info)
    if user is None:
        return False
    p = _member_project(info, slug, project)
    if p is None:
        return False
    asset = FileAsset.objects.filter(
        workspace__slug=slug, project=p, issue_id=issue, id=attachment
    ).first()
    if asset is None:
        return False
    asset.is_deleted = True
    asset.deleted_at = timezone.now()
    asset.save(update_fields=["is_deleted", "deleted_at"])
    return True


BINDABLES = [mutation]
