# prompts/__init__.py
#
# Central location for all LLM prompt templates.
# Keeping prompts here (rather than inlined in engine files) makes them
# easy to tune without touching business logic.
#
# Exports:
#   from prompts.planner  import PLANNING_SYSTEM_PROMPT, PLANNING_USER_TEMPLATE
#   from prompts.verifier import VERIFIER_SYSTEM_PROMPT, VERIFIER_USER_TEMPLATE
#   from prompts.memory   import SUMMARY_SYSTEM_PROMPT
#   from prompts.memory   import USER_EXTRACTION_SYSTEM, AGENT_EXTRACTION_SYSTEM
#   from prompts.coordinator import build_coordinator_plan_system, COORDINATOR_PLAN_USER_TEMPLATE
#   from prompts.coordinator import COORDINATOR_SYNTHESIS_SYSTEM, COORDINATOR_SYNTHESIS_USER_TEMPLATE
