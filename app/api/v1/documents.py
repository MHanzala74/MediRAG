"""Document upload and listing endpoints."""
import uuid
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
from app.core.security import require_admin
from app.rag.ingestion import ingest_document
from app.db.models import list_docs
from app.schemas.docs import UploadResponse, DocsListResponse
from app.utils.logger import logger

router = APIRouter(prefix="/documents", tags=["Documents"])

ALLOWED_TYPES = {"application/pdf"}


@router.post("/upload", response_model=UploadResponse, summary="Upload a PDF document (admin only)")
async def upload_document(
    file: UploadFile = File(...),
    role: str = Form(..., description="Role that can access this document (user | admin)"),
    user: dict = Depends(require_admin),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'admin'")

    doc_id = str(uuid.uuid4())
    logger.info("Upload started: %s by %s (role=%s)", file.filename, user["username"], role)

    try:
        chunk_count = await ingest_document(file, role, doc_id, user["username"])
    except Exception as e:
        logger.error("Ingestion failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Document ingestion failed: {e}")

    return UploadResponse(
        message=f"'{file.filename}' uploaded and indexed successfully",
        doc_id=doc_id,
        filename=file.filename,
        chunk_count=chunk_count,
        accessible_to=role,
    )


@router.get("/list", response_model=DocsListResponse, summary="List all uploaded documents (admin only)")
def list_documents(user: dict = Depends(require_admin)):
    docs = list_docs()
    return DocsListResponse(total=len(docs), documents=docs)
