# EVA Documentation — Master Index

**Version:** 1.0 (Electron App Release)  
**Last updated:** October 2026  
**Status key:** ✅ Complete · 🔧 Draft · 📋 Planned

---

## Introduction

| # | Document | Status |
|---|----------|--------|
| 1 | [Getting Started](01-introduction/01-getting-started.md) | ✅ |
| 2 | [What is EVA?](01-introduction/02-what-is-eva.md) | ✅ |
| 3 | [Key Concepts Glossary](01-introduction/03-key-concepts-glossary.md) | ✅ |

## Conversations & Chat

| # | Document | Status |
|---|----------|--------|
| 1 | [Conversations](02-conversations/01-conversations.md) | ✅ |
| 2 | [Context & Attachments](02-conversations/02-context-and-attachments.md) | ✅ |
| 3 | [Chat Mode vs Plan Mode](02-conversations/03-chat-mode-vs-plan-mode.md) | ✅ |
| 4 | [Approvals](02-conversations/04-approvals.md) | ✅ |
| 5 | [Conversation History](02-conversations/05-conversation-history.md) | ✅ |
| 6 | [Long Conversations](02-conversations/06-long-conversations.md) | ✅ |
| 7 | [Activity and the Preview Pane](02-conversations/07-activity-and-preview.md) | ✅ |

## Tools

| # | Document | Status |
|---|----------|--------|
| 1 | [What are Tools?](03-tools/01-what-are-tools.md) | ✅ |
| 2 | [Controlling Tool Access](03-tools/02-enabling-disabling-tools.md) | ✅ |

## Plan-Execute-Verify

| # | Document | Status |
|---|----------|--------|
| 1 | [Plan-Execute-Verify (PEV)](04-plan-execute-verify/01-what-is-pev.md) | ✅ |

## Projects

| # | Document | Status |
|---|----------|--------|
| 1 | [What are Projects?](05-projects/01-what-are-projects.md) | ✅ |
| 2 | [Creating and Managing a Project](05-projects/02-creating-a-project.md) | ✅ |

## Memory

| # | Document | Status |
|---|----------|--------|
| 1 | [How EVA Remembers You](06-memory/01-how-eva-remembers-you.md) | ✅ |
| 2 | [What EVA Knows About You](06-memory/02-what-eva-knows.md) | ✅ |
| 3 | [Memory & Privacy](06-memory/03-memory-and-privacy.md) | ✅ |

## Knowledge Base (RAG)

| # | Document | Status |
|---|----------|--------|
| 1 | [Knowledge Base (Document Search)](07-rag/01-knowledge-base.md) | ✅ |

## Subagents

| # | Document | Status |
|---|----------|--------|
| 1 | [What are Subagents?](08-subagents/01-what-are-subagents.md) | ✅ |
| 2 | [Built-in Agents](08-subagents/02-built-in-agents.md) | ✅ |
| 3 | [Creating Custom Agents](08-subagents/03-creating-custom-agents.md) | ✅ |

## Daemons (Background Agents)

| # | Document | Status |
|---|----------|--------|
| 1 | [What are Daemons?](09-daemons/01-what-are-daemons.md) | ✅ |

## Skills

| # | Document | Status |
|---|----------|--------|
| 1 | [What are Skills?](10-skills/01-what-are-skills.md) | ✅ |
| 2 | [Managing Your Skill Library](10-skills/02-managing-your-skill-library.md) | ✅ |

## Hooks

| # | Document | Status |
|---|----------|--------|
| 1 | [What are Hooks?](11-hooks/01-what-are-hooks.md) | ✅ |

## MCP Servers

| # | Document | Status |
|---|----------|--------|
| 1 | [What are MCP Servers?](12-mcp/01-what-are-mcp-servers.md) | ✅ |

## Providers & Models

| # | Document | Status |
|---|----------|--------|
| 1 | [Providers & Models](13-providers/01-configuring-providers.md) | ✅ |

## Analytics

| # | Document | Status |
|---|----------|--------|
| 1 | [Usage and Costs](14-analytics/01-usage-and-costs.md) | ✅ |

## Configuration Reference

| # | Document | Status |
|---|----------|--------|
| 1 | [Configuration Reference](15-configuration/01-configuration-reference.md) | ✅ |

---

## Appendix

| — | [EVA vs Competitors](16-appendix/01-eva-vs-competitors.md) | ✅ |
| — | [Privacy & Security](16-appendix/02-privacy-and-security.md) | ✅ |
| — | [Technology Stack](16-appendix/03-technology-stack.md) | ✅ |
| — | [Open Source](16-appendix/04-open-source.md) | ✅ |

---

## How to Use This Documentation

- **First time?** Start at [Getting Started](01-introduction/01-getting-started.md) and follow the **Next →** links through each section.
- **Looking for a feature?** Jump directly to the relevant section above.
- **Comparing EVA to other tools?** See the [Appendix](16-appendix/01-eva-vs-competitors.md).
- **Want to know the licence, contribute, or see what support to expect?** See [Open Source](16-appendix/04-open-source.md).
- All docs follow the same structure: *What is it → When to use it → How to set it up → Example*.

---

## Adding a New Page

1. Create the `.md` file in the appropriate numbered folder (e.g. `02-conversations/08-new-topic.md`).
2. Add an entry to [`manifest.json`](manifest.json) in the matching section — `id` is the file path without the `.md` extension, `title` is the display name shown in the sidebar.

That's all. No frontend code changes are needed. The file is served automatically; the manifest entry makes it appear in the sidebar and prev/next navigation.
