# core/tools/loader.py

import importlib
import pkgutil
from logging import getLogger
from pathlib import Path

from core.tools.registry import DefaultToolRegistry, CustomToolRegistry

logger = getLogger(__name__)


def load_tools_from_directory(directory: Path):
    """
    Imports all `.py` tool files inside a directory.
    Each module must register tools using @default_tool or @custom_tool.
    """
    if not directory.exists():
        logger.warning(
            "Tools directory does not exist",
            extra={"directory": str(directory)},
        )
        return

    logger.debug(
        "Loading tools from directory",
        extra={"directory": str(directory)},
    )
    for module_info in pkgutil.iter_modules([str(directory)]):
        if module_info.name.startswith("_"):
            continue

        module_name = module_info.name
        full_module_path = f"tools.{directory.name}.{module_name}"

        try:
            importlib.import_module(full_module_path)
            logger.debug(
                "Tool module loaded",
                extra={
                    "directory": directory.name,
                    "tool_module": module_name,
                },
            )
        except Exception as e:
            logger.error(
                "Failed to load tool module",
                extra={
                    "directory": directory.name,
                    "tool_module": module_name,
                    "error": str(e),
                },
                exc_info=True,
            )


def load_all_tools():
    """
    Loads tools from tools/default and tools/custom.
    """
    logger.info("Starting tool loading")
    root = Path(__file__).resolve().parents[2]   # project root
    tools_root = root / "tools"

    default_dir = tools_root / "default"
    custom_dir = tools_root / "custom"

    load_tools_from_directory(default_dir)
    load_tools_from_directory(custom_dir)

    total = len(DefaultToolRegistry.all()) + len(CustomToolRegistry.all())
    logger.info(
        "Tool loading completed",
        extra={
            "default_tools": len(DefaultToolRegistry.all()),
            "custom_tools": len(CustomToolRegistry.all()),
            "total_tools": total,
        },
    )
