from pydantic import BaseModel
from typing import List

class UploadResponse(BaseModel):
    message: str
    doc_id: str
    filename: str
    chunk_count: int
    accessible_to: str

class DocInfo(BaseModel):
    doc_id: str
    filename: str
    role: str
    uploaded_by: str
    chunk_count: int
    created_at: str

class DocsListResponse(BaseModel):
    total: int
    documents: List[DocInfo]
