# OVERLAY: mobile-graphql — workflow approval-request lifecycle (A).
from ariadne import MutationType, ObjectType, QueryType
from graphql import GraphQLError

from plane.db.models import ApprovalRequest, Issue
from plane.graphql.context import get_user
from plane.graphql.resolvers import _member_project
from plane.workflow.service import (
    ApprovalError,
    create_approval_request,
    decide_approval_request,
    visible_approval_requests,
)

query = QueryType()
mutation = MutationType()
approval_type = ObjectType("WorkflowApprovalRequestType")


def _raise(e):
    raise GraphQLError(e.message, extensions={"code": e.code})


@mutation.field("requestWorkflowApproval")
def resolve_request_workflow_approval(_, info, slug, project, workItem, toState, comment=None):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    issue = Issue.objects.filter(project=p, id=workItem).first()
    if issue is None:
        return None
    try:
        return create_approval_request(user, issue, toState, comment=comment)
    except ApprovalError as e:
        _raise(e)


def _decide(info, slug, project, request, action, reason=None):
    user = get_user(info)
    if user is None:
        return None
    p = _member_project(info, slug, project)
    if p is None:
        return None
    req = ApprovalRequest.objects.filter(project=p, id=request).first()
    if req is None:
        return None
    try:
        return decide_approval_request(user, req, action, reason=reason)
    except ApprovalError as e:
        _raise(e)


@mutation.field("approveWorkflowRequest")
def resolve_approve_workflow_request(_, info, slug, project, request, reason=None):
    return _decide(info, slug, project, request, "approve", reason)


@mutation.field("rejectWorkflowRequest")
def resolve_reject_workflow_request(_, info, slug, project, request, reason=None):
    return _decide(info, slug, project, request, "reject", reason)


@mutation.field("cancelWorkflowRequest")
def resolve_cancel_workflow_request(_, info, slug, project, request):
    return _decide(info, slug, project, request, "cancel")


@query.field("workflowApprovalRequests")
def resolve_workflow_approval_requests(_, info, slug, project, workItem=None, status="PENDING"):
    user = get_user(info)
    if user is None:
        return []
    p = _member_project(info, slug, project)
    if p is None:
        return []
    # `visible_approval_requests` normalises the SDL default "PENDING" (and any
    # explicit value) to the model's lowercase stored value.
    return visible_approval_requests(user, p.id, work_item_id=workItem, status=status)


@approval_type.field("workItem")
def _r_work_item(obj, info):
    return str(obj.work_item_id)


@approval_type.field("fromState")
def _r_from_state(obj, info):
    return str(obj.from_state_id)


@approval_type.field("toState")
def _r_to_state(obj, info):
    return str(obj.to_state_id)


@approval_type.field("requestedBy")
def _r_requested_by(obj, info):
    return str(obj.requested_by_id)


@approval_type.field("decidedBy")
def _r_decided_by(obj, info):
    return str(obj.decided_by_id) if obj.decided_by_id else None


@approval_type.field("project")
def _r_project(obj, info):
    return str(obj.project_id)


@approval_type.field("workspace")
def _r_workspace(obj, info):
    return str(obj.workspace_id)


BINDABLES = [query, mutation, approval_type]
