# core/rag/models.py
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class Chunk(BaseModel):
    id: str
    text: str
    metadata: Dict[str, Any]
    vector: List[float]
    score: Optional[float] = None   # B-030: similarity score (0–1, higher = more relevant); None for ingestion chunks

class DeleteFileRequest(BaseModel):
    file_name: str