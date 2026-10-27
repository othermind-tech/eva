"""
V6.59 Phase 7 -- the token-signing key lives only in the credential store, and
a token must be backed by a session that belongs to the user it names.

Before: the key was exported into the server's os.environ at startup and read
into `config.settings.SECRET_KEY` (default ""): readable by other processes of
the same user, inherited by any spawn site that forgot to filter it, and -- if
the variable were ever missing -- silently the EMPTY string, with which anyone
can forge a token. Verification also checked only that the token's `sid` named
a live session, not that the session belonged to the token's `sub`, so the key
plus ANY live session (anyone can create one by signing up) impersonated anyone.
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException
from jose import JWTError, jwt

from core.auth import signing_key
from core.auth.signing_key import SigningKeyUnavailable, get_signing_key
from providers import credentials
from providers.credentials import MemoryBackend

ROOT = Path(__file__).resolve().parent.parent


def setUpModule():
    signing_key.reset_cache()


class _BrokenReads(MemoryBackend):
    """A store that cannot be read (locked / no backend)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.writes = 0

    def get(self, name):
        raise RuntimeError("credential store locked")

    def set(self, name, value):
        self.writes += 1
        super().set(name, value)


class _ReadableButUnwritable(MemoryBackend):
    def set(self, name, value):
        raise RuntimeError("read-only store")


class AccessorTests(unittest.TestCase):
    def setUp(self):
        signing_key.reset_cache()

    def test_created_on_first_use_and_persisted(self):
        self.assertIsNone(credentials.read_secret("SECRET_KEY"))
        key = get_signing_key()
        self.assertGreaterEqual(len(key), 32)
        self.assertEqual(credentials.read_secret("SECRET_KEY"), key)

    def test_stable_across_calls_and_across_a_cache_reset(self):
        first = get_signing_key()
        self.assertEqual(get_signing_key(), first)
        signing_key.reset_cache()
        self.assertEqual(get_signing_key(), first)          # re-read from the store, not regenerated

    def test_an_existing_key_is_used_so_nobody_is_logged_out(self):
        credentials.write_secret("SECRET_KEY", "key-that-was-already-there-" + "x" * 30)
        self.assertEqual(get_signing_key(), "key-that-was-already-there-" + "x" * 30)

    def test_the_environment_is_never_consulted_or_written(self):
        with patch.dict(os.environ, {"SECRET_KEY": "from-the-environment"}):
            key = get_signing_key()
            self.assertNotEqual(key, "from-the-environment")
            self.assertEqual(os.environ["SECRET_KEY"], "from-the-environment")   # untouched
        os.environ.pop("SECRET_KEY", None)
        signing_key.reset_cache()
        get_signing_key()
        self.assertNotIn("SECRET_KEY", os.environ)

    def test_unreadable_store_fails_closed_and_never_generates_a_replacement(self):
        broken = _BrokenReads()
        credentials.set_backend(broken)
        with self.assertRaises(SigningKeyUnavailable):
            get_signing_key()
        self.assertEqual(broken.writes, 0, "a store that can't be READ must not trigger key creation")

    def test_unwritable_store_on_first_run_fails_closed(self):
        credentials.set_backend(_ReadableButUnwritable())
        with self.assertRaises(SigningKeyUnavailable):
            get_signing_key()

    def test_a_blank_stored_key_is_refused_not_used_and_not_replaced(self):
        for blank in ("   ", "\n"):
            store = MemoryBackend({"SECRET_KEY": blank})
            credentials.set_backend(store)
            signing_key.reset_cache()
            with self.assertRaises(SigningKeyUnavailable):
                get_signing_key()
            self.assertEqual(store.get("SECRET_KEY"), blank)    # left as found

    def test_never_returns_an_empty_key(self):
        for _ in range(3):
            signing_key.reset_cache()
            credentials.set_backend(MemoryBackend())
            self.assertTrue(get_signing_key().strip())

    def test_a_swapped_store_is_not_served_the_previous_stores_key(self):
        first = get_signing_key()
        credentials.set_backend(MemoryBackend({"SECRET_KEY": "second-store-key-" + "y" * 30}))
        self.assertNotEqual(get_signing_key(), first)
        self.assertEqual(get_signing_key(), "second-store-key-" + "y" * 30)

    def test_once_loaded_a_later_store_failure_does_not_break_a_running_server(self):
        key = get_signing_key()
        store = credentials.current_backend()
        store.get = MagicMock(side_effect=RuntimeError("locked"))      # store becomes unreadable afterwards
        self.assertEqual(get_signing_key(), key)                        # served from memory

    def test_first_run_creation_is_atomic_under_concurrency(self):
        import threading
        results = []
        signing_key.reset_cache()
        credentials.set_backend(MemoryBackend())
        threads = [threading.Thread(target=lambda: results.append(get_signing_key())) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(results)), 1)


class TokenTests(unittest.TestCase):
    def setUp(self):
        signing_key.reset_cache()

    def test_roundtrip(self):
        from core.auth.security import create_access_token, decode_access_token
        payload = decode_access_token(create_access_token("user-1", "sess-1"))
        self.assertEqual((payload["sub"], payload["sid"]), ("user-1", "sess-1"))

    def test_token_signed_with_another_key_is_rejected(self):
        from core.auth.security import decode_access_token
        forged = jwt.encode({"sub": "u", "sid": "s", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                            "some-other-key", algorithm="HS256")
        with self.assertRaises(JWTError):
            decode_access_token(forged)

    def test_token_signed_with_an_empty_key_is_rejected(self):
        """The old failure mode: with an empty key anyone could mint this."""
        from core.auth.security import decode_access_token
        forged = jwt.encode({"sub": "u", "sid": "s", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                            "", algorithm="HS256")
        with self.assertRaises(JWTError):
            decode_access_token(forged)

    def test_tampered_token_is_rejected(self):
        from core.auth.security import create_access_token, decode_access_token
        token = create_access_token("user-1", "sess-1")
        head, body, sig = token.split(".")
        with self.assertRaises(JWTError):
            decode_access_token(f"{head}.{body}x.{sig}")

    def test_signing_and_verifying_fail_closed_when_the_key_is_unavailable(self):
        from core.auth.security import create_access_token, decode_access_token
        token = create_access_token("user-1", "sess-1")
        credentials.set_backend(_BrokenReads())
        with self.assertRaises(SigningKeyUnavailable):
            create_access_token("user-1", "sess-1")
        with self.assertRaises(SigningKeyUnavailable):
            decode_access_token(token)

    def test_rotating_the_key_invalidates_existing_tokens(self):
        from core.auth.security import create_access_token, decode_access_token
        token = create_access_token("user-1", "sess-1")
        credentials.write_secret("SECRET_KEY", "a-brand-new-key-" + "z" * 40)
        signing_key.reset_cache()
        with self.assertRaises(JWTError):
            decode_access_token(token)


def _session(user_id="user-1", revoked=False, expires_in_hours=1):
    return NS(id="sess-1", user_id=user_id, revoked=revoked,
              expires_at=datetime.now(timezone.utc) + timedelta(hours=expires_in_hours))


class _FakeDb:
    def __init__(self, session):
        self._session = session

    async def get(self, _model, _id):
        return self._session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _db_factory(session):
    return lambda: _FakeDb(session)


class BindingTests(unittest.IsolatedAsyncioTestCase):
    """A token's `sub` must match the owner of the session its `sid` names."""

    def setUp(self):
        signing_key.reset_cache()

    def test_session_matches_user(self):
        from core.auth.security import session_matches_user
        self.assertTrue(session_matches_user(_session("u1"), "u1"))
        self.assertFalse(session_matches_user(_session("u1"), "u2"))
        self.assertFalse(session_matches_user(None, "u1"))
        self.assertFalse(session_matches_user(NS(), "u1"))          # no user_id at all

    async def _auth(self, token_user, session):
        from core.auth import dependencies as deps
        from core.auth.security import create_access_token
        token = create_access_token(token_user, "sess-1")
        ready = {"provider": "groq", "model": "m"}
        with patch.object(deps, "AsyncSessionLocal", _db_factory(session)), \
             patch.object(deps, "get_user_session", return_value=ready):
            return await deps.get_current_auth(token=token)

    async def test_matching_user_is_authenticated(self):
        out = await self._auth("user-1", _session("user-1"))
        self.assertEqual(out, {"user_id": "user-1", "session_id": "sess-1"})

    async def test_a_live_session_of_another_user_does_not_authenticate(self):
        """The impersonation path: valid signature + someone else's live session."""
        with self.assertRaises(HTTPException) as cm:
            await self._auth("victim-user", _session("attacker-user"))
        self.assertEqual(cm.exception.status_code, 401)

    async def test_missing_session_is_rejected(self):
        with self.assertRaises(HTTPException) as cm:
            await self._auth("user-1", None)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_revoked_and_expired_sessions_are_still_rejected(self):
        for s in (_session("user-1", revoked=True), _session("user-1", expires_in_hours=-1)):
            with self.assertRaises(HTTPException) as cm:
                await self._auth("user-1", s)
            self.assertEqual(cm.exception.status_code, 401)

    async def _sse(self, token_user, session):
        from api import eva_response
        from core.auth.security import create_access_token
        token = create_access_token(token_user, "sess-1")
        request = NS(headers={"Authorization": f"Bearer {token}"})
        broker = NS(client=NS(get=AsyncMock(return_value=None), delete=AsyncMock()))
        with patch("core.db.session.AsyncSessionLocal", _db_factory(session)), \
             patch("core.broker.local_broker", broker):
            return await eva_response._resolve_sse_auth(request, None)

    async def test_sse_path_matching_user_is_authenticated(self):
        self.assertEqual(await self._sse("user-1", _session("user-1")), "user-1")

    async def test_sse_path_rejects_a_session_of_another_user(self):
        with self.assertRaises(HTTPException) as cm:
            await self._sse("victim-user", _session("attacker-user"))
        self.assertEqual(cm.exception.status_code, 401)

    async def test_sse_path_rejects_a_forged_token(self):
        from api import eva_response
        forged = jwt.encode({"sub": "u", "sid": "sess-1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                            "", algorithm="HS256")
        request = NS(headers={"Authorization": f"Bearer {forged}"})
        broker = NS(client=NS(get=AsyncMock(return_value=None), delete=AsyncMock()))
        with patch("core.db.session.AsyncSessionLocal", _db_factory(_session("u"))), \
             patch("core.broker.local_broker", broker):
            with self.assertRaises(HTTPException) as cm:
                await eva_response._resolve_sse_auth(request, None)
        self.assertEqual(cm.exception.status_code, 401)


class BlackboardTests(unittest.TestCase):
    def test_encryption_key_comes_from_the_accessor(self):
        from core import blackboard
        signing_key.reset_cache()
        self.assertEqual(blackboard._derive_key("exec-1"), blackboard._derive_key("exec-1"))
        self.assertNotEqual(blackboard._derive_key("exec-1"), blackboard._derive_key("exec-2"))
        before = blackboard._derive_key("exec-1")
        credentials.write_secret("SECRET_KEY", "different-key-" + "q" * 40)
        signing_key.reset_cache()
        self.assertNotEqual(blackboard._derive_key("exec-1"), before)


class NoEnvironmentReadersTests(unittest.TestCase):
    """Source guard: nothing may read the signing key from the environment or settings."""

    def test_no_application_code_reads_secret_key_from_the_environment_or_settings(self):
        pats = [re.compile(r"""(getenv|environ(\.get)?)\s*[\(\[]\s*["']SECRET_KEY["']"""),
                re.compile(r"""settings\s+import[^\n]*\bSECRET_KEY\b"""),
                re.compile(r"""\bsettings\.SECRET_KEY\b"""),
                re.compile(r"""core\.auth\.security\s+import[^\n]*\bSECRET_KEY\b""")]
        offenders = []
        for top in ("api", "core", "providers", "tools", "utils", "config", "main.py", "entrypoint.py", "alembic"):
            base = ROOT / top
            files = [base] if base.is_file() else list(base.rglob("*.py"))
            for path in files:
                rel = path.relative_to(ROOT).as_posix()
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if line.lstrip().startswith("#"):
                        continue
                    if any(p.search(line) for p in pats):
                        offenders.append(f"{rel}:{n}: {line.strip()}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_settings_constant_is_gone(self):
        import config.settings as s
        self.assertFalse(hasattr(s, "SECRET_KEY"))

    def test_every_verifier_uses_the_shared_decode_and_binding(self):
        for rel in ("core/auth/dependencies.py", "api/eva_response.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("decode_access_token(", src, rel)
            self.assertIn("session_matches_user(", src, rel)
        self.assertEqual(len(re.findall(r"jwt\.decode\(", (ROOT / "core/auth/security.py").read_text(encoding="utf-8"))), 1)


if __name__ == "__main__":
    unittest.main()
