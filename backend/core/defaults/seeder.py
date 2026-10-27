# core/defaults/seeder.py
#
# V5.0 Sprint 5 — Default Agents & Skills Catalog (B-074)
#
# Seeds the Tier 1 default subagent configurations and default skills for a
# user on first signup, and provides a re-seed path for existing users.
#
# Seeding is:
#   - Idempotent: calling seed_user_defaults() multiple times is safe.
#     Agents use upsert (name is unique per user); skills are only copied
#     if the target file does not already exist (never overwrites user edits).
#   - Non-fatal: errors are logged as warnings so they never block signup.
#   - Synchronous for skills (disk copy), async for agents (DB upsert).
#
# Directory layout (relative to repo root):
#   default_agents/       YAML definitions for default SubagentConfigs
#   default_mcps/         YAML definitions for default MCPConfigs (M-01)
#   skills/               Default SKILL.md libraries (also user-editable)
#
# User workspace layout:
#   ~/eva_workspace/{user_id}/skills/   Skills are copied here on first seed

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

# Repo root is two levels up from this file (core/defaults/seeder.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Default agent YAML definitions
DEFAULT_AGENTS_DIR = _REPO_ROOT / "default_agents"

# Default skills (shipped with the repo, copied into each user's workspace)
DEFAULT_SKILLS_DIR = _REPO_ROOT / "skills"

# Default MCP server definitions (M-01)
DEFAULT_MCPS_DIR = _REPO_ROOT / "default_mcps"


# ── Public API ────────────────────────────────────────────────────────────────

async def seed_user_defaults(user_id: str, force: bool = False) -> dict:
    """
    Seed default agents and skills for *user_id*.

    Safe to call at signup and on re-seed requests.  Returns a summary dict:
    {
        "agents_seeded": int,
        "agents_skipped": int,
        "skills_seeded": int,
        "skills_skipped": int,
        "errors": [str],
    }

    Args:
        force: When True, existing default agents are overwritten from the YAML
               definitions (useful after YAML changes like tool list updates).
               Skills are never force-overwritten — user edits are preserved.
    """
    result: dict[str, Any] = {
        "agents_seeded": 0,
        "agents_skipped": 0,
        "skills_seeded": 0,
        "skills_skipped": 0,
        "mcps_seeded": 0,
        "mcps_skipped": 0,
        "mcps_pending_install": [],
        "errors": [],
    }

    # ── 1. Seed skills (disk copy, sync) ──────────────────────────────────────
    _seed_skills(user_id, result)

    # ── 2. Seed agents (DB upsert, async) ─────────────────────────────────────
    await _seed_agents(user_id, result, force=force)

    # ── 3. Seed default MCP configs (DB insert, async) ────────────────────────
    await _seed_mcps(user_id, result)

    logger.info(
        "[DEFAULTS] Seeding complete for user %s: %d agents seeded, "
        "%d skills seeded, %d errors",
        user_id,
        result["agents_seeded"],
        result["skills_seeded"],
        len(result["errors"]),
    )
    return result


async def seed_default_mcps(user_id: str) -> dict:
    """
    Seed only the default MCP configs for *user_id* (POST /mcp/seed-defaults).

    Insert-only and name-keyed: a config whose name the user already has is
    skipped, never overwritten, so user edits (args, enabled flag) survive
    every re-seed. Seeded rows honour the YAML's `enabled` flag (default
    False) and never connect to the server -- no npx launch, no tool sync --
    so seeding is fast and side-effect free; enabling a config later
    auto-syncs its tools (api/mcp.py::update_mcp_config).

    Returns {"mcps_seeded", "mcps_skipped", "mcps_pending_install", "errors"}.
    "mcps_pending_install" lists seeded configs whose `requires` executables
    are not on PATH ([{"name", "missing"}]) -- still seeded (disabled), so the
    user can install the dependency and enable them.
    """
    result: dict[str, Any] = {
        "mcps_seeded": 0,
        "mcps_skipped": 0,
        "mcps_pending_install": [],
        "errors": [],
    }
    await _seed_mcps(user_id, result)
    return result


async def backfill_new_skills_for_all_users() -> dict:
    """
    Re-run skill seeding for every existing user -- adds any skill folders
    shipped since that user's last seed (e.g. agent-builder/daemon-builder
    added after the user already signed up), while leaving everything else
    in their workspace untouched.

    Safe to call on every boot: mirrors config/env_loader.py::load_eva_env()'s
    own "idempotent, call every startup" migration pattern -- no persisted
    "have I already backfilled" flag needed, since _seed_skills()'s own
    per-folder existence check already makes repeated calls a no-op once a
    skill has been copied once. Given EVA's single-tenant/single-machine
    scale (typically one user, occasionally a handful), the cost of
    checking every user on every boot is negligible -- a few filesystem
    stats per user, no network calls.

    Deliberately scoped to skills only, not default agents: agent
    re-seeding for existing users is already a separate, human-triggered
    action (POST /agents/seed-defaults?force=true) precisely because
    overwriting an agent's config needs an explicit opt-in; skills are
    always additive-only (never overwritten), so no such gate is needed
    here.
    """
    from sqlalchemy import select
    from core.db.session import AsyncSessionLocal
    from core.db.models import User

    summary: dict[str, Any] = {"users_checked": 0, "skills_seeded": 0, "errors": []}

    try:
        async with AsyncSessionLocal() as db:
            db_result = await db.execute(select(User.id))
            user_ids = [row[0] for row in db_result.all()]
    except Exception as exc:
        msg = f"Cannot list users for skill backfill: {exc}"
        logger.warning("[DEFAULTS] %s", msg)
        summary["errors"].append(msg)
        return summary

    for user_id in user_ids:
        summary["users_checked"] += 1
        per_user: dict[str, Any] = {"skills_seeded": 0, "skills_skipped": 0, "errors": []}
        try:
            _seed_skills(user_id, per_user)
        except Exception as exc:
            per_user["errors"].append(str(exc))
        summary["skills_seeded"] += per_user["skills_seeded"]
        summary["errors"].extend(f"user {user_id}: {e}" for e in per_user["errors"])

    if summary["skills_seeded"]:
        logger.info(
            "[DEFAULTS] Skill backfill: seeded %d new skill instance(s) across %d user(s) checked",
            summary["skills_seeded"], summary["users_checked"],
        )
    else:
        logger.debug(
            "[DEFAULTS] Skill backfill: nothing new to seed (%d user(s) checked)",
            summary["users_checked"],
        )
    return summary


def list_default_agents() -> list[dict]:
    """
    Return parsed data for all default agent YAML files.
    Useful for the management endpoint and tests.
    """
    agents = []
    if not DEFAULT_AGENTS_DIR.exists():
        return agents
    for yaml_file in sorted(DEFAULT_AGENTS_DIR.glob("*.yaml")):
        data = _parse_agent_yaml(yaml_file)
        if data:
            agents.append(data)
    return agents


def list_default_mcps() -> list[dict]:
    """Return parsed data (placeholders unresolved) for all default MCP YAML files."""
    mcps = []
    if not DEFAULT_MCPS_DIR.exists():
        return mcps
    for yaml_file in sorted(DEFAULT_MCPS_DIR.glob("*.yaml")):
        data = _parse_agent_yaml(yaml_file)
        if data:
            mcps.append(data)
    return mcps


def list_default_skills() -> list[str]:
    """
    Return the names of all default skills (directory names under skills/).
    """
    if not DEFAULT_SKILLS_DIR.exists():
        return []
    return sorted(
        d.name
        for d in DEFAULT_SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    )


# ── Skills seeding ────────────────────────────────────────────────────────────

def _seed_skills(user_id: str, result: dict) -> None:
    """Copy default skills to the user's workspace skills directory."""
    if not DEFAULT_SKILLS_DIR.exists():
        logger.debug("[DEFAULTS] No default skills directory found at %s", DEFAULT_SKILLS_DIR)
        return

    try:
        from utils.workspace import get_user_skills_dir  # noqa: PLC0415
        user_skills_dir = get_user_skills_dir(user_id)
        user_skills_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        msg = f"Cannot resolve user skills directory: {exc}"
        logger.warning("[DEFAULTS] %s", msg)
        result["errors"].append(msg)
        return

    for skill_src in sorted(DEFAULT_SKILLS_DIR.iterdir()):
        if not skill_src.is_dir():
            continue
        skill_md = skill_src / "SKILL.md"
        if not skill_md.exists():
            continue

        skill_name = skill_src.name
        skill_dest = user_skills_dir / skill_name

        if skill_dest.exists():
            result["skills_skipped"] += 1
            logger.debug("[DEFAULTS] Skill '%s' already exists for user %s — skipping", skill_name, user_id)
            continue

        try:
            shutil.copytree(str(skill_src), str(skill_dest))
            result["skills_seeded"] += 1
            logger.info("[DEFAULTS] Seeded skill '%s' for user %s", skill_name, user_id)
        except Exception as exc:
            msg = f"Failed to copy skill '{skill_name}': {exc}"
            logger.warning("[DEFAULTS] %s", msg)
            result["errors"].append(msg)


# ── Agent seeding ─────────────────────────────────────────────────────────────

async def _seed_agents(user_id: str, result: dict, force: bool = False) -> None:
    """Upsert default SubagentConfig rows for the user.

    Args:
        force: When True, overwrite existing agents from the YAML definitions.
               When False (default), skip agents that already exist in the DB.
    """
    if not DEFAULT_AGENTS_DIR.exists():
        logger.debug("[DEFAULTS] No default agents directory found at %s", DEFAULT_AGENTS_DIR)
        return

    try:
        from core.agents.registry import subagent_registry  # noqa: PLC0415
    except Exception as exc:
        msg = f"Cannot import subagent_registry: {exc}"
        logger.warning("[DEFAULTS] %s", msg)
        result["errors"].append(msg)
        return

    for yaml_file in sorted(DEFAULT_AGENTS_DIR.glob("*.yaml")):
        data = _parse_agent_yaml(yaml_file)
        if not data:
            continue

        name = data.get("name", "")
        if not name:
            logger.warning("[DEFAULTS] Agent YAML %s has no name — skipping", yaml_file.name)
            continue

        # Check if this agent already exists for the user
        try:
            existing = await subagent_registry.get(user_id, name)
        except Exception as exc:
            msg = f"DB lookup failed for agent '{name}': {exc}"
            logger.warning("[DEFAULTS] %s", msg)
            result["errors"].append(msg)
            continue

        if existing is not None and not force:
            result["agents_skipped"] += 1
            logger.debug("[DEFAULTS] Agent '%s' already exists for user %s — skipping", name, user_id)
            continue

        action = "Updated" if existing is not None else "Seeded"
        try:
            await subagent_registry.upsert(user_id, {
                "name":          data.get("name"),
                "description":   data.get("description", ""),
                "system_prompt": data.get("system_prompt", ""),
                "model":         data.get("model") or None,
                "provider":      data.get("provider") or None,
                "tools":         data.get("tools") or None,
                "max_steps":     int(data.get("max_steps", 10)),
                "is_default":    True,
            })
            result["agents_seeded"] += 1
            logger.info("[DEFAULTS] %s agent '%s' for user %s", action, name, user_id)
        except Exception as exc:
            msg = f"Failed to upsert agent '{name}': {exc}"
            logger.warning("[DEFAULTS] %s", msg)
            result["errors"].append(msg)


# ── MCP seeding ───────────────────────────────────────────────────────────────

_MCP_TRANSPORTS = ("stdio", "http", "sse")


def _mcp_placeholders(user_id: str) -> dict[str, str]:
    # NOTE: {cdp_endpoint} (the CDP-embedded preview pane) is deliberately
    # NOT resolved here. workspace/browser_channel below are static per
    # user and correct to bake into the DB row once at seed time; a live
    # CDP endpoint changes over the app's lifetime (pane opens/closes,
    # restarts) and must be re-checked on every actual connection attempt
    # instead -- see core/mcp/preview_registry.py's resolve_connection_args(),
    # called from core/mcp/session_pool.py and core/mcp/manager.py.
    from utils.workspace import get_user_workspace  # noqa: PLC0415
    return {
        "workspace": get_user_workspace(user_id),
        # Edge ships with every Windows install; Chrome is the safest guess elsewhere.
        "browser_channel": "msedge" if sys.platform == "win32" else "chrome",
    }


def _resolve_placeholders(value: Any, placeholders: dict[str, str]) -> Any:
    if isinstance(value, str):
        for key, replacement in placeholders.items():
            value = value.replace("{" + key + "}", replacement)
        return value
    if isinstance(value, list):
        return [_resolve_placeholders(v, placeholders) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_placeholders(v, placeholders) for k, v in value.items()}
    return value


def _validate_mcp_definition(data: dict) -> Optional[str]:
    """Return an error message if the YAML definition is unusable, else None."""
    if not data.get("name"):
        return "missing 'name'"
    from core.mcp.naming import is_valid_mcp_config_name  # noqa: PLC0415
    if not is_valid_mcp_config_name(data["name"]):
        return f"name {data['name']!r} is not a valid MCP config name"
    transport = data.get("transport")
    if transport not in _MCP_TRANSPORTS:
        return f"transport must be one of {_MCP_TRANSPORTS}, got {transport!r}"
    if transport == "stdio" and not data.get("command"):
        return "stdio transport requires 'command'"
    if transport in ("http", "sse") and not data.get("url"):
        return f"{transport} transport requires 'url'"
    return None


async def _seed_mcps(user_id: str, result: dict) -> None:
    """Insert default MCPConfig rows the user does not already have (by name)."""
    if not DEFAULT_MCPS_DIR.exists():
        logger.debug("[DEFAULTS] No default MCPs directory found at %s", DEFAULT_MCPS_DIR)
        return

    try:
        from core.db.session import AsyncSessionLocal  # noqa: PLC0415
        from core.db.repository import MCPRepo  # noqa: PLC0415
        async with AsyncSessionLocal() as db:
            existing_names = {c.name for c in await MCPRepo.list_configs(db, user_id)}
        placeholders = _mcp_placeholders(user_id)
    except Exception as exc:
        msg = f"Cannot prepare MCP seeding: {exc}"
        logger.warning("[DEFAULTS] %s", msg)
        result["errors"].append(msg)
        return

    for yaml_file in sorted(DEFAULT_MCPS_DIR.glob("*.yaml")):
        data = _parse_agent_yaml(yaml_file)
        if not data:
            continue

        problem = _validate_mcp_definition(data)
        if problem:
            msg = f"Default MCP {yaml_file.name} is invalid: {problem}"
            logger.warning("[DEFAULTS] %s", msg)
            result["errors"].append(msg)
            continue

        name = data["name"]
        if name in existing_names:
            result["mcps_skipped"] += 1
            logger.debug("[DEFAULTS] MCP '%s' already exists for user %s — skipping", name, user_id)
            continue

        missing = [exe for exe in (data.get("requires") or []) if shutil.which(exe) is None]

        row: dict[str, Any] = {
            "name": name,
            "transport": data["transport"],
            "enabled": bool(data.get("enabled", False)),
        }
        for field in ("command", "args", "env", "cwd", "url", "headers"):
            if data.get(field) is not None:
                row[field] = _resolve_placeholders(data[field], placeholders)

        try:
            async with AsyncSessionLocal() as db:
                await MCPRepo.create_config(db, user_id, row)
        except Exception as exc:
            msg = f"Failed to seed MCP '{name}': {exc}"
            logger.warning("[DEFAULTS] %s", msg)
            result["errors"].append(msg)
            continue

        result["mcps_seeded"] += 1
        if missing:
            result["mcps_pending_install"].append({"name": name, "missing": missing})
        logger.info(
            "[DEFAULTS] Seeded MCP '%s' for user %s (enabled=%s%s)",
            name, user_id, row["enabled"],
            f", missing on PATH: {', '.join(missing)}" if missing else "",
        )


# ── YAML parsing ──────────────────────────────────────────────────────────────

def _parse_agent_yaml(yaml_file: Path) -> Optional[dict]:
    """
    Parse an agent definition YAML file.

    Supports PyYAML if available; falls back to a minimal inline parser
    that handles the subset used in default_agents/*.yaml.
    """
    try:
        content = yaml_file.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("[DEFAULTS] Cannot read %s: %s", yaml_file.name, exc)
        return None

    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else None
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("[DEFAULTS] PyYAML failed for %s (%s), using fallback", yaml_file.name, exc)

    # Minimal fallback parser for the simple key: value / block scalar subset
    return _simple_yaml_parse(content)


def _simple_yaml_parse(content: str) -> dict:
    """
    Minimal YAML parser covering the subset used in default_agents/*.yaml:
    - Simple `key: scalar` pairs
    - Block scalars (| and >) — collected until next top-level key
    - Comment lines starting with #
    - null values
    """
    result: dict = {}
    lines = content.splitlines()
    i = 0
    n = len(lines)

    import re
    top_key_re = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:(.*)')

    def _coerce(val: str) -> Any:
        v = val.strip()
        if v in ("null", "~", ""):
            return None
        if v in ("true", "True"):
            return True
        if v in ("false", "False"):
            return False
        try:
            return int(v)
        except ValueError:
            pass
        return v.strip("\"'")

    list_item_re = re.compile(r'^\s+-\s+(.*)')

    while i < n:
        line = lines[i]
        # Skip comments and blank lines
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue

        m = top_key_re.match(line)
        if not m:
            i += 1
            continue

        key = m.group(1)
        rest = m.group(2).strip()

        # Block scalar: | (literal) or > (folded)
        if rest in ("|", ">"):
            scalar_lines = []
            i += 1
            indent: Optional[int] = None
            while i < n:
                bl = lines[i]
                if bl.strip() == "" or bl.lstrip().startswith("#"):
                    scalar_lines.append("")
                    i += 1
                    continue
                # Determine indent from first non-blank content line
                stripped = bl.lstrip()
                cur_indent = len(bl) - len(stripped)
                if indent is None:
                    indent = cur_indent
                if cur_indent < indent:
                    break  # back to top level
                scalar_lines.append(bl[indent:] if indent else bl)
                i += 1
            # Strip trailing blank lines
            while scalar_lines and not scalar_lines[-1].strip():
                scalar_lines.pop()
            if rest == "|":
                result[key] = "\n".join(scalar_lines) + "\n"
            else:  # >
                # Folded: join lines, keep paragraph breaks
                folded_parts = []
                current_para = []
                for sl in scalar_lines:
                    if sl.strip() == "":
                        if current_para:
                            folded_parts.append(" ".join(current_para))
                            current_para = []
                        folded_parts.append("")
                    else:
                        current_para.append(sl.strip())
                if current_para:
                    folded_parts.append(" ".join(current_para))
                result[key] = "\n".join(folded_parts).strip()
        elif rest == "":
            # Empty inline value — may be followed by a sequence (list) block.
            # Peek ahead: if the next non-blank/non-comment line is a list item,
            # collect the sequence.  Otherwise store None.
            i += 1
            list_items = []
            while i < n:
                peek = lines[i]
                stripped_peek = peek.strip()
                # Skip comment-only continuation lines
                if stripped_peek.startswith("#"):
                    i += 1
                    continue
                lm = list_item_re.match(peek)
                if lm:
                    list_items.append(lm.group(1).strip().strip("\"'"))
                    i += 1
                elif stripped_peek == "":
                    i += 1
                    continue
                else:
                    break  # next top-level key or unrecognised content
            result[key] = list_items if list_items else None
        else:
            # Inline value (possibly null)
            result[key] = _coerce(rest)
            i += 1

    return result
