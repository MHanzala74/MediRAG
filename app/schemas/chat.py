from pydantic import BaseModel, Field
from typing import List

class ChatRequest(BaseModel):
    message: str       = Field(..., min_length=1, max_length=1000)
    session_id: str    = Field(..., description="Unique session ID for chat history")

class Source(BaseModel):
    filename: str
    doc_id: str
    chunk_preview: str

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
