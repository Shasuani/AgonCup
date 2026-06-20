"""Authenticated symmetric encryption for PII at rest (PLAN.md §7 Phase 1 / §10).

Phase-1 Privacy/PII requirement: **encrypt OAuth tokens + verification state at
rest** (PLAN.md §7 "Privacy/PII"). This module provides the single, narrow
primitive used by the accounts/auth layers to wrap sensitive strings before they
touch the database, and to unwrap them on read.

Design
------
- We use ``cryptography``'s Fernet: authenticated symmetric encryption
  (AES-128-CBC + HMAC-SHA256, with a versioned token format). Authentication
  means a tampered ciphertext fails *loudly* on decrypt rather than yielding
  garbage plaintext — exactly the property we want for tokens at rest.
- The key is a **urlsafe-base64 32-byte** value sourced from the environment
  variable :data:`PII_ENCRYPTION_KEY` (see "Configuration" below). Generate one
  with ``Fernet.generate_key()``.

Key rotation (MultiFernet-ready)
--------------------------------
:func:`load_fernet` returns a single :class:`Fernet`, but the loader accepts a
``,``-separated list of keys in :data:`PII_ENCRYPTION_KEY` and, when more than
one is present, returns a :class:`MultiFernet`. ``MultiFernet`` **encrypts with
the first key** and **decrypts with any** — so rotation is: prepend the new key,
deploy (new writes use it, old ciphertext still decrypts), later re-encrypt
existing rows (``MultiFernet.rotate``) and drop the retired key. No call site
changes are required.

Configuration (owned by the Auth phase)
---------------------------------------
This module deliberately does **not** add a field to ``app.config.Settings``
(that file is owned by the Auth phase). It reads the key directly via
``os.environ[PII_ENCRYPTION_KEY]`` with a documented dev-only fallback. The Auth
agent should add a ``Settings`` field + ``infra/.env.example`` entry named
:data:`PII_ENCRYPTION_KEY`. Until then this loader is self-sufficient.

Fail-loud policy
----------------
Outside development the key is **mandatory**: a missing/blank key raises
:class:`MissingEncryptionKeyError` at first use. In ``development``/``test`` we
fall back to a *fixed, well-known, insecure* dev key so the app boots without
extra setup — this key MUST NEVER be used in staging/production.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.logging import get_logger

__all__ = [
    "PII_ENCRYPTION_KEY_ENV",
    "MissingEncryptionKeyError",
    "decrypt",
    "encrypt",
    "load_fernet",
]

logger = get_logger(__name__)

#: Name of the environment variable holding the PII encryption key(s).
#:
#: A single urlsafe-base64 32-byte Fernet key, or several joined by ``,`` for
#: rotation (first = primary/encrypting key). EXPOSED for the Auth phase to wire
#: into ``Settings`` + ``infra/.env.example``.
PII_ENCRYPTION_KEY_ENV = "PII_ENCRYPTION_KEY"

#: Name of the environment variable selecting the runtime environment. Mirrors
#: ``Settings.environment`` (owned by Auth) without importing it, so this module
#: stays dependency-free of config field changes.
_ENVIRONMENT_ENV = "ENVIRONMENT"

#: Environments where a missing key is tolerated via the insecure dev fallback.
_DEV_ENVIRONMENTS = frozenset({"development", "test"})

#: Fixed, INSECURE key used only when no key is configured in a dev environment.
#: Deterministic so encrypted dev fixtures round-trip across restarts. This is a
#: valid 32-byte urlsafe-base64 Fernet key whose bytes decode to the readable
#: marker ``dev-insecure-pii-key-do-not-use!``; never use outside development/test.
_DEV_FALLBACK_KEY = "ZGV2LWluc2VjdXJlLXBpaS1rZXktZG8tbm90LXVzZSE="


class MissingEncryptionKeyError(RuntimeError):
    """Raised when ``PII_ENCRYPTION_KEY`` is absent outside development.

    PLAN.md §7 mandates encrypting PII at rest; refusing to boot without a key in
    non-dev environments prevents silently persisting plaintext tokens.
    """


def _current_environment() -> str:
    """Return the normalized runtime environment name (defaults to development)."""
    return os.environ.get(_ENVIRONMENT_ENV, "development").strip().lower()


def _raw_keys() -> str:
    """Resolve the raw key material from the environment, with a dev fallback.

    Returns the raw (possibly ``,``-separated) key string. Raises
    :class:`MissingEncryptionKeyError` when unset/blank in a non-dev environment.
    """
    raw = os.environ.get(PII_ENCRYPTION_KEY_ENV, "").strip()
    if raw:
        return raw

    env = _current_environment()
    if env in _DEV_ENVIRONMENTS:
        logger.warning(
            "pii_encryption_key_missing_using_dev_fallback",
            environment=env,
            env_var=PII_ENCRYPTION_KEY_ENV,
        )
        return _DEV_FALLBACK_KEY

    raise MissingEncryptionKeyError(
        f"{PII_ENCRYPTION_KEY_ENV} is required in environment {env!r}. "
        'Generate one with `python -c "from cryptography.fernet import Fernet; '
        'print(Fernet.generate_key().decode())"`.'
    )


def load_fernet() -> MultiFernet | Fernet:
    """Build the (Multi)Fernet primitive from the configured key material.

    A single key yields a :class:`Fernet`; multiple ``,``-separated keys yield a
    :class:`MultiFernet` (first key encrypts, all keys decrypt) for rotation.

    Raises:
        MissingEncryptionKeyError: key absent in a non-dev environment.
        ValueError: a configured key is not a valid 32-byte urlsafe-base64 value.
    """
    parts = [part.strip() for part in _raw_keys().split(",") if part.strip()]
    fernets = [Fernet(part.encode("ascii")) for part in parts]
    if len(fernets) == 1:
        return fernets[0]
    return MultiFernet(fernets)


def encrypt(plaintext: str) -> str:
    """Encrypt ``plaintext`` into an authenticated, urlsafe-base64 token.

    Args:
        plaintext: the sensitive UTF-8 string (e.g. an OAuth token) to protect.

    Returns:
        An opaque Fernet token (``str``) safe to persist at rest (PLAN.md §7).
    """
    fernet = load_fernet()
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt and authenticate a token produced by :func:`encrypt`.

    Args:
        token: an opaque Fernet token previously returned by :func:`encrypt`.

    Returns:
        The original plaintext string.

    Raises:
        cryptography.fernet.InvalidToken: the token was tampered with, was
            produced under a key this process cannot decrypt with, or is
            malformed. Surfaced (not swallowed) so callers fail loudly.
    """
    fernet = load_fernet()
    try:
        plaintext = fernet.decrypt(token.encode("ascii"))
    except InvalidToken:
        # Re-raise without the original token to avoid logging ciphertext/PII.
        logger.warning("pii_decrypt_invalid_token")
        raise
    return plaintext.decode("utf-8")
