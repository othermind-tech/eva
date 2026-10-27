---
name: web-search-methodology
description: "Use when a task requires structured web research beyond a single search
  query: competitive analysis, technical background reading, fact-checking across
  multiple sources, or any question where source quality and recency matter. Triggers
  on phrases like 'research', 'find out', 'what is the current state of', 'compare',
  'look up multiple sources', or when the user expects a cited answer."
license: MIT
allowed-tools: [web_search, write_file]
compatibility: "Requires web_search tool access."
---

## Web Search Methodology

You are performing structured web research. Follow this methodology to produce
accurate, well-sourced, reproducible results.

---

### Step 1 — Decompose the Question

Before searching, break the research question into independent sub-questions.

**Example:** "What are the best vector databases for production use in 2025?"
Becomes:
1. What vector databases exist that are production-ready?
2. What are the performance benchmarks for each?
3. What are the licensing and cost models?
4. What do practitioners say in recent discussions (Reddit, HN, blogs)?

State your sub-questions explicitly before running any searches.

---

### Step 2 — Search Strategy

**Query construction:**
- Start broad, then narrow. First query establishes the landscape.
- Use site: operators for authoritative sources: `site:docs.example.com feature X`
- Use date filters for time-sensitive topics: `after:2024-01-01`
- Try multiple phrasings of the same question — different phrasings surface
  different sources.

**Source prioritisation (highest to lowest):**
1. Official documentation / primary sources
2. Peer-reviewed papers, technical reports
3. Engineering blog posts from known organisations
4. Reputable tech news (not opinion aggregators)
5. Community discussions (Reddit, HN, Stack Overflow) — useful for sentiment
   and real-world experience, not for facts

**Minimum searches:** Run at least 3 independent searches for any non-trivial
question. Single-source answers are not research.

---

### Step 3 — Evaluate Sources

For each source, note:
- **Recency:** When was it published? Is it still current?
- **Authority:** Who wrote it? Do they have relevant expertise?
- **Bias:** Does the source have a commercial interest in the answer?
- **Corroboration:** Is this claim supported by other independent sources?

Red flags:
- No publication date
- Author with no credentials in the domain
- Content that only appears on one site
- Commercial content presented as neutral analysis

---

### Step 4 — Cross-Reference Conflicting Information

When sources disagree:
- Note both positions explicitly — do NOT silently favour one
- Check if the conflict is about different versions, dates, or use cases
- If the conflict cannot be resolved, report it as an open question

---

### Step 5 — Synthesise and Write

Structure your output:
1. **Direct answer** — one paragraph answering the original question
2. **Detail sections** — one section per sub-question
3. **Caveats** — what might change, what you could not verify
4. **Sources** — numbered list at the end

**Citation format:**
```
## Sources
[1] Title of Page — https://example.com/page — accessed 2025-05-06
[2] ...
```

Reference citations inline: "According to the official docs [1], ..."

---

### Step 6 — Confidence Assessment

End your report with a confidence rating:
- **High** — Multiple independent authoritative sources agree
- **Medium** — Reasonable sources agree but some uncertainty remains
- **Low** — Limited sources, conflicting information, or rapidly evolving topic

State the reason for your confidence rating.
