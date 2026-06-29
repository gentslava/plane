# OVERLAY: mobile-auth
# JWT minting + DRF Bearer authentication for the Plane native mobile app.
#
# This module is part of the fork overlay layer (not present in Community Plane).
# It issues short-lived access tokens and longer-lived refresh tokens signed with
# ``settings.SECRET_KEY`` (HS256) and authenticates ``Authorization: Bearer <jwt>``
# requests against the standard Plane ``User`` model.

import datetime
import logging
import uuid

import jwt
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from plane.db.models import User

logger = logging.getLogger("plane")

# Token lifetimes — matched to Plane Cloud's djangorestframework-simplejwt config
# (captured via mitm: access 15 min, refresh 90 days).
ACCESS_TOKEN_TTL = datetime.timedelta(minutes=15)
REFRESH_TOKEN_TTL = datetime.timedelta(days=90)

ALGORITHM = "HS256"


def _now() -> datetime.datetime:
    # Timezone-aware UTC; jwt encodes datetimes as POSIX timestamps.
    return datetime.datetime.now(tz=datetime.timezone.utc)


def _encode(user, token_type: str, ttl: datetime.timedelta) -> tuple[str, int]:
    """Encode a single JWT of the given ``token_type`` and return (token, exp_ts)."""
    issued_at = _now()
    expires_at = issued_at + ttl
    # Claim set mirrors djangorestframework-simplejwt (what the app receives from
    # Plane Cloud): token_type, exp, iat, jti, user_id.
    payload = {
        "token_type": token_type,
        "exp": int(expires_at.timestamp()),
        "iat": int(issued_at.timestamp()),
        "jti": uuid.uuid4().hex,
        "user_id": str(user.id),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)
    # PyJWT < 2 returns bytes; PyJWT >= 2 returns str. Normalise to str.
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token, int(expires_at.timestamp())


def access_token_for(user) -> tuple[str, int]:
    """Mint an access token. Returns (token, unix_expiry)."""
    return _encode(user, "access", ACCESS_TOKEN_TTL)


def refresh_token_for(user) -> tuple[str, int]:
    """Mint a refresh token. Returns (token, unix_expiry)."""
    return _encode(user, "refresh", REFRESH_TOKEN_TTL)


def mint_tokens(user) -> tuple[str, str]:
    """Mint an (access, refresh) pair for ``user``.

    Kept as the primary public helper described in the contract; callers that
    also need the access expiry should use :func:`access_token_for` directly.
    """
    access, _ = access_token_for(user)
    refresh, _ = refresh_token_for(user)
    return access, refresh


def decode_token(
    token: str, expected_type: str | None = None, verify_exp: bool = True
) -> dict:
    """Decode and validate a mobile JWT.

    Raises ``AuthenticationFailed`` on expiry, signature/format errors, or a
    token-type mismatch when ``expected_type`` is provided.

    ``verify_exp=False`` skips the expiry check: used only by the token-check /
    refresh recovery path, which re-mints a fresh pair (exactly like Plane
    Cloud's token exchange). The signature is still verified, so an expired
    token can be exchanged but never forged. The 15-min access TTL stays in
    force for every *authenticated* request (MobileJWTAuthentication keeps
    ``verify_exp=True``); only the explicit "give me a fresh pair" endpoints are
    lenient, so a backgrounded app re-opening after the access token lapsed
    recovers its session instead of being logged out.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"verify_exp": verify_exp},
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationFailed("Token has expired.")
    except jwt.InvalidTokenError:
        raise AuthenticationFailed("Invalid token.")

    if expected_type is not None and payload.get("token_type") != expected_type:
        raise AuthenticationFailed("Invalid token type.")

    return payload


class MobileJWTAuthentication(BaseAuthentication):
    """DRF authentication backend for ``Authorization: Bearer <jwt>``.

    DECLINES (returns ``None``) rather than failing whenever the Bearer cannot
    be turned into an authenticated user — no header, malformed header, or an
    expired / invalid / wrong-type token. Declining matters: a raised
    ``AuthenticationFailed`` here becomes a DRF 401 *before* permission checks,
    so it would also reject ``AllowAny`` public endpoints (``/api/instances/``,
    ``/auth/get-csrf-token/``) that the native app must reach to recover its
    session once the 15-min access token lapses — deadlocking it into an
    infinite loading spinner. By declining instead, protected endpoints still
    return 401 via ``IsAuthenticated`` (no weakening), while public endpoints
    stay reachable — matching Plane Cloud, where these answer regardless of
    Bearer validity. Only valid access tokens authenticate. The lenient
    token-check / refresh-token recovery endpoints decode separately with
    ``verify_exp=False``.
    """

    keyword = "Bearer"

    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")

        if not auth_header:
            return None

        parts = auth_header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            # Not a Bearer credential we own -> let other authenticators try.
            return None

        token = parts[1]
        try:
            payload = decode_token(token, expected_type="access")
        except AuthenticationFailed:
            # Expired / malformed / non-access Bearer: decline so public
            # endpoints stay reachable for session recovery (see class docstring).
            return None

        user_id = payload.get("user_id")
        if not user_id:
            return None

        try:
            user = User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            return None

        return (user, token)

    def authenticate_header(self, request):
        return self.keyword
