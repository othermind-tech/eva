---
name: playwright-browser-testing
description: "Use when you need to see a web page running in a real browser: testing or debugging a web app or site you are building or fixing, reproducing a UI bug, reading browser console errors or failed network requests, or verifying a fix after editing code. Drives the playwright__browser_* tools (the Playwright MCP, which must be enabled) together with background_process to run the dev server. Triggers on: browser, console errors, localhost, dev server, web app, webpage, website, blank page, page not loading, frontend, UI bug."
license: MIT
allowed-tools: [background_process, read_file, edit_file, write_file, find_files, playwright__browser_navigate, playwright__browser_snapshot, playwright__browser_find, playwright__browser_click, playwright__browser_type, playwright__browser_console_messages, playwright__browser_network_requests, playwright__browser_network_request, playwright__browser_wait_for, playwright__browser_evaluate, playwright__browser_tabs, playwright__browser_take_screenshot]
compatibility: "Requires the playwright MCP to be enabled (tools named playwright__browser_*), Node.js, and Microsoft Edge (Windows) or Google Chrome."
trigger:
  type: keyword
  keywords: [browser, playwright, console, localhost, dev server, web app, webapp, web page, webpage, page, pages, site, website, frontend, front-end, ui, html, css, javascript, react, vite, blank, not loading]
---

## Playwright browser testing

Use this to look at a web page the way a user would, find what is broken, fix the
code, and prove the fix. Every claim you make about the page must come from a tool
result you read after a fresh page load, not from assumption.

### Before you start

- The tools are named `playwright__browser_*`. If they are not in your tool list or
  manifest, the Playwright MCP is not enabled: tell the user to enable it in the MCP
  panel. Do not work around it with `http_get` (it blocks localhost).
- If your tool instructions say so, call `describe_tool` for a tool before its first use.
- You cannot see images. Screenshots are saved as files you cannot view. Work from
  text: the page snapshot, console messages and network requests.

### The loop

1. **Serve the app** with `background_process` (`action="start"`) -- the project's dev
   command, or `python -m http.server <port> --bind 127.0.0.1` for plain static files.
   Never use the `shell` tool for a server: it waits for the command to exit and hangs.
   Keep the `process_id`. To wait until the server is ready, use
   `background_process` `action="tail"` with `wait_seconds` (for example 10) rather than
   polling.
2. **Open the page**: `playwright__browser_navigate` with `{"url": "http://127.0.0.1:<port>/"}`.
3. **Read the console errors**: `playwright__browser_console_messages` with
   `{"level": "error"}` and nothing else.
   - Do not pass `filename`: that writes the messages to a file instead of returning them.
   - Do not pass `all` after you have made a fix: it returns every message since the
     session began, including errors you already fixed, and you will think the fix failed.
   - Each message ends with `@ <url>:<line>` -- that points at the source.
4. **Check failed requests**: `playwright__browser_network_requests` (successful static
   files are hidden by default; `{"static": true}` shows them; `{"filter": "/api/"}` narrows
   by URL regex). `playwright__browser_network_request` with `{"index": N}` returns the
   headers and body of one request.
5. **Look at the page**: `playwright__browser_snapshot` returns the page as text with
   element refs such as `[ref=e3]`. `playwright__browser_find` with `{"text": "Add"}` finds a
   ref more cheaply.
6. **Interact**: `playwright__browser_click` with `{"target": "e3"}` -- `target` is the ref
   from the latest snapshot (or a unique selector); there is no `ref` parameter.
   `playwright__browser_type` takes `target`, `text` and optional `submit`.
   `playwright__browser_wait_for` takes `text`, `textGone` or `time` (seconds).
   Take a fresh snapshot after the page changes; old refs go stale.
7. **Fix the source** with `edit_file` (small, targeted edits), then **navigate to the same
   URL again** and repeat step 3 without `all`. A clean console after a fresh navigation,
   plus a snapshot showing the expected page, is your proof.
8. **Clean up**: `background_process` `action="stop"` with the `process_id`, unless the user
   should keep the app running -- in that case tell them the URL.

### Details that trip models up

- `playwright__browser_tabs` takes `action` of `list`, `new`, `close` or `select` (plus
  `index` and `url`). There is no `create`.
- `playwright__browser_evaluate` takes `function`, for example `"() => document.title"`.
  Use it to read page state the snapshot does not show.
  `playwright__browser_run_code_unsafe` runs arbitrary code: last resort only.
- A page that pops an alert or confirm blocks everything until you call
  `playwright__browser_handle_dialog` with `{"accept": true}`.
- Other useful ones: `playwright__browser_press_key` (`key`),
  `playwright__browser_select_option` (`target`, `values`),
  `playwright__browser_fill_form` (`fields`), `playwright__browser_navigate_back`.
- Tool results that mention a file (console logs, screenshots) use absolute paths; open
  them with `read_file`.
- If the same call fails twice with the same error, change approach (re-snapshot, use a
  different target) instead of repeating it.

### Guardrails

- Only browse to the app you are testing (`localhost` / `127.0.0.1`) or to URLs the user
  gave you. For general research use `web_search` or `http_get`.
- Never type real credentials or personal data into a page.
- Fix the cause, not the symptom: do not wrap code in try/catch just to silence a console
  error.
