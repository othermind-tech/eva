import json
from typing import Any, Dict


def normalize_tool_arguments(raw_args: Any) -> Dict[str, Any]:
    """
    Normalize provider tool-call arguments into a Python dict.
    Providers often return JSON strings.
    """
    if raw_args is None:
        return {}

    if isinstance(raw_args, dict):
        return raw_args

    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            raise ValueError(
                f"Invalid tool arguments JSON: {raw_args}"
            )

    raise TypeError(
        f"Unsupported tool arguments type: {type(raw_args)}"
    )
