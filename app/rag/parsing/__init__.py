"""
Document parsing entry point.

    parse_document(path, mode)   mode: "fast" | "deep" | "auto"

PDF   fast → pypdf · deep → Docling · auto → pypdf, then Docling if the PDF looks scanned
other DOCX, PPTX, HTML, Markdown, images … → Docling (always)
"""
from pathlib import Path

from app.core.config import settings
from app.rag.parsing.docling_parser import docling_available, parse_with_docling
from app.rag.parsing.models import Element, ParsedDocument, ParseError, ParserUnavailable
from app.rag.parsing.pypdf_parser import parse_pdf_fast
from app.utils.logger import logger

__all__ = ["parse_document", "supported_extensions", "docling_available",
           "ParsedDocument", "Element", "ParseError", "ParserUnavailable"]

PDF_EXTENSIONS = {".pdf"}
DOCLING_EXTENSIONS = {".docx", ".pptx", ".html", ".htm", ".md", ".markdown", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def supported_extensions() -> set[str]:
    """PDFs always; the other formats only when Docling is installed."""
    return PDF_EXTENSIONS | (DOCLING_EXTENSIONS if docling_available() else set())


def _deep(path: Path) -> ParsedDocument:
    return parse_with_docling(path, ocr=settings.docling_ocr, tables=settings.docling_tables,
                              models_dir=settings.docling_models_dir, timeout_s=settings.docling_timeout_s)


def looks_scanned(parsed: ParsedDocument) -> bool:
    pages = parsed.n_pages or 0
    return pages > 0 and parsed.n_chars / pages < settings.parser_scanned_min_chars


def parse_document(path: Path, mode: str | None = None) -> ParsedDocument:
    mode = mode or settings.parser
    ext = path.suffix.lower()

    if ext not in PDF_EXTENSIONS:                      # DOCX / PPTX / HTML / MD / images
        return _deep(path)
    if mode == "fast":
        return parse_pdf_fast(path)
    if mode == "deep":
        return _deep(path)

    # auto: cheap path first, escalate only when it clearly failed to extract text
    fast = parse_pdf_fast(path)
    if not looks_scanned(fast):
        return fast
    logger.info("'%s' looks scanned (%.0f chars/page) → trying Docling OCR", path.name,
                fast.n_chars / max(fast.n_pages or 1, 1))
    try:
        deep = _deep(path)
        deep.warnings.append("Scanned PDF detected: parsed with OCR.")
        return deep
    except ParseError as e:
        fast.warnings.append(f"Looks scanned but deep parsing was not possible: {e}")
        return fast
