# Usage & Costs

EVA tracks every token it sends and receives, every model call it makes, and the estimated cost of each. The Analytics panel gives you a live view of that data — filterable by time range and provider — so you always know where your spend is going.

Open the **Analytics panel** from the sidebar (the bar chart icon).

---

## Time range

The time range filter controls which executions are included in all numbers shown on the page. Five options are available, selectable from the pill buttons in the panel header:

| Option | What it covers |
|--------|---------------|
| **7d** | The last 7 days ending today |
| **30d** | The last 30 days ending today |
| **90d** | The last 90 days ending today |
| **All** | Every execution since your account was created |
| **Custom** | A specific date range you define |

For **Custom**, a date row appears below the header with From and To date pickers. Set both dates and click **Apply** — all figures update to reflect that window.

---

## Provider filter

Next to the range pills is a provider dropdown — **All providers** by default. Selecting a specific provider narrows every number on the page (stat cards, token breakdown, provider breakdown) to executions that used that provider only. Useful for comparing spend across providers or auditing one provider's usage in isolation.

---

## Summary stats

Four stat cards across the top of the panel give you the headline numbers for the selected range and provider filter.

**Total Tokens** — every token consumed across all executions in the window. This is the sum of prompt (input) tokens and completion (output) tokens. For a task that makes several model calls — a tool-using run, a plan with several steps — the figure adds up *every* call, not just the last one, so expect larger and more accurate numbers than a single reply suggests. Large counts are abbreviated — thousands as K, millions as M (e.g. `4.2M`).

**Total Cost** — estimated spend in USD for the selected window. Formatted to four decimal places (e.g. `$0.0024`) to show meaningful values even for small amounts. Each run is priced on the model that actually answered it — which matters if a fallback provider took over — and uses the discounted rate for tokens served from the provider's prompt cache. Displays `–` when cost data is unavailable for the period — this happens for local Ollama inference (which has no cost) or when a provider doesn't return usage data. If only *some* runs in the window have a known price (for example a subscription service or an unpriced model), the total carries a trailing `+` — `$0.0021+` — and hovering explains that it's a partial sum. A missing price means "unknown", never "free".

**Executions** — the number of agent runs included in the window. One execution corresponds to one time you sent a message and EVA ran its agent loop. A long conversation with many back-and-forth turns will have many executions.

**Avg / Exec** — the average token count per execution. Useful for understanding whether your workload is lightweight (quick Q&A) or heavy (long documents, multi-step tool use). Displayed as `–` if no executions exist in the selected window.

---

## Token breakdown

Below the stat cards, the **Token breakdown** section shows how total tokens split between prompt (input) and completion (output) using a two-colour bar:

- **Teal** — prompt tokens. Everything you send to the model: your message, EVA's system prompt, tool schemas, memory context, conversation history, and RAG results.
- **Purple** — completion tokens. Everything the model generates back: its reasoning, tool call arguments, and final response text. Reasoning tokens are part of this figure, not an extra amount on top.

The exact counts for each are shown below the bar. In a typical EVA session, prompt tokens are usually the larger portion because the context window carries a lot of scaffolding.

Below the counts, a **Cached** line shows how much of the prompt was served from the provider's prompt cache (for example *Cached 4.6K of prompt (49%)*). Cached tokens are billed at a discount, and they are a *subset* of the prompt — not added to it. Runs from before this was tracked show no cached figure. A per-execution footer under a reply shows the same detail (for example "416 cached").

---

## By provider · model

The lower section breaks usage down by every distinct (provider, model) combination that ran during the selected window. Each card shows:

**Model name** — the specific model used (e.g. `claude-3-5-sonnet-20241022`).

**Provider · runs** — the provider name and how many executions used this combination.

**Cost** — estimated spend for this provider/model pair. Shown in green when data is available. Displayed as `–` when pricing data was unavailable at execution time (e.g. a model not yet in EVA's pricing database, or local Ollama inference).

**Total tokens** — token count for this provider/model pair. Large numbers are abbreviated: values in the thousands show as K (e.g. `142.3K`), values in the millions show as M (e.g. `1.24M`).

**Token split bar** — a mini version of the main breakdown bar, showing the prompt vs completion ratio for this specific provider/model. The exact input (`in: X`) and output (`out: X`) counts appear below it.

At the top of this section, a summary line shows the total number of entries and the grand total cost across all rows (e.g. `4 entries · $0.0341 total`).

If a provider filter is active, only cards matching that provider appear.

---

## Cost accuracy

The cost figures shown in EVA are **estimates**, not billing statements. They are calculated from LiteLLM's public pricing data at the time each execution ran. Older executions keep the numbers they were recorded with; the improved accounting applies to runs from this version on.

A note at the bottom of the panel reads: *"Cost estimates are informational only. Actual billing depends on your provider contracts."*

Factors that can cause a discrepancy between EVA's estimates and your provider invoice:

- **Pricing changes** — providers update their per-token rates; EVA's data refreshes periodically but may lag briefly.
- **Tiered or negotiated pricing** — enterprise or volume-discount contracts have rates different from the public pricing EVA uses.
- **Features with separate billing** — some providers charge separately for certain features (image processing, extended context, priority queues). These may not be captured in token-level pricing.
- **Local inference** — Ollama Local always shows `–` for cost because no billing occurs.

Use the Analytics panel to understand your usage patterns and relative spend across providers. For authoritative billing figures, always refer to your provider's dashboard.

---

## Refresh

The **Refresh** button (top-right of the panel) re-fetches both the summary stats and the provider breakdown from the backend. Data is cached for 60 seconds; use Refresh if you've just sent a message and want the latest counts immediately.

---

> **Next:** [Configuration Reference](../15-configuration/01-configuration-reference.md) — every `eva.env` setting and what it controls.
