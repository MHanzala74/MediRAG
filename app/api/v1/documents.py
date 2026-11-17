"""Document management (admin only): upload, status, list, delete."""
import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from app.core.config import settings
from app.core.security import require_admin
from app.db.models import create_doc_record, delete_doc_record, get_doc, list_docs
from app.rag import ingestion
from app.rag.parsing import docling_available, supported_extensions
from app.schemas.docs import CapabilitiesResponse, DocInfo, DocsListResponse, UploadResponse
from app.utils.logger import logger

router = APIRouter(prefix="/documents", tags=["Documents"], dependencies=[Depends(require_admin)])

MODES = ["auto", "fast", "deep"]


@router.get("/capabilities", response_model=CapabilitiesResponse, summary="Supported file types and parsing modes")
def capabilities():
    return CapabilitiesResponse(docling_installed=docling_available(), extensions=sorted(supported_extensions()),
                                modes=MODES, default_mode=settings.parser, max_upload_mb=settings.max_upload_mb)


@router.post("/upload", response_model=UploadResponse, status_code=202, summary="Upload a document (parsed in the background)")
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    role: str = Form(..., description="Who can use it: 'user' = everyone, 'admin' = admins only"),
    parser: str | None = Form(None, description="auto (default) | fast (pypdf) | deep (Docling: layout, tables, OCR)"),
    wait: bool = Query(False, description="Block until parsing finishes (scripts/tests). Default: return immediately"),
    user: dict = Depends(require_admin),
):
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'admin'")
    mode = parser or settings.parser
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"parser must be one of {MODES}")
    if mode == "deep" and not docling_available():
        raise HTTPException(status_code=400, detail="Deep parsing needs Docling: pip install docling (see requirements-docling.txt)")

    doc_id = str(uuid.uuid4())
    try:
        path, name = await ingestion.save_upload(file, doc_id)
    except ingestion.UploadRejected as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    create_doc_record(doc_id, name, role, user["username"])
    logger.info("Upload accepted: %s by %s (role=%s, mode=%s)", name, user["username"], role, mode)
    future = ingestion.submit_ingestion(path, name, role, doc_id, user["username"], mode)

    if wait:
        await asyncio.wrap_future(future)
        doc = get_doc(doc_id)
        if doc["status"] == "failed":
            raise HTTPException(status_code=422, detail=doc["error"])
        response.status_code = 200                                  # finished (202 = accepted, still processing)
        return UploadResponse(message=f"'{name}' indexed", doc_id=doc_id, filename=name, chunk_count=doc["chunk_count"],
                              accessible_to=role, status="ready", parser=doc["parser"], note=doc["note"])
    return UploadResponse(message=f"'{name}' received — parsing in the background", doc_id=doc_id, filename=name,
                          accessible_to=role, status="processing")


@router.get("/list", response_model=DocsListResponse, summary="List all uploaded documents")
def list_documents():
    docs = list_docs()
    return DocsListResponse(total=len(docs), documents=docs)


@router.get("/{doc_id}", response_model=DocInfo, summary="Status of one document")
def document_status(doc_id: str):
    doc = get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/{doc_id}", summary="Delete a document and all its indexed chunks")
def delete_document(doc_id: str, user: dict = Depends(require_admin)):
    doc = get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc["status"] == "processing":
        raise HTTPException(status_code=409, detail="The document is still being processed")
    try:
        ingestion.delete_chunks(doc_id)
    except Exception as e:
        logger.error("Could not delete chunks of %s: %s", doc_id, e)
        raise HTTPException(status_code=500, detail="Could not remove the document from the search index")
    for f in Path(settings.docs_upload_dir).glob(f"{doc_id}_*"):
        f.unlink(missing_ok=True)
    delete_doc_record(doc_id)
    logger.info("Document %s ('%s') deleted by %s", doc_id, doc["filename"], user["username"])
    return {"deleted": doc_id}
