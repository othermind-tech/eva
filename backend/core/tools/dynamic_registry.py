# EVA: Dynamic Tool Discovery - Core Patch v1
# Stage 1: Add DynamicToolRegistry (base + memory-backed)

from typing import Dict, List, Optional, Callable
from pydantic import BaseModel
import threading

# ---- Tool Schema (minimal MCP-style) ----
class ToolManifest(BaseModel):
    name: str
    description: str
    input_schema: dict  # JSON schema (to be adapted later)
    output_schema: Optional[dict] = None
    version: Optional[str] = "1.0"
    origin: Optional[str] = "local"

# ---- Dynamic Registry ----
class DynamicToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolManifest] = {}
        self._lock = threading.Lock()
        self._on_change_callbacks: List[Callable[[str, ToolManifest], None]] = []

    def register_tool(self, manifest: ToolManifest):
        with self._lock:
            self._tools[manifest.name] = manifest
            self._emit_change("added", manifest)

    def remove_tool(self, name: str):
        with self._lock:
            if name in self._tools:
                removed = self._tools.pop(name)
                self._emit_change("removed", removed)

    def get(self, name: str) -> Optional[ToolManifest]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolManifest]:
        return list(self._tools.values())

    def on_change(self, callback: Callable[[str, ToolManifest], None]):
        self._on_change_callbacks.append(callback)

    def _emit_change(self, event: str, manifest: ToolManifest):
        for cb in self._on_change_callbacks:
            cb(event, manifest)
