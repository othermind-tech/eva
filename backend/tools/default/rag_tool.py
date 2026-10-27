import asyncio
import os
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, model_validator
from core.tools.base import ToolBase, ToolResult, ToolAuthority
from core.tools.registry import default_tool
from core.rag.retriever import search_vectors
from core.rag.embed import embed_text
from core.rag.ingestion import index_chunks
from core.rag.models import Chunk
from utils.file_utils import process_file_bytes, INDEXABLE_EXTENSIONS
from utils.workspace import resolve_path_for_user
from config.settings import RAG_INGEST_MAX_CHUNKS

class RagRetrieveArgs(BaseModel):
    query: str
    top_k: int = 10
    file_name: Optional[str] = None
    project_id: Optional[str] = None   # B-052: restrict search to a project's documents

@default_tool(
    name="rag_retrieve",
    description=(
        "Search the user's own uploaded documents and files in their private "
        "knowledge base, using semantic similarity. This is NOT a web search — it "
        "only finds content the user has explicitly uploaded to EVA, never public "
        "internet content. Optionally filter by file_name or project_id (B-052). "
        "Returns the top-K most relevant chunks with similarity scores. "
        "USE web_search INSTEAD for general internet research. "
        "Safe to call speculatively — returns an empty result if the user has no "
        "relevant uploads, at negligible cost."
    ),
    args_schema=RagRetrieveArgs,
    category="search",
    summary="Search the RAG knowledge base for semantically similar document chunks.",
)
class RagRetrieveTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        # Bug fix (Phase D audit): user_id was previously a model-supplied
        # argument -- the model has no legitimate way to know its own
        # authenticated user_id, so every real call searched under
        # whatever it guessed/hallucinated and silently found nothing,
        # regardless of what had actually been ingested. Every other tool
        # in this codebase already derives this from execution context
        # (see memory_tools.py's module docstring for the established
        # convention) -- this tool predates that convention.
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available for retrieval."})
        query = args["query"]
        top_k = args.get("top_k", 10)
        file_name = args.get("file_name")
        project_id = args.get("project_id")

        query_vector = embed_text(query)

        metadata_filter = []
        if file_name:
            metadata_filter.append(
                {"key": "source_filename", "match": {"value": file_name}}
            )
        if project_id:
            metadata_filter.append(
                {"key": "project_id", "match": {"value": project_id}}
            )

        chunks = search_vectors(
            tenant_id=user_id,
            query_vector=query_vector,
            top_k=top_k,
            metadata_filter=metadata_filter,
        )

        return ToolResult(
            result={
                "chunks": [
                    {
                        "text":     c.text,
                        "metadata": c.metadata,
                        "score":    c.score,   # B-030: similarity score (0–1)
                    }
                    for c in chunks
                ]
            }
        )


class RagIngestArgs(BaseModel):
    path: Optional[str] = None
    content: Optional[str] = None
    label: Optional[str] = None
    project_id: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_input(self):
        if bool(self.path) == bool(self.content):
            raise ValueError(
                "Provide exactly one of 'path' or 'content', not both or neither."
            )
        return self


@default_tool(
    name="rag_ingest",
    description=(
        "Index a file or a piece of text into the user's private RAG knowledge "
        "base, so it can be found later with rag_retrieve. Use this for content "
        "too large or detailed to keep in a compact summary or note -- a long "
        "document, extensive research notes, a big reference file -- rather than "
        "trying to compress everything you find into a short summary. "
        "Provide EITHER 'path' (an existing file, resolved into the workspace "
        "the same way read_file works) OR 'content' (raw text you already have) "
        "-- never both, never neither. If a project is active in this "
        "conversation, ingested content is automatically scoped to it unless "
        "you explicitly pass a different project_id. "
        f"Accepted file extensions: {', '.join(sorted(INDEXABLE_EXTENSIONS))}. "
        f"Capped at {RAG_INGEST_MAX_CHUNKS} chunks per call -- if you hit that "
        "limit, narrow what you're ingesting (e.g. one section at a time)."
    ),
    args_schema=RagIngestArgs,
    authority=ToolAuthority.MUTATING,
    category="search",
    summary="Index a file or text into the RAG knowledge base for later retrieval.",
)
class RagIngestTool(ToolBase):
    async def run(self, args: dict) -> ToolResult:
        execution = getattr(self, "execution", None)
        user_id = getattr(execution, "user_id", None)
        if not user_id:
            return ToolResult(result={"error": "No user context available for ingestion."})

        path = args.get("path")
        content = args.get("content")
        label = args.get("label")
        project_id = args.get("project_id")

        if path:
            resolved_path = resolve_path_for_user(
                user_id,
                path,
                getattr(execution, "conversation_id", None),
                getattr(execution, "project_workspace", None),
            )
            try:
                with open(resolved_path, "rb") as f:
                    raw_bytes = f.read()
            except FileNotFoundError:
                return ToolResult(result={"error": f"File not found: {resolved_path}"})
            except Exception as exc:
                return ToolResult(result={"error": str(exc)})
            filename = label or os.path.basename(resolved_path)
        else:
            filename = label or "content.txt"
            if "." not in filename:
                filename = f"{filename}.txt"
            raw_bytes = content.encode("utf-8")

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in INDEXABLE_EXTENSIONS:
            return ToolResult(result={
                "error": f"Unsupported file type '.{ext}' for indexing.",
                "accepted_extensions": sorted(INDEXABLE_EXTENSIONS),
            })

        # CPU-bound work -- offload to a thread so it doesn't block the
        # shared event loop, matching how the existing REST ingestion
        # pipeline (api/rag.py _run_ingestion_job) already does this.
        chunk_texts = await asyncio.to_thread(process_file_bytes, raw_bytes, filename)

        if len(chunk_texts) > RAG_INGEST_MAX_CHUNKS:
            return ToolResult(result={
                "error": (
                    f"Content is too large to ingest in one call "
                    f"({len(chunk_texts)} chunks, limit {RAG_INGEST_MAX_CHUNKS}). "
                    "Narrow what you're ingesting -- e.g. one section at a time."
                ),
            })

        # Explicit project_id wins; otherwise scope to the active project
        # if this conversation has one, matching how file tools already
        # auto-resolve workspace context without requiring the model to
        # pass it every time.
        effective_project_id = project_id or getattr(execution, "parent_project_id", None)

        chunks: List[Chunk] = []
        for i, text in enumerate(chunk_texts):
            vector = await asyncio.to_thread(embed_text, text)
            metadata: Dict[str, Any] = {"source_filename": filename, "chunk_index": i}
            if effective_project_id:
                metadata["project_id"] = effective_project_id
            chunks.append(Chunk(
                id=f"{user_id}:{filename}:{i}",
                text=text,
                metadata=metadata,
                vector=vector,
            ))

        await asyncio.to_thread(index_chunks, user_id, chunks, effective_project_id)

        retrieval_hint = "rag_retrieve"
        if effective_project_id:
            retrieval_hint += f" with project_id='{effective_project_id}'"
        retrieval_hint += f", optionally filtered with file_name='{filename}'"

        return ToolResult(result={
            "ok": True,
            "source_filename": filename,
            "chunk_count": len(chunks),
            "project_id": effective_project_id,
            "message": (
                f"Indexed {len(chunks)} chunk(s) from '{filename}'. "
                f"Find it later with {retrieval_hint}."
            ),
        })
