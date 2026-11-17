from typing import List, Optional

from pydantic import BaseModel


class UploadResponse(BaseModel):
    message: str
    doc_id: str
    filename: str
    chunk_count: int = 0
    accessible_to: str
    status: str = "processing"          # processing | ready | failed
    parser: Optional[str] = None
    note: Optional[str] = None


class DocInfo(BaseModel):
    doc_id: str
    filename: str
    role: str
    uploaded_by: str
    chunk_count: int
    created_at: str
    status: str = "ready"
    error: Optional[str] = None
    note: Optional[str] = None
    pages: Optional[int] = None
    parser: Optional[str] = None


class DocsListResponse(BaseModel):
    total: int
    documents: List[DocInfo]


class CapabilitiesResponse(BaseModel):
    docling_installed: bool
    extensions: List[str]
    modes: List[str]
    default_mode: str
    max_upload_mb: int
