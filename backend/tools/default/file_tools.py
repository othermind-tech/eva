"""
Filesystem tools for EVA.

Authority classification:
- read_file        → read-only
- read_file_range  → read-only
- list_files       → read-only
- stat_file        → read-only
- write_file       → mutating
- edit_file        → mutating
- delete_file      → destructive

Design principles:
- Deterministic
- Explicit
- No sandboxing
- No recursion by default
- No implicit policy
"""

from pydantic import BaseModel
from typing import List, Optional
from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.registry import default_tool
import os
import shutil
import time

from utils.workspace import resolve_path_for_user


def replace_first_match(content: str, old_string: str, new_string: str) -> Optional[str]:
    """
    Replace the first occurrence of old_string with new_string in content.
    Returns None if old_string is not found (caller decides how to report
    that as a tool error) -- shared between EditFileTool and
    ProjectArtifactWriteTool's 'edit' action so both use identical
    replace-first-match semantics, not two divergent implementations.
    """
    idx = content.find(old_string)
    if idx == -1:
        return None
    return content[:idx] + new_string + content[idx + len(old_string):]

# ─────────────────────────────────────────────
# Read File (entire content)
# ─────────────────────────────────────────────

class ReadFileArgs(BaseModel):
    path: str


@default_tool(
    name="read_file",
    description="Read and return the entire contents of a local file. Relative paths resolve against the active project's folder, else this conversation's folder. Use absolute paths only for locations outside it.",
    args_schema=ReadFileArgs,
    authority=ToolAuthority.READ_ONLY,
    category="filesystem",
    summary="Read the full contents of a file from the workspace.",
)
class ReadFileTool(ToolBase):

    async def run(self, args: dict) -> ToolResult:
        path = resolve_path_for_user(self.execution.user_id, args["path"], getattr(self.execution, "conversation_id", None), getattr(self.execution, "project_workspace", None))

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            return ToolResult(result={"content": content})

        except FileNotFoundError:
            return ToolResult(result={"error": f"File not found: {path}"})

        except Exception as e:
            return ToolResult(result={"error": str(e)})


# ─────────────────────────────────────────────
# Read File Range (line-based)
# ─────────────────────────────────────────────

class ReadFileRangeArgs(BaseModel):
    path: str
    start_line: int
    end_line: int


@default_tool(
    name="read_file_range",
    description="Read a specific line range from a local file (1-based, inclusive). Relative paths resolve against the active project's folder, else this conversation's folder. Use absolute paths only for locations outside it.",
    args_schema=ReadFileRangeArgs,
    authority=ToolAuthority.READ_ONLY,
    category="filesystem",
    summary="Read a specific line range (start–end) from a file.",
)
class ReadFileRangeTool(ToolBase):

    async def run(self, args: dict) -> ToolResult:
        path = resolve_path_for_user(self.execution.user_id, args["path"], getattr(self.execution, "conversation_id", None), getattr(self.execution, "project_workspace", None))
        start = args["start_line"]
        end = args["end_line"]

        if start < 1 or end < start:
            return ToolResult(
                result={"error": "Invalid line range specified."}
            )

        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            selected = lines[start - 1 : end]

            return ToolResult(
                result={
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "content": "".join(selected)
                }
            )

        except FileNotFoundError:
            return ToolResult(result={"error": f"File not found: {path}"})

        except Exception as e:
            return ToolResult(result={"error": str(e)})


# ─────────────────────────────────────────────
# List Files
# ─────────────────────────────────────────────

class ListFilesArgs(BaseModel):
    path: str = "."
    include_dirs: bool = True   # subfolders are listed (name ending in '/'); False = files only
    extensions: Optional[List[str]] = None  # e.g. ["py", "md"]


@default_tool(
    name="list_files",
    description="List what is in one local directory: files, and subfolders (shown with a trailing '/'; pass include_dirs=false for files only), with optional extension filtering. Not recursive (it has no recursive option) -- use find_files to search inside subfolders. Relative paths resolve against the active project's folder, else this conversation's folder. Use absolute paths only for locations outside it.",
    args_schema=ListFilesArgs,
    authority=ToolAuthority.READ_ONLY,
    category="filesystem",
    summary="List the files and subfolders in one directory.",
)
class ListFilesTool(ToolBase):

    async def run(self, args: dict) -> ToolResult:
        path = resolve_path_for_user(self.execution.user_id, args.get("path", "."), getattr(self.execution, "conversation_id", None), getattr(self.execution, "project_workspace", None))
        include_dirs = args.get("include_dirs", True)
        extensions = args.get("extensions")

        try:
            if not os.path.exists(path):
                return ToolResult(result={"error": f"Path does not exist: {path}"})

            if not os.path.isdir(path):
                return ToolResult(result={"error": f"Path is not a directory: {path}"})

            normalized_exts = None
            if extensions:
                normalized_exts = {
                    ext.lower().lstrip(".") for ext in extensions
                }

            entries = []
            for name in os.listdir(path):
                full_path = os.path.join(path, name)

                if os.path.isfile(full_path):
                    if normalized_exts:
                        if "." not in name:
                            continue
                        ext = name.rsplit(".", 1)[-1].lower()
                        if ext not in normalized_exts:
                            continue
                    entries.append(name)

                elif include_dirs and os.path.isdir(full_path):
                    entries.append(name + "/")

            return ToolResult(
                result={
                    "path": path,
                    "entries": sorted(entries),
                    "filtered_extensions": sorted(normalized_exts) if normalized_exts else None
                }
            )

        except Exception as e:
            return ToolResult(result={"error": str(e)})


# ─────────────────────────────────────────────
# File Metadata
# ─────────────────────────────────────────────

class StatFileArgs(BaseModel):
    path: str


@default_tool(
    name="stat_file",
    description="Return metadata for a local file or directory. Relative paths resolve against the active project's folder, else this conversation's folder. Use absolute paths only for locations outside it.",
    args_schema=StatFileArgs,
    authority=ToolAuthority.READ_ONLY,
    category="filesystem",
    summary="Get metadata (size, type, timestamps) for a file or directory.",
)
class StatFileTool(ToolBase):

    async def run(self, args: dict) -> ToolResult:
        path = resolve_path_for_user(self.execution.user_id, args["path"], getattr(self.execution, "conversation_id", None), getattr(self.execution, "project_workspace", None))

        try:
            if not os.path.exists(path):
                return ToolResult(result={"error": f"Path does not exist: {path}"})

            stat = os.stat(path)

            return ToolResult(
                result={
                    "path": path,
                    "type": "directory" if os.path.isdir(path) else "file",
                    "size_bytes": stat.st_size,
                    "modified_time": int(stat.st_mtime),
                    "accessed_time": int(stat.st_atime),
                    "created_time": int(stat.st_ctime),
                }
            )

        except Exception as e:
            return ToolResult(result={"error": str(e)})


# ─────────────────────────────────────────────
# Write File
# ─────────────────────────────────────────────

class WriteFileArgs(BaseModel):
    path: str
    content: str
    overwrite: bool = True


@default_tool(
    name="write_file",
    description="Write content to a local file. Relative paths resolve against the active project's folder, else this conversation's folder. Use absolute paths only for locations outside it.",
    args_schema=WriteFileArgs,
    authority=ToolAuthority.MUTATING,
    category="filesystem",
    summary="Write or overwrite a file with the provided content.",
)
class WriteFileTool(ToolBase):
    path_arg_name = "path"  # B2-1.1: out-of-workspace approval gate

    async def run(self, args: dict) -> ToolResult:
        path = resolve_path_for_user(self.execution.user_id, args["path"], getattr(self.execution, "conversation_id", None), getattr(self.execution, "project_workspace", None))
        content = args["content"]
        overwrite = args.get("overwrite", True)

        try:
            parent_dir = os.path.dirname(path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            if not overwrite and os.path.exists(path):
                return ToolResult(
                    result={"error": f"File already exists and overwrite is disabled: {path}"}
                )

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            return ToolResult(
                result={
                    "path": path,
                    "bytes_written": len(content.encode("utf-8"))
                }
            )

        except Exception as e:
            return ToolResult(result={"error": str(e)})


# ─────────────────────────────────────────────
# Edit File (targeted replacement)
# ─────────────────────────────────────────────

class EditFileArgs(BaseModel):
    path: str
    old_string: str
    new_string: str


@default_tool(
    name="edit_file",
    description=(
        "Make a precise, targeted edit to an existing file -- replaces the first "
        "occurrence of old_string with new_string, leaving the rest of the file "
        "untouched. Use this instead of write_file when changing part of a file; "
        "write_file requires resending the entire file content, which is slower, "
        "costlier, and risks accidentally dropping unrelated content. old_string "
        "must match the file's current content exactly -- read the file first if "
        "unsure. Errors clearly if old_string is not found, rather than guessing."
    ),
    args_schema=EditFileArgs,
    authority=ToolAuthority.MUTATING,
    category="filesystem",
    summary="Replace the first occurrence of old_string with new_string in a file.",
)
class EditFileTool(ToolBase):
    path_arg_name = "path"  # B2-1.1: out-of-workspace approval gate

    async def run(self, args: dict) -> ToolResult:
        path = resolve_path_for_user(self.execution.user_id, args["path"], getattr(self.execution, "conversation_id", None), getattr(self.execution, "project_workspace", None))
        old_string = args["old_string"]
        new_string = args["new_string"]

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return ToolResult(result={"error": f"File not found: {path}"})
        except Exception as e:
            return ToolResult(result={"error": str(e)})

        updated = replace_first_match(content, old_string, new_string)
        if updated is None:
            return ToolResult(result={
                "error": f"old_string not found in {path}. Re-read the file to confirm its exact current content before editing.",
            })

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(updated)
        except Exception as e:
            return ToolResult(result={"error": str(e)})

        return ToolResult(result={"path": path, "bytes_written": len(updated.encode("utf-8"))})


# ─────────────────────────────────────────────
# Delete File / Directory
# ─────────────────────────────────────────────

class DeleteFileArgs(BaseModel):
    path: str
    recursive: bool = False


@default_tool(
    name="delete_file",
    description="Delete a local file or directory. Directory deletion requires explicit recursive permission. Relative paths resolve against the active project's folder, else this conversation's folder. Use absolute paths only for locations outside it.",
    args_schema=DeleteFileArgs,
    authority=ToolAuthority.DESTRUCTIVE,
    category="filesystem",
    summary="Delete a file or directory (requires recursive=true for non-empty dirs).",
)
class DeleteFileTool(ToolBase):
    path_arg_name = "path"  # B2-1.1: out-of-workspace approval gate

    async def run(self, args: dict) -> ToolResult:
        path = resolve_path_for_user(self.execution.user_id, args["path"], getattr(self.execution, "conversation_id", None), getattr(self.execution, "project_workspace", None))
        recursive = args.get("recursive", False)

        try:
            if not os.path.exists(path):
                return ToolResult(result={"error": f"Path does not exist: {path}"})

            if os.path.isfile(path):
                os.remove(path)
                return ToolResult(result={"path": path, "deleted": "file"})

            if os.path.isdir(path):
                if recursive:
                    shutil.rmtree(path)
                    return ToolResult(
                        result={"path": path, "deleted": "directory_recursive"}
                    )

                if os.listdir(path):
                    return ToolResult(
                        result={
                            "error": (
                                f"Directory is not empty: {path}. "
                                "Set recursive=true to delete non-empty directories."
                            )
                        }
                    )

                os.rmdir(path)
                return ToolResult(
                    result={"path": path, "deleted": "directory"}
                )

            return ToolResult(result={"error": f"Unsupported path type: {path}"})

        except Exception as e:
            return ToolResult(result={"error": str(e)})
