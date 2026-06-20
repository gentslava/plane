# OVERLAY: mobile-graphql — corpus-replay regression guard.
#
# Seeds one row of every entity the native app reads, then:
#   Pass 1: executes every QUERY from the real app corpus (operations-raw.graphql)
#           against the live ariadne schema and ASSERTS none crashes on a non-null
#           violation or resolver exception — the bug class where a non-null SDL field
#           not backed by a model column (e.g. workspaceWorkItemMention, NotificationType
#           .isIntakeIssue) returns null and takes the whole query down.
#   Pass 2: statically validates ALL 188 ops (queries + mutations) against the SDL and
#           reports contract gaps (a selected field missing from our schema). Truncated
#           corpus entries are repaired/skipped; informational (does not fail the build).
#
# Skips cleanly when the corpus file is absent (it lives in overlay/, outside apps/api).
from pathlib import Path
import re
import uuid

import pytest
from ariadne import graphql_sync
from django.test import RequestFactory
from django.utils import timezone

from plane.graphql.schema import schema
from plane.db.models import (
    Cycle, CycleIssue, Intake, IntakeIssue, Issue, IssueAssignee, IssueComment,
    IssueLabel, IssueLink, IssueType, Label, Module, ModuleIssue, Notification,
    Page, ProjectPage, ProjectMember, State, Sticky, UserFavorite,
    UserRecentVisit, Workspace, WorkspaceMember, WorkspaceMemberInvite, Project,
)

# apps/api/plane/tests/contract/app/<this> -> repo root is parents[6].
OPS = Path(__file__).resolve().parents[6] / "overlay/features/mobile-graphql/reference/operations-raw.graphql"


def parse_ops(text):
    parts = re.split(r"(?m)^###[ \t]+(.+?)[ \t]*$", text)
    it = iter(parts[1:])
    out = {}
    for name, body in zip(it, it):
        out[name.strip()] = body.strip()
    return out


def kind(body):
    m = re.search(r"\b(query|mutation|subscription)\b", body)
    return m.group(1) if m else "query"


def varspecs(body):
    # name -> raw type string (e.g. "String!", "[String!]!", "Boolean")
    return dict(re.findall(r"\$([A-Za-z0-9_]+)\s*:\s*([^\s,)$]+)", body))


@pytest.mark.django_db
def test_app_corpus_no_crash_fields(create_user):
    if not OPS.exists():
        pytest.skip(f"app operation corpus not present at {OPS}")
    u = create_user
    ws = Workspace.objects.create(name="Probe WS", owner=u, slug="probe-ws")
    WorkspaceMember.objects.create(workspace=ws, member=u, role=20)

    proj = Project.objects.create(
        name="Probe", identifier="PRB", workspace=ws, project_lead=u,
    )
    ProjectMember.objects.create(project=proj, member=u, workspace=ws, role=20)

    common = dict(project=proj, workspace=ws, created_by=u)

    it_default = IssueType.objects.create(workspace=ws, name="Task", is_default=True)
    it_epic = IssueType.objects.create(workspace=ws, name="Epic", is_epic=True)

    state = State.objects.create(name="Todo", color="#fff", group="unstarted", **common)
    label = Label.objects.create(name="bug", color="#f00", workspace=ws, project=proj, created_by=u)

    parent = Issue.objects.create(name="Parent", state=state, sequence_id=1, type=it_default, **common)
    issue = Issue.objects.create(name="Child", state=state, sequence_id=2, type=it_default, parent=parent, **common)
    epic = Issue.objects.create(name="Epic A", state=state, sequence_id=3, type=it_epic, **common)
    IssueLabel.objects.create(issue=issue, label=label, project=proj, workspace=ws)
    IssueAssignee.objects.create(issue=issue, assignee=u, workspace=ws, project=proj)

    cycle = Cycle.objects.create(
        name="C1", owned_by=u, start_date=timezone.now(),
        end_date=timezone.now() + timezone.timedelta(days=7), **common,
    )
    CycleIssue.objects.create(cycle=cycle, issue=issue, **common)
    module = Module.objects.create(name="M1", status="planned", **common)
    ModuleIssue.objects.create(module=module, issue=issue, **common)

    intake = Intake.objects.create(name="Intake", **common)
    intake_issue = Issue.objects.create(name="Intake item", state=state, sequence_id=4, type=it_default, **common)
    IntakeIssue.objects.create(intake=intake, issue=intake_issue, status=-2, source="IN_APP", **common)

    page = Page.objects.create(name="Page 1", owned_by=u, workspace=ws, created_by=u, access=0)
    ProjectPage.objects.create(page=page, project=proj, workspace=ws, created_by=u)

    sticky = Sticky.objects.create(name="note", owner=u, workspace=ws, created_by=u)

    IssueComment.objects.create(issue=issue, comment_html="<p>hi</p>", actor=u, **common)
    IssueLink.objects.create(issue=issue, url="https://x.y", **common)

    for f in ("comment", "state"):
        Notification.objects.create(
            workspace=ws, receiver=u, triggered_by=u, entity_name="issue",
            entity_identifier=issue.id, title="n", sender=str(u.id),
            data={"issue_activity": {"field": f}}, read_at=None, archived_at=None,
        )

    UserFavorite.objects.create(user=u, entity_type="project", entity_identifier=proj.id, workspace=ws)
    UserRecentVisit.objects.create(user=u, entity_name="issue", entity_identifier=issue.id, workspace=ws, project=proj)

    invite = WorkspaceMemberInvite.objects.create(
        workspace=ws, email=u.email, token="invtok", role=15, accepted=False,
    )

    ident = f"{proj.identifier}-{issue.sequence_id}"
    V = {
        "slug": ws.slug, "workspace": ws.slug, "workspaceSlug": ws.slug,
        "workspaceSlugInput": ws.slug, "workspaceInput": ws.slug,
        "project": str(proj.id), "projectId": str(proj.id), "projects": [str(proj.id)],
        "id": str(issue.id), "ids": [str(issue.id)],
        "epic": str(epic.id), "cycle": str(cycle.id), "module": str(module.id),
        "modules": [str(module.id)], "page": str(page.id), "pageIds": [str(page.id)],
        "pages": [str(page.id)], "issue": str(issue.id), "workItem": str(issue.id),
        "workitem": str(issue.id), "workItemIds": [str(issue.id)],
        "subIssueIds": [str(issue.id)], "relatedWorkItemIds": [str(issue.id)],
        "relatedIssueIds": [str(issue.id)], "parentIssueId": str(parent.id),
        "parent": str(parent.id), "state": str(state.id), "labels": [str(label.id)],
        "sticky": str(sticky.id), "intakeWorkItem": str(intake_issue.id),
        "initiative": str(uuid.uuid4()), "collection": str(uuid.uuid4()),
        "assetId": str(uuid.uuid4()), "assetIds": [str(uuid.uuid4())],
        "entityId": str(issue.id), "entityIdentifier": str(issue.id),
        "entityName": "issue", "entityType": "issue", "type": "all",
        "workItemIdentifier": ident, "cursor": "100:0:0", "limit": 100, "size": 10,
        "search": "test", "query": "test", "name": "probe", "title": "probe",
        "email": "probe@example.com", "token": "x", "invitationId": str(uuid.uuid4()),
        "commentIds": [str(uuid.uuid4())], "groupBy": "state", "orderBy": "created_at",
        "priority": "none", "startDate": None, "targetDate": None,
    }

    def fallback(typ):
        if typ.startswith("["):
            return []
        if typ.startswith("Boolean"):
            return False
        if typ.startswith("Int"):
            return 0
        if typ.startswith("JSON"):
            return {}
        return ""

    # Per-operation variable overrides so root lookups RESOLVE (exercising their full
    # nested selection sets, where deeper non-null bugs hide).
    OVERRIDE = {
        "IntakeWorkItemByWorkItemQuery": {"workItem": str(intake_issue.id)},
        "IntakeWorkItemByWorkItem": {"workItem": str(intake_issue.id)},
        "PublicWorkspaceInviteQuery": {"invitationId": str(invite.id), "email": u.email},
        "PublicWorkspaceInviteV2Query": {"invitationId": str(invite.id), "slug": ws.slug, "token": "invtok"},
    }

    ops = parse_ops(OPS.read_text())
    queries = {n: b for n, b in ops.items() if kind(b) == "query"}

    req = RequestFactory().post("/graphql/")
    req.user = u

    crashes = []
    for name, body in sorted(queries.items()):
        specs = varspecs(body)
        variables = {}
        for vn, vt in specs.items():
            variables[vn] = V[vn] if vn in V else fallback(vt)
        variables.update(OVERRIDE.get(name, {}))
        try:
            ok, result = graphql_sync(schema, {"query": body, "variables": variables}, context_value=req)
        except Exception as e:  # noqa: BLE001
            crashes.append((name, f"PYEXC {type(e).__name__}: {e}"))
            continue
        for err in (result or {}).get("errors", []) or []:
            msg = err.get("message", "")
            # Only the bug class: non-null violations + resolver-raised exceptions.
            if "Cannot return null for non-nullable" in msg or "Expected value of type" in msg \
               or "Int cannot represent" in msg or "internal" in msg.lower() \
               or err.get("extensions", {}).get("exception"):
                path = ".".join(str(p) for p in (err.get("path") or []))
                crashes.append((name, f"{msg} @ {path}"))

    # --- Pass 2: static validation of ALL 188 ops (queries + mutations) against the
    # schema. Catches contract gaps the app would hit: a selected field missing from our
    # SDL, an unknown argument, a type mismatch — each crashes the whole operation at
    # validation, before any resolver runs. Side-effect free (no execution).
    from graphql import parse, validate

    def repair(b):
        # The corpus is a binary dump; some ops are truncated mid-selection. Recover the
        # captured PREFIX so its field names still validate: drop trailing stray tokens,
        # then balance ( ) and { } so it parses. We only mine the result for unknown-
        # field/arg signal, never for structural correctness.
        s = b.rstrip()
        while s and s[-1] in "T^:$, \t\n":
            s = s[:-1].rstrip()
        s += ")" * max(0, s.count("(") - s.count(")"))
        s += "}" * max(0, s.count("{") - s.count("}"))
        return s

    REAL = ("Cannot query field", "Unknown argument", "Unknown type",
            "is not defined by", "Field ", "argument ")
    val_errors = []
    truncated = 0
    for name, body in sorted(ops.items()):
        repaired = False
        try:
            doc = parse(body)
        except Exception:  # noqa: BLE001
            try:
                doc = parse(repair(body))
                repaired = True
                truncated += 1
            except Exception as e:  # noqa: BLE001
                val_errors.append((name, f"UNREPAIRABLE {type(e).__name__}"))
                continue
        for ve in validate(schema, doc):
            msg = ve.message
            if "is never used" in msg or ("Variable" in msg and "is not defined" in msg):
                continue
            # On repaired (truncated) ops keep only true contract-gap signal; drop the
            # structural noise the truncation itself introduces.
            if repaired and not any(k in msg for k in REAL):
                continue
            val_errors.append((name, ("[repaired] " if repaired else "") + msg))

    print("\n\n========== EXHAUSTIVE PROBE ==========")
    print("Pass 1 — executed %d queries against seeded data:" % len(queries))
    if not crashes:
        print("  >>> CLEAN: no non-null/exception errors")
    else:
        print("  >>> %d crash-class errors:" % len(crashes))
        for n, m in crashes:
            print(f"    [{n}] {m}")
    print("Pass 2 — validated %d ops (queries+mutations) against SDL [%d truncated→repaired]:" % (len(ops), truncated))
    if not val_errors:
        print("  >>> CLEAN: every op validates against the schema")
    else:
        print("  >>> %d validation findings:" % len(val_errors))
        for n, m in val_errors:
            print(f"    [{n}] {m}")
    print("======================================\n")

    # The guard: no executed app query may crash on a non-null/exception field.
    assert not crashes, "non-null/exception crashes in app queries:\n" + "\n".join(
        f"  [{n}] {m}" for n, m in crashes
    )
