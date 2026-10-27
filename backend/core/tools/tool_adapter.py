# core/tools/tool_adapter.py
# Converts ToolManifest → executable ToolBase subclass

import re
import uuid
from typing import Any, Dict, List, Optional, Type, Union

from pydantic import create_model

from core.mcp.runtime import MCPRuntime
from core.tools.base import ToolBase
from core.tools.dynamic_registry import ToolManifest

# M-06: matches [^A-Za-z0-9_] so a generated nested-model name is always a
# legal Python identifier fragment, regardless of what characters an MCP
# server's property names contain.
_NON_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


def _resolve_ref(ref: str, defs: Dict[str, dict]) -> dict:
    """
    Resolve a JSON Schema $ref against the schema's own $defs/definitions.
    Only local refs ("#/$defs/Foo", "#/definitions/Foo") are meaningful here
    -- MCP tool schemas are self-contained, single-document schemas, so a
    remote/external $ref would be a genuinely malformed manifest; falls back
    to an empty schema (-> str, same as the old shallow behaviour) rather
    than raising, so one bad property doesn't break the whole tool.
    """
    key = ref.rsplit("/", 1)[-1]
    return defs.get(key, {})


def _json_type_to_python(
    prop_def: dict, prop_name: str, defs: Dict[str, dict], model_name_prefix: str,
) -> Any:
    """
    Convert one JSON Schema node into a Python/typing type, recursing into
    nested objects (-> a real nested Pydantic model, not `dict`), $ref
    (resolved against defs), anyOf/oneOf (-> typing.Union), and arrays of
    objects (-> List[NestedModel]) -- the structure the original shallow
    converter lost entirely (nested objects -> opaque dict) or actively
    mistyped (property has no top-level "type" because it's a $ref/anyOf
    node -> silently fell through to `str`).
    """
    if "$ref" in prop_def:
        return _json_type_to_python(
            _resolve_ref(prop_def["$ref"], defs), prop_name, defs, model_name_prefix
        )

    variants = prop_def.get("anyOf") or prop_def.get("oneOf")
    if variants:
        py_types: List[Any] = []
        for variant in variants:
            vt = _json_type_to_python(variant, prop_name, defs, model_name_prefix)
            if vt not in py_types:  # de-dupe, e.g. repeated {"type": "null"} entries
                py_types.append(vt)
        return Union[tuple(py_types)] if len(py_types) > 1 else py_types[0]

    if "allOf" in prop_def:
        # Schema composition: merge every sub-schema's properties/required
        # into one model rather than picking just one arbitrarily.
        merged: dict = {"type": "object", "properties": {}, "required": []}
        for sub in prop_def["allOf"]:
            sub = _resolve_ref(sub["$ref"], defs) if "$ref" in sub else sub
            merged["properties"].update(sub.get("properties", {}))
            merged["required"].extend(sub.get("required", []))
        return _json_type_to_python(merged, prop_name, defs, model_name_prefix)

    t = prop_def.get("type")

    if t == "object" and prop_def.get("properties"):
        nested_name = f"{model_name_prefix}_{_NON_IDENT_RE.sub('_', prop_name.title())}"
        return _build_object_model(prop_def, nested_name, defs)
    if t == "object":
        return dict  # genuinely schema-less object (no declared properties)
    if t == "array":
        items = prop_def.get("items")
        item_type = (
            _json_type_to_python(items, prop_name, defs, model_name_prefix)
            if items else Any
        )
        return List[item_type]
    if t == "integer":
        return int
    if t == "boolean":
        return bool
    if t == "number":
        return float
    if t == "null":
        return type(None)
    if t == "string":
        return str
    if isinstance(t, list):
        # JSON Schema also allows "type": ["string", "null"] directly.
        py_types = [
            _json_type_to_python({**prop_def, "type": sub}, prop_name, defs, model_name_prefix)
            for sub in t
        ]
        return Union[tuple(py_types)] if len(py_types) > 1 else py_types[0]

    # No recognizable type/$ref/anyOf/oneOf/allOf node -- genuinely
    # untyped per the source schema, not a conversion failure. `str` is
    # the same fallback the original converter used for this one case.
    return str


def _build_object_model(schema: dict, model_name: str, defs: Dict[str, dict]) -> Type:
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: Dict[str, tuple] = {}

    for prop_name, prop_def in props.items():
        py_type = _json_type_to_python(prop_def, prop_name, defs, model_name)
        if "default" in prop_def:
            # A property that declares a default is optional in practice, even
            # when the schema also lists it under "required" -- servers whose
            # schemas come from zod's output mode (Playwright MCP: level,
            # static, scale) do exactly that. Enforcing "required" literally
            # rejects calls the server itself would happily accept.
            fields[prop_name] = (Optional[py_type], prop_def["default"])
        elif prop_name in required:
            fields[prop_name] = (py_type, ...)
        else:
            fields[prop_name] = (Optional[py_type], None)

    return create_model(model_name, **fields)


def json_schema_to_pydantic_model(schema: dict, model_name: str) -> Type:
    """
    Convert a JSON Schema object into a Pydantic model, recursively --
    nested objects become real nested models (not opaque dicts), $ref
    resolves against the schema's own $defs/definitions, and anyOf/oneOf
    become typing.Union, so a tool with non-trivial parameter structure
    gets an accurate schema instead of a flattened/mistyped one.

    Once this model exists, nothing downstream needs to change: builder.py
    calls .model_json_schema() on whatever Pydantic model a tool has (this
    one included) to build what's sent to the LLM provider, and
    dispatcher.py validates real tool-call arguments against it the same
    way it does for every other tool -- both already handle nested
    Pydantic models correctly, since that's core, well-tested Pydantic
    behaviour. The bug was entirely in this function building a shallow
    model in the first place, not in anything reading it afterward.
    """
    defs = schema.get("$defs") or schema.get("definitions") or {}
    return _build_object_model(schema, model_name, defs)


def _strip_nulls(value: Any, keep: frozenset = frozenset()) -> Any:
    """
    Recursively drop None-valued keys from an argument dict.

    keep: top-level keys whose null must be preserved -- the schema's own "required" fields. A
    required, nullable property ({"type": ["string", "null"]}) may be answered with an explicit
    null, and dropping it would turn a valid call into "required field missing".

    The args model gives every optional property a None default, and the dispatcher hands the
    tool ``parsed_args.dict()`` -- so a property the model simply left out arrives as an explicit
    null. Native tools accept that, but an external MCP server validates against its own schema
    and rejects null for an optional boolean/string ("expected boolean, received null"), which
    made calls like ``browser_console_messages {}`` impossible without inventing values. Omitting
    the key is what "optional" means to the server.
    """
    if isinstance(value, dict):
        return {k: _strip_nulls(v) for k, v in value.items() if v is not None or k in keep}
    if isinstance(value, list):
        return [_strip_nulls(v) for v in value]
    return value


def build_toolbase_subclass_from_manifest(manifest: ToolManifest) -> Type[ToolBase]:
    """
    Create a ToolBase subclass from a ToolManifest.
    Will raise NotImplementedError at runtime until MCP or remote binding is injected.
    """
    schema_model = json_schema_to_pydantic_model(
        manifest.input_schema,
        model_name=f"Args_{manifest.name}_{uuid.uuid4().hex[:6]}"
    )

    _required_keys = frozenset((manifest.input_schema or {}).get("required") or [])

    class DynamicTool(ToolBase):
        name = manifest.name
        description = manifest.description
        args_schema = schema_model
        category = "mcp"

        async def run(self, args: Dict[str, Any]):
            # ConversationExecution has no `db` attribute -- MCPRuntime needs
            # its own session, same as every other DB-touching call site.
            from core.db.session import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                return await MCPRuntime.execute(
                    db=db,
                    user_id=self.execution.user_id,
                    tool_name=self.name,
                    arguments=_strip_nulls(args, keep=_required_keys),
                    project_id=getattr(self.execution, "parent_project_id", None),
                    user_registry=getattr(self.execution, "user_registry", None),
                )
    return DynamicTool
