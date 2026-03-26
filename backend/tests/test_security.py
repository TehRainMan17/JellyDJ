"""
Security regression tests for JellyDJ — HIGH severity findings.

Tests three areas hardened by the security audit:

1. Startup secret validation
   - auth._secret_key() and crypto._get_fernet() must raise on missing /
     insecure keys.  These are called at boot in main.py lifespan() so a bad
     .env aborts the process before accepting requests.

2. SetupStatusResponse.backdoor_active
   - The /api/auth/setup-status endpoint must return backdoor_active=True
     exactly when SETUP_ALLOW_AFTER_CONFIGURE=true, Jellyfin is configured,
     and setup credentials are present — the condition that leaves a permanent
     admin bypass active.

3. Webhook secret enforcement (_verify_webhook_secret)
   - Default (no secret set): requests are rejected with HTTP 401
   - WEBHOOK_SECRET set: correct secret passes, wrong secret rejects
   - WEBHOOK_SECRET_REQUIRED=false: requests pass through regardless
   - Token accepted via X-Jellyfin-Token header OR ?token= query param

4. Setup login blocked post-configure (no SETUP_ALLOW_AFTER_CONFIGURE)
   - _jellyfin_is_configured=True + flag absent → 403 even with valid creds

Run with: docker exec jellydj-backend python -m pytest tests/test_security.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_request(headers: dict | None = None, query_params: dict | None = None):
    """Build a minimal mock starlette Request for _verify_webhook_secret."""
    req = MagicMock()
    req.headers = headers or {}
    req.query_params = query_params or {}
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    return req


# ═════════════════════════════════════════════════════════════════════════════
# 1. Startup secret validation
# ═════════════════════════════════════════════════════════════════════════════

class TestStartupSecretValidation:
    """
    auth._secret_key() and crypto._get_fernet() are called at boot.
    They must raise RuntimeError for any missing or insecure key value so the
    server aborts rather than starting with a forged-token-friendly secret.
    """

    # ── auth._secret_key ──────────────────────────────────────────────────

    def test_jwt_key_missing_raises(self, monkeypatch):
        import auth
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            auth._secret_key()

    def test_jwt_key_insecure_default_raises(self, monkeypatch):
        import auth
        monkeypatch.setenv("JWT_SECRET_KEY", "dev-insecure-secret-change-me")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="insecure default"):
            auth._secret_key()

    def test_jwt_key_second_insecure_default_raises(self, monkeypatch):
        import auth
        monkeypatch.setenv("JWT_SECRET_KEY", "change-me-generate-a-real-secret")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="insecure default"):
            auth._secret_key()

    def test_jwt_key_whitespace_only_raises(self, monkeypatch):
        import auth
        monkeypatch.setenv("JWT_SECRET_KEY", "   ")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            auth._secret_key()

    def test_jwt_key_strong_value_succeeds(self, monkeypatch):
        import auth
        monkeypatch.setenv("JWT_SECRET_KEY", "strong-" + "x" * 57)
        result = auth._secret_key()
        assert result.startswith("strong-")

    def test_jwt_key_falls_back_to_secret_key(self, monkeypatch):
        import auth
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.setenv("SECRET_KEY", "fallback-" + "y" * 55)
        result = auth._secret_key()
        assert result.startswith("fallback-")

    # ── crypto._get_fernet ────────────────────────────────────────────────

    def test_fernet_missing_secret_key_raises(self, monkeypatch):
        import crypto
        crypto._fernet = None
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            crypto._get_fernet()
        crypto._fernet = None

    def test_fernet_insecure_default_raises(self, monkeypatch):
        import crypto
        crypto._fernet = None
        monkeypatch.setenv("SECRET_KEY", "dev-insecure-secret-change-me")
        with pytest.raises(RuntimeError, match="insecure default"):
            crypto._get_fernet()
        crypto._fernet = None

    def test_fernet_empty_secret_key_raises(self, monkeypatch):
        import crypto
        crypto._fernet = None
        monkeypatch.setenv("SECRET_KEY", "")
        with pytest.raises(RuntimeError):
            crypto._get_fernet()
        crypto._fernet = None

    def test_fernet_strong_secret_key_succeeds(self, monkeypatch):
        import crypto
        crypto._fernet = None
        monkeypatch.setenv("SECRET_KEY", "z" * 64)
        fernet = crypto._get_fernet()
        assert fernet is not None
        crypto._fernet = None


# ═════════════════════════════════════════════════════════════════════════════
# 2. SetupStatusResponse.backdoor_active
# ═════════════════════════════════════════════════════════════════════════════

class TestSetupStatusBackdoorActive:
    """
    backdoor_active must be True only in the exact combination that creates a
    persistent admin bypass: credentials set + Jellyfin configured +
    SETUP_ALLOW_AFTER_CONFIGURE=true.

    Any other combination must return False to avoid false positives that could
    confuse operators or cause the frontend to show spurious warnings.
    """

    def _call_setup_status(self, monkeypatch, *, configured: bool, allow_after: str,
                           has_creds: bool) -> dict:
        """
        Call the setup_status endpoint function directly with a fake DB session.
        Returns the response as a dict.
        """
        from routers.auth import setup_status

        # Configure env vars
        if allow_after:
            monkeypatch.setenv("SETUP_ALLOW_AFTER_CONFIGURE", allow_after)
        else:
            monkeypatch.delenv("SETUP_ALLOW_AFTER_CONFIGURE", raising=False)

        if has_creds:
            monkeypatch.setenv("SETUP_USERNAME", "admin")
            monkeypatch.setenv("SETUP_PASSWORD", "secret123")
        else:
            monkeypatch.delenv("SETUP_USERNAME", raising=False)
            monkeypatch.delenv("SETUP_PASSWORD", raising=False)

        # Fake DB session
        mock_db = MagicMock()
        from models import ConnectionSettings
        if configured:
            fake_row = MagicMock(spec=ConnectionSettings)
            fake_row.base_url = "http://jellyfin:8096"
            mock_db.query.return_value.filter_by.return_value.first.return_value = fake_row
        else:
            mock_db.query.return_value.filter_by.return_value.first.return_value = None

        response = setup_status(db=mock_db)
        return {
            "setup_available": response.setup_available,
            "jellyfin_configured": response.jellyfin_configured,
            "backdoor_active": response.backdoor_active,
        }

    # Backdoor IS active
    def test_backdoor_active_when_configured_allow_after_true_with_creds(self, monkeypatch):
        result = self._call_setup_status(
            monkeypatch, configured=True, allow_after="true", has_creds=True
        )
        assert result["backdoor_active"] is True

    def test_backdoor_active_with_allow_after_1(self, monkeypatch):
        result = self._call_setup_status(
            monkeypatch, configured=True, allow_after="1", has_creds=True
        )
        assert result["backdoor_active"] is True

    def test_backdoor_active_with_allow_after_yes(self, monkeypatch):
        result = self._call_setup_status(
            monkeypatch, configured=True, allow_after="yes", has_creds=True
        )
        assert result["backdoor_active"] is True

    # Backdoor NOT active
    def test_not_backdoor_when_not_configured(self, monkeypatch):
        """Not configured yet — normal bootstrap state, no backdoor."""
        result = self._call_setup_status(
            monkeypatch, configured=False, allow_after="true", has_creds=True
        )
        assert result["backdoor_active"] is False

    def test_not_backdoor_when_flag_absent(self, monkeypatch):
        """Jellyfin configured, flag not set — setup login is correctly blocked."""
        result = self._call_setup_status(
            monkeypatch, configured=True, allow_after="", has_creds=True
        )
        assert result["backdoor_active"] is False

    def test_not_backdoor_when_flag_false(self, monkeypatch):
        result = self._call_setup_status(
            monkeypatch, configured=True, allow_after="false", has_creds=True
        )
        assert result["backdoor_active"] is False

    def test_not_backdoor_when_no_creds(self, monkeypatch):
        """Flag is set but credentials are absent — endpoint rejects anyway."""
        result = self._call_setup_status(
            monkeypatch, configured=True, allow_after="true", has_creds=False
        )
        assert result["backdoor_active"] is False

    def test_setup_available_false_when_configured_and_no_flag(self, monkeypatch):
        """Normal post-setup state: login not available, backdoor not active."""
        result = self._call_setup_status(
            monkeypatch, configured=True, allow_after="", has_creds=True
        )
        assert result["setup_available"] is False
        assert result["backdoor_active"] is False

    def test_setup_available_true_during_bootstrap(self, monkeypatch):
        """First-time setup: login available, backdoor not active."""
        result = self._call_setup_status(
            monkeypatch, configured=False, allow_after="", has_creds=True
        )
        assert result["setup_available"] is True
        assert result["backdoor_active"] is False


# ═════════════════════════════════════════════════════════════════════════════
# 3. Webhook secret enforcement
# ═════════════════════════════════════════════════════════════════════════════

class TestWebhookSecretEnforcement:
    """
    _verify_webhook_secret() is the only gate protecting the playback-event
    injection endpoint.  Every path must be explicitly tested.
    """

    def _verify(self, monkeypatch, *, webhook_secret="", required="true",
                header_token="", query_token=""):
        from routers.webhooks import _verify_webhook_secret

        if webhook_secret:
            monkeypatch.setenv("WEBHOOK_SECRET", webhook_secret)
        else:
            monkeypatch.delenv("WEBHOOK_SECRET", raising=False)

        monkeypatch.setenv("WEBHOOK_SECRET_REQUIRED", required)

        req = _make_request(
            headers={"X-Jellyfin-Token": header_token} if header_token else {},
            query_params={"token": query_token} if query_token else {},
        )
        return _verify_webhook_secret(req)

    # ── No secret configured ──────────────────────────────────────────────

    def test_blocks_when_no_secret_and_required_default(self, monkeypatch):
        """Default: no secret → 401."""
        with pytest.raises(HTTPException) as exc:
            self._verify(monkeypatch, webhook_secret="")
        assert exc.value.status_code == 401

    def test_blocks_when_no_secret_and_required_true(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            self._verify(monkeypatch, webhook_secret="", required="true")
        assert exc.value.status_code == 401

    def test_blocks_when_no_secret_and_required_1(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            self._verify(monkeypatch, webhook_secret="", required="1")
        assert exc.value.status_code == 401

    def test_passes_when_no_secret_and_required_false(self, monkeypatch):
        """Operator has explicitly opted out for a private LAN install."""
        result = self._verify(monkeypatch, webhook_secret="", required="false")
        assert result is None  # function returns None on success

    def test_passes_when_no_secret_and_required_0(self, monkeypatch):
        result = self._verify(monkeypatch, webhook_secret="", required="0")
        assert result is None

    def test_passes_when_no_secret_and_required_no(self, monkeypatch):
        result = self._verify(monkeypatch, webhook_secret="", required="no")
        assert result is None

    # ── Secret configured: header token ──────────────────────────────────

    def test_passes_with_correct_header_token(self, monkeypatch):
        result = self._verify(
            monkeypatch,
            webhook_secret="my-secret-token",
            header_token="my-secret-token",
        )
        assert result is None

    def test_blocks_with_wrong_header_token(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            self._verify(
                monkeypatch,
                webhook_secret="my-secret-token",
                header_token="wrong-token",
            )
        assert exc.value.status_code == 401

    def test_blocks_with_empty_header_token(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            self._verify(monkeypatch, webhook_secret="my-secret-token", header_token="")
        assert exc.value.status_code == 401

    def test_blocks_with_no_token_at_all(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            self._verify(monkeypatch, webhook_secret="my-secret-token")
        assert exc.value.status_code == 401

    # ── Secret configured: query-param token fallback ────────────────────

    def test_passes_with_correct_query_token(self, monkeypatch):
        result = self._verify(
            monkeypatch,
            webhook_secret="my-secret-token",
            query_token="my-secret-token",
        )
        assert result is None

    def test_blocks_with_wrong_query_token(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            self._verify(
                monkeypatch,
                webhook_secret="my-secret-token",
                query_token="wrong",
            )
        assert exc.value.status_code == 401

    # ── Header takes precedence over query param ──────────────────────────

    def test_header_takes_precedence_over_query_param(self, monkeypatch):
        """When both are provided, the header is used (and it's correct here)."""
        result = self._verify(
            monkeypatch,
            webhook_secret="correct-secret",
            header_token="correct-secret",
            query_token="wrong-query-token",
        )
        assert result is None

    def test_header_wrong_blocks_even_if_query_correct(self, monkeypatch):
        """Wrong header token overrides a correct query param."""
        with pytest.raises(HTTPException) as exc:
            self._verify(
                monkeypatch,
                webhook_secret="correct-secret",
                header_token="wrong-header-token",
                query_token="correct-secret",
            )
        assert exc.value.status_code == 401

    # ── Error message should not leak the expected secret value ──────────

    def test_401_detail_does_not_contain_expected_secret(self, monkeypatch):
        secret = "super-secret-value-12345"
        with pytest.raises(HTTPException) as exc:
            self._verify(
                monkeypatch,
                webhook_secret=secret,
                header_token="wrong",
            )
        assert secret not in exc.value.detail


# ═════════════════════════════════════════════════════════════════════════════
# 4. Setup login blocked after Jellyfin is configured
# ═════════════════════════════════════════════════════════════════════════════

class TestSetupLoginBlockedPostConfigure:
    """
    Once Jellyfin is configured, the setup-login endpoint must reject
    credentials unless SETUP_ALLOW_AFTER_CONFIGURE=true.

    This prevents the setup account from acting as a permanent backdoor for
    operators who leave SETUP_USERNAME / SETUP_PASSWORD in their .env after
    the initial setup is complete.
    """

    def _configured_db(self):
        """Return a mock DB session that reports Jellyfin as configured."""
        from models import ConnectionSettings
        mock_db = MagicMock()
        fake_row = MagicMock(spec=ConnectionSettings)
        fake_row.base_url = "http://jellyfin:8096"
        mock_db.query.return_value.filter_by.return_value.first.return_value = fake_row
        return mock_db

    def _unconfigured_db(self):
        """Return a mock DB session that reports Jellyfin as NOT configured."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter_by.return_value.first.return_value = None
        return mock_db

    def _fake_request(self, ip="127.0.0.1"):
        req = MagicMock()
        req.headers = {}
        req.client = MagicMock()
        req.client.host = ip
        return req

    def test_setup_login_blocked_when_jellyfin_configured_no_flag(self, monkeypatch):
        """Normal post-setup state — setup login must be 403."""
        from routers.auth import setup_login, SetupLoginRequest
        monkeypatch.setenv("SETUP_USERNAME", "setupadmin")
        monkeypatch.setenv("SETUP_PASSWORD", "SetupPass123!")
        monkeypatch.delenv("SETUP_ALLOW_AFTER_CONFIGURE", raising=False)
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-" + "k" * 55)

        body = SetupLoginRequest(username="setupadmin", password="SetupPass123!")
        with pytest.raises(HTTPException) as exc:
            setup_login(
                request=self._fake_request(),
                body=body,
                db=self._configured_db(),
            )
        assert exc.value.status_code == 403

    def test_setup_login_blocked_when_jellyfin_configured_flag_false(self, monkeypatch):
        from routers.auth import setup_login, SetupLoginRequest
        monkeypatch.setenv("SETUP_USERNAME", "setupadmin")
        monkeypatch.setenv("SETUP_PASSWORD", "SetupPass123!")
        monkeypatch.setenv("SETUP_ALLOW_AFTER_CONFIGURE", "false")
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-" + "k" * 55)

        body = SetupLoginRequest(username="setupadmin", password="SetupPass123!")
        with pytest.raises(HTTPException) as exc:
            setup_login(
                request=self._fake_request(),
                body=body,
                db=self._configured_db(),
            )
        assert exc.value.status_code == 403

    def test_setup_login_allowed_during_bootstrap(self, monkeypatch):
        """Jellyfin not yet configured — setup login must succeed with correct creds."""
        from routers.auth import setup_login, SetupLoginRequest
        monkeypatch.setenv("SETUP_USERNAME", "setupadmin")
        monkeypatch.setenv("SETUP_PASSWORD", "SetupPass123!")
        monkeypatch.delenv("SETUP_ALLOW_AFTER_CONFIGURE", raising=False)
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-" + "k" * 55)

        # Stub out _record_setup_event so no real DB write happens
        with patch("routers.auth._record_setup_event"):
            body = SetupLoginRequest(username="setupadmin", password="SetupPass123!")
            response = setup_login(
                request=self._fake_request(),
                body=body,
                db=self._unconfigured_db(),
            )
        assert response.access_token
        assert response.is_admin is True

    def test_setup_login_rejects_wrong_password_during_bootstrap(self, monkeypatch):
        from routers.auth import setup_login, SetupLoginRequest
        monkeypatch.setenv("SETUP_USERNAME", "setupadmin")
        monkeypatch.setenv("SETUP_PASSWORD", "CorrectPassword!")
        monkeypatch.delenv("SETUP_ALLOW_AFTER_CONFIGURE", raising=False)
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-" + "k" * 55)

        body = SetupLoginRequest(username="setupadmin", password="WrongPassword!")
        with pytest.raises(HTTPException) as exc:
            setup_login(
                request=self._fake_request(),
                body=body,
                db=self._unconfigured_db(),
            )
        assert exc.value.status_code == 401

    def test_setup_login_rejects_wrong_username_during_bootstrap(self, monkeypatch):
        from routers.auth import setup_login, SetupLoginRequest
        monkeypatch.setenv("SETUP_USERNAME", "setupadmin")
        monkeypatch.setenv("SETUP_PASSWORD", "CorrectPassword!")
        monkeypatch.delenv("SETUP_ALLOW_AFTER_CONFIGURE", raising=False)
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-" + "k" * 55)

        body = SetupLoginRequest(username="wronguser", password="CorrectPassword!")
        with pytest.raises(HTTPException) as exc:
            setup_login(
                request=self._fake_request(),
                body=body,
                db=self._unconfigured_db(),
            )
        assert exc.value.status_code == 401

    def test_setup_login_blocked_when_no_creds_configured(self, monkeypatch):
        """SETUP_USERNAME / SETUP_PASSWORD not set → setup mode not available → 403."""
        from routers.auth import setup_login, SetupLoginRequest
        monkeypatch.delenv("SETUP_USERNAME", raising=False)
        monkeypatch.delenv("SETUP_PASSWORD", raising=False)
        monkeypatch.delenv("SETUP_ALLOW_AFTER_CONFIGURE", raising=False)
        monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-" + "k" * 55)

        body = SetupLoginRequest(username="anyone", password="anything")
        with pytest.raises(HTTPException) as exc:
            setup_login(
                request=self._fake_request(),
                body=body,
                db=self._unconfigured_db(),
            )
        assert exc.value.status_code == 403
