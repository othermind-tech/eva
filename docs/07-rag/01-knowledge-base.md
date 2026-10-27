# Knowledge Base (Document Search)

EVA can search through documents you upload and retrieve the specific passages that are relevant to your question. You don't have to read a 200-page manual to find the paragraph that matters — you ask, and EVA finds it.

This is what the Knowledge Base is for.

---

## What the Knowledge Base does

When you upload a document to the Knowledge Base, EVA processes it and stores it in a way that allows semantic search — meaning it can find passages based on meaning, not just exact keyword matches. Ask "what does the contract say about liability?" and EVA will find the relevant clauses even if you didn't use the exact wording from the document.

This works for any kind of document: contracts, technical manuals, research papers, internal policy docs, product specifications, reference guides. Anything you find yourself needing to consult repeatedly is a good candidate.

---

## Supported file formats

| Format | What gets indexed |
|--------|------------------|
| PDF | All extractable text content |
| DOCX / DOC | Document body text |
| CSV / XLS / XLSX | All rows, converted to searchable text |
| TXT, Markdown, JSON, YAML | Full text content |
| Source code files | Full text content |

Any text-extractable file can be indexed. Binary files — images, compiled executables, archives — cannot. A password-protected PDF can't be indexed either: the upload fails with *"This PDF is locked. Remove the password and upload it again."* A scanned PDF that is only page images has no text to index.

---

## How indexing works

Uploading a file to the Knowledge Base starts a background processing job. EVA extracts the text, splits it into overlapping segments, and converts each segment into a numerical representation that enables fast semantic search. All of this processing runs locally on your machine — your document content is never sent to an external service during indexing.

Indexing is asynchronous: after uploading, you will see a progress indicator while the document is being processed. Larger documents take longer. Once indexing completes, the document is immediately available for search.

If indexing fails for any reason, EVA will report the error. The rest of EVA continues working normally — the Knowledge Base is a standalone capability that does not affect other features.

---

## How EVA uses the Knowledge Base in conversation

During a conversation, EVA can search the Knowledge Base automatically when it determines that relevant documents might help answer your question. When it searches, it retrieves the most relevant passages from your indexed documents and uses them to inform its response — citing what it found.

You can also ask EVA to search directly:

> *"What does our SLA document say about response time guarantees?"*
> *"Find the section on authentication in the API spec."*
> *"What were the conclusions of the Q3 research report?"*

EVA will search, retrieve the relevant passages, and answer based on what it finds. You do not need to specify which file to look in — EVA searches across everything in your Knowledge Base unless you direct it to a specific document.

---

## EVA can add to the Knowledge Base too

Uploading is not the only way content gets indexed. During a conversation, EVA can index content into the Knowledge Base itself — a file it just read, or a passage worth remembering for later — the same way it retrieves from it. This runs through the same local processing as a manual upload, capped at a maximum number of chunks per call so one action can't balloon into a long-running operation. If a project is active, content EVA indexes this way is scoped to that project by default, same as anything you upload while working inside it.

---

## Global and project-scoped knowledge

Documents in the Knowledge Base can exist at two scopes.

**Global** — documents you upload from the main Knowledge Base panel are accessible in any conversation. Use this for reference material that is relevant across different work: your company's style guide, a technical spec you consult regularly, a reference manual for a library you use often.

**Project-scoped** — documents uploaded to a project's Knowledge section are only searched in conversations within that project. When EVA searches in a project conversation, it automatically limits its search to that project's documents. Use this for material that belongs specifically to a piece of work: the codebase's architecture document, a client brief, research collected for that project.

---

## Managing your Knowledge Base

**Viewing indexed files** — the Knowledge Base panel lists all your indexed documents with their file sizes and upload dates. A search bar lets you filter the list by filename. Project-scoped documents appear under their respective project's Knowledge tab.

**Deleting a document** — removing a document from the Knowledge Base deletes both the stored file and all its indexed content. Once deleted, EVA can no longer search it. This cannot be undone.

---

## What the Knowledge Base is not

**Not a web search** — the Knowledge Base only searches documents you have uploaded. It has no access to the internet or to documents you have not explicitly added.

**Not real-time** — documents must be uploaded and fully indexed before EVA can search them. A document you upload mid-conversation is not available until indexing completes.

**Not ideal for very short documents** — a one-page note or a brief README will index correctly, but semantic search is most useful when there is enough content to have meaningful variation between passages. Short documents are often better attached directly to a conversation.

---

> **Next:** [What are Subagents?](../08-subagents/01-what-are-subagents.md) — how EVA delegates work to specialised agents.
