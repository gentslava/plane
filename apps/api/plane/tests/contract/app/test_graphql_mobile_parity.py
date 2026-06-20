# OVERLAY: mobile-graphql — regression guard for the native app's GraphQL startup
# contract. Replays startup operations against the real ariadne schema and asserts
# the field/enum contract the app depends on. The anchor case seeds an unread
# Notification and runs CatchUpQuery: the gateway used to return the raw
# Notification.entity_name ("issue") into CatchUpActivityTypeEnum {COMMENT, ACTIVITY},
# which fails enum serialization and crashes the whole query the moment any unread
# notification exists. This test would have caught that.
import uuid

import pytest
from ariadne import graphql_sync
from django.test import RequestFactory

from plane.db.models import Notification
from plane.graphql.schema import schema

CATCHUP_QUERY = """
query CatchUpQuery($slug: String!) {
  catchUps(slug: $slug) {
    id
    type
    count
    workItem { id name projectIdentifier sequenceId intakeId }
    firstUnread { id type }
    lastUnread { id type }
  }
}
"""

TIMEZONE_QUERY = "query { timezoneList { value query label } }"


def _exec(query, variables, user):
    request = RequestFactory().post("/graphql/")
    request.user = user
    ok, result = graphql_sync(
        schema, {"query": query, "variables": variables}, context_value=request
    )
    assert ok, result
    assert "errors" not in result, result.get("errors")
    return result["data"]


@pytest.mark.django_db
def test_catchups_enum_never_crashes(create_user, workspace):
    """An unread notification must not break CatchUpQuery; enum fields must be members."""
    entity = uuid.uuid4()
    for field in ("comment", "state"):
        Notification.objects.create(
            workspace=workspace,
            receiver=create_user,
            triggered_by=create_user,
            entity_name="issue",
            entity_identifier=entity,
            title="seed",
            sender="test",
            data={"issue_activity": {"field": field}},
            read_at=None,
            archived_at=None,
        )

    catch_ups = _exec(CATCHUP_QUERY, {"slug": workspace.slug}, create_user)["catchUps"]

    assert len(catch_ups) == 1
    card = catch_ups[0]
    assert card["count"] == 2
    assert card["type"] in ("WORK_ITEM", "INTAKE", "EPIC")
    # The crux: both activity-type values are valid enum members, derived from the
    # activity field (comment -> COMMENT, anything else -> ACTIVITY) — never the raw
    # entity_name. The grouped pair covers both notifications, so both values appear.
    pair = {card["firstUnread"]["type"], card["lastUnread"]["type"]}
    assert pair == {"COMMENT", "ACTIVITY"}, pair


@pytest.mark.django_db
def test_timezone_list_shape(create_user):
    """Mirror Cloud's richer timezone shape: friendly label + offset-bearing query."""
    tzs = _exec(TIMEZONE_QUERY, {}, create_user)["timezoneList"]

    assert len(tzs) > 100
    assert all(t["value"] and t["query"] and t["label"] for t in tzs)  # String! non-null
    # label is a friendly name (not the raw IANA id) and query carries the offset.
    assert any(t["label"] != t["value"] for t in tzs)
    assert any("GMT" in t["query"] for t in tzs)
