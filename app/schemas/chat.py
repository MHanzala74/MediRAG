from pydantic import BaseModel, Field
from typing import List

class ChatRequest(BaseModel):
    message: str       = Field(..., min_length=1, max_length=1000)
    session_id: str    = Field(..., min_length=1, max_length=100, description="Unique session ID for chat history")

class Source(BaseModel):
    filename: str
    doc_id: str
    chunk_preview: str
    page: int | None = None          # first page of the cited passage (None for DOCX/HTML/Markdown)
    section: str | None = None       # heading path, e.g. "Diabetes Guide > Symptoms"

class ChatResponse(BaseModel):
    answer: str
    sources: List[Source]
    session_id: str
    model_used: str
    retrieval_method: str
    guardrail_passed: bool = True

class HistoryItem(BaseModel):
    role: str
    content: str

class ChatHistoryResponse(BaseModel):
    session_id: str
    history: List[HistoryItem]
