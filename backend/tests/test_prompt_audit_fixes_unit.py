# tests/test_prompt_audit_fixes_unit.py
#
# Unit tests for the 2026-07-21 prompt-file audit, done ahead of finalizing
# the A-10 fix (per the user's request: audit every preloaded prompt/preamble
# against the actual codebase while making the change, not just the one file).
# Covers seven findings across prompts/*.py, core/memory/extractor.py, and
# tools/default/coordinate_tool.py.
#
# Two of the seven are real code-logic changes, not just prompt text, and get
# the most thorough coverage here:
#   - S-10: the "supersedes" memory-extraction branch could overwrite a
#     profile-locked fact (user.full_name / user.about) with no guard at all,
#     even though the sibling "new" branch already had one.
#   - Agent-entry "sensitive" self-flagging: AGENT_EXTRACTION_SYSTEM's output
#     schema had no sensitive field, so the LLM had no way to mark its own
#     extracted entry as sensitive (only a human via POST /memory/agent could).
#
# Run with:
#     cd <project_root>
#     pytest tests/test_prompt_audit_fixes_unit.py -v

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# ===========================================================================
# S-10 — "supersedes" branch profile-lock guard (core/memory/extractor.py)
# ===========================================================================

class TestSupersedesProfileLockGuard(unittest.IsolatedAsyncioTestCase):

    def _make_extractor_deps(self, existing_facts, llm_response_items):
        """Common mocking scaffold: existing facts fetched from the DB, and
        the LLM's parsed JSON response for the user-facts extraction call."""
        import core.memory.extractor as mod

        mock_facts = [MagicMock(**f) for f in existing_facts]
        for mock_fact, f in zip(mock_facts, existing_facts):
            mock_fact.id = f["id"]

        patches = [
            patch.object(mod.MemoryRepo, "get_user_facts", new=AsyncMock(return_value=mock_facts)),
            patch.object(mod.MemoryRepo, "update_user_fact", new=AsyncMock()),
            patch.object(mod.MemoryRepo, "add_user_fact", new=AsyncMock()),
            patch.object(mod.MemoryExtractor, "_call_llm", new=AsyncMock(return_value="ignored")),
            patch.object(
                mod.MemoryExtractor, "_parse_json_list",
                new=lambda self, raw, max_items: llm_response_items,
            ),
        ]
        return patches, mock_facts

    async def test_supersedes_targeting_a_profile_locked_fact_is_skipped(self):
        import core.memory.extractor as mod

        existing = [{"id": "fact-1", "slot_key": "user.full_name", "source": "profile",
                     "fact": "Alex Morgan Reyes"}]
        llm_items = [{
            "fact": "Alex M R", "category": "context", "slot_key": "user.full_name",
            "action": "supersedes", "supersedes_id": "fact-1",
        }]
        patches, _ = self._make_extractor_deps(existing, llm_items)

        extractor = mod.MemoryExtractor()
        with patches[0], patches[1] as mock_update, patches[2], patches[3], patches[4]:
            wrote_any = await extractor._extract_and_store_user_facts(
                conversation_text="irrelevant", user_id="u1", session={},
                conversation_id="c1",
            )

        mock_update.assert_not_awaited()
        self.assertFalse(wrote_any, "a profile-locked fact must not be reported as written")

    async def test_supersedes_targeting_a_non_profile_fact_still_updates(self):
        """Regression guard: the new guard must not block legitimate updates
        to facts the LLM itself originally wrote."""
        import core.memory.extractor as mod

        existing = [{"id": "fact-2", "slot_key": "user.occupation", "source": "auto_extracted",
                     "fact": "Backend engineer"}]
        llm_items = [{
            "fact": "Senior backend engineer", "category": "context", "slot_key": "user.occupation",
            "action": "supersedes", "supersedes_id": "fact-2",
        }]
        patches, _ = self._make_extractor_deps(existing, llm_items)

        extractor = mod.MemoryExtractor()
        with patches[0], patches[1] as mock_update, patches[2], patches[3], patches[4]:
            wrote_any = await extractor._extract_and_store_user_facts(
                conversation_text="irrelevant", user_id="u1", session={},
                conversation_id="c1",
            )

        mock_update.assert_awaited_once()
        self.assertEqual(mock_update.call_args.kwargs["fact_id"], "fact-2")
        self.assertTrue(wrote_any)

    async def test_new_branch_profile_lock_guard_still_works(self):
        """Regression guard for the ORIGINAL guard (the 'new' branch, S-10's
        starting point) -- must still block a same-slot 'new' classification
        against a profile-locked fact, unaffected by this change.

        The 'new' branch's singular-slot check queries
        MemoryRepo.get_user_fact_by_slot() directly -- a different call than
        the get_user_facts()-backed existing_by_id dict the 'supersedes'
        branch (tests above) uses -- so it needs its own mock here."""
        import core.memory.extractor as mod

        existing = [{"id": "fact-3", "slot_key": "user.about", "source": "profile",
                     "fact": "Loves hiking"}]
        llm_items = [{
            "fact": "Enjoys the outdoors", "category": "context", "slot_key": "user.about",
            "action": "new", "supersedes_id": None,
        }]
        patches, mock_facts = self._make_extractor_deps(existing, llm_items)

        extractor = mod.MemoryExtractor()
        with patches[0], patches[1] as mock_update, patches[2] as mock_add, patches[3], patches[4], \
             patch.object(mod.MemoryRepo, "get_user_fact_by_slot", new=AsyncMock(return_value=mock_facts[0])):
            wrote_any = await extractor._extract_and_store_user_facts(
                conversation_text="irrelevant", user_id="u1", session={},
                conversation_id="c1",
            )

        mock_update.assert_not_awaited()
        mock_add.assert_not_awaited()
        self.assertFalse(wrote_any)


# ===========================================================================
# Agent-entry "sensitive" self-flagging (core/memory/extractor.py)
# ===========================================================================

class TestAgentEntrySensitiveFlag(unittest.IsolatedAsyncioTestCase):
    """
    A-04 fix (2026-09-14): the old standalone `_extract_agent_entries()`
    (LLM call + parse only, no dedupe) was folded into
    `_extract_and_store_agent_entries()`, which also fetches existing
    entries for the scope and writes directly -- so these tests now mock
    `_call_llm`/`_parse_json_list` plus `MemoryRepo.get_agent_entries` (the
    new existing-entries fetch) and assert against the DB write, the same
    shape TestSupersedesProfileLockGuard above already uses for user facts.
    """

    async def test_extract_agent_entries_parses_sensitive_true(self):
        import core.memory.extractor as mod

        extractor = mod.MemoryExtractor()
        with patch.object(mod.MemoryRepo, "get_agent_entries", new=AsyncMock(return_value=[])), \
             patch.object(mod.MemoryExtractor, "_call_llm", new=AsyncMock(return_value="ignored")), \
             patch.object(mod.MemoryExtractor, "_parse_json_list", new=lambda self, raw, max_items: [
                 {"type": "artifact", "content": "API key stored at X", "tags": ["auth"],
                  "sensitive": True, "action": "new", "supersedes_id": None},
             ]), \
             patch.object(mod.MemoryRepo, "add_agent_entry", new=AsyncMock()) as mock_add:
            await extractor._extract_and_store_agent_entries(
                conversation_text="conv text", session={}, user_id="u1",
                scope_key="proj-1", conversation_id="c1", execution_id="e1",
            )

        mock_add.assert_awaited_once()
        self.assertIs(mock_add.call_args.kwargs["sensitive"], True)

    async def test_extract_agent_entries_defaults_sensitive_false_when_omitted(self):
        import core.memory.extractor as mod

        extractor = mod.MemoryExtractor()
        with patch.object(mod.MemoryRepo, "get_agent_entries", new=AsyncMock(return_value=[])), \
             patch.object(mod.MemoryExtractor, "_call_llm", new=AsyncMock(return_value="ignored")), \
             patch.object(mod.MemoryExtractor, "_parse_json_list", new=lambda self, raw, max_items: [
                 {"type": "note", "content": "some note", "tags": [], "action": "new", "supersedes_id": None},
             ]), \
             patch.object(mod.MemoryRepo, "add_agent_entry", new=AsyncMock()) as mock_add:
            await extractor._extract_and_store_agent_entries(
                conversation_text="conv text", session={}, user_id="u1",
                scope_key="proj-1", conversation_id="c1", execution_id="e1",
            )

        mock_add.assert_awaited_once()
        self.assertIs(mock_add.call_args.kwargs["sensitive"], False)

    async def test_run_passes_sensitive_flag_through_to_add_agent_entry(self):
        """The full wiring, not just the pipeline helper: a sensitive=True
        item from the LLM must reach MemoryRepo.add_agent_entry's own
        sensitive kwarg, not get dropped anywhere in between."""
        import core.memory.extractor as mod
        from core.db.models import Conversation

        extractor = mod.MemoryExtractor()
        mock_conv = MagicMock(spec=Conversation)
        mock_conv.project_id = None
        # MEMORY_MIN_MESSAGES_FOR_EXTRACTION defaults to 3 -- _run() returns
        # early, before ever reaching Scope 3 extraction, if fewer messages
        # are present. Three is the minimum to actually exercise this path.
        user_messages = [
            MagicMock(role="user", content="x" * 50),
            MagicMock(role="assistant", content="y" * 50),
            MagicMock(role="user", content="z" * 50),
        ]

        with patch.object(mod.DBRepo, "list_messages_for_conversation", new=AsyncMock(return_value=user_messages)), \
             patch.object(mod.DBRepo, "get_conversation", new=AsyncMock(return_value=mock_conv)), \
             patch.object(mod.MemoryExtractor, "_has_extraction_signal", return_value=False), \
             patch.object(mod.MemoryRepo, "get_agent_entries", new=AsyncMock(return_value=[])), \
             patch.object(mod.MemoryExtractor, "_call_llm", new=AsyncMock(return_value="ignored")), \
             patch.object(mod.MemoryExtractor, "_parse_json_list", new=lambda self, raw, max_items: [
                 {"type": "error", "content": "leaked token in log", "tags": ["security"],
                  "sensitive": True, "action": "new", "supersedes_id": None},
             ]), \
             patch.object(mod.MemoryRepo, "add_agent_entry", new=AsyncMock()) as mock_add:

            await extractor._run(
                conversation_id="c1", user_id="u1",
                session={"provider": "p", "model": "m", "agent_scope_key": "proj-1",
                         "agent_memory_enabled": True},
                execution_id="e1", execution_mode="plan_execute",
            )

        mock_add.assert_awaited_once()
        self.assertTrue(mock_add.call_args.kwargs["sensitive"])


# ===========================================================================
# Coordinator dynamic per-phase task cap (prompts/coordinator.py)
# ===========================================================================

class TestCoordinatorDynamicCap(unittest.TestCase):

    def test_injects_the_real_configured_cap(self):
        from prompts.coordinator import build_coordinator_plan_system

        text = build_coordinator_plan_system(8, 3)
        self.assertIn("1–8 tasks", text)
        self.assertNotIn("{max_tasks_per_phase}", text, "placeholder must be fully substituted")

    def test_injects_the_real_configured_dynamic_agent_cap(self):
        """A-05: the dynamic-agent cap placeholder must also be substituted,
        independently of the per-phase task cap above."""
        from prompts.coordinator import build_coordinator_plan_system

        text = build_coordinator_plan_system(5, 7)
        self.assertIn("At most 7 distinct invented agent", text)
        self.assertNotIn("{max_dynamic_agents}", text, "placeholder must be fully substituted")

    def test_stale_hardcoded_cap_and_wrong_cross_reference_are_gone(self):
        from prompts.coordinator import build_coordinator_plan_system

        text = build_coordinator_plan_system(5, 3)
        self.assertNotIn(
            "parallel cap is 5 concurrent agents", text,
            "must not conflate coordinate's own per-phase cap with the "
            "separate parallel_delegate tool's MAX_PARALLEL_TASKS setting",
        )

    def test_json_example_braces_survive_the_str_replace(self):
        """The template's own JSON example block must remain intact --
        confirms str.replace() (not .format()) was actually used, since
        .format() would raise on these same literal braces."""
        from prompts.coordinator import build_coordinator_plan_system

        text = build_coordinator_plan_system(5, 3)
        self.assertIn('"goal_summary"', text)
        self.assertIn('"synthesis_instruction"', text)

    def test_coordinate_tool_calls_the_builder_not_the_stale_constant(self):
        """Regression guard for the call-site rename in coordinate_tool.py."""
        import tools.default.coordinate_tool as mod

        self.assertFalse(hasattr(mod, "COORDINATOR_PLAN_SYSTEM"),
                          "the stale static constant must not still be imported")
        self.assertTrue(callable(mod.build_coordinator_plan_system))


# ===========================================================================
# Pure prompt-text regressions -- string presence/absence guards so these
# don't silently drift back to the audited-and-fixed wording.
# ===========================================================================

class TestPromptTextRegressions(unittest.TestCase):

    def test_soul_preamble_no_longer_asserts_universal_preloading(self):
        from prompts.soul import EVA_PLATFORM_PREAMBLE

        self.assertNotIn("all tools are pre-loaded", EVA_PLATFORM_PREAMBLE.lower())
        self.assertIn("describe_tool", EVA_PLATFORM_PREAMBLE)

    def test_project_prompt_names_the_literal_blocked_token(self):
        from prompts.project import PROJECT_ARTIFACT_UPDATE_PROMPT

        self.assertIn("literal word BLOCKED", PROJECT_ARTIFACT_UPDATE_PROMPT)

    def test_planner_tool_hint_no_longer_lists_fictional_tools(self):
        from prompts.planner import PLANNING_SYSTEM_PROMPT, REPLAN_SYSTEM_PROMPT

        for prompt in (PLANNING_SYSTEM_PROMPT, REPLAN_SYSTEM_PROMPT):
            self.assertNotIn("shell_command", prompt)
            self.assertNotIn("code_analysis", prompt)

    def test_memory_slot_vocabulary_lists_reserved_profile_locked_slots(self):
        from prompts.memory import _SLOT_VOCABULARY

        self.assertIn("user.full_name", _SLOT_VOCABULARY)
        self.assertIn("user.about", _SLOT_VOCABULARY)
        self.assertIn("RESERVED", _SLOT_VOCABULARY)

    def test_agent_extraction_schema_documents_sensitive_field(self):
        from prompts.memory import AGENT_EXTRACTION_SYSTEM

        self.assertIn('"sensitive"', AGENT_EXTRACTION_SYSTEM)


# ===========================================================================
# A-04 live-found (2026-09-15) — _parse_json_list truncated at a nested
# array's closing bracket, silently discarding every real extraction result
# ===========================================================================
#
# Root cause: a non-greedy regex (r"\[.*?\]") matched only up to the FIRST
# "]" in the LLM's response. Every extraction schema (both Scope 2 user
# facts and Scope 3 agent entries) requires a "tags": [...] array field, so
# real responses always contain a nested array before the outer array's own
# closing bracket -- the regex matched that inner bracket instead, producing
# truncated/invalid JSON that silently parsed to []. Confirmed live against
# a real Anthropic response before this fix; this test
# reproduces the exact same shape without needing a real LLM call.
#
# Fixed to use find()/rfind() (first "[" to last "]"), mirroring the
# already-correct pattern core/execution/verifier.py's _extract_json() uses
# for the single-object case.

class TestParseJsonListNestedArrayBug(unittest.IsolatedAsyncioTestCase):

    def test_extractor_parses_item_with_nested_tags_array(self):
        import core.memory.extractor as mod

        extractor = mod.MemoryExtractor()
        raw = (
            '```json\n'
            '[\n'
            '  {\n'
            '    "type": "decision",\n'
            '    "content": "PostgreSQL chosen over SQLite for concurrent writes",\n'
            '    "tags": ["zap-cli", "postgresql", "architecture", "concurrency"],\n'
            '    "sensitive": false,\n'
            '    "action": "new",\n'
            '    "supersedes_id": null\n'
            '  }\n'
            ']\n'
            '```'
        )
        items = extractor._parse_json_list(raw, max_items=5)
        self.assertEqual(len(items), 1, f"expected 1 item, got {items!r}")
        self.assertEqual(items[0]["type"], "decision")
        self.assertEqual(
            items[0]["tags"],
            ["zap-cli", "postgresql", "architecture", "concurrency"],
        )

    def test_extractor_parses_multiple_items_each_with_tags(self):
        import core.memory.extractor as mod

        extractor = mod.MemoryExtractor()
        raw = (
            '[\n'
            '  {"type": "error", "content": "first", "tags": ["a", "b"], '
            '"sensitive": false, "action": "new", "supersedes_id": null},\n'
            '  {"type": "success", "content": "second", "tags": ["c"], '
            '"sensitive": false, "action": "new", "supersedes_id": null}\n'
            ']'
        )
        items = extractor._parse_json_list(raw, max_items=5)
        self.assertEqual(len(items), 2, f"expected 2 items, got {items!r}")
        self.assertEqual(items[0]["content"], "first")
        self.assertEqual(items[1]["content"], "second")

    def test_extractor_still_returns_empty_list_for_genuinely_malformed_json(self):
        import core.memory.extractor as mod

        extractor = mod.MemoryExtractor()
        self.assertEqual(extractor._parse_json_list("not json at all", max_items=5), [])
        self.assertEqual(extractor._parse_json_list("[", max_items=5), [])
        self.assertEqual(extractor._parse_json_list("[]", max_items=5), [])

    def test_reconciler_parses_item_with_nested_array_shape_too(self):
        import core.memory.reconciler as rec

        raw = (
            '```json\n'
            '[\n'
            '  {"delete_id": "abc123", "reason": "duplicate of a newer entry", '
            '"related_tags": ["x", "y"]}\n'
            ']\n'
            '```'
        )
        items = rec._parse_json_list(raw, max_items=5)
        self.assertEqual(len(items), 1, f"expected 1 item, got {items!r}")
        self.assertEqual(items[0]["delete_id"], "abc123")


if __name__ == "__main__":
    unittest.main()
