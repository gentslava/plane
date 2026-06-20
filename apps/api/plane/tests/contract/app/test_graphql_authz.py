# OVERLAY: mobile-graphql — authz guard. The gateway authenticates with the mobile JWT
# but must also AUTHORIZE: a user may only reach a workspace they actively belong to.
# Without _member_project's membership check, a member of workspace A could mutate/read
# workspace B by passing B's slug + known UUIDs (cross-tenant IDOR) — the class upstream
# hardened in its bulk endpoints (#9269/#9270).
from types import SimpleNamespace

import pytest

from plane.db.models import Project, User, Workspace, WorkspaceMember
from plane.graphql.resolvers import _member_project


def _info(user):
    return SimpleNamespace(context=SimpleNamespace(user=user))


def _other_user(tag):
    return User.objects.create(email=f"{tag}@authz.test", username=f"{tag}-authz")


@pytest.mark.django_db
def test_member_project_blocks_non_members(create_user):
    u = create_user

    # Workspace A — u IS an active member.
    ws_a = Workspace.objects.create(name="A", owner=u, slug="authz-a")
    WorkspaceMember.objects.create(workspace=ws_a, member=u, role=20)
    proj_a = Project.objects.create(name="PA", identifier="PA", workspace=ws_a, project_lead=u)

    # Workspace B — owned by someone else; u is NOT a member.
    other = _other_user("o1")
    ws_b = Workspace.objects.create(name="B", owner=other, slug="authz-b")
    WorkspaceMember.objects.create(workspace=ws_b, member=other, role=20)
    proj_b = Project.objects.create(name="PB", identifier="PB", workspace=ws_b, project_lead=other)

    info = _info(u)

    # Member: resolves the project.
    assert _member_project(info, "authz-a", str(proj_a.id)) is not None

    # Non-member: blocked even though the project exists and the slug/UUID are valid.
    assert _member_project(info, "authz-b", str(proj_b.id)) is None

    # Deactivated membership is also blocked.
    WorkspaceMember.objects.filter(workspace=ws_a, member=u).update(is_active=False)
    assert _member_project(info, "authz-a", str(proj_a.id)) is None

    # Unauthenticated context is blocked.
    assert _member_project(_info(None), "authz-a", str(proj_a.id)) is None


@pytest.mark.django_db
def test_create_sub_issue_no_op_for_non_member(create_user):
    """A non-member's createSubIssue must not re-parent the target workspace's issues."""
    from plane.db.models import Issue, State
    from plane.graphql.mutations.work_items import resolve_create_sub_issue

    other = _other_user("o1")
    ws_b = Workspace.objects.create(name="B2", owner=other, slug="authz-b2")
    WorkspaceMember.objects.create(workspace=ws_b, member=other, role=20)
    proj_b = Project.objects.create(name="PB2", identifier="PB2", workspace=ws_b, project_lead=other)
    st = State.objects.create(name="s", color="#fff", group="backlog", project=proj_b, workspace=ws_b, created_by=other)
    parent = Issue.objects.create(name="parent", state=st, sequence_id=1, project=proj_b, workspace=ws_b, created_by=other)
    child = Issue.objects.create(name="child", state=st, sequence_id=2, project=proj_b, workspace=ws_b, created_by=other)

    # create_user is NOT a member of ws_b → mutation must bail, child stays un-parented.
    result = resolve_create_sub_issue(
        None, _info(create_user), slug="authz-b2", project=str(proj_b.id),
        parentIssueId=str(parent.id), subIssueIds=[str(child.id)],
    )
    assert result is False
    child.refresh_from_db()
    assert child.parent_id is None
