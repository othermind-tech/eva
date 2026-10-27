# providers/credentials.py
#
# The one place EVA reads, writes and deletes provider API keys (V6.59 Phase 2).
#
# Keys live in the OS credential store (Windows Credential Manager, service
# "eva", one entry per provider named after its env var -- the layout the
# env loader has always used, so existing stored keys keep working).
#
# Before this module, a key reached a handler by one of two unrelated routes:
#   * "Group B" (model listing, key validation, status) read os.getenv() inside
#     each providers/capability/*/provider_api.py;
#   * "Group A" (the agent engine's chat handlers) was usually handed
#     api_key=None -- session["api_key"] is never populated -- and worked only
#     because the OpenAI/Groq/LiteLLM SDKs silently fall back to their own
#     environment variable, which startup had filled from the keyring.
# Both depended on keys being copied into os.environ, which also hands them to
# every child process the agent starts (shell tools, MCP servers). Now every
# route resolves through this module, and handlers always receive an explicit
# key, so the os.environ copy is no longer needed (removed in Phase 3).
#
# There is deliberately no os.environ fallback: a stray system-wide
# GROQ_API_KEY must never be picked up (the env loader's "strict isolation"
# guarantee, now enforced at the point of use).
#
# Pure stdlib + lazy `keyring` import: config/env_loader.py's import chain
# reaches this before logging or settings exist.

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional, Protocol, Tuple

from providers import registry

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "eva"

# A key saved through POST /secrets invalidates the cache immediately; the TTL
# only bounds how long an out-of-band change (editing Credential Manager by
# hand) can go unnoticed, and avoids a keyring round-trip per request.
_CACHE_TTL_SECONDS = 30.0

OLLAMA_HOST_ENV = "OLLAMA_HOST"


class ProviderKeyError(RuntimeError):
    """Raised when a required API key is not available."""


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class CredentialBackend(Protocol):
    def get(self, name: str) -> Optional[str]: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> bool: ...


class KeyringBackend:
    """OS credential store via the `keyring` package."""

    def get(self, name: str) -> Optional[str]:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, name)

    def set(self, name: str, value: str) -> None:
        import keyring
        keyring.set_password(KEYRING_SERVICE, name, value)

    def delete(self, name: str) -> bool:
        import keyring
        from keyring.errors import PasswordDeleteError
        try:
            keyring.delete_password(KEYRING_SERVICE, name)
            return True
        except PasswordDeleteError:
            return False


class MemoryBackend:
    """In-process dict. Used by the test suite so tests never touch (or depend
    on) the developer's real credential store."""

    def __init__(self, initial: Optional[Dict[str, str]] = None):
        self._data: Dict[str, str] = dict(initial or {})

    def get(self, name: str) -> Optional[str]:
        return self._data.get(name)

    def set(self, name: str, value: str) -> None:
        self._data[name] = value

    def delete(self, name: str) -> bool:
        return self._data.pop(name, None) is not None


_backend: CredentialBackend = KeyringBackend()
_cache: Dict[str, Tuple[float, Optional[str]]] = {}
_lock = threading.Lock()


def set_backend(backend: CredentialBackend) -> CredentialBackend:
    """Swap the storage backend (tests). Returns the previous one."""
    global _backend
    with _lock:
        previous, _backend = _backend, backend
        _cache.clear()
    return previous


def clear_cache() -> None:
    with _lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Read / write / delete
# ---------------------------------------------------------------------------

def _entry_name(provider: str) -> Optional[str]:
    """Credential-store entry name for a provider (None = keyless/unknown)."""
    spec = registry.get_spec(provider)
    return spec.env_key if spec else None


def get_api_key(provider: str) -> Optional[str]:
    """
    The stored API key for `provider`, or None if none is stored (or the
    provider is local/unknown). Read straight from the credential store.
    """
    name = _entry_name(provider)
    if not name:
        return None
    now = time.monotonic()
    with _lock:
        hit = _cache.get(name)
        if hit and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
        backend = _backend
    try:
        value = backend.get(name) or None
    except Exception as exc:  # locked store, no backend, ... -> behave as "not configured"
        logger.warning("credential store read failed for %s: %s", name, exc)
        return None   # not cached: retry on the next call
    with _lock:
        if _backend is backend:
            _cache[name] = (now, value)
    return value


def set_api_key(provider: str, value: str) -> str:
    """Store a key. Returns the credential-store entry name. Raises ValueError
    for providers that take no key."""
    name = _entry_name(provider)
    if not name:
        raise ValueError(f"Provider '{provider}' does not use an API key.")
    _backend.set(name, value)
    with _lock:
        _cache.pop(name, None)
    return name


def delete_api_key(provider: str) -> bool:
    """Remove a stored key. True if one existed."""
    name = _entry_name(provider)
    if not name:
        raise ValueError(f"Provider '{provider}' does not use an API key.")
    removed = _backend.delete(name)
    with _lock:
        _cache.pop(name, None)
    return removed


def has_api_key(provider: str) -> bool:
    return bool(get_api_key(provider))


# ---------------------------------------------------------------------------
# Named (non-provider) secrets
# ---------------------------------------------------------------------------
# EVA keeps a few secrets that are not provider keys -- today the token-signing
# key (core/auth/signing_key.py) -- in the same credential store. They differ
# from provider keys in one way that matters: "the store could not be read"
# must NOT look like "no secret stored", because the caller's response to
# "absent" is to create one (and replacing a signing key logs every user out
# and, if mishandled, could mean signing with a weaker value). So these two
# calls never swallow a backend error and never use the read cache.

def read_secret(name: str) -> Optional[str]:
    """The stored value of a named secret, or None if it is absent. Raises if
    the store cannot be read (unlike get_api_key, which treats that as absent)."""
    return _backend.get(name) or None


def write_secret(name: str, value: str) -> None:
    """Store a named secret."""
    _backend.set(name, value)


def current_backend() -> CredentialBackend:
    """The active storage backend (lets callers detect a backend swap)."""
    return _backend


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_api_key(provider: str, explicit_key: Optional[str] = None) -> str:
    """
    The credential a handler should use for `provider`:
      1. `explicit_key`, if given (legacy per-call override);
      2. the stored key;
      3. for local Ollama, the host URL (OLLAMA_HOST -- non-secret config, not
         a credential, so it is the one value still read from the process env).
    Returns "" (never None) when nothing is available; use require_api_key()
    where a missing key should fail loudly.
    """
    if explicit_key:
        return explicit_key
    spec = registry.get_spec(provider)
    if spec is None:
        return ""
    if spec.is_local:
        return os.getenv(OLLAMA_HOST_ENV, "")
    return get_api_key(spec.name) or ""


def require_api_key(provider: str, explicit_key: Optional[str] = None) -> str:
    """resolve_api_key(), raising ProviderKeyError if there is no key."""
    key = resolve_api_key(provider, explicit_key)
    if not key:
        spec = registry.get_spec(provider)
        label = spec.display_name if spec else provider
        raise ProviderKeyError(
            f"No API key configured for {label}. "
            f"Add it under Profile → API Keys (POST /secrets/{spec.name if spec else provider})."
        )
    return key


def is_configured(provider: str, explicit_key: Optional[str] = None) -> bool:
    """True when `provider` can be used: a key (explicit or stored) exists, or
    the provider is local. Unknown providers are never configured."""
    spec = registry.get_spec(provider)
    if spec is None:
        return False
    if spec.is_local or explicit_key:
        return True
    if spec.env_key is None:
        return True
    return has_api_key(spec.name)
