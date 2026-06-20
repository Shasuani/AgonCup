"""Unit tests for PII-at-rest crypto (PLAN.md §7 Phase 1 Privacy/PII).

Covers: round-trip, tamper detection, wrong-key rejection, fail-loud on a
missing key in non-dev, the dev fallback, and MultiFernet rotation behaviour.
All in-process — no network, no DB.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.security import crypto
from app.security.crypto import (
    PII_ENCRYPTION_KEY_ENV,
    MissingEncryptionKeyError,
    decrypt,
    encrypt,
    load_fernet,
)


@pytest.fixture
def configured_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set a freshly generated single key in the environment for the test."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv(PII_ENCRYPTION_KEY_ENV, key)
    monkeypatch.setenv("ENVIRONMENT", "test")
    return key


def test_round_trip(configured_key: str) -> None:
    plaintext = "oauth-access-token-abc123:с-юникодом"  # noqa: RUF001 - non-ascii on purpose
    token = encrypt(plaintext)
    assert token != plaintext
    assert decrypt(token) == plaintext


def test_ciphertext_is_not_plaintext(configured_key: str) -> None:
    plaintext = "verification-nonce-42"
    token = encrypt(plaintext)
    assert plaintext not in token


def test_tamper_detection(configured_key: str) -> None:
    """A modified ciphertext must fail authentication, not decrypt silently."""
    token = encrypt("sensitive")
    # Flip a byte in the middle of the token to corrupt the HMAC-covered body.
    mid = len(token) // 2
    swapped = "A" if token[mid] != "A" else "B"
    tampered = token[:mid] + swapped + token[mid + 1 :]
    assert tampered != token
    with pytest.raises(InvalidToken):
        decrypt(tampered)


def test_wrong_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token encrypted under key A cannot be decrypted under key B."""
    monkeypatch.setenv("ENVIRONMENT", "test")

    monkeypatch.setenv(PII_ENCRYPTION_KEY_ENV, Fernet.generate_key().decode("ascii"))
    token = encrypt("secret")

    monkeypatch.setenv(PII_ENCRYPTION_KEY_ENV, Fernet.generate_key().decode("ascii"))
    with pytest.raises(InvalidToken):
        decrypt(token)


def test_missing_key_fails_loudly_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PII_ENCRYPTION_KEY_ENV, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(MissingEncryptionKeyError):
        encrypt("secret")


def test_missing_key_fails_loudly_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PII_ENCRYPTION_KEY_ENV, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(MissingEncryptionKeyError):
        load_fernet()


def test_dev_fallback_round_trips_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PII_ENCRYPTION_KEY_ENV, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert decrypt(encrypt("dev-secret")) == "dev-secret"


def test_blank_key_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PII_ENCRYPTION_KEY_ENV, "   ")
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(MissingEncryptionKeyError):
        load_fernet()


def test_single_key_loads_plain_fernet(configured_key: str) -> None:
    assert isinstance(load_fernet(), Fernet)


def test_multi_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Old ciphertext stays readable after prepending a new primary key."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")

    # Encrypt under the old key only.
    monkeypatch.setenv(PII_ENCRYPTION_KEY_ENV, old_key)
    legacy_token = encrypt("legacy-token")

    # Rotate: new primary first, old retained for decryption.
    monkeypatch.setenv(PII_ENCRYPTION_KEY_ENV, f"{new_key},{old_key}")
    primitive = load_fernet()
    assert isinstance(primitive, MultiFernet)
    # Old ciphertext still decrypts...
    assert decrypt(legacy_token) == "legacy-token"
    # ...and new writes round-trip too.
    assert decrypt(encrypt("fresh-token")) == "fresh-token"


def test_dev_fallback_key_is_valid_fernet() -> None:
    """The hardcoded dev fallback must be a usable Fernet key."""
    Fernet(crypto._DEV_FALLBACK_KEY.encode("ascii"))
