"""Security primitives: PII-at-rest crypto (PLAN.md §7 Phase 1 / §10 Privacy).

Phase-1 scope: authenticated symmetric encryption of OAuth tokens and account
verification state at rest. See `app.security.crypto`.
"""

from __future__ import annotations

from app.security.crypto import (
    PII_ENCRYPTION_KEY_ENV,
    MissingEncryptionKeyError,
    decrypt,
    encrypt,
    load_fernet,
)

__all__ = [
    "PII_ENCRYPTION_KEY_ENV",
    "MissingEncryptionKeyError",
    "decrypt",
    "encrypt",
    "load_fernet",
]
