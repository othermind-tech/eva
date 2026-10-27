"""
V6.59 Phases 3 + 7 -- process-environment hygiene.

No secret EVA manages is placed in os.environ: provider keys are resolved on
demand from the credential store (Phase 2/3) and the token-signing key is read
from it by core/auth/signing_key.py (Phase 7; see test_signing_key_unit.py).
The provider-key variables a user may have set in their own environment are
stripped from every environment handed to a child process EVA starts for the
agent; the user's other variables pass through untouched.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers import credentials, registry
from utils.child_env import child_environment, EVA_INTERNAL_SECRET_VARS

ROOT = Path(__file__).resolve().parent.parent


class EnvLoaderTests(unittest.TestCase):
    def _run_loader(self, eva_env_text=None):
        from config import env_loader
        tmp = Path(tempfile.mkdtemp()) / "eva.env"
        if eva_env_text is not None:
            tmp.write_text(eva_env_text, encoding="utf-8")
        with patch.object(env_loader, "_EVA_ENV_PATH", tmp):
            env_loader.load_eva_env()
        return tmp

    def test_stored_provider_keys_are_not_exported_to_environ(self):
        credentials.set_api_key("groq", "gsk-secret")
        credentials.set_api_key("openai", "sk-secret")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GROQ_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            self._run_loader()
            self.assertNotIn("GROQ_API_KEY", os.environ)
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertNotIn("gsk-secret", os.environ.values())

    def test_users_own_system_variables_are_left_alone(self):
        """The resolver ignores them; the loader must not delete them."""
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKIA-user-own", "GROQ_API_KEY": "user-own"}):
            self._run_loader()
            self.assertEqual(os.environ.get("AWS_ACCESS_KEY_ID"), "AKIA-user-own")
            self.assertEqual(os.environ.get("GROQ_API_KEY"), "user-own")
            self.assertEqual(credentials.resolve_api_key("groq"), "")   # still never used

    def test_signing_key_is_created_in_the_store_and_never_exported(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECRET_KEY", None)
            self.assertIsNone(credentials.read_secret("SECRET_KEY"))
            self._run_loader()
            self.assertTrue(credentials.read_secret("SECRET_KEY"))      # created on first run
            self.assertNotIn("SECRET_KEY", os.environ)                   # ...and not exported

    def test_an_existing_signing_key_is_kept_not_replaced(self):
        existing = "existing-key-value-xxxxxxxxxxxxxxxxxxxxxxxx"
        credentials.write_secret("SECRET_KEY", existing)
        with patch.dict(os.environ, {}, clear=False):
            self._run_loader()
        self.assertEqual(credentials.read_secret("SECRET_KEY"), existing)

    def test_eva_env_provider_key_migrates_to_credential_store_only(self):
        with patch.dict(os.environ, {}, clear=False):
            path = self._run_loader("DEEPSEEK_API_KEY=sk-from-file\n")
            self.assertEqual(credentials.get_api_key("deepseek"), "sk-from-file")
            self.assertNotIn("DEEPSEEK_API_KEY", os.environ)
            self.assertNotIn("sk-from-file", path.read_text(encoding="utf-8"))

    def test_migration_is_idempotent_and_does_not_overwrite(self):
        credentials.set_api_key("deepseek", "sk-already")
        with patch.dict(os.environ, {}, clear=False):
            self._run_loader("DEEPSEEK_API_KEY=sk-from-file\n")
            self.assertEqual(credentials.get_api_key("deepseek"), "sk-already")

    def test_a_secret_key_line_in_eva_env_is_never_exported(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SECRET_KEY", None)
            self._run_loader("SECRET_KEY=from-the-file\nSOME_PLAIN_SETTING=1\n")
            self.assertNotIn("SECRET_KEY", os.environ)
            os.environ.pop("SOME_PLAIN_SETTING", None)


class ChildEnvironmentTests(unittest.TestCase):
    def test_a_variable_named_secret_key_is_the_users_own_and_passes_through(self):
        """EVA keeps no secret in its environment any more, so SECRET_KEY there is
        e.g. a Django project's -- stripping it would break their scripts."""
        with patch.dict(os.environ, {"SECRET_KEY": "users-django-key", "PATH": "/bin"}):
            env = child_environment()
            self.assertEqual(env["SECRET_KEY"], "users-django-key")
            self.assertEqual(env["PATH"], "/bin")

    def test_case_insensitive_names(self):
        self.assertEqual(child_environment({"Groq_Api_Key": "x", "A": "1"}), {"A": "1"})

    def test_users_other_credentials_are_kept(self):
        env = child_environment({"GITHUB_TOKEN": "ghp", "AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "s"})
        self.assertEqual(set(env), {"GITHUB_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"})

    def test_every_managed_provider_key_variable_is_stripped(self):
        """The reproduced live finding: a user-level GROQ_API_KEY inherited by
        the server must not reach the agent's shell."""
        names = {s.env_key for s in registry.PROVIDERS.values() if s.env_key and s.env_key != "AWS_ACCESS_KEY_ID"}
        self.assertIn("GROQ_API_KEY", names)
        env = child_environment({n: "leak" for n in names} | {"PATH": "/bin"})
        self.assertEqual(env, {"PATH": "/bin"})

    def test_explicit_session_export_can_still_pass_a_key(self):
        env = child_environment({"GROQ_API_KEY": "leak"}, {"GROQ_API_KEY": "deliberate"})
        self.assertEqual(env, {"GROQ_API_KEY": "deliberate"})

    def test_extra_wins_and_does_not_mutate_base(self):
        base = {"A": "1", "GROQ_API_KEY": "s"}
        env = child_environment(base, {"A": "2", "B": "3"})
        self.assertEqual(env, {"A": "2", "B": "3"})
        self.assertEqual(base, {"A": "1", "GROQ_API_KEY": "s"})

    def test_eva_keeps_no_secret_of_its_own_in_the_environment(self):
        self.assertEqual(EVA_INTERNAL_SECRET_VARS, frozenset())


class SpawnSitesTests(unittest.TestCase):
    def test_shell_session_effective_env(self):
        from core.shell.session import ShellSession
        with patch.dict(os.environ, {"GROQ_API_KEY": "users-own"}):
            s = ShellSession(conversation_id="c")
            s.env_vars = {"FOO": "bar"}
            env = s.effective_env()
            self.assertNotIn("GROQ_API_KEY", env)
            self.assertEqual(env["FOO"], "bar")

    def test_real_child_process_cannot_see_a_managed_provider_key(self):
        code = "import os;print(os.environ.get('GROQ_API_KEY'), os.environ.get('EVA_TEST_VISIBLE'))"
        with patch.dict(os.environ, {"GROQ_API_KEY": "users-own", "EVA_TEST_VISIBLE": "yes"}):
            out = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, env=child_environment(),
            ).stdout.strip()
        self.assertEqual(out, "None yes")

    def test_every_spawn_site_uses_the_sanitized_env(self):
        for rel in ("core/shell/session.py", "core/shell/executor.py",
                    "tools/default/git_tool.py", "core/hooks/runner.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("child_environment(", src, rel)


class NoProviderKeyExportTests(unittest.TestCase):
    def test_no_application_code_writes_a_provider_key_into_environ(self):
        names = {s.env_key for s in registry.PROVIDERS.values() if s.env_key}
        pat = re.compile(r"os\.environ\[[^\]]+\]\s*=|os\.environ\.(setdefault|update)\(|os\.putenv\(")
        offenders = []
        for top in ("api", "core", "providers", "tools", "utils", "config"):
            for path in (ROOT / top).rglob("*.py"):
                rel = path.relative_to(ROOT).as_posix()
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if line.lstrip().startswith("#") or not pat.search(line):
                        continue
                    if "key_name" in line or any(k in line for k in names):
                        offenders.append(f"{rel}:{n}: {line.strip()}")
        # api/secrets._write_config writes OLLAMA_HOST (non-secret) through a variable named key_name.
        offenders = [o for o in offenders if not o.startswith("api/secrets.py")]
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
