# Chat Mode vs Plan Mode

EVA uses two distinct strategies to handle your requests. The one it chooses has a meaningful effect on how reliably it handles complex work — and how quickly it responds.

---

## Chat mode

For straightforward requests — questions, lookups, quick tasks, single-step work — EVA reasons through your message and responds in one pass. Tools are available in chat mode and EVA can call them, but it commits to a direction immediately rather than planning ahead first.

Chat mode is fast. For the majority of everyday interactions, it is the right approach.

The `delegate_to_agent` tool is available in chat mode — EVA can hand off a sub-task to a named subagent and incorporate its result without switching to Plan mode. Multi-agent orchestration tools (`parallel_delegate`, `coordinate`) remain Plan mode only.

---

## Plan mode

For complex, multi-step work, EVA uses a structured approach called Plan-Execute-Verify (PEV). It generates an explicit numbered plan, executes each step with tools, then verifies whether the result meets the original goal. If something failed or was incomplete, it replans and continues.

The additional structure costs time, but it produces substantially more reliable results on work where a wrong early step would undermine everything that follows. Research-then-write tasks, code that needs to compile and pass tests, multi-file operations where order matters — these are where Plan mode earns its overhead.

For a full breakdown of the three phases and how adaptive re-planning works, see [Plan-Execute-Verify](../04-plan-execute-verify/01-what-is-pev.md).

---

## How EVA decides

When Agent Mode is set to **Auto** (the default), EVA classifies every incoming message and routes it to the appropriate strategy.

Single-intent messages — questions, greetings, requests that describe a single clear output — go to chat mode. Messages that describe multi-stage goals, reference multiple outputs, or contain signals like "research and then write", "find all X and do Y for each", or "read this and fix what's wrong" route to plan mode. For genuinely ambiguous cases, a lightweight classifier makes the call.

| Message | Mode | Reason |
|---------|------|--------|
| "What's the capital of France?" | Chat | Single question |
| "Summarise this document" | Chat | Single-pass task |
| "Research the top 3 cloud providers and write a comparison" | Plan | Two distinct stages |
| "Find all TODO comments in the codebase and open a GitHub issue for each" | Plan | Multi-step with external actions |
| "Read the error logs and fix the bug" | Plan | Diagnose, then implement |

---

## Overriding the mode

Auto routing works well, but there are times you want direct control. EVA can be locked to either mode — from the Modes button in the conversation header, or in Session settings under Agent Mode. The change takes effect on your next message and can be switched back at any time.

---

## While EVA is working

Regardless of mode, EVA works on one task at a time within a conversation. Sending a new message is not possible while a task is running. If EVA is heading in the wrong direction or you want to redirect, you can cancel the current task at any point — whatever output EVA produced up to that point stays in the conversation — and then send a new message.

---

> **Next:** [Approvals](04-approvals.md) — when EVA pauses for permission and how to configure it.
