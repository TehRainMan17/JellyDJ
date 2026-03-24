"""
Tests for auth.py — JWT authentication and permission enforcement.

auth.py is the gate for every authenticated endpoint in JellyDJ.  A bug here
can allow unauthenticated access, privilege escalation (non-admin gaining
admin), or one user reading/modifying another user's playlists/templates.

Covers:
  - _secret_key(): prefers JWT_SECRET_KEY, falls back to SECRET_KEY, rejects
    insecure defaults and missing values
  - create_access_token() / decode_access_token() roundtrip
  - decode_access_token() rejects expired, wrong-key, and malformed tokens
  - create_access_token() does not mutate the caller's payload dict
  - create_refresh_token(): length, hex format, uniqueness
  - hash_token(): determinism, known SHA-256 output, length
  - get_current_user(): returns UserContext for valid token; raises HTTP 401
    for expired / garbage / missing-claim tokens
  - require_admin(): passes admins through; raises HTTP 403 for non-admins
  - assert_owns_template(): admin bypass, owner pass, non-owner 403
  - assert_owns_playlist(): admin bypass, owner pass, non-owner 403

Run with: docker exec jellydj-backend python -m pytest tests/test_auth.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import hashlib
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt, JWTError

import auth
from auth import (
    ALGORITHM,
    UserContext,
    assert_owns_playlist,
    assert_owns_template,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    get_current_user,
    hash_token,
    require_admin,
)

# A strong key used in all tests; never matches any insecure-defaults list.
STRONG_KEY = "test-jwt-secret-key-" + "t" * 44


# ── _secret_key() ─────────────────────────────────────────────────────────────

class TestSecretKey:
    """Key selection and rejection logic for the JWT signing key."""

    def test_prefers_jwt_secret_key_over_secret_key(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "jwt-specific-" + "j" * 51)
        monkeypatch.setenv("SECRET_KEY", "fallback-" + "f" * 55)
        key = auth._secret_key()
        assert key.startswith("jwt-specific-")

    def test_falls_back_to_secret_key_when_jwt_unset(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.setenv("SECRET_KEY", "fallback-" + "f" * 55)
        key = auth._secret_key()
        assert key.startswith("fallback-")

    def test_raises_when_both_keys_unset(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            auth._secret_key()

    def test_raises_for_insecure_default_dev(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "dev-insecure-secret-change-me")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="insecure default"):
            auth._secret_key()

    def test_raises_for_insecure_default_changeme(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "change-me-generate-a-real-secret")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="insecure default"):
            auth._secret_key()

    def test_whitespace_only_jwt_key_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "   ")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            auth._secret_key()

    def test_empty_jwt_key_falls_back_to_secret_key(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "")
        monkeypatch.setenv("SECRET_KEY", "fallback-" + "f" * 55)
        key = auth._secret_key()
        assert key.startswith("fallback-")


# ── create_access_token / decode_access_token ─────────────────────────────────

class TestAccessToken:
    """JWT creation and decoding — the token used for every authenticated request."""

    @pytest.fixture(autouse=True)
    def set_key(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", STRONG_KEY)
        monkeypatch.delenv("SECRET_KEY", raising=False)

    def _payload(self, **overrides):
        base = {"user_id": "uid-123", "username": "testuser", "is_admin": False}
        base.update(overrides)
        return base

    # Happy-path roundtrip

    def test_decoded_user_id_matches(self):
        token = create_access_token(self._payload())
        assert decode_access_token(token)["user_id"] == "uid-123"

    def test_decoded_username_matches(self):
        token = create_access_token(self._payload())
        assert decode_access_token(token)["username"] == "testuser"

    def test_decoded_is_admin_true(self):
        token = create_access_token(self._payload(is_admin=True))
        assert decode_access_token(token)["is_admin"] is True

    def test_decoded_is_admin_false(self):
        token = create_access_token(self._payload(is_admin=False))
        assert decode_access_token(token)["is_admin"] is False

    def test_token_is_a_string(self):
        assert isinstance(create_access_token(self._payload()), str)

    def test_token_contains_exp_claim(self):
        token = create_access_token(self._payload())
        assert "exp" in decode_access_token(token)

    def test_does_not_mutate_caller_payload(self):
        """create_access_token must copy the payload, not inject 'exp' into the caller's dict."""
        payload = self._payload()
        original_keys = set(payload.keys())
        create_access_token(payload)
        assert set(payload.keys()) == original_keys

    # Rejection cases

    def test_expired_token_raises_jwt_error(self):
        payload = self._payload()
        payload["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        token = jwt.encode(payload, STRONG_KEY, algorithm=ALGORITHM)
        with pytest.raises(JWTError):
            decode_access_token(token)

    def test_wrong_key_raises_jwt_error(self):
        payload = self._payload()
        payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=1)
        token = jwt.encode(payload, "wrong-key-" + "w" * 54, algorithm=ALGORITHM)
        with pytest.raises(JWTError):
            decode_access_token(token)

    def test_garbage_string_raises_jwt_error(self):
        with pytest.raises(JWTError):
            decode_access_token("not.a.jwt.token")

    def test_empty_string_raises_jwt_error(self):
        with pytest.raises(JWTError):
            decode_access_token("")

    def test_tampered_signature_raises_jwt_error(self):
        token = create_access_token(self._payload())
        # Flip the last character of the signature segment
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(JWTError):
            decode_access_token(tampered)


# ── create_refresh_token ──────────────────────────────────────────────────────

class TestCreateRefreshToken:
    """Opaque refresh tokens — stored as SHA-256 hashes in the DB."""

    def test_returns_128_char_hex_string(self):
        token = create_refresh_token()
        assert len(token) == 128
        assert all(c in "0123456789abcdef" for c in token)

    def test_is_string(self):
        assert isinstance(create_refresh_token(), str)

    def test_unique_across_twenty_calls(self):
        tokens = {create_refresh_token() for _ in range(20)}
        assert len(tokens) == 20


# ── hash_token ────────────────────────────────────────────────────────────────

class TestHashToken:
    """hash_token() is used to store refresh tokens safely in the database."""

    def test_deterministic(self):
        assert hash_token("abc") == hash_token("abc")

    def test_returns_64_char_hex(self):
        digest = hash_token("some-refresh-token")
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_different_tokens_produce_different_hashes(self):
        assert hash_token("token-a") != hash_token("token-b")

    def test_matches_stdlib_sha256(self):
        raw = "verify-against-stdlib"
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert hash_token(raw) == expected


# ── get_current_user ──────────────────────────────────────────────────────────

class TestGetCurrentUser:
    """
    get_current_user() is the FastAPI dependency that gates every authenticated
    endpoint.  It must accept valid tokens and reject invalid ones with HTTP 401.
    """

    @pytest.fixture(autouse=True)
    def set_key(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", STRONG_KEY)
        monkeypatch.delenv("SECRET_KEY", raising=False)

    def _credentials(self, token: str) -> HTTPAuthorizationCredentials:
        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = token
        return creds

    def _valid_token(self, user_id="uid-1", username="alice", is_admin=False) -> str:
        return create_access_token(
            {"user_id": user_id, "username": username, "is_admin": is_admin}
        )

    # Happy path

    def test_returns_user_context_with_correct_user_id(self):
        ctx = get_current_user(self._credentials(self._valid_token()))
        assert ctx.user_id == "uid-1"

    def test_returns_user_context_with_correct_username(self):
        ctx = get_current_user(self._credentials(self._valid_token()))
        assert ctx.username == "alice"

    def test_returns_non_admin_flag(self):
        ctx = get_current_user(self._credentials(self._valid_token(is_admin=False)))
        assert ctx.is_admin is False

    def test_returns_admin_flag(self):
        ctx = get_current_user(self._credentials(self._valid_token(is_admin=True)))
        assert ctx.is_admin is True

    def test_is_admin_defaults_false_when_claim_absent(self):
        payload = {
            "user_id": "uid-1",
            "username": "alice",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = jwt.encode(payload, STRONG_KEY, algorithm=ALGORITHM)
        ctx = get_current_user(self._credentials(token))
        assert ctx.is_admin is False

    # Rejection cases — all must be HTTP 401

    def test_raises_401_for_expired_token(self):
        payload = {
            "user_id": "uid-1", "username": "alice", "is_admin": False,
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        token = jwt.encode(payload, STRONG_KEY, algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as exc:
            get_current_user(self._credentials(token))
        assert exc.value.status_code == 401

    def test_raises_401_for_garbage_token(self):
        with pytest.raises(HTTPException) as exc:
            get_current_user(self._credentials("garbage-token"))
        assert exc.value.status_code == 401

    def test_raises_401_when_user_id_missing_from_payload(self):
        payload = {
            "username": "alice", "is_admin": False,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = jwt.encode(payload, STRONG_KEY, algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as exc:
            get_current_user(self._credentials(token))
        assert exc.value.status_code == 401

    def test_raises_401_when_username_missing_from_payload(self):
        payload = {
            "user_id": "uid-1", "is_admin": False,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = jwt.encode(payload, STRONG_KEY, algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as exc:
            get_current_user(self._credentials(token))
        assert exc.value.status_code == 401

    def test_raises_401_for_wrong_key_token(self):
        payload = {
            "user_id": "uid-1", "username": "alice", "is_admin": False,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        token = jwt.encode(payload, "wrong-key-" + "w" * 54, algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as exc:
            get_current_user(self._credentials(token))
        assert exc.value.status_code == 401


# ── require_admin ─────────────────────────────────────────────────────────────

class TestRequireAdmin:
    """
    require_admin() gates admin-only endpoints.
    A non-admin user must always receive HTTP 403, never slip through.
    """

    def test_admin_user_passes_through(self):
        user = UserContext(user_id="uid-1", username="admin", is_admin=True)
        result = require_admin(user)
        assert result is user

    def test_non_admin_raises_403(self):
        user = UserContext(user_id="uid-2", username="regular", is_admin=False)
        with pytest.raises(HTTPException) as exc:
            require_admin(user)
        assert exc.value.status_code == 403

    def test_non_admin_error_message_is_informative(self):
        user = UserContext(user_id="uid-2", username="regular", is_admin=False)
        with pytest.raises(HTTPException) as exc:
            require_admin(user)
        assert "Administrator" in exc.value.detail or "admin" in exc.value.detail.lower()


# ── assert_owns_template ──────────────────────────────────────────────────────

class TestAssertOwnsTemplate:
    """
    assert_owns_template() guards template modification routes.
    Admin can modify any template; regular users only their own.
    """

    def _template(self, owner_id: str):
        t = MagicMock()
        t.owner_user_id = owner_id
        return t

    def test_admin_can_access_any_template(self):
        user = UserContext(user_id="uid-admin", username="admin", is_admin=True)
        # Should not raise even though user_id differs from owner
        assert_owns_template(self._template("uid-other"), user)

    def test_owner_can_access_own_template(self):
        user = UserContext(user_id="uid-1", username="alice", is_admin=False)
        assert_owns_template(self._template("uid-1"), user)

    def test_non_owner_raises_403(self):
        user = UserContext(user_id="uid-1", username="alice", is_admin=False)
        with pytest.raises(HTTPException) as exc:
            assert_owns_template(self._template("uid-2"), user)
        assert exc.value.status_code == 403

    def test_admin_with_own_template_still_passes(self):
        user = UserContext(user_id="uid-admin", username="admin", is_admin=True)
        assert_owns_template(self._template("uid-admin"), user)


# ── assert_owns_playlist ──────────────────────────────────────────────────────

class TestAssertOwnsPlaylist:
    """
    assert_owns_playlist() guards playlist access routes.
    Same ownership semantics as templates.
    """

    def _playlist(self, owner_id: str):
        p = MagicMock()
        p.owner_user_id = owner_id
        return p

    def test_admin_can_access_any_playlist(self):
        user = UserContext(user_id="uid-admin", username="admin", is_admin=True)
        assert_owns_playlist(self._playlist("uid-other"), user)

    def test_owner_can_access_own_playlist(self):
        user = UserContext(user_id="uid-1", username="alice", is_admin=False)
        assert_owns_playlist(self._playlist("uid-1"), user)

    def test_non_owner_raises_403(self):
        user = UserContext(user_id="uid-1", username="alice", is_admin=False)
        with pytest.raises(HTTPException) as exc:
            assert_owns_playlist(self._playlist("uid-2"), user)
        assert exc.value.status_code == 403

    def test_admin_with_own_playlist_still_passes(self):
        user = UserContext(user_id="uid-admin", username="admin", is_admin=True)
        assert_owns_playlist(self._playlist("uid-admin"), user)
