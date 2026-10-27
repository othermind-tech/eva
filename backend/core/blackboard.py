# core/blackboard.py
#
# G-004: Execution-scoped shared blackboard for multi-agent coordination.
#
# Security model (6 layers):
#   L1 — Execution isolation:  key = blackboard:{execution_id}  (server-side only, never LLM-controlled)
#   L2 — Write ownership:      slot must start with the writing agent's name prefix
#   L3 — Sensitive flag:       sensitive=True entries show as placeholders in memory recall
#   L4 — Append mode:          mode="overwrite"|"append" for future parallel-agent safety
#   L5 — Encryption at rest:   optional AES-GCM via BLACKBOARD_ENCRYPT env var
#   L6 — Auto-expiry:          TTL refreshed on every write + explicit delete on completion

import json
import time
import logging
from typing import Any, Dict, List, Optional

from config.settings import BLACKBOARD_ENCRYPT, BLACKBOARD_TTL_SECONDS
from core.auth.signing_key import get_signing_key

logger = logging.getLogger(__name__)

# ── Optional encryption (L5) ──────────────────────────────────────────────────
import os as _os
_ENCRYPT_ENABLED: bool = BLACKBOARD_ENCRYPT

if _ENCRYPT_ENABLED:
    try:
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        logger.warning("BLACKBOARD_ENCRYPT=true but 'cryptography' package not installed — disabling encryption")
        _ENCRYPT_ENABLED = False


def _derive_key(execution_id: str) -> bytes:
    """Derive a per-execution AES-256 key via HKDF(SERVER_SECRET + execution_id)."""
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    secret = get_signing_key().encode()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=execution_id.encode(),
    ).derive(secret)


def _encrypt_value(data: str, key: bytes) -> str:
    """AES-GCM encrypt; returns hex(nonce + ciphertext)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = _os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, data.encode(), None)
    return (nonce + ct).hex()


def _decrypt_value(data: str, key: bytes) -> str:
    """AES-GCM decrypt; returns plaintext string."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = bytes.fromhex(data)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


# ── Main class ────────────────────────────────────────────────────────────────

class ExecutionBlackboard:
    """
    Per-execution shared scratchpad for multi-agent coordination.

    All agents within the same execution (orchestrator + all subagents)
    share exactly one blackboard, keyed by the server-injected execution_id.
    The execution_id is NEVER exposed as a tool parameter — making cross-
    execution reads structurally impossible regardless of what the LLM outputs.

    Slot naming convention (L2):
        Each agent may only write to slots prefixed with its own name:
            researcher_a:findings    ← owned by researcher_a
            orchestrator:plan        ← owned by the orchestrator / main agent
        Agents may READ any slot regardless of prefix.

    Value envelope (stored in broker hash field):
        {
          "content":    str,
          "written_by": str,
          "written_at": float (Unix timestamp),
          "mode":       "overwrite" | "append",
          "sensitive":  bool
        }

    TTL (L6):
        Extended on every write.  Explicitly deleted when the execution
        completes normally, is cancelled, or is aborted.  TTL provides
        the fallback cleanup on crash/restart.
    """

    _ORCHESTRATOR_PREFIX = "orchestrator:"
    _INBOX_PREFIX        = "inbox:"          # G-007: reserved for send_message() — NOT writable via write()

    def __init__(self, redis: Any, execution_id: str, ttl_seconds: int = BLACKBOARD_TTL_SECONDS):
        self._redis = redis   # LocalRedisAdapter (duck-typed broker client)
        self._execution_id = execution_id            # immutable — never exposed to tools
        self._key = f"blackboard:{execution_id}"     # derived once, never changeable
        self._ttl = ttl_seconds
        self._enc_key: Optional[bytes] = (
            _derive_key(execution_id) if _ENCRYPT_ENABLED else None
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _encode(self, data: str) -> str:
        if _ENCRYPT_ENABLED and self._enc_key:
            return _encrypt_value(data, self._enc_key)
        return data

    def _decode(self, data: str) -> str:
        if _ENCRYPT_ENABLED and self._enc_key:
            return _decrypt_value(data, self._enc_key)
        return data

    # ------------------------------------------------------------------
    # L2: slot ownership enforcement
    # ------------------------------------------------------------------

    def _assert_write_allowed(self, slot: str, written_by: str) -> None:
        """
        Raise PermissionError if agent tries to write outside its namespace.

        Allowed prefixes for the general write() path:
          - "{written_by}:"     own namespace (e.g. researcher_a:findings)
          - "orchestrator:"     shared coordination namespace

        The "inbox:" prefix is intentionally NOT allowed here.
        Cross-agent messages must use send_message() / read_messages() below —
        purpose-built methods that enforce sender identity server-side (G-007).
        This prevents any agent from spoofing or flooding another's inbox via
        the general blackboard_write tool.
        """
        if (
            slot.startswith(f"{written_by}:")
            or slot.startswith(self._ORCHESTRATOR_PREFIX)
        ):
            return
        raise PermissionError(
            f"Agent '{written_by}' cannot write to slot '{slot}'. "
            f"Slots must start with '{written_by}:' or 'orchestrator:'. "
            f"To send a message to another agent use the send_agent_message tool."
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write(
        self,
        slot: str,
        content: str,
        written_by: str,
        mode: str = "overwrite",
        sensitive: bool = False,
    ) -> None:
        """
        Write (or append) a value to a named slot.

        L2: slot namespace enforced before any broker call.
        L4: mode='append' pushes to a broker list; mode='overwrite' uses a hash field.
        L6: TTL refreshed on every successful write.
        """
        self._assert_write_allowed(slot, written_by)

        envelope = json.dumps({
            "content":    content,
            "written_by": written_by,
            "written_at": time.time(),
            "mode":       mode,
            "sensitive":  sensitive,
        })
        encoded = self._encode(envelope)

        try:
            if mode == "append":
                list_key = f"{self._key}:list:{slot}"
                await self._redis.rpush(list_key, encoded)
                await self._redis.expire(list_key, self._ttl)
            else:
                await self._redis.hset(self._key, slot, encoded)
                await self._redis.expire(self._key, self._ttl)
        except Exception as exc:
            logger.error("Blackboard write failed slot=%s: %s", slot, exc)
            raise

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def read(self, slot: str) -> Optional[Any]:
        """
        Read a single named slot.

        Returns the envelope dict for overwrite-mode slots,
        or a list of envelope dicts for append-mode slots.
        Returns None if the slot does not exist.
        """
        try:
            # Try hash field first (overwrite mode)
            raw = await self._redis.hget(self._key, slot)
            if raw is not None:
                return json.loads(self._decode(raw))
            # Fall back to list key (append mode)
            list_key = f"{self._key}:list:{slot}"
            items = await self._redis.lrange(list_key, 0, -1)
            if not items:
                return None
            return [json.loads(self._decode(i)) for i in items]
        except Exception as exc:
            logger.error("Blackboard read failed slot=%s: %s", slot, exc)
            return None

    async def read_all(self) -> Dict[str, Any]:
        """
        Read all overwrite-mode slots.
        Returns {slot_name: envelope_dict}.
        """
        try:
            raw = await self._redis.hgetall(self._key)
            return {
                k: json.loads(self._decode(v))
                for k, v in raw.items()
            }
        except Exception as exc:
            logger.error("Blackboard read_all failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Delete / cleanup (L6)
    # ------------------------------------------------------------------

    async def delete(self) -> None:
        """
        Explicitly delete the entire blackboard.
        Called by runner.py when the execution reaches a terminal state.
        Also cleans up any append-mode list keys.
        """
        try:
            await self._redis.delete(self._key)
            # Scan and delete list keys: blackboard:{exec_id}:list:*
            pattern = f"{self._key}:list:*"
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
                if keys:
                    await self._redis.delete(*keys)
                if cursor == 0:
                    break
        except Exception as exc:
            logger.warning("Blackboard delete failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # G-007: Inter-agent messaging — dedicated inbox channel
    #
    # These methods bypass _assert_write_allowed deliberately.
    # The caller (send_agent_message tool) is responsible for passing
    # from_agent=execution.caller_agent_name — the LLM never controls it.
    # This keeps the general write() path fully locked while enabling
    # controlled cross-agent communication.
    # ------------------------------------------------------------------

    async def send_message(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
    ) -> None:
        """
        Append a message to to_agent's inbox.

        Bypasses ownership check intentionally — the send_agent_message
        tool derives from_agent from execution.caller_agent_name (server-side),
        so LLM cannot forge the sender or target arbitrary non-inbox slots.
        """
        slot     = f"{self._INBOX_PREFIX}{to_agent}"
        envelope = json.dumps({
            "content":    content,
            "written_by": from_agent,
            "written_at": time.time(),
            "mode":       "append",
            "sensitive":  False,
        })
        encoded  = self._encode(envelope)
        try:
            list_key = f"{self._key}:list:{slot}"
            await self._redis.rpush(list_key, encoded)
            await self._redis.expire(list_key, self._ttl)
        except Exception as exc:
            logger.error(
                "Blackboard send_message failed from=%s to=%s: %s", from_agent, to_agent, exc
            )
            raise

    async def read_messages(self, agent_name: str) -> List[Any]:
        """
        Return all messages in agent_name's inbox as a list of envelope dicts.
        Returns [] if the inbox is empty or doesn't exist.
        """
        slot     = f"{self._INBOX_PREFIX}{agent_name}"
        list_key = f"{self._key}:list:{slot}"
        try:
            items = await self._redis.lrange(list_key, 0, -1)
            if not items:
                return []
            return [json.loads(self._decode(i)) for i in items]
        except Exception as exc:
            logger.error("Blackboard read_messages failed agent=%s: %s", agent_name, exc)
            return []

    async def clear_inbox(self, agent_name: str) -> None:
        """Delete all messages from agent_name's inbox (consume semantics)."""
        slot     = f"{self._INBOX_PREFIX}{agent_name}"
        list_key = f"{self._key}:list:{slot}"
        try:
            await self._redis.delete(list_key)
        except Exception as exc:
            logger.warning("Blackboard clear_inbox failed agent=%s: %s", agent_name, exc)

    async def clear_append_slot(self, slot: str) -> None:
        """
        Delete an append-mode list slot (generic — used for non-inbox slots).
        No-op if the slot does not exist.
        """
        try:
            list_key = f"{self._key}:list:{slot}"
            await self._redis.delete(list_key)
        except Exception as exc:
            logger.warning("Blackboard clear_append_slot failed slot=%s: %s", slot, exc)

    async def exists(self) -> bool:
        """Return True if the blackboard has any data."""
        try:
            return bool(await self._redis.exists(self._key))
        except Exception:
            return False
