# core/auth/signing_key.py
#
# The token-signing key (V6.59 Phase 7).
#
# This key signs and verifies every access token and derives the blackboard
# encryption keys. It used to be exported into the server's os.environ at
# startup and read back from there into a module constant
# (`config.settings.SECRET_KEY`, default ""). Consequences:
#   * it sat in the process environment, readable through the OS by other
#     processes of the same user, and inherited by any child process whose
#     spawn site forgot to filter it (safe only as long as every future spawn
#     site remembers);
#   * a missing variable silently became the EMPTY string -- tokens signed with
#     an empty key can be forged by anyone.
#
# Now it lives only in the OS credential store (Windows Credential Manager,
# service "eva", entry "SECRET_KEY" -- the same entry as before, so existing
# tokens stay valid and nobody is logged out). It is read on first use and held
# in this module's memory; os.environ is never consulted or written.
#
# FAIL CLOSED: get_signing_key() either returns a non-empty key or raises
# SigningKeyUnavailable. There is no "" fallback. In particular, a store that
# cannot be READ is an error, never a reason to generate a fresh key -- that
# would invalidate every session (and mask a broken store). A key is only
# created when the store was read successfully and genuinely has none (first
# run).
#
# Pure stdlib + providers.credentials, so config/env_loader.py can call it at
# startup before logging or settings exist.

from __future__ import annotations

import logging
import secrets
import threading
from typing import Optional, Tuple

from providers import credentials

logger = logging.getLogger(__name__)

#: Credential-store entry name (unchanged from the pre-Phase-7 layout).
SIGNING_KEY_ENTRY = "SECRET_KEY"

_lock = threading.Lock()
# (backend the key was loaded from, key). Keyed on the backend OBJECT so that a
# swapped store (the test suite's in-memory one) is never served a stale key.
_cache: Optional[Tuple[object, str]] = None


class SigningKeyUnavailable(RuntimeError):
    """The signing key cannot be obtained; authentication must not proceed."""


def _load_or_create(backend: object) -> str:
    try:
        value = credentials.read_secret(SIGNING_KEY_ENTRY)
    except Exception as exc:
        raise SigningKeyUnavailable(
            "The token-signing key could not be read from the OS credential store; "
            "refusing to continue rather than sign tokens with a fallback key."
        ) from exc

    if value is None:
        # First run on this machine: the store was read fine and has no key.
        value = secrets.token_urlsafe(48)
        try:
            credentials.write_secret(SIGNING_KEY_ENTRY, value)
        except Exception as exc:
            raise SigningKeyUnavailable(
                "A token-signing key could not be saved to the OS credential store."
            ) from exc
        logger.info("[AUTH] token-signing key generated and stored in the OS credential store")
        return value

    if not value.strip():
        raise SigningKeyUnavailable(
            "The stored token-signing key is blank; refusing to sign or verify tokens with it."
        )
    return value


def get_signing_key() -> str:
    """The signing key (created on first run). Never empty; raises
    SigningKeyUnavailable instead of falling back."""
    global _cache
    backend = credentials.current_backend()
    with _lock:
        if _cache is not None and _cache[0] is backend:
            return _cache[1]
        key = _load_or_create(backend)
        _cache = (backend, key)
        return key


def reset_cache() -> None:
    """Forget the in-memory key (tests, and after a deliberate key change)."""
    global _cache
    with _lock:
        _cache = None
