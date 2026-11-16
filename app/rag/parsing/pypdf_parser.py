"""Fast path: plain text extraction with pypdf (text-based PDFs only; no layout, tables or OCR)."""
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.rag.parsing.models import Element, ParsedDocument, ParseError


def parse_pdf_fast(path: Path) -> ParsedDocument:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ParseError("The PDF is password-protected.")
        elements = []
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                elements.append(Element("text", text, number))
        return ParsedDocument(elements, parser="pypdf", n_pages=len(reader.pages))
    except ParseError:
        raise
    except (PyPdfError, ValueError, OSError) as e:
        raise ParseError(f"Could not read the PDF: {e}") from e
