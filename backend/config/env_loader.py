"""
config/env_loader.py

Loads EVA's configuration at startup with two storage backends:

  Secrets (provider API keys, the token-signing key):
    Stored in Windows Credential Manager via `keyring` (service name "eva").
    NEVER read from Windows/system environment variables.
    On first startup, migrated automatically from eva.env if present there,
    and the key lines are removed from eva.env so it contains no secrets.

  Config (OLLAMA_HOST, LOG_LEVEL, etc.):
    Stored in eva.env — not secrets, safe to commit once migration has run.
    Copied into os.environ (Step 4 below) so both config/settings.py's
    pydantic BaseSettings classes AND its bare os.getenv()-based module
    constants pick it up -- the file is a single override surface for
    every documented setting, not just the pydantic-backed subset.

Provider API keys are NOT placed in os.environ (V6.59 Phase 3). Every reader
resolves them from the credential store through providers/credentials.py, which
never consults the process environment -- so a key can't leak into child
processes (shell tools, background processes, git) or be picked up from a stray
system variable. The token-signing key is not exported either (V6.59 Phase 7):
core/auth/signing_key.py reads it from the credential store on first use and
fails closed instead of ever falling back to an empty key. Nothing secret is
placed in the process environment.

Call load_eva_env() as the very first statement in every real process entry
point, before any other imports that might read os.environ -- currently
main.py (entrypoint.py picks this up transitively via `from main import app`)
and alembic/env.py (needed for a standalone `alembic upgrade` invocation,
not just server-embedded migrations). Test entry points deliberately do NOT
call this -- tests must stay isolated from a developer's real local eva.env.
"""
import logging as _logging
import os
from providers.capability.base import PROVIDER_KEY_MAP

# ---------------------------------------------------------------------------
# Pre-configure logger for startup output.
# load_eva_env() runs before configure_logging() so we can't use the root
# logger.  This named logger writes directly to stdout via its own handler,
# with propagate=False to avoid double-logging once the full log system starts.
# ---------------------------------------------------------------------------
_log = _logging.getLogger("eva.startup")
if not _log.handlers:
    _h = _logging.StreamHandler()
    _h.setFormatter(_logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
    _log.addHandler(_h)
    _log.setLevel(_logging.DEBUG)
    _log.propagate = False
from pathlib import Path

# ---------------------------------------------------------------------------
# All provider API key env var names EVA knows about.
# Derived from PROVIDER_KEY_MAP so this list never drifts from the canonical
# provider registry.  Local providers (Ollama) map to None and are excluded.
#
# Note: AWS_ACCESS_KEY_ID (bedrock) is also a system-standard env var name.
# EVA no longer touches the process environment for provider keys, so a
# user's own AWS_* variables are left alone (and EVA ignores them: keys are
# resolved only from the credential store).
# ---------------------------------------------------------------------------
_PROVIDER_API_KEYS: tuple[str, ...] = tuple(
    v for v in PROVIDER_KEY_MAP.values() if v is not None
)
from providers.registry import PROVIDERS as _PROVIDERS   # noqa: E402
_KEYED_PROVIDERS = tuple(s for s in _PROVIDERS.values() if s.env_key)

# eva.env lives at the project root (same directory as main.py / run_worker.py)
_EVA_ENV_PATH = Path(__file__).resolve().parent.parent / "eva.env"

# All secrets are stored under this service name in Windows Credential Manager.
from providers import credentials as _credentials
from providers.credentials import KEYRING_SERVICE   # one definition, shared with the key store


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env-style file into a dict. Strips quotes and comments."""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            result[k] = v
    return result


def load_eva_env() -> None:
    """
    Load EVA secrets and configuration at startup. Must be called first.

    Steps
    -----
    1. Token-signing key — created and stored in the credential store if absent
       (first run). It is NOT exported to os.environ.

    2. One-time migration — provider API keys present in eva.env but absent
       from keyring are written to Windows Credential Manager and removed from
       eva.env. Idempotent: safe to call on every startup.

    3. Report which providers have a stored key (names only, never values).
       Nothing is copied into os.environ: keys are read on demand from the
       credential store (providers/credentials.py).
    """
    # ── Read eva.env (non-secret config + migration source) ──────────────────
    eva_vals: dict[str, str] = {}
    if _EVA_ENV_PATH.exists():
        try:
            from dotenv import dotenv_values
            raw = dotenv_values(_EVA_ENV_PATH)
            eva_vals = {k: v for k, v in raw.items() if v is not None}
        except ImportError:
            eva_vals = _parse_env_file(_EVA_ENV_PATH)
        _log.info("[ENV] eva.env found: %s", _EVA_ENV_PATH)
    else:
        _log.info("[ENV] eva.env not found at %s (will be created as needed)", _EVA_ENV_PATH)

    # ── Step 1: token-signing key — create on first run; NEVER exported ──────
    # The key lives only in the credential store and is read on use by
    # core.auth.signing_key (fails closed). Touching it here makes first-run
    # creation (and an unreadable store) surface at startup instead of on the
    # first login. It is deliberately not put in os.environ.
    from core.auth.signing_key import get_signing_key
    get_signing_key()
    _log.info("[ENV] Token-signing key available in the OS credential store")

    # ── Step 2: One-time migration eva.env → credential store ───────────────
    try:
        from dotenv import unset_key as _unset_key
        _can_unset = True
    except ImportError:
        _can_unset = False

    migrated: list[str] = []
    for spec in _KEYED_PROVIDERS:
        # Skip keys already stored — migration is idempotent.
        if _credentials.has_api_key(spec.name):
            continue
        val = eva_vals.get(spec.env_key, "").strip()
        if val:
            _credentials.set_api_key(spec.name, val)
            if _can_unset and _EVA_ENV_PATH.exists():
                _unset_key(str(_EVA_ENV_PATH), spec.env_key)
            migrated.append(spec.env_key)
            _log.info("[ENV] Migrated %s → Windows Credential Manager (removed from eva.env)", spec.env_key)

    # ── Step 3: Report configured providers (names only; no env copy) ────────
    loaded:  list[str] = [s.env_key for s in _KEYED_PROVIDERS if _credentials.has_api_key(s.name)]
    cleared: list[str] = [s.env_key for s in _KEYED_PROVIDERS if s.env_key not in loaded]

    # ── Step 4: General (non-secret) config rehydration — eva.env → os.environ ──
    # config/settings.py has two independent config mechanisms: a handful of
    # pydantic BaseSettings classes (Settings, OllamaSettings, ChromaSettings,
    # etc.) read eva.env directly via their own env_file config -- those
    # already work. The overwhelming majority of settings, though, are bare
    # module-level constants (`X: int = int(os.getenv("X", "default"))`),
    # which only ever see real os.environ -- eva.env's general config lines
    # were parsed into eva_vals above but never copied anywhere, so those
    # ~63 documented "eva.env-overridable" settings silently never were.
    #
    # setdefault(), not direct assignment: a value already in os.environ (a
    # genuine OS/system env var, or something a caller set before this ran)
    # must keep winning over an eva.env value -- matching the precedence the
    # pydantic-settings classes already use themselves (constructor kwargs >
    # OS env > eva.env file > code default), so a setting behaves identically
    # regardless of which of the two mechanisms it happens to be declared with.
    #
    # Secrets are explicitly excluded -- provider API keys are handled only
    # via Step 3 above (keyring is authoritative, never eva.env), and
    # the token-signing key only via Step 1 (credential store/auto-generated). Neither should
    # ever be sourced from a general eva.env copy, even defensively.
    rehydrated: list[str] = []
    for cfg_key, cfg_val in eva_vals.items():
        if cfg_key in _PROVIDER_API_KEYS or cfg_key == "SECRET_KEY":
            continue
        if cfg_key not in os.environ:
            os.environ[cfg_key] = cfg_val
            rehydrated.append(cfg_key)

    # ── Startup summary ───────────────────────────────────────────────────────
    if migrated:
        _log.info("[ENV] %d key(s) migrated from eva.env to Windows Credential Manager.", len(migrated))
        _log.info("[ENV] eva.env no longer contains secrets — safe to version-control.")

    if loaded:
        _log.info("[ENV] Provider API keys found in the credential store (%d): %s",
                  len(loaded), ", ".join(loaded))
    else:
        _log.info("[ENV] No provider API keys configured — set them via the EVA UI (Profile → API Keys).")

    if rehydrated:
        _log.info("[ENV] %d general config key(s) loaded from eva.env: %s", len(rehydrated), ", ".join(rehydrated))
