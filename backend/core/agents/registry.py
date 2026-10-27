# core/agents/registry.py
#
# G-003: SubagentRegistry
#
# Provides async CRUD access to per-user subagent configurations stored in
# the `subagent_configs` DB table.  Stateless — no in-memory cache — so it
# is safe to use from multiple worker processes simultaneously.
#
# Usage:
#   from core.agents.registry import subagent_registry
#   cfg = await subagent_registry.get(user_id, "researcher")

from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import select, and_, delete

from core.db.session import AsyncSessionLocal
from core.db.models import SubagentConfig

logger = logging.getLogger(__name__)


class SubagentRegistry:
    """Async DB-backed registry for per-user subagent configurations."""

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, user_id: str, name: str) -> Optional[SubagentConfig]:
        """Return the SubagentConfig row for (user_id, name), or None."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SubagentConfig).where(
                    and_(
                        SubagentConfig.user_id == user_id,
                        SubagentConfig.name == name,
                    )
                )
            )
            return result.scalar_one_or_none()

    async def list(
        self,
        user_id: str,
        project_id: Optional[str] = None,
    ) -> List[SubagentConfig]:
        """Return subagent configs for a user, ordered by name.

        If project_id is given, returns agents belonging to that project PLUS
        all default agents (is_default=True) — these are always available
        regardless of project assignment.  This ensures the UI shows exactly
        what the runtime will allow (R1 consistency).
        Pass project_id=None to return all agents regardless of project.
        """
        async with AsyncSessionLocal() as db:
            q = select(SubagentConfig).where(SubagentConfig.user_id == user_id)
            if project_id is not None:
                from sqlalchemy import or_  # noqa: PLC0415
                q = q.where(
                    or_(
                        SubagentConfig.project_id == project_id,
                        SubagentConfig.is_default == True,  # noqa: E712
                    )
                )
            result = await db.execute(q.order_by(SubagentConfig.name))
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def upsert(self, user_id: str, data: dict) -> SubagentConfig:
        """
        Create or replace a subagent config for (user_id, data["name"]).

        ``data`` keys: name, description, system_prompt, model, provider,
                       tools, max_steps, allowed_mcp_servers, project_id,
                       is_default (all optional except name).

        Returns the persisted SubagentConfig row.
        """
        name = data.get("name", "").strip()
        if not name:
            raise ValueError("Subagent name must not be empty.")

        # R4 enforcement: default agents are global system templates and cannot
        # be scoped to a project.  Clone the agent first to create a
        # project-specific copy (POST /agents/{name}/clone).
        if data.get("is_default") and data.get("project_id"):
            raise ValueError(
                "Default agents cannot be assigned to a project. "
                "Use POST /agents/{name}/clone to create a project-scoped copy first."
            )

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SubagentConfig).where(
                    and_(
                        SubagentConfig.user_id == user_id,
                        SubagentConfig.name == name,
                    )
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update in-place
                existing.description         = data.get("description",         existing.description)
                existing.system_prompt       = data.get("system_prompt",       existing.system_prompt)
                existing.model               = data.get("model",               existing.model)
                existing.provider            = data.get("provider",            existing.provider)
                existing.tools               = data.get("tools",               existing.tools)
                existing.max_steps           = data.get("max_steps",           existing.max_steps)
                existing.allowed_mcp_servers = data.get("allowed_mcp_servers", existing.allowed_mcp_servers)
                existing.project_id          = data.get("project_id",          existing.project_id)
                # Only update is_default when explicitly supplied in data
                if "is_default" in data:
                    existing.is_default = bool(data["is_default"])
                row = existing
            else:
                row = SubagentConfig(
                    user_id=user_id,
                    name=name,
                    description=data.get("description"),
                    system_prompt=data.get("system_prompt"),
                    model=data.get("model"),
                    provider=data.get("provider"),
                    tools=data.get("tools"),
                    max_steps=data.get("max_steps", 5),
                    allowed_mcp_servers=data.get("allowed_mcp_servers"),
                    project_id=data.get("project_id"),
                    is_default=bool(data.get("is_default", False)),
                )
                db.add(row)

            await db.commit()
            await db.refresh(row)

            logger.info(
                "Subagent config upserted",
                extra={"user_id": user_id, "agent_name": name},
            )
            return row

    async def delete(self, user_id: str, name: str) -> bool:
        """
        Delete the subagent config for (user_id, name).
        Returns True if a row was deleted, False if it did not exist.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(SubagentConfig).where(
                    and_(
                        SubagentConfig.user_id == user_id,
                        SubagentConfig.name == name,
                    )
                )
            )
            await db.commit()
            deleted = result.rowcount > 0
            if deleted:
                logger.info(
                    "Subagent config deleted",
                    extra={"user_id": user_id, "agent_name": name},
                )
            return deleted


# Module-level singleton — import and use directly.
subagent_registry = SubagentRegistry()
