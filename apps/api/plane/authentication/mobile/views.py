# OVERLAY: mobile-auth
# Instrumented mobile auth endpoints for the Plane native (Flutter) app.
#
# Goal of THIS iteration: capture the exact wire contract the app speaks. Every
# handler logs the *full* inbound request at INFO under the ``[MOBILE-AUTH]``
# prefix BEFORE doing anything else, then returns a plausible response so the
# app keeps progressing through its flow. Grep server logs with ``[MOBILE-AUTH]``.
#
# Endpoints (proxied under ``/auth/mobile/...``):
#   * MobileSessionTokenEndpoint  POST/GET  -> mint access+refresh from the
#                                              WebView session cookie.
#   * MobileTokenCheckEndpoint    GET/POST  -> validate Bearer or cookie auth.
#   * MobileRefreshTokenEndpoint  POST      -> exchange a refresh token for a
#                                              fresh access token.

import logging
import os
import sys
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.authentication.mobile.jwt import (
    MobileJWTAuthentication,
    access_token_for,
    decode_token,
    refresh_token_for,
)
from plane.authentication.session import BaseSessionAuthentication

logger = logging.getLogger("plane")

LOG_PREFIX = "[MOBILE-AUTH]"

# Diagnostic [MOBILE-AUTH] request logging — off by default; set the env var
# MOBILE_DEBUG_LOG=1 to re-enable it when debugging the native auth flow.
_DEBUG_LOG = os.environ.get("MOBILE_DEBUG_LOG", "0") == "1"

# Headers we care most about for reversing the contract. We still dump *all*
# headers, but these get pulled out explicitly so they are easy to spot.
_INTERESTING_HEADERS = (
    "HTTP_AUTHORIZATION",
    "HTTP_COOKIE",
    "HTTP_X_CSRFTOKEN",
    "HTTP_USER_AGENT",
    "HTTP_REFERER",
    "HTTP_ORIGIN",
    "CONTENT_TYPE",
)


def _all_headers(request) -> dict:
    """Return every HTTP_* header (plus content meta) from request.META."""
    headers = {}
    for key, value in request.META.items():
        if key.startswith("HTTP_") or key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            headers[key] = value
    return headers


def _safe_body(request):
    """Best-effort extraction of the parsed request body for logging."""
    try:
        # request.data triggers DRF parsing; dict() makes QueryDict loggable.
        data = request.data
        if hasattr(data, "dict"):
            return data.dict()
        return data
    except Exception as exc:  # noqa: BLE001 - logging must never raise
        return f"<unparseable body: {exc!r}>"


def log_request(request, endpoint: str) -> None:
    """Log the full inbound request at INFO. Must never raise. Gated by MOBILE_DEBUG_LOG."""
    if not _DEBUG_LOG:
        return
    try:
        interesting = {
            key: request.META.get(key) for key in _INTERESTING_HEADERS
        }
        user = getattr(request, "user", None)
        is_authenticated = bool(
            getattr(user, "is_authenticated", False)
        )
        message = (
            "%s %s | method=%s path=%s | query=%s | key_headers=%s | "
            "all_headers=%s | body=%s | user=%s is_authenticated=%s"
            % (
                LOG_PREFIX,
                endpoint,
                request.method,
                request.get_full_path(),
                dict(request.GET),
                interesting,
                _all_headers(request),
                _safe_body(request),
                getattr(user, "id", None),
                is_authenticated,
            )
        )
        # The "plane" logger swallows INFO in this deployment, so write straight
        # to stderr (captured by gunicorn → container logs) to guarantee output.
        print(message, file=sys.stderr, flush=True)
        logger.info(message)
    except Exception as exc:  # noqa: BLE001 - logging must never break the flow
        print(
            "%s %s | failed to log request: %r" % (LOG_PREFIX, endpoint, exc),
            file=sys.stderr,
            flush=True,
        )


# OVERLAY: mobile-auth — token plumbing helpers.
#
# We don't yet know the *exact* wire contract the app speaks, so these helpers
# are deliberately permissive: pull a token from wherever the app might put it
# (Authorization: Bearer, or any of the common body keys) and accept it whether
# it was minted as an access or a refresh token. Combined with the [MOBILE-AUTH]
# request logging this lets the app progress while we observe what it actually
# sends. TODO(overlay): tighten once the contract is confirmed from the logs.

_TOKEN_BODY_KEYS = ("refresh_token", "access_token", "token", "session", "session_token")


def _extract_token(request):
    """Return a token from ``Authorization: Bearer`` or a known body key."""
    auth = request.META.get("HTTP_AUTHORIZATION", "") or ""
    if auth.lower().startswith("bearer "):
        candidate = auth.split(" ", 1)[1].strip()
        if candidate:
            return candidate
    try:
        data = request.data
    except Exception:  # noqa: BLE001 - never let parsing break auth
        return None
    if hasattr(data, "get"):
        for key in _TOKEN_BODY_KEYS:
            value = data.get(key)
            if value:
                return value
    return None


def _decode_any(token):
    """Decode a token, accepting either an access or a refresh token type.

    This is the recovery path for token-check / refresh-token: the app re-opens
    and presents the token it stored at login. By the time it does, the 15-min
    access token has usually lapsed, so we decode with ``verify_exp=False`` and
    hand back a fresh pair — Plane Cloud's token-exchange semantics. The HS256
    signature is still verified (an expired token can be exchanged, never
    forged); request authentication elsewhere stays strict about expiry.
    """
    if not token:
        return None
    for expected in ("refresh", "access"):
        try:
            return decode_token(token, expected_type=expected, verify_exp=False)
        except Exception:  # noqa: BLE001 - try the next type
            continue
    return None


def _user_from_payload(payload):
    """Resolve the active user referenced by a decoded token payload."""
    if not payload:
        return None
    from plane.db.models import User

    try:
        return User.objects.get(id=payload.get("user_id"), is_active=True)
    except User.DoesNotExist:
        return None


def _token_pair(user):
    """Mint a fresh SimpleJWT-style access+refresh pair.

    This is the exact shape Plane Cloud's ``token-check``/``refresh-token`` return:
    ``{"access_token": <jwt>, "refresh_token": <jwt>}`` — nothing else.
    """
    access, _ = access_token_for(user)
    refresh, _ = refresh_token_for(user)
    return {"access_token": access, "refresh_token": refresh}


def _create_web_session(user):
    """Create a real Plane web session for ``user`` and return its key.

    ``session-token`` returns ``{session_name, session_id}`` so the app can make
    cookie-authenticated (WebView / web) requests. We mint a Django session keyed
    to the user — mirroring Plane Cloud's response — instead of returning tokens.
    """
    from importlib import import_module

    from django.contrib.auth import (
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
        SESSION_KEY,
    )

    store = import_module(settings.SESSION_ENGINE).SessionStore()
    store[SESSION_KEY] = str(user.pk)
    backends = getattr(settings, "AUTHENTICATION_BACKENDS", None) or [
        "django.contrib.auth.backends.ModelBackend"
    ]
    store[BACKEND_SESSION_KEY] = backends[0]
    try:
        store[HASH_SESSION_KEY] = user.get_session_auth_hash()
    except Exception:  # noqa: BLE001 - session auth hash is best-effort
        pass
    store.save()
    return store.session_key


@method_decorator(csrf_exempt, name="dispatch")
class MobileSessionTokenEndpoint(APIView):
    """Exchange the WebView session cookie for mobile JWT tokens.

    The in-app WebView completes the normal web login (session cookie + CSRF),
    the server redirects to ``/m/auth?token=...``, and the app then calls this
    endpoint carrying the session cookie. We pick up that cookie via
    ``BaseSessionAuthentication`` and mint tokens for ``request.user``.

    NOTE: ``csrf_exempt`` is applied on dispatch ONLY for this capture
    iteration so the request is guaranteed to be logged before any CSRF check
    can reject it. TODO(overlay): restore CSRF enforcement on POST for prod once
    the app's X-CSRFToken behaviour is confirmed (the app fetches it from
    ``/auth/get-csrf-token/``).
    """

    # BaseSessionAuthentication picks up the WebView cookie and (per Plane's
    # base class) does not enforce CSRF itself.
    authentication_classes = [BaseSessionAuthentication]
    permission_classes = [AllowAny]

    def _handle(self, request):
        log_request(request, "session-token")

        # The app finalises login here by POSTing the deep-link access token as a
        # ``Authorization: Bearer`` header (empty body, no cookie) and expects the
        # full mobile session back. Recover the user from the cookie OR the token.
        user = request.user if (request.user and request.user.is_authenticated) else None
        if user is None:
            user = _user_from_payload(_decode_any(_extract_token(request)))

        if user is None:
            # Mirror djangorestframework-simplejwt's invalid-token error.
            return Response(
                {
                    "detail": "Given token not valid for any token type",
                    "code": "token_not_valid",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Cloud returns {session_name, session_id} — a web session the app uses for
        # cookie-authenticated requests. NOT tokens.
        session_key = _create_web_session(user)
        response = Response(
            {
                "session_name": settings.SESSION_COOKIE_NAME,
                "session_id": session_key,
            },
            status=status.HTTP_200_OK,
        )
        # ALSO set the session cookie on the response so the app's HTTP cookie jar
        # picks it up (Dart relies on Set-Cookie, not the body, to carry the
        # session into subsequent /api/ requests — otherwise it re-auths in a loop).
        response.set_cookie(
            key=settings.SESSION_COOKIE_NAME,
            value=session_key,
            max_age=getattr(settings, "SESSION_COOKIE_AGE", 604800),
            domain=getattr(settings, "SESSION_COOKIE_DOMAIN", None),
            path=getattr(settings, "SESSION_COOKIE_PATH", "/"),
            secure=getattr(settings, "SESSION_COOKIE_SECURE", True),
            httponly=getattr(settings, "SESSION_COOKIE_HTTPONLY", True),
            samesite=getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax"),
        )
        # Cloud also sets a csrftoken cookie here (needed for the app's POST/PATCH).
        response.set_cookie(
            key=getattr(settings, "CSRF_COOKIE_NAME", "csrftoken"),
            value=get_token(request),
            max_age=getattr(settings, "CSRF_COOKIE_AGE", 31449600),
            domain=getattr(settings, "CSRF_COOKIE_DOMAIN", None),
            path=getattr(settings, "CSRF_COOKIE_PATH", "/"),
            secure=getattr(settings, "CSRF_COOKIE_SECURE", True),
            httponly=getattr(settings, "CSRF_COOKIE_HTTPONLY", False),
            samesite=getattr(settings, "CSRF_COOKIE_SAMESITE", "Lax"),
        )
        return response

    def post(self, request):
        return self._handle(request)

    def get(self, request):
        return self._handle(request)


@method_decorator(csrf_exempt, name="dispatch")
class MobileTokenCheckEndpoint(APIView):
    """Report whether the caller is authenticated via Bearer JWT or cookie.

    Accepts either a mobile access token (``Authorization: Bearer``) or the
    WebView session cookie. ``csrf_exempt`` on dispatch keeps the capture
    iteration from rejecting POSTs before logging (TODO: re-enable for prod).
    """

    # NOTE: MobileJWTAuthentication is intentionally NOT used here — it *raises*
    # (DRF 401 before the view runs) on a non-access/expired Bearer, which would
    # bypass our permissive recovery. We resolve the Bearer/body token manually
    # in _handle via _extract_token. Cookie auth stays for the WebView path.
    authentication_classes = [BaseSessionAuthentication]
    permission_classes = [AllowAny]

    def _handle(self, request):
        log_request(request, "token-check")

        # 1) Already authenticated via Bearer (MobileJWTAuthentication) or cookie.
        user = request.user if (request.user and request.user.is_authenticated) else None
        # 2) Otherwise try to recover the token from the body (the app may POST
        #    the token it received from the deep link rather than as a header).
        if user is None:
            user = _user_from_payload(_decode_any(_extract_token(request)))

        if user is not None:
            # Cloud's token-check exchanges the (opaque) deep-link token for a
            # fresh JWT pair: {access_token, refresh_token} — nothing else.
            return Response(_token_pair(user), status=status.HTTP_200_OK)

        return Response(
            {"error": "Invalid token"},
            status=status.HTTP_403_FORBIDDEN,
        )

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)


@method_decorator(csrf_exempt, name="dispatch")
class MobileRefreshTokenEndpoint(APIView):
    """Exchange a valid refresh token for a fresh access token.

    No authentication backends here: the credential is the ``refresh_token`` in
    the request body, not a session or Bearer header.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        log_request(request, "refresh-token")

        # The credential is the token in the body/header. Accept either a refresh
        # or an access token (the deep-link token may be either), mint a fresh
        # full session.
        token = _extract_token(request)
        if not token:
            return Response(
                {"error": "MISSING_REFRESH_TOKEN"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user = _user_from_payload(_decode_any(token))
        if user is None:
            return Response(
                {
                    "detail": "Given token not valid for any token type",
                    "code": "token_not_valid",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Cloud's refresh-token returns a fresh {access_token, refresh_token} pair.
        return Response(_token_pair(user), status=status.HTTP_200_OK)


# OVERLAY: mobile-auth
@method_decorator(csrf_exempt, name="dispatch")
class MAuthBridgeEndpoint(APIView):
    """Web auth bridge the native app opens in its in-app WebView at ``/m/auth``.

    Flow (reverse-engineered from the app):
      1. App opens ``{server}/m/auth`` in a WebView (shared cookies).
      2. Not authenticated -> redirect to the web sign-in page, asking it to
         return to ``/m/auth`` after a successful login (``next_path``).
      3. After web login the WebView lands back on ``/m/auth`` *with* the session
         cookie -> we mint a mobile access token and redirect to
         ``/m/auth?token=<jwt>``. The app intercepts that navigation and grabs
         the token (it never actually loads the page).

    ``?token=`` requests are answered with a tiny 200 page so that, if the app
    does load it, we don't bounce into a redirect loop. This is a CAPTURE
    iteration: every hit is logged under ``[MOBILE-AUTH]``.
    """

    authentication_classes = [BaseSessionAuthentication]
    permission_classes = [AllowAny]

    def _web_url(self) -> str:
        return (getattr(settings, "WEB_URL", "") or getattr(settings, "APP_BASE_URL", "") or "").rstrip("/")

    # OVERLAY: mobile-auth — callback-схема flutter_web_auth_2 (из AndroidManifest
    # CallbackActivity): приложение ждёт возврата на app.plane.so://...?token=
    CALLBACK_SCHEME = "app.plane.so"

    def get(self, request):
        log_request(request, "m-auth")
        web_url = self._web_url()

        if request.user and request.user.is_authenticated:
            access, _ = access_token_for(request.user)
            refresh, _ = refresh_token_for(request.user)
            # Возврат в нативное приложение через custom-scheme deep link.
            # Внешний браузер (Custom Tab) получает navigation на app.plane.so://
            # и передаёт его ОС → открывается приложение (flutter_web_auth_2
            # CallbackActivity). Django HttpResponseRedirect блокирует не-http(s)
            # схемы, поэтому редиректим через meta/JS на HTML-странице.
            #
            # Имя query-параметра, которое читает приложение, точно неизвестно —
            # кладём токен сразу под несколько вероятных имён (и access, и refresh),
            # эндпоинты token-check/refresh-token принимают любой из них.
            query = urlencode(
                {
                    "token": access,
                    "access_token": access,
                    "refresh_token": refresh,
                }
            )
            deeplink = f"{self.CALLBACK_SCHEME}://auth?{query}"
            html = (
                "<!doctype html><html><head><meta charset=utf-8>"
                f'<meta http-equiv="refresh" content="0;url={deeplink}">'
                f"<script>location.replace({deeplink!r});</script>"
                "</head><body>Returning to app…</body></html>"
            )
            return HttpResponse(html)

        # Not authenticated -> bounce to the web login, returning to /m/auth.
        return HttpResponseRedirect(f"{web_url}/sign-in?next_path=/m/auth")
