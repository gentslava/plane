# OVERLAY: mobile-graphql — POST /graphql/ endpoint for the native app.
import json
import logging
import os
import sys

from ariadne import graphql_sync
from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from plane.authentication.mobile.jwt import MobileJWTAuthentication

from .schema import schema

# ariadne logs every field error with a full traceback; during the incremental
# roll-out the not-yet-implemented non-null fields would flood the logs. We surface
# the concise [MOBILE-GQL] error line ourselves instead.
logging.getLogger("ariadne").setLevel(logging.CRITICAL)

# Diagnostic [MOBILE-GQL] request/error logging — off by default; set the env var
# MOBILE_DEBUG_LOG=1 to re-enable it when debugging the native app's GraphQL flow.
_DEBUG_LOG = os.environ.get("MOBILE_DEBUG_LOG", "0") == "1"


def _resolve_user(request):
    """The app authenticates GraphQL with the mobile Bearer JWT; fall back to the
    Django session (cookie ``session-id``) if a session is already attached."""
    try:
        result = MobileJWTAuthentication().authenticate(request)
        if result is not None:
            return result[0]
    except Exception:
        pass
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


@csrf_exempt
def graphql_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    user = _resolve_user(request)
    if user is None:
        # Parity with Plane Cloud's gateway.
        return JsonResponse({"message": "Authentication required"}, status=401)
    request.user = user

    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"errors": [{"message": "Invalid JSON body"}]}, status=400)

    # Diagnostic capture (gated by MOBILE_DEBUG_LOG): the 'plane' logger is muted in
    # the deploy, so print to stderr. Reveals which operations the app calls, in
    # order, with variables.
    if _DEBUG_LOG:
        _q = (data.get("query") or "").replace("\n", " ")
        print(
            f"[MOBILE-GQL] op={data.get('operationName')} vars={data.get('variables')} query={_q[:1500]}",
            file=sys.stderr,
            flush=True,
        )

    success, result = graphql_sync(schema, data, context_value=request, debug=settings.DEBUG)

    if _DEBUG_LOG and result.get("errors"):
        print(f"[MOBILE-GQL] op={data.get('operationName')} errors={result['errors']}", file=sys.stderr, flush=True)

    return JsonResponse(result, status=200 if success else 400)
