# Plan-Execute-Verify (PEV)

When you send a message, EVA has to decide how to approach it. Simple questions — explanations, lookups, opinions — get a direct answer. Complex tasks that require taking real actions across multiple steps get a different treatment: Plan, Execute, Verify. This is what EVA calls PEV mode.

---

## Why PEV exists

A single conversational response is the right approach for most things. But some tasks are too involved for a direct reply: build this application, refactor these files, research this topic and write a report, set up this environment. These tasks have dependencies between steps, they can partially fail, and the cost of making a wrong assumption early is high.

Most tools handle this the same way — they start running, and they keep running. If step two produces bad output, step three will build on it anyway. Errors compound silently.

PEV addresses this by making EVA plan before it acts and verify what it did after each step. A separate verification pass checks whether each step's output actually achieves its stated goal — not whether it finished without errors, but whether it accomplished what it was supposed to accomplish. When a step fails that check, EVA does not continue. It re-plans the remaining work with the benefit of knowing what went wrong.

---

## How EVA decides which mode to use

In Auto mode, EVA classifies every incoming message through three layers before doing any work.

**Layer 1 — instant heuristics.** The message is tested against a set of patterns at zero cost. Greetings, short questions, factual lookups, "what is X" or "can you explain" messages are immediately handled as conversational. Messages that contain file or code creation, git operations, multi-step imperatives ("and then", "after that"), research-then-write tasks, or deployment instructions are immediately routed to PEV.

**Layer 2 — LLM classifier.** When the message is genuinely ambiguous — not clearly one or the other — EVA makes a lightweight call to your configured provider to classify it. This call costs around 150 tokens. If it fails for any reason, EVA defaults to conversational rather than blocking.

**Layer 3 — session override.** If you have set Agent Mode to Converse or Plan in Session settings or via the Modes button, that takes precedence over both layers above. The classification does not run.

---

## The three phases

### Plan

Once routed to PEV, EVA generates a step-by-step plan before taking any action. The plan appears in the conversation before execution begins — you see the overall goal, each step's title and description, and a hint about which tool will likely be used to complete it.

Plans contain two to six concrete, independently executable steps. The planner writes each step specifically enough to be verifiable after the fact: not "set up the environment" but "install the packages listed in requirements.txt using pip in the project root". The plan is generated using your own configured provider and model — no additional API key is required.

If plan generation itself fails for any reason, EVA falls back to a single-step plan and continues rather than stopping.

Every plan also carries a short list of **success criteria** — one to four checkable statements describing what "done" looks like for the plan as a whole. They aren't shown as steps; EVA uses them at the end to judge whether the finished plan really achieved its goal (see [Closing summary and final check](#closing-summary-and-final-check)).

**Plan approval.** By default, EVA proceeds straight from a generated plan into execution. If you turn on **Plan approval** (Session settings → Output & Safety), EVA pauses after generating the plan and waits for your decision before running any step. You have three options: **Approve** the plan as generated; **edit** any step's title, description, or tool hint and approve your edited version — EVA executes exactly what you submitted, not the original; or **Reject** it. If you give a reason when rejecting, EVA writes a revised plan that takes your feedback into account and asks again — up to two rounds. A rejection with no reason, or one after the revision rounds are used up, cancels the execution. An edited plan is validated before it's accepted — if an edit leaves a step incomplete (an empty title, for example), EVA reports the problem inline so you can fix it without losing your other edits. See [Approvals](../02-conversations/04-approvals.md#plan-approval) for the full walkthrough.

### Execute

Each step in the plan runs as its own agent, with its own reasoning loop. EVA thinks about the step, calls the tools needed to complete it, reads the results, and reasons again — iterating until the step is done or has reached its limit. All tool calls and their results are visible in the conversation stream as they happen.

PEV mode uses the same **progressive disclosure** model as chat mode — tools are activated on demand as EVA reasons through each step, rather than presenting the full tool catalogue at the start. Four orchestration tools are pre-seeded immediately when a PEV execution begins, without requiring a `describe_tool()` call: `coordinate`, `delegate_to_agent`, `parallel_delegate`, and `trigger_daemon`. All other tools remain behind progressive disclosure.

Long-running steps are protected from exhausting the model's context window: EVA automatically trims oversized tool results and compresses older history in the background as a task runs, so a task that generates a lot of intermediate output is far less likely to fail partway through from context exhaustion.

When a step benefits from specialisation or parallelism, EVA can delegate to subagents from within the Execute phase — handing off to a configured specialist agent, spawning a dynamic one for the task at hand, or running multiple subagents concurrently. See [Subagents](../08-subagents/01-what-are-subagents.md) for a full account of how delegation works.

When the `coordinate` orchestration tool is used, it appears in the stream as a single collapsed summary line — for example, `Coordinated 2 phases · 5/6` — rather than a bordered card. Click it to expand the full phase breakdown: each phase with its execution strategy (parallel or sequential), each agent's result with a success or failure indicator, and the coordinator's final synthesis. Failed agents within a phase are surfaced directly in the breakdown rather than buried in logs.

If a step triggers a tool action that requires your approval, EVA pauses and waits for your decision before continuing. The task does not stop — it resumes from the approval point once you respond.

Each step is told what the earlier steps actually did — which tools ran and whether they succeeded — so later steps build on real results rather than starting blind.

**Skipping work that's already done.** Sometimes a step accomplishes more than its own description asked for — fixing a bug while investigating it, say — and a later step in the plan becomes redundant. When the verifier sees clear evidence, from real tool calls, that one or more of the *next* steps are already satisfied, EVA skips them rather than running them again. It is deliberately conservative: it only looks ahead a couple of steps at most, only after the current step has passed verification, and it never skips the plan's final step, which is needed to write the closing summary. Skipped steps appear in the plan trace struck through and labelled *skipped* — never shown as completed work that didn't happen. This is on by default and saves time and cost on plans with overlapping steps. If you'd rather every step always run, turn off **Skip completed steps** in Session settings → Output & Safety.

**Waiting instead of blocking.** A task doesn't have to sit idle while something slow finishes. EVA can start a background process or a background subagent, then pause the whole task and resume automatically when a timer elapses or the background work finishes. Paused tasks show up in the Activity tab, where you can resume or cancel them yourself. See [Background work](../03-tools/01-what-are-tools.md#background-work) and [Activity and the Preview pane](../02-conversations/07-activity-and-preview.md).

**Step limits.** Each step's reasoning loop is bounded by a step limit. If EVA reaches it mid-task, it first attempts one best-effort answer using whatever it has gathered so far. If that isn't sufficient and the feature is enabled, a banner appears asking whether to continue: *Step limit reached (6/6). Continue for 2 more?* Choosing **Continue** gives EVA a fresh allotment of steps and resumes exactly where it left off; **Stop** ends the task cleanly with whatever was completed. The number of extensions available is controlled by **Step extensions** in Session settings (default 2 — set to 0 to disable the gate and always stop at the limit). If you don't respond, EVA stops automatically after an hour rather than waiting indefinitely.

### Verify

After each step completes, EVA runs a separate verification pass. It reads the step's specification alongside the output that was just produced, the record of which tools the step actually ran (and whether they succeeded), and what earlier steps did, and makes a binary judgement: pass or fail.

The verifier judges whether the step's **goal** was achieved, not whether the output repeats the step's wording. A step isn't failed because it took a different route or phrased its result differently, as long as the real-world outcome is right. But a claim isn't enough: a step passes only when there is evidence — a real, successful tool call in the record — that the work happened. Partial completion counts as a failure. An error counts as a failure. Empty output counts as a failure. A step that makes no tool calls of its own can still pass if an earlier step's record shows the work was already done.

When a step fails, the verifier names the concrete reason — the actual error, the missing output, the wrong result — and that reason is what the re-planner works from.

If verification itself cannot run — a provider error or an unreadable response — the step is *not* marked as verified, and EVA never assumes it passed. But it isn't treated as a failed step either, because the step itself did nothing wrong: EVA records a warning and moves on to the next step instead of spending a re-plan on a problem that wasn't the step's fault.

### Closing summary and final check

The final step of every plan writes a closing summary for you: what was accomplished, in relation to the plan's goal, based only on what was actually observed. You still see each step's output stream live while the task runs, but the saved answer is that closing summary — not a stitched-together transcript of every step.

After the last step, EVA runs one more independent check on the plan as a whole, comparing the evidence in the step records against the plan's success criteria. It is deliberately separate from the closing summary, which it treats as a claim rather than as proof. The verdict is one of three:

- **Success** — the criteria were met. The closing summary stands as written.
- **Pending** — the evidence doesn't settle it either way.
- **Not successful** — the evidence shows a criterion wasn't met.

For *pending* and *not successful*, EVA adds a short note after a divider, directly beneath the closing summary, explaining what it couldn't confirm or what fell short. It doesn't retry automatically — PEV already re-plans when steps fail, and a second retry loop on top could spin. You see the honest verdict and decide what to do next. If the check itself can't run, the result is reported as *pending*, never as a success.

---

## Adaptive re-planning

When a step fails verification, EVA does not stop the task. It calls the planner again — this time with complete context: the original goal, which steps completed, what the failed step was supposed to do, what it actually produced, and what the verifier identified as the problem.

The re-planner generates a revised set of remaining steps from the failure point forward. It may retry the step with a different approach, work around what failed, or restructure the remaining work based on what the failure revealed. If re-planning itself fails, EVA substitutes a single retry step and continues.

A re-plan can't quietly drop work: if the revised plan leaves out steps that were still to come, EVA restores them after the retry. And a step that failed but was then resolved by a re-plan doesn't count against the run — the final tally counts only failures that were never recovered from, while the step's own history still shows it failed.

This cycle can repeat for each subsequent step failure, up to a configured limit. The overall task does not stop on the first problem — it adapts. If the AI provider itself fails, so that a step can't run at all, the run ends as failed rather than continuing without it.

---

## What you see during a PEV run

A status bar appears above the conversation while EVA is working, showing the current step number, total steps, and step title, plus elapsed time and token count at the right edge. It's deliberately understated — plain muted text rather than colour-coded blocks — so it doesn't compete with the actual response for attention. It fades out a few seconds after the task completes.

Alongside it, the conversation itself gains a live plan trace: a single collapsed line that updates as the task progresses — `Planning…`, then `Step 2/3: Write unit tests · verifying…`, and finally a one-line summary once the whole plan finishes, such as `Completed plan · 3/3 steps · 1 skipped · 1 replan` (or `Plan cancelled` if you rejected it). The count of failed steps includes only failures that were never recovered from. Click it to expand the full trace: the original plan's goal and steps, and — per step — its verification result and, if it failed, the reason it was re-planned. This trace persists in the conversation after the task completes and after a reload, so you can always come back and see exactly what happened and why, without digging through raw logs.

---

## Controlling the mode

**Modes button** — in the conversation header, the Modes button lets you switch between Auto, Converse, and Plan for the current conversation without opening Session settings. This change is immediate and applies from your next message.

**Session settings** — the Agent Mode card sets the default for all new conversations. Auto is the recommended default for most users. Force Plan if you consistently want structured execution regardless of message phrasing. Force Converse if you want EVA to respond without taking autonomous action.

Note that PEV takes longer than a conversational response — each step involves one or more LLM calls, and the verify pass adds to that. For quick questions, explanations, or anything where you want an immediate answer, Converse mode removes that overhead.

---

## Stopping mid-task

Clicking Stop during a PEV run interrupts EVA genuinely mid-execution — including a tool call already in progress. A running shell command is killed rather than left to finish; an in-flight web request or other tool call is cancelled the same way. This typically takes effect within a few seconds.

The Stop button shows a **Stopping…** state while EVA confirms the interruption, rather than disappearing immediately. Once confirmed, a *Cancelled by user.* note appears in place of a response. The conversation records the state at the point of cancellation, including which steps completed, which failed, and which did not run — along with the tokens actually spent up to that point.

---

> **Next:** [What are Projects?](../05-projects/01-what-are-projects.md) — organising related conversations and work into a persistent context.
