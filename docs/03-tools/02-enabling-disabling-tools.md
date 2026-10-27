# Controlling Tool Access

The Tools section of Session settings gives you a view of every tool available to EVA — its name, authority level, source, and current status. It is also where you control what EVA is permitted to do.

---

## The tool browser

The browser organises tools into three groups: **Default**, **Custom**, and **MCP**. A search bar filters across all three groups by name or description.

Clicking any tool name expands its description. Each tool also shows its authority badge and an **Auth Blocked** indicator if it has been excluded by the authority ceiling.

---

## What you can and cannot control

Default tools — the built-in tools that ship with EVA — are **always active**. They show an **Always On** status and have no individual toggle. There is no way to disable a specific default tool; the only mechanism for restricting them is the Allowed Authority ceiling described below.

**Custom tools**, if you have registered any, show an individual **Allowed / Disabled** toggle. Disabling a custom tool removes it from the active tool set — the LLM has no knowledge it exists and cannot call it.

**MCP tools** from connected external servers follow their parent server's enabled state. If a server is disabled, all its tools are listed as disabled and cannot be re-enabled at the individual tool level.

---

## Allowed Authority — restricting default tools

The **Allowed Authority** card in the Behaviour section of Session settings is the primary control for restricting what default tools can do. It acts as a session-wide ceiling: any tool whose authority level falls outside the allowed set is blocked from running and shows an **Auth Blocked** badge in the browser.

Three settings are available: **Read** (read-only tools only), **Mutating** (read-only and mutating), and **All** (no authority restriction). This ceiling applies across all tool sources — default, custom, and MCP alike.

This is a hard restriction. A tool that is authority-blocked cannot run regardless of approval settings. If the ceiling is set to Read and you approve a tool call from EVA, it still will not execute if the tool is mutating.

---

## Approval vs blocking — the distinction

These controls work at different layers and serve different purposes.

Setting the Allowed Authority ceiling **removes a class of tools entirely** — the LLM does not know they exist and cannot attempt to call them.

The Tool Approval setting **keeps tools available** but gates each use on your confirmation. EVA can call the tool, but pauses first and shows you exactly what it plans to do.

Use the authority ceiling when you want a categorical guarantee — no shell commands in this session, no file writes, no destructive operations. Use approval when you want access to a capability but with explicit oversight over each individual use.

---

> **Next:** [What is Plan-Execute-Verify?](../04-plan-execute-verify/01-what-is-pev.md) — how EVA structures complex multi-step tasks.
