# tests/test_tool_adapter_unit.py
#
# Unit tests for M-06: core/tools/tool_adapter.py's JSON-Schema-to-Pydantic
# converter used for MCP tool schemas. The original converter only read
# top-level properties/type -- nested objects collapsed to an opaque dict,
# and $ref/anyOf/oneOf/allOf properties (which have no top-level "type" key)
# silently fell through to `str`, an actively wrong type rather than just a
# vague one. Fixed to recurse properly; these tests guard against
# regressing back to the shallow behaviour.

import unittest

from core.tools.tool_adapter import json_schema_to_pydantic_model


class TestNestedObjects(unittest.TestCase):
    """Inline nested objects must become real nested models, not opaque dicts."""

    def test_inline_nested_object_becomes_real_model_not_dict(self):
        schema = {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "zip": {"type": "string"},
                    },
                    "required": ["city"],
                },
            },
            "required": ["address"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({"address": {"city": "Boston", "zip": "02101"}})

        # The old bug: this field would just be a plain dict with no
        # attribute access and no validation of its own shape.
        self.assertTrue(hasattr(instance.address, "city"))
        self.assertEqual(instance.address.city, "Boston")
        self.assertEqual(instance.address.zip, "02101")

    def test_nested_object_required_field_is_enforced(self):
        schema = {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
            "required": ["address"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        with self.assertRaises(Exception):
            Model.model_validate({"address": {}})  # missing required "city"

    def test_array_of_nested_objects(self):
        schema = {
            "type": "object",
            "properties": {
                "stops": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            },
            "required": ["stops"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({"stops": [{"name": "A"}, {"name": "B"}]})
        self.assertEqual(instance.stops[0].name, "A")
        self.assertEqual(instance.stops[1].name, "B")


class TestRefResolution(unittest.TestCase):
    """
    $ref properties have no top-level "type" key -- the original converter
    fell through to its default (`str`), silently mistyping the field
    rather than just losing detail.
    """

    def test_ref_resolves_to_real_nested_model_not_string(self):
        schema = {
            "type": "object",
            "$defs": {
                "Passenger": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                    "required": ["name", "age"],
                },
            },
            "properties": {
                "passenger": {"$ref": "#/$defs/Passenger"},
            },
            "required": ["passenger"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({"passenger": {"name": "Alice", "age": 30}})

        self.assertTrue(hasattr(instance.passenger, "name"))
        self.assertEqual(instance.passenger.name, "Alice")
        self.assertEqual(instance.passenger.age, 30)
        # The old bug: this would coerce/accept a bare string here instead.
        self.assertNotIsInstance(instance.passenger, str)

    def test_ref_using_legacy_definitions_keyword(self):
        """Older JSON Schema drafts use "definitions" instead of "$defs"."""
        schema = {
            "type": "object",
            "definitions": {
                "Point": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                    "required": ["x", "y"],
                },
            },
            "properties": {"origin": {"$ref": "#/definitions/Point"}},
            "required": ["origin"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({"origin": {"x": 1, "y": 2}})
        self.assertEqual(instance.origin.x, 1)

    def test_array_of_ref_objects(self):
        schema = {
            "type": "object",
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {"sku": {"type": "string"}},
                    "required": ["sku"],
                },
            },
            "properties": {
                "items": {"type": "array", "items": {"$ref": "#/$defs/Item"}},
            },
            "required": ["items"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({"items": [{"sku": "ABC"}]})
        self.assertEqual(instance.items[0].sku, "ABC")


class TestAnyOfOneOf(unittest.TestCase):
    """
    anyOf/oneOf properties also have no top-level "type" key -- same
    silent-mistype-as-string bug as $ref.
    """

    def test_anyof_accepts_and_preserves_each_variant_type(self):
        schema = {
            "type": "object",
            "properties": {
                "id": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            },
            "required": ["id"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")

        int_instance = Model.model_validate({"id": 42})
        self.assertEqual(int_instance.id, 42)
        self.assertIsInstance(int_instance.id, int)  # not coerced to "42"

        str_instance = Model.model_validate({"id": "abc-123"})
        self.assertEqual(str_instance.id, "abc-123")

    def test_anyof_with_nested_object_variant(self):
        schema = {
            "type": "object",
            "properties": {
                "target": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
                            "required": ["lat", "lon"],
                        },
                    ]
                },
            },
            "required": ["target"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({"target": {"lat": 1.5, "lon": 2.5}})
        self.assertEqual(instance.target.lat, 1.5)

    def test_oneof_behaves_like_anyof(self):
        schema = {
            "type": "object",
            "properties": {
                "value": {"oneOf": [{"type": "boolean"}, {"type": "string"}]},
            },
            "required": ["value"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({"value": True})
        self.assertIs(instance.value, True)


class TestAllOf(unittest.TestCase):
    def test_allof_merges_properties_from_all_branches(self):
        schema = {
            "type": "object",
            "properties": {
                "combined": {
                    "allOf": [
                        {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
                        {"type": "object", "properties": {"b": {"type": "integer"}}, "required": ["b"]},
                    ]
                },
            },
            "required": ["combined"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({"combined": {"a": "x", "b": 1}})
        self.assertEqual(instance.combined.a, "x")
        self.assertEqual(instance.combined.b, 1)


class TestSchemaRoundTrip(unittest.TestCase):
    """
    The generated model must itself produce a complete, correct JSON
    schema when re-serialized -- this is what actually reaches the LLM
    provider via core/tools/builder.py's build_provider_tools(), which
    just calls .model_json_schema() on whatever Pydantic model a tool has.
    """

    def test_generated_model_schema_preserves_nested_structure(self):
        schema = {
            "type": "object",
            "$defs": {
                "Passenger": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            "properties": {"passenger": {"$ref": "#/$defs/Passenger"}},
            "required": ["passenger"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        out_schema = Model.model_json_schema()

        # Must have its own $defs with the nested model's real fields --
        # not a flattened top-level "passenger": {"type": "string"}.
        self.assertIn("$defs", out_schema)
        nested_def = next(iter(out_schema["$defs"].values()))
        self.assertIn("name", nested_def["properties"])
        self.assertEqual(nested_def["properties"]["name"]["type"], "string")


class TestSimpleFieldsUnaffected(unittest.TestCase):
    """Plain top-level fields (the case the original converter did handle) must still work."""

    def test_simple_types_still_convert_correctly(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
                "active": {"type": "boolean"},
                "score": {"type": "number"},
            },
            "required": ["name"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({
            "name": "x", "count": 5, "active": True, "score": 1.5,
        })
        self.assertEqual(instance.name, "x")
        self.assertEqual(instance.count, 5)
        self.assertTrue(instance.active)
        self.assertEqual(instance.score, 1.5)

    def test_optional_field_defaults_to_none(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "nickname": {"type": "string"}},
            "required": ["name"],
        }
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({"name": "x"})
        self.assertIsNone(instance.nickname)

    def test_zero_parameter_schema(self):
        """MCP's most common shape for simple tools -- must still work (regression guard)."""
        schema = {"type": "object", "properties": {}, "required": []}
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        instance = Model.model_validate({})
        self.assertEqual(instance.model_dump(), {})


class TestDefaultedFieldsAreOptional(unittest.TestCase):
    """
    Playwright MCP's schemas list defaulted fields (level/static/scale) under
    "required" (zod output mode). The server accepts calls that omit them, so
    enforcing "required" literally rejected valid calls with "Field required".
    """

    def _schema(self):
        return {
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["error", "info"], "default": "info"},
                "all": {"type": "boolean"},
                "url": {"type": "string"},
            },
            "required": ["level", "url"],
        }

    def test_required_field_with_default_may_be_omitted_and_takes_the_default(self):
        Model = json_schema_to_pydantic_model(self._schema(), "TestArgs")
        instance = Model.model_validate({"url": "http://x"})
        self.assertEqual(instance.level, "info")

    def test_explicit_value_for_a_defaulted_field_still_wins(self):
        Model = json_schema_to_pydantic_model(self._schema(), "TestArgs")
        self.assertEqual(Model.model_validate({"url": "u", "level": "error"}).level, "error")

    def test_required_field_without_default_is_still_enforced(self):
        Model = json_schema_to_pydantic_model(self._schema(), "TestArgs")
        with self.assertRaises(Exception):
            Model.model_validate({"level": "error"})

    def test_generated_schema_no_longer_marks_the_defaulted_field_required(self):
        Model = json_schema_to_pydantic_model(self._schema(), "TestArgs")
        sent_to_llm = Model.model_json_schema()
        self.assertNotIn("level", sent_to_llm.get("required", []))
        self.assertIn("url", sent_to_llm["required"])

    def test_zero_valued_defaults_are_kept(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer", "default": 0}}, "required": ["n"]}
        Model = json_schema_to_pydantic_model(schema, "TestArgs")
        self.assertEqual(Model.model_validate({}).n, 0)


class TestOmittedOptionalArgsAreNotSentAsNull(unittest.TestCase):
    """
    Live finding (2026-09-21): playwright__browser_console_messages {} failed with
    "expected boolean, received null at all / expected string, received null at filename"
    because every unset optional field was forwarded to the MCP server as an explicit null.
    """

    def _run_through_dynamic_tool(self, model_args, required=(), filename_schema=None):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from core.tools.dynamic_registry import ToolManifest
        from core.tools.tool_adapter import build_toolbase_subclass_from_manifest

        manifest = ToolManifest(
            name="playwright.browser_console_messages",
            description="d",
            input_schema={
                "type": "object",
                "properties": {
                    "level": {"type": "string", "default": "info"},
                    "all": {"type": "boolean"},
                    "filename": filename_schema or {"type": "string"},
                },
                "required": list(required),
            },
        )
        tool_cls = build_toolbase_subclass_from_manifest(manifest)
        parsed = tool_cls.args_schema.model_validate(model_args)
        tool = tool_cls.__new__(tool_cls)
        tool.execution = MagicMock(user_id="u", parent_project_id=None, user_registry=None)

        captured = {}

        async def fake_execute(**kwargs):
            captured.update(kwargs["arguments"])
            return MagicMock()

        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        session_cm.__aexit__ = AsyncMock(return_value=False)
        with patch("core.tools.tool_adapter.MCPRuntime.execute", side_effect=fake_execute),              patch("core.db.session.AsyncSessionLocal", return_value=session_cm):
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(tool.run(parsed.model_dump()))
        return captured

    def test_omitted_optionals_are_absent_not_null(self):
        sent = self._run_through_dynamic_tool({})
        self.assertNotIn("all", sent)
        self.assertNotIn("filename", sent)
        self.assertEqual(sent, {"level": "info"})  # the schema default is a real value and is kept

    def test_explicit_null_from_the_model_is_dropped_too(self):
        sent = self._run_through_dynamic_tool({"all": None, "filename": None, "level": "error"})
        self.assertEqual(sent, {"level": "error"})

    def test_real_values_including_false_and_zero_survive(self):
        sent = self._run_through_dynamic_tool({"all": False, "filename": "x.log"})
        self.assertEqual(sent, {"level": "info", "all": False, "filename": "x.log"})

    def test_a_null_the_schema_requires_is_kept(self):
        # A required, nullable property may legitimately be answered with an explicit null; dropping
        # it would turn a valid call into "required field missing".
        sent = self._run_through_dynamic_tool(
            {"filename": None, "all": None}, required=["filename"],
            filename_schema={"anyOf": [{"type": "string"}, {"type": "null"}]})
        self.assertIn("filename", sent)
        self.assertIsNone(sent["filename"])
        self.assertNotIn("all", sent)

    def test_nested_nulls_are_stripped(self):
        from core.tools.tool_adapter import _strip_nulls
        self.assertEqual(_strip_nulls({"a": {"b": None, "c": 0}, "d": [{"e": None, "f": ""}], "g": None}),
                         {"a": {"c": 0}, "d": [{"f": ""}]})


if __name__ == "__main__":
    unittest.main()
