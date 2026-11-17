"""
Document ingestion:  upload → validate → parse (pypdf / Docling) → chunk → embed → ChromaDB

Parsing + embedding can take minutes (OCR!), so it runs in a single background worker thread:
the upload request returns immediately and the document's `status` (processing → ready | failed)
is tracked in PostgreSQL. A document is searchable only once it is `ready`, because its chunks are
written to ChromaDB as the very last step.
"""
import re
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.db.models import update_doc
from app.rag.chunking import chunk_document
from app.rag.parsing import ParseError, parse_document, supported_extensions
from app.rag.vectorstore import get_vectorstore
from app.utils.logger import logger

# One worker: Docling is memory-hungry, and concurrent OCR jobs would compete for RAM/CPU.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")

_MAGIC = {
    ".pdf": (b"%PDF-",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8",), ".jpeg": (b"\xff\xd8",),
    ".tif": (b"II*\x00", b"MM\x00*"), ".tiff": (b"II*\x00", b"MM\x00*"),
    ".docx": (b"PK\x03\x04",), ".pptx": (b"PK\x03\x04",),
}


_DOCLING_ONLY = {".docx", ".pptx", ".html", ".htm", ".md", ".markdown", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class UploadRejected(ValueError):
    """The upload is not acceptable (type, size, content). `status_code` is the HTTP code to answer with."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def safe_filename(name: str | None) -> str:
    base = Path(name or "").name                                  # drop any directory part
    base = re.sub(r"[^A-Za-z0-9._ ()\-]", "_", base).strip(" .")[:150]
    return base or "document"


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


async def save_upload(file: UploadFile, doc_id: str) -> tuple[Path, str]:
    """Stream the upload to disk, enforcing type (extension + magic bytes) and size. Returns (path, clean_name)."""
    name = safe_filename(file.filename)
    ext = Path(name).suffix.lower()
    allowed = supported_extensions()
    if ext not in allowed:
        hint = " (install Docling to enable this format)" if ext in _DOCLING_ONLY else ""
        raise UploadRejected(f"Unsupported file type '{ext or 'none'}'. Allowed: {', '.join(sorted(allowed))}{hint}")

    upload_dir = Path(settings.docs_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{doc_id}_{name}"
    limit = settings.max_upload_mb * 1024 * 1024
    size, head = 0, b""
    try:
        with open(path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                if not head:
                    head = chunk[:2048]
                size += len(chunk)
                if size > limit:
                    raise UploadRejected(f"File is larger than the {settings.max_upload_mb} MB limit.", 413)
                out.write(chunk)
        if size == 0:
            raise UploadRejected("The file is empty.")
        magic = _MAGIC.get(ext)
        if (magic and not head.startswith(magic)) or (not magic and not _looks_like_text(head)):
            raise UploadRejected(f"The file content does not look like a valid {ext} file.")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    logger.info("Saved upload: %s (%d bytes)", path.name, size)
    return path, name


@dataclass
class IngestResult:
    chunks: int
    pages: int | None
    parser: str
    warnings: list[str] = field(default_factory=list)


def delete_chunks(doc_id: str) -> None:
    get_vectorstore()._collection.delete(where={"doc_id": doc_id})


def ingest_file(path: Path, filename: str, role: str, doc_id: str, uploaded_by: str, mode: str | None = None) -> IngestResult:
    """Synchronous, CPU-heavy part: parse → chunk → embed → store."""
    parsed = parse_document(path, mode)
    chunks = chunk_document(parsed, source=filename, doc_id=doc_id, role=role, uploaded_by=uploaded_by)
    if not chunks:
        raise ParseError(
            "No text could be extracted. If this is a scanned document, enable OCR by installing Docling "
            "(PARSER=auto or deep); see the README.")
    store = get_vectorstore()
    try:
        store.add_documents(chunks)
        if hasattr(store, "persist"):
            store.persist()
    except Exception:
        try:
            delete_chunks(doc_id)                                  # never leave a half-indexed document behind
        except Exception:                                          # pragma: no cover - best effort
            logger.warning("Could not clean up partial chunks for %s", doc_id)
        raise
    logger.info("Ingested '%s': %d chunks via %s (%s pages)", filename, len(chunks), parsed.parser, parsed.n_pages)
    return IngestResult(len(chunks), parsed.n_pages, parsed.parser, parsed.warnings)


def run_ingestion(path: Path, filename: str, role: str, doc_id: str, uploaded_by: str, mode: str | None) -> IngestResult | None:
    """Worker entry point: runs ingest_file and records the outcome in PostgreSQL. Never raises."""
    try:
        res = ingest_file(path, filename, role, doc_id, uploaded_by, mode)
        update_doc(doc_id, status="ready", chunk_count=res.chunks, pages=res.pages, parser=res.parser,
                   note=" ".join(res.warnings) or None, error=None)
        return res
    except ParseError as e:
        logger.warning("Ingestion of '%s' failed: %s", filename, e)
        update_doc(doc_id, status="failed", error=str(e))
    except Exception as e:
        logger.error("Ingestion of '%s' crashed: %s", filename, e, exc_info=True)
        update_doc(doc_id, status="failed", error=f"Unexpected error ({type(e).__name__}). See the server log.")
    return None


def submit_ingestion(path: Path, filename: str, role: str, doc_id: str, uploaded_by: str, mode: str | None) -> Future:
    return _executor.submit(run_ingestion, path, filename, role, doc_id, uploaded_by, mode)
