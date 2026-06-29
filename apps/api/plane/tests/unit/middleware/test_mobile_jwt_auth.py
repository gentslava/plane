# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Unit tests for MobileJWTAuthentication (OVERLAY: mobile-auth).

Guards the recovery contract for the native app: an expired / invalid / wrong
type Bearer must DECLINE (return None), never raise. A raised
AuthenticationFailed becomes a DRF 401 before permission checks, which also
rejects AllowAny public endpoints (/api/instances/, /auth/get-csrf-token/) the
app needs to recover its session after the 15-min access token lapses —
deadlocking it into an infinite loading spinner. Declining keeps protected
endpoints returning 401 via IsAuthenticated (no weakening) while public ones
stay reachable, matching Plane Cloud.
"""

import datetime
import uuid

import jwt
import pytest
from django.conf import settings
from django.test import RequestFactory

from plane.authentication.mobile.jwt import (
    ALGORITHM,
    MobileJWTAuthentication,
    access_token_for,
    refresh_token_for,
)


def _bearer_request(token=None):
    factory = RequestFactory()
    extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
    return factory.get("/api/instances/", **extra)


def _expired_access_token(user):
    """A correctly-signed access token whose exp is in the past."""
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    payload = {
        "token_type": "access",
        "exp": int((now - datetime.timedelta(minutes=5)).timestamp()),
        "iat": int((now - datetime.timedelta(minutes=20)).timestamp()),
        "jti": uuid.uuid4().hex,
        "user_id": str(user.id),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)
    return token.decode("utf-8") if isinstance(token, bytes) else token


@pytest.mark.unit
class TestMobileJWTAuthentication:
    def test_no_bearer_header_declines(self):
        assert MobileJWTAuthentication().authenticate(_bearer_request()) is None

    def test_non_bearer_scheme_declines(self):
        factory = RequestFactory()
        request = factory.get("/api/instances/", HTTP_AUTHORIZATION="Token abc")
        assert MobileJWTAuthentication().authenticate(request) is None

    def test_garbage_bearer_declines_not_raises(self):
        # Malformed token: decode fails -> must decline so public endpoints work.
        result = MobileJWTAuthentication().authenticate(_bearer_request("not.a.jwt"))
        assert result is None

    @pytest.mark.django_db
    def test_valid_access_token_authenticates(self, create_user):
        token, _ = access_token_for(create_user)
        user, returned = MobileJWTAuthentication().authenticate(_bearer_request(token))
        assert user == create_user
        assert returned == token

    @pytest.mark.django_db
    def test_expired_access_token_declines_not_raises(self, create_user):
        # The core regression: an expired access token must decline (None),
        # not raise — otherwise public recovery endpoints 401 and the app hangs.
        token = _expired_access_token(create_user)
        result = MobileJWTAuthentication().authenticate(_bearer_request(token))
        assert result is None

    @pytest.mark.django_db
    def test_refresh_token_on_access_path_declines(self, create_user):
        # A refresh token is the wrong type for request auth -> decline.
        token, _ = refresh_token_for(create_user)
        result = MobileJWTAuthentication().authenticate(_bearer_request(token))
        assert result is None

    def test_forged_signature_declines(self):
        # A well-formed access token signed with the WRONG key must never
        # authenticate — signature verification stays strict (no bypass).
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        forged = jwt.encode(
            {
                "token_type": "access",
                "exp": int((now + datetime.timedelta(minutes=15)).timestamp()),
                "iat": int(now.timestamp()),
                "jti": uuid.uuid4().hex,
                "user_id": str(uuid.uuid4()),
            },
            "not-the-real-secret-key",
            algorithm=ALGORITHM,
        )
        if isinstance(forged, bytes):
            forged = forged.decode("utf-8")
        assert MobileJWTAuthentication().authenticate(_bearer_request(forged)) is None

    def test_missing_user_id_declines(self):
        # Correctly-signed, unexpired access token with no user_id claim.
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        token = jwt.encode(
            {
                "token_type": "access",
                "exp": int((now + datetime.timedelta(minutes=15)).timestamp()),
                "iat": int(now.timestamp()),
                "jti": uuid.uuid4().hex,
            },
            settings.SECRET_KEY,
            algorithm=ALGORITHM,
        )
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        assert MobileJWTAuthentication().authenticate(_bearer_request(token)) is None

    @pytest.mark.django_db
    def test_deactivated_user_declines(self, create_user):
        # Valid signature but the account is disabled: no authentication, and
        # declining still yields 401 on protected endpoints (no bypass).
        token, _ = access_token_for(create_user)
        create_user.is_active = False
        create_user.save()
        result = MobileJWTAuthentication().authenticate(_bearer_request(token))
        assert result is None
