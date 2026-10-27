# tools/default/project_tools.py
#
# Phase B: ProjectArtifactWriteTool — lets an agent record findings directly
# into a project's .eva artifacts, without going through the DB-message-based
# ProjectArtifactUpdater pipeline (that pipeline reads real chat Message rows,
# which headless/automated runs -- e.g. daemons -- deliberately never persist).
#
# Reuses the exact same file-writing primitives ProjectArtifactUpdater itself
# uses (ProjectManager.get_artifact_path + append_entry/set_section from
# core/project/eva_format.py) -- zero DB dependency, same as that code.

from typing import Optional
from pydantic import BaseModel, Field, model_validator

from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.registry import default_tool
from core.project.manager import ProjectManager
from core.project.eva_format import append_entry, set_section, read_file as read_eva_file, write_file as write_eva_file
from tools.default.file_tools import replace_first_match

_FILE_MAP = {
    "tasks": "tasks.eva",
    "knowledge": "knowledge.eva",
    "errors": "errors.eva",
    "policies": "policies.eva",
}
_VALID_ACTIONS = ("append", "replace_section", "edit")


class ProjectArtifactWriteArgs(BaseModel):
    file: str = Field(description="Which artifact to write to: tasks, knowledge, errors, or policies.")
    action: str = Field(description="'append' to add a new entry, 'replace_section' to overwrite a named section, or 'edit' to replace one existing piece of text with another.")
    section: Optional[str] = Field(
        default=None,
        description="Section name (e.g. KNOWLEDGE, TASKS) -- required when action is 'replace_section'.",
    )
    content: Optional[str] = Field(
        default=None,
        description="The entry or section content to write, formatted per the target file's own conventions -- required for 'append' and 'replace_section'.",
    )
    old_string: Optional[str] = Field(
        default=None,
        description="Existing text to replace -- required for 'edit'. Must match the file's current content exactly; read the file first if unsure.",
    )
    new_string: Optional[str] = Field(
        default=None,
        description="Replacement text -- required for 'edit'.",
    )

    @model_validator(mode="after")
    def _validate(self):
        if self.file not in _FILE_MAP:
            raise ValueError(f"file must be one of {sorted(_FILE_MAP)}, got '{self.file}'.")
        if self.action not in _VALID_ACTIONS:
            raise ValueError(f"action must be one of {_VALID_ACTIONS}.")
        if self.action == "replace_section" and not (self.section or "").strip():
            raise ValueError("section is required when action is 'replace_section'.")
        if self.action in ("append", "replace_section") and not (self.content or "").strip():
            raise ValueError("content is required when action is 'append' or 'replace_section'.")
        if self.action == "edit" and (self.old_string is None or self.new_string is None):
            raise ValueError("old_string and new_string are both required when action is 'edit'.")
        return self


@default_tool(
    name="project_artifact_write",
    description=(
        "Record findings directly into the active project's knowledge base -- "
        "tasks.eva, knowledge.eva, errors.eva, or policies.eva. Use 'edit' to update "
        "something already recorded (prefer this over 'append' when new information "
        "updates or restates an existing entry -- don't create a near-duplicate); "
        "'append' to add a genuinely new, non-overlapping entry; 'replace_section' to "
        "overwrite a named section wholesale (rare -- most of the file has changed at "
        "once). Only usable when a project is active in this execution."
    ),
    args_schema=ProjectArtifactWriteArgs,
    authority=ToolAuthority.MUTATING,
    category="project",
    summary="Write, edit, or overwrite content directly in a project's .eva knowledge files.",
)
class ProjectArtifactWriteTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        project_id = getattr(execution, "parent_project_id", None)
        workspace_path = getattr(execution, "project_workspace", None)

        if not user_id or not project_id:
            return ToolResult(result={"error": "No active project in this execution context."})

        file_key = args["file"]
        action = args["action"]
        filename = _FILE_MAP[file_key]

        path = ProjectManager.get_artifact_path(user_id, project_id, filename, workspace_path)

        if action == "append":
            append_entry(path, args["content"])
        elif action == "replace_section":
            section = args["section"].strip().upper()
            set_section(path, section, args["content"])
        else:
            current = read_eva_file(path)
            updated = replace_first_match(current, args["old_string"], args["new_string"])
            if updated is None:
                return ToolResult(result={
                    "error": f"old_string not found in {filename}. Read the file first to confirm its exact current content.",
                })
            write_eva_file(path, updated)

        ProjectManager.append_journal(
            user_id=user_id,
            project_id=project_id,
            content=f"project_artifact_write: {action} to {filename}.",
            workspace_path=workspace_path,
        )
        ProjectManager.touch_updated(user_id, project_id, workspace_path)

        return ToolResult(result={"ok": True, "file": filename, "action": action})


class ProjectArtifactReadArgs(BaseModel):
    file: str = Field(description="Which artifact to read: tasks, knowledge, errors, or policies.")

    @model_validator(mode="after")
    def _validate(self):
        if self.file not in _FILE_MAP:
            raise ValueError(f"file must be one of {sorted(_FILE_MAP)}, got '{self.file}'.")
        return self


@default_tool(
    name="project_artifact_read",
    description=(
        "Read the current content of one of the active project's knowledge files -- "
        "tasks, knowledge, errors, or policies -- addressed by the same logical name "
        "project_artifact_write uses (e.g. file=\"knowledge\"), not a filesystem path. "
        "Use this before writing to check what's already recorded, so you can choose "
        "'edit' over 'append' when something is already covered. Always returns the "
        "file's full current content, uncapped -- if a project has accumulated a very "
        "large amount of knowledge, use judgment: focus on the most relevant recent "
        "entries, narrow down with search_files against the file's content, or delegate "
        "a summarization pass, rather than either ignoring the size or stalling on it. "
        "Only usable when a project is active in this execution."
    ),
    args_schema=ProjectArtifactReadArgs,
    authority=ToolAuthority.READ_ONLY,
    category="project",
    summary="Read the full current content of a project's .eva knowledge file.",
)
class ProjectArtifactReadTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        project_id = getattr(execution, "parent_project_id", None)
        workspace_path = getattr(execution, "project_workspace", None)

        if not user_id or not project_id:
            return ToolResult(result={"error": "No active project in this execution context."})

        file_key = args["file"]
        filename = _FILE_MAP[file_key]
        content = ProjectManager.read_artifact(user_id, project_id, filename, workspace_path=workspace_path)

        return ToolResult(result={"file": filename, "content": content})
