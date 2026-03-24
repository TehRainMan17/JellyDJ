"""
Tests for crypto.py — Fernet credential encryption.

All sensitive credentials (Jellyfin, Lidarr, Spotify, Last.fm API keys) are
encrypted at rest using this module, so failures here compromise every stored
secret in the database.

Covers:
  - _get_fernet(): rejection of missing / insecure SECRET_KEY values
  - _get_fernet(): module-level singleton is reused, not re-derived each call
  - encrypt() / decrypt() roundtrip for various payloads
  - decrypt() raises InvalidToken for garbage, truncated, or cross-key ciphertext
  - Key stability: same SECRET_KEY always derives the same Fernet key across
    singleton resets (simulates app restart)
  - Key isolation: ciphertext from key A cannot be decrypted with key B

Run with: docker exec jellydj-backend python -m pytest tests/test_crypto.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from cryptography.fernet import InvalidToken

import crypto


@pytest.fixture(autouse=True)
def reset_fernet_singleton():
    """
    Reset the module-level _fernet singleton before and after each test.
    Without this, a test that initialises the singleton with one key would
    pollute subsequent tests that patch SECRET_KEY to a different value.
    """
    original = crypto._fernet
    crypto._fernet = None
    yield
    crypto._fernet = original


# ── _get_fernet(): insecure key rejection ─────────────────────────────────────

class TestGetFernetKeyRejection:
    """_get_fernet() must refuse keys that would make stored credentials trivially decryptable."""

    def test_unset_secret_key_raises(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            crypto._get_fernet()

    def test_empty_secret_key_raises(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "")
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            crypto._get_fernet()

    def test_whitespace_only_raises(self, monkeypatch):
        # strip() is applied — all-whitespace is treated as empty
        monkeypatch.setenv("SECRET_KEY", "   \t  ")
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            crypto._get_fernet()

    def test_insecure_default_dev_raises(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "dev-insecure-secret-change-me")
        with pytest.raises(RuntimeError, match="insecure default"):
            crypto._get_fernet()

    def test_insecure_default_changeme_raises(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "change-me-generate-a-real-secret")
        with pytest.raises(RuntimeError, match="insecure default"):
            crypto._get_fernet()

    def test_strong_key_succeeds(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "a" * 64)
        fernet = crypto._get_fernet()
        assert fernet is not None


# ── _get_fernet(): singleton ──────────────────────────────────────────────────

class TestGetFernetSingleton:
    """Key derivation runs once; the same Fernet instance is returned on repeat calls."""

    def test_same_instance_returned_on_second_call(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "a" * 64)
        f1 = crypto._get_fernet()
        f2 = crypto._get_fernet()
        assert f1 is f2


# ── encrypt / decrypt roundtrip ───────────────────────────────────────────────

class TestEncryptDecryptRoundtrip:

    @pytest.fixture(autouse=True)
    def set_key(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "b" * 64)

    def test_roundtrip_simple_api_key(self):
        plaintext = "my-jellyfin-api-key-12345"
        assert crypto.decrypt(crypto.encrypt(plaintext)) == plaintext

    def test_roundtrip_empty_string(self):
        assert crypto.decrypt(crypto.encrypt("")) == ""

    def test_roundtrip_unicode(self):
        plaintext = "café-résumé-naïve-日本語"
        assert crypto.decrypt(crypto.encrypt(plaintext)) == plaintext

    def test_roundtrip_long_value(self):
        # Realistic upper bound: tokens, UUIDs, keys concatenated
        plaintext = "x" * 10_000
        assert crypto.decrypt(crypto.encrypt(plaintext)) == plaintext

    def test_encrypt_returns_string(self):
        assert isinstance(crypto.encrypt("hello"), str)

    def test_encrypted_token_does_not_contain_plaintext(self):
        plaintext = "super-secret-api-key"
        token = crypto.encrypt(plaintext)
        assert plaintext not in token

    def test_encrypt_produces_different_tokens_each_call(self):
        # Fernet uses a random IV — identical plaintext → different ciphertext
        t1 = crypto.encrypt("same-value")
        t2 = crypto.encrypt("same-value")
        assert t1 != t2


# ── decrypt: failure modes ────────────────────────────────────────────────────

class TestDecryptFailures:

    @pytest.fixture(autouse=True)
    def set_key(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "c" * 64)

    def test_garbage_token_raises_invalid_token(self):
        with pytest.raises(InvalidToken):
            crypto.decrypt("this-is-not-a-valid-fernet-token")

    def test_truncated_token_raises(self):
        token = crypto.encrypt("some-value")
        with pytest.raises(Exception):
            crypto.decrypt(token[:20])

    def test_empty_string_raises(self):
        with pytest.raises(Exception):
            crypto.decrypt("")

    def test_plaintext_passed_as_token_raises(self):
        # Callers must never store plaintext then pass it to decrypt()
        with pytest.raises(Exception):
            crypto.decrypt("raw-api-key-never-encrypted")


# ── Key isolation ─────────────────────────────────────────────────────────────

class TestKeyIsolation:
    """A ciphertext produced with one SECRET_KEY must not be decryptable with another."""

    def test_cross_key_decrypt_raises(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "key-alpha-" + "a" * 54)
        token = crypto.encrypt("my-secret-value")

        # Simulate key rotation / wrong key
        crypto._fernet = None
        monkeypatch.setenv("SECRET_KEY", "key-beta--" + "b" * 54)

        with pytest.raises(InvalidToken):
            crypto.decrypt(token)


# ── Key stability ─────────────────────────────────────────────────────────────

class TestKeyStability:
    """
    The same SECRET_KEY must always derive the same Fernet key so that tokens
    encrypted before an app restart remain decryptable after.
    """

    def test_token_decrypts_after_singleton_reset(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "stable-secret-" + "s" * 50)
        token = crypto.encrypt("persistent-api-key")

        # Reset the singleton — simulates an app restart
        crypto._fernet = None

        assert crypto.decrypt(token) == "persistent-api-key"

    def test_two_singleton_resets_still_decrypt(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "stable-secret-" + "s" * 50)
        token = crypto.encrypt("persistent-api-key")

        crypto._fernet = None
        crypto._fernet = None  # double reset, still same key

        assert crypto.decrypt(token) == "persistent-api-key"
