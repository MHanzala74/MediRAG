"""
Document ingestion pipeline:
  PDF upload → parse → semantic chunk → embed → store in ChromaDB

Supports: PDF (with metadata tagging per role and doc_id)
"""
import os
from pathlib import Path
from fastapi import UploadFile
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.rag.vectorstore import get_vectorstore
from app.db.models import save_doc_metadata
from app.core.config import settings
from app.utils.logger import logger
from app.utils.exceptions import RAGException


async def ingest_document(file: UploadFile, role: str, doc_id: str, uploaded_by: str) -> int:
    """
    Parse, chunk, embed and store a PDF document in ChromaDB.
    Returns the number of chunks stored.
    """
    upload_dir = Path(settings.docs_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    save_path = upload_dir / f"{doc_id}_{file.filename}"
    contents = await file.read()

    with open(save_path, "wb") as f:
        f.write(contents)

    logger.info("Saved file: %s (%d bytes)", save_path, len(contents))

    try:
        loader = PyPDFLoader(str(save_path))
        raw_docs = loader.load()
    except Exception as e:
        raise RAGException(e)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ".", " ", ""],
    )
    chunks = splitter.split_documents(raw_docs)

    for i, chunk in enumerate(chunks):
        chunk.metadata.update({
            "role":     role,
            "doc_id":   doc_id,
            "source":   file.filename,
            "chunk_id": i,
            "uploaded_by": uploaded_by,
        })

    vectorstore = get_vectorstore()
    vectorstore.add_documents(chunks)
    vectorstore.persist()

    # Save metadata to PostgreSQL
    save_doc_metadata(doc_id, file.filename, role, uploaded_by, len(chunks))

    logger.info("Ingested '%s': %d chunks → ChromaDB (role=%s)", file.filename, len(chunks), role)
    return len(chunks)
