"""
What the pinned Playwright MCP server (default_mcps/playwright.yaml) actually
exposes, captured from a real `tools/list` against @playwright/mcp 0.0.82.

Shared by tests/test_prompt_tool_references_unit.py (offline: keeps
skills/playwright-browser-testing/SKILL.md honest) and
tests/test_playwright_mcp_live.py (against a live server: proves this list still
matches what the server really returns).

WHEN YOU BUMP THE PINNED VERSION in default_mcps/playwright.yaml: re-capture this
list (the live script prints any difference), then re-read the skill against the
new tool descriptions before updating PINNED_VERSION.
"""

PINNED_VERSION = "0.0.82"

# Bare tool names as the server reports them; EVA exposes each as
# "playwright__<name>" (see core/mcp/naming.py).
TOOL_NAMES = frozenset({
    "browser_close", "browser_resize", "browser_console_messages",
    "browser_handle_dialog", "browser_emulate_media", "browser_evaluate",
    "browser_file_upload", "browser_drop", "browser_find", "browser_fill_form",
    "browser_press_key", "browser_type", "browser_navigate",
    "browser_navigate_back", "browser_network_requests",
    "browser_network_request", "browser_run_code_unsafe",
    "browser_take_screenshot", "browser_snapshot", "browser_click",
    "browser_drag", "browser_hover", "browser_select_option", "browser_tabs",
    "browser_wait_for",
})

# Parameter names, for the tools the skill gives worked examples for.
PARAMS = {
    "browser_console_messages": {"level", "all", "filename"},
    "browser_navigate": {"url"},
    "browser_click": {"element", "target", "doubleClick", "button", "modifiers"},
    "browser_type": {"element", "target", "text", "submit", "slowly"},
    "browser_find": {"text", "regex"},
    "browser_network_requests": {"static", "filter", "filename"},
    "browser_network_request": {"index", "part", "filename"},
    "browser_snapshot": {"target", "filename", "depth", "boxes"},
    "browser_wait_for": {"time", "text", "textGone"},
    "browser_tabs": {"action", "index", "url"},
    "browser_evaluate": {"element", "target", "function", "filename"},
    "browser_handle_dialog": {"accept", "promptText"},
}
