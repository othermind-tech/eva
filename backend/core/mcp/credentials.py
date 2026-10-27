# core/mcp/credentials.py
"""
S-11 -- MCP credential security.

MCPConfig.env / .headers hold arbitrary key/value pairs a user supplies for
a given MCP server -- some of which are genuine secrets (a Notion
integration token, a GitHub PAT) and some of which aren't (DEBUG=false, a
Content-Type header), mixed together in one JSON blob. This module
auto-detects which keys look like secrets, moves their real values into the
OS-native credential store (the exact same `keyring` mechanism
api/secrets.py already uses for provider API keys -- Windows Credential
Manager / macOS Keychain / Linux Secret Service), and replaces them in
place with a redaction placeholder -- so the DB row, and every API response
built from it, only ever hold the placeholder. The real value is resolved
back only at actual connection time (core/mcp/manager.py,
core/mcp/session_pool.py, and the three api/mcp.py call sites that open a
live MCPConnection).

Deliberately no new UI, no new request field: this runs automatically on
every create/update, scanning the exact same env/headers a user already
submits today (whether hand-built or pasted from a standard MCP config
JSON) -- a competitive-analysis pass (Claude Code, Claude Desktop, ChatGPT,
OpenHands) found nothing in that set that protects the at-rest/API-response
half of this problem better than this design does; OpenHands' web UI has a
nicer *input* form (a password-masked field) but its own frontend code
suggests its backend still returns the real value on read, which this
design never does.

is_secret_key() is adapted from OpenHands SDK's
openhands-sdk/openhands/sdk/utils/redact.py (MIT License,
https://github.com/OpenHands/software-agent-sdk) -- confirmed genuinely
MIT via the GitHub API before reuse.

Design note on the per-field API (why there's no single do-everything
function): env and headers are processed as two fully independent fields,
each keyring-ref-namespaced by field name (mcp:{config_id}:{field}:{key})
so an env key and a header key with the same name can never collide. This
also sidesteps a real ambiguity a combined function would hit: `None` has
to mean two different things on a partial update -- "this field wasn't
part of the request at all, leave it and its keyring entries completely
alone" vs. "the client explicitly cleared this field, wipe its secrets
too" -- and collapsing both into one call made that distinction impossible
to recover correctly. Callers (api/mcp.py) make that provided-vs-explicit-
None decision themselves and call the right one-field function.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Placeholder written into env/headers in place of a real secret value.
REDACTED_PLACEHOLDER = "••••••••"

# Substring patterns (case-insensitive) that mark a key as secret-shaped.
# Adapted from OpenHands SDK's SECRET_KEY_PATTERNS, with "AUTH" added --
# EVA's own earlier draft heuristic independently wanted a bare "AUTH"
# prefix (matches e.g. "AUTH_HEADER", not just "AUTHORIZATION").
_SECRET_KEY_PATTERNS = frozenset({
    "AUTHORIZATION", "COOKIE", "CREDENTIAL", "KEY", "PASSWORD",
    "SECRET", "SESSION", "TOKEN", "AUTH",
})


def is_secret_key(key: str) -> bool:
    """Case-insensitive substring match against known secret key patterns.

    Examples:
        >>> is_secret_key("NOTION_TOKEN")
        True
        >>> is_secret_key("Authorization")
        True
        >>> is_secret_key("DEBUG")
        False
    """
    key_upper = key.upper()
    return any(pattern in key_upper for pattern in _SECRET_KEY_PATTERNS)


def _keyring_ref(config_id: str, field: str, key: str) -> str:
    """Build the keyring reference name for one field's one secret key.

    Namespaced "mcp:{config_id}:{field}:{key}" -- confirmed collision-free
    against the existing provider-key scheme in api/secrets.py, which uses
    bare UPPER_SNAKE_CASE names with no colons. The {field} segment (env vs
    headers) additionally prevents an env key and a header key that happen
    to share a name from ever resolving to the same keyring entry.
    """
    return f"mcp:{config_id}:{field}:{key}"


def refs_for_field(credential_refs: Optional[List[Dict[str, str]]], field: str) -> Dict[str, str]:
    """Filter a config's full credential_refs list down to one field,
    returning {key: credential_ref}. Used by callers to scope existing refs
    before calling extract_and_store_secrets_for_field for that field only."""
    return {
        r["key"]: r["credential_ref"]
        for r in (credential_refs or [])
        if r.get("field") == field
    }


def extract_and_store_secrets_for_field(
    config_id: str,
    field: str,
    values: Dict[str, str],
    existing_refs_for_field: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """
    Scan one field's dict (env, or headers) for secret-shaped keys, write
    real values to keyring, and return (redacted_values, new_refs) --
    new_refs is this field's slice of what should end up in credential_refs
    (the caller merges it with the other field's slice and any untouched
    field's existing entries).

    A value that already equals REDACTED_PLACEHOLDER is treated as
    "unchanged" (the common case on update, when a client re-submits a
    config it read back from the API without touching that field): the
    existing keyring entry is carried forward as-is, nothing is re-written.

    Any existing ref for this field whose key no longer appears in `values`
    at all (the key was removed, not just left unchanged) has its keyring
    entry deleted -- callers must only invoke this for a field that is
    genuinely being replaced; see clear_field_secrets() for the "field
    explicitly set to null" case, which this function does not handle.

    Non-fatal per key: a keyring write failure for one key is logged and
    that key's real value is left in place unredacted rather than silently
    dropping data or crashing the create/update request -- callers should
    treat this as best-effort hardening, not a guarantee enforced by an
    exception.
    """
    import keyring
    from config.env_loader import KEYRING_SERVICE

    existing = existing_refs_for_field or {}
    result: Dict[str, str] = {}
    new_refs: List[Dict[str, str]] = []
    seen: set = set()

    for k, v in values.items():
        seen.add(k)
        if not is_secret_key(k):
            result[k] = v
            continue

        if v == REDACTED_PLACEHOLDER:
            if k in existing:
                new_refs.append({"field": field, "key": k, "credential_ref": existing[k]})
            result[k] = REDACTED_PLACEHOLDER
            continue

        ref = existing.get(k) or _keyring_ref(config_id, field, k)
        try:
            keyring.set_password(KEYRING_SERVICE, ref, v)
            new_refs.append({"field": field, "key": k, "credential_ref": ref})
            result[k] = REDACTED_PLACEHOLDER
        except Exception as exc:
            logger.warning(
                "[MCP] keyring write failed for '%s.%s' -- leaving value "
                "unredacted for this key: %s",
                field, k, exc,
                extra={"config_id": config_id},
            )
            result[k] = v

    # Clean up keyring entries for keys that existed in this field before
    # but are gone from `values` now (removed, not just left unchanged).
    for k, ref in existing.items():
        if k not in seen:
            try:
                keyring.delete_password(KEYRING_SERVICE, ref)
            except Exception as exc:
                logger.debug(
                    "[MCP] keyring delete skipped for '%s' (already gone?): %s",
                    ref, exc,
                )

    return result, new_refs


def clear_field_secrets(existing_refs_for_field: Optional[Dict[str, str]]) -> None:
    """
    Delete every keyring entry for one field that the client explicitly
    cleared (set to null / removed entirely), as opposed to a field that
    simply wasn't part of the update request at all -- callers must not
    call this for a field that's merely absent from the request, only for
    one genuinely being wiped.
    """
    import keyring
    from config.env_loader import KEYRING_SERVICE

    for ref in (existing_refs_for_field or {}).values():
        try:
            keyring.delete_password(KEYRING_SERVICE, ref)
        except Exception as exc:
            logger.debug(
                "[MCP] keyring delete skipped for '%s' (already gone?): %s",
                ref, exc,
            )


def resolve_secrets(config) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, str]]]:
    """
    Given a persisted MCPConfig (env/headers may contain
    REDACTED_PLACEHOLDER values), return the REAL env/headers with actual
    secret values resolved from keyring.

    This is the single chokepoint every real connection-construction site
    calls instead of reading config.env/config.headers directly -- the DB
    and every API response keep the placeholder; only an actual connection
    attempt ever sees the real value.

    Fails safe per-key: if a credential_ref's keyring lookup fails or
    returns None (e.g. manually cleared from the OS credential store
    outside EVA), that key's value is left as the placeholder rather than
    raising -- a broken keyring entry surfaces as a connection auth
    failure the user can see and fix, not an unrelated crash somewhere
    else in the stack.
    """
    import keyring
    from config.env_loader import KEYRING_SERVICE

    all_refs = getattr(config, "credential_refs", None) or []

    def _resolve(d: Optional[Dict[str, str]], field: str) -> Optional[Dict[str, str]]:
        if d is None:
            return None
        refs_by_key = {r["key"]: r["credential_ref"] for r in all_refs if r.get("field") == field}
        if not refs_by_key:
            return d
        result = dict(d)
        for k, ref in refs_by_key.items():
            if k not in result:
                continue
            try:
                real_value = keyring.get_password(KEYRING_SERVICE, ref)
            except Exception as exc:
                logger.warning("[MCP] keyring lookup failed for '%s': %s", ref, exc)
                real_value = None
            if real_value is not None:
                result[k] = real_value
        return result

    return _resolve(config.env, "env"), _resolve(config.headers, "headers")


def delete_credentials(credential_refs: Optional[List[Dict[str, str]]]) -> None:
    """
    Remove every keyring entry for a deleted MCP config (both fields).

    keyring has no cascade-delete of its own -- call this explicitly
    before/after removing the MCPConfig row (DELETE /mcp/{config_id}),
    otherwise the real secret values would sit in the OS credential store
    forever with nothing left in EVA that references them.
    """
    import keyring
    from config.env_loader import KEYRING_SERVICE

    for ref_entry in (credential_refs or []):
        ref = ref_entry.get("credential_ref")
        if not ref:
            continue
        try:
            keyring.delete_password(KEYRING_SERVICE, ref)
        except Exception as exc:
            logger.debug(
                "[MCP] keyring delete skipped for '%s' (already gone?): %s",
                ref, exc,
            )


async def migrate_existing_plaintext_credentials(db) -> int:
    """
    One-time idempotent pass: for every MCPConfig row that predates S-11
    (credential_refs is NULL, not just empty), run the same extraction the
    create/update endpoints use and persist the result.

    Safe to call on every boot -- a row that's already been migrated (has
    a real, persisted [] because it had no secret-shaped keys, or a
    populated list) is a no-op; `is not None` is the sentinel, not
    truthiness, specifically so "migrated, nothing to redact" and
    "not migrated yet" stay distinguishable. Mirrors
    config/env_loader.py::load_eva_env()'s existing one-time provider-key
    migration precedent, called once from main.py's lifespan.

    Returns the number of rows migrated (0 on a clean boot where every row
    was already migrated).
    """
    from sqlalchemy import select
    from core.db.models import MCPConfig

    result = await db.execute(select(MCPConfig))
    configs = result.scalars().all()

    migrated_count = 0
    for cfg in configs:
        if cfg.credential_refs is not None:
            continue  # already migrated, including the "found nothing" case

        all_refs: List[Dict[str, str]] = []
        if cfg.env is not None:
            redacted_env, env_refs = extract_and_store_secrets_for_field(
                cfg.id, "env", cfg.env,
            )
            cfg.env = redacted_env
            all_refs.extend(env_refs)
        if cfg.headers is not None:
            redacted_headers, header_refs = extract_and_store_secrets_for_field(
                cfg.id, "headers", cfg.headers,
            )
            cfg.headers = redacted_headers
            all_refs.extend(header_refs)

        cfg.credential_refs = all_refs  # always assign, even [] -- see sentinel note above
        migrated_count += 1

    if migrated_count:
        await db.commit()

    return migrated_count
