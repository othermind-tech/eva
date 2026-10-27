# Context & Attachments

EVA can work with more than the text of your messages. Documents, images, and references to past work can all be shared with EVA and treated as active context — not just uploaded and forgotten, but available as working knowledge throughout the conversation.

Everything attached to a conversation is available for every message that follows, not just the next one. Attach a document once and you can ask questions about it, cross-reference it, and ask EVA to act on it across an entire working session.

---

## Documents

EVA reads documents you share and reasons over their full contents. Supported formats include PDF, DOCX, plain text, Markdown, CSV, JSON, YAML, XML, and most code file types.

What happens after you attach a document is determined automatically based on its size — and this is where EVA does something most tools do not.

**Short documents** (under roughly 24,000 words / ~90 pages) are injected directly and in full into the conversation. EVA works from the complete text without any chunking or summarisation — the entire document is present in context and available for every question you ask.

**Long documents** are automatically indexed into your local knowledge base. EVA chunks and vector-indexes the content on your machine and retrieves the relevant passages in response to each question, rather than holding the whole file in memory. There is no manual setup required and no separate tool to configure — the routing happens at the moment you attach the file.

This is not a limitation of large files — it is the correct behaviour for each case. A short document benefits from being read in full; a long document benefits from semantic search. EVA makes that call for you.

> *A contract that fits in context is attached and read completely — EVA can reason about any clause, not just the ones a search returned. A 400-page specification is indexed — EVA retrieves the sections relevant to your question, accurately and without you having to specify where to look.*

**Password-protected PDFs.** EVA can't read a PDF that needs a password to open, and it tells you so instead of attaching an empty document: *"This PDF is locked. Remove the password and upload it again."* A PDF that is merely "protected" with an empty password — common for files that only restrict printing — opens normally. A damaged file, or one that isn't really a PDF, is rejected with the reader's own error message. PDFs that are scanned images contain no text to extract; EVA doesn't run character recognition on them.

---

## Images

When your active model supports vision, EVA can analyse images you share alongside your messages. This applies to screenshots, diagrams, charts, UI mockups, photos, or any visual where meaning cannot be captured in text.

Vision capability is model-specific — not every model supports it. EVA indicates whether your current model supports image input in the provider and model settings.

Images reach the model as real images, not as a description. EVA only does this for models it has *confirmed* can read them; when a model's vision support is unknown, EVA doesn't guess, and the image is replaced by a short text placeholder instead. The same applies to screenshots taken by the agent's [browser tools](../12-mcp/01-what-are-mcp-servers.md#default-mcp-browser-automation): a vision-capable model can look at the screenshot itself, while other models work from the page's text structure, console and network output. A single image over 5 MB is sent as a placeholder rather than as an image, to keep requests a sensible size. If you'd rather images never go to a model as images, this can be turned off entirely in the [Configuration Reference](../15-configuration/01-configuration-reference.md).

> *A developer pastes a screenshot of an error dialog they cannot reproduce in text and asks EVA what is happening. EVA reads the stack trace in the image and explains the cause.*

---

## Referencing past conversations

Any previous conversation can be pulled into the current one as context. EVA reads the referenced thread — the full exchange, including tool calls and results — generates a structured summary, and injects it into your current conversation before you send your next message.

This is a capability that no mainstream chat application offers. In ChatGPT, Perplexity, or Claude, each conversation is an island — when you open a new chat, the slate is blank and you are on your own to re-explain what happened before. In EVA, you can point to a previous thread and continue building on it without re-explaining anything.

The use case is work that spans multiple sessions. Research conducted last week becomes the input for a strategy conversation today. A debugging thread from a previous day becomes the context for writing tests. Decisions made in one conversation become the brief for a new one. The knowledge you built up stays accessible and actionable.

When EVA generates the reference summary, it caches it locally so that subsequent references to the same conversation are served instantly without repeating the work.

> *Several days of research and iteration in one conversation produced a detailed competitive analysis. In a new conversation focused on positioning, that earlier thread is referenced. EVA has access to what was found, what approaches were ruled out, and what was concluded — without a single line of copy-paste.*

**If a referenced conversation has been deleted**, EVA will return an error when you try to reference it — the record no longer exists. If you think you may want to reference a conversation later, export it before deleting it.

---

## Referencing past executions

A past execution can also be pulled in as context — but what it provides is different from a conversation reference, and it is worth understanding the distinction.

**Referencing a conversation** gives EVA the dialogue: the questions asked, the responses given, the thinking that happened, the conclusions reached. It is the knowledge layer.

**Referencing an execution** gives EVA the mechanics: which tools were called, in what order, what each one returned, how long the task took, how many steps it required, whether it succeeded. It is the audit trail and the operational record.

The two serve different purposes. You reference a conversation when you want to carry forward knowledge or conclusions. You reference an execution when you want to audit what was done, reproduce a previous approach, or investigate the specifics of how a task was carried out.

> *An overnight research execution produced structured findings. The next morning, that execution is referenced in a new conversation. EVA is asked to identify the sources it used and explain why certain results were prioritised — information visible only in the tool trace, not the conversation summary.*

---

> **Next:** [Chat Mode vs Plan Mode](03-chat-mode-vs-plan-mode.md) — how EVA approaches different kinds of tasks.
