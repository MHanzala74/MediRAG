"""
Deep path: Docling — layout analysis, table structure, OCR for scanned pages/images, and
DOCX / PPTX / HTML / Markdown / image inputs. Everything runs locally (no cloud calls).

Docling is an OPTIONAL dependency (`pip install docling`, see requirements-docling.txt).
Importing this module never imports Docling; only the functions that need it do.
"""
import importlib.util
from functools import lru_cache
from pathlib import Path

from app.rag.parsing.models import Element, ParsedDocument, ParseError, ParserUnavailable
from app.utils.logger import logger

_INSTALL_HINT = "Install Docling with: pip install docling   (see requirements-docling.txt)"


def docling_available() -> bool:
    return importlib.util.find_spec("docling") is not None


def elements_from_docling(doc) -> list[Element]:
    """
    Turn a DoclingDocument into Elements, keeping page numbers, a running heading path,
    tables as Markdown (with caption) and figure captions. Pure function → unit-testable.
    """
    from docling_core.types.doc import DocItemLabel, PictureItem, TableItem

    elements: list[Element] = []
    headings: list[tuple[int, str]] = []                      # (level, text)
    section = lambda: " > ".join(t for _, t in headings)      # noqa: E731

    for item, _depth in doc.iterate_items():                  # body only: page headers/footers are skipped
        prov = getattr(item, "prov", None)
        page = prov[0].page_no if prov else None
        label = getattr(item, "label", None)

        if isinstance(item, TableItem):
            md = item.export_to_markdown(doc=doc).strip()
            if md:
                caption = (item.caption_text(doc) or "").strip()
                elements.append(Element("table", f"{caption}\n{md}" if caption else md, page, section()))
        elif isinstance(item, PictureItem):
            caption = (item.caption_text(doc) or "").strip()
            if caption:
                elements.append(Element("figure", f"Figure: {caption}", page, section()))
        elif label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
            continue                                          # running headers/footers repeat on every page: noise
        elif label == DocItemLabel.CAPTION:
            continue                                          # already attached to its table / figure
        elif label == DocItemLabel.TITLE:
            text = (getattr(item, "text", "") or "").strip()
            if text:
                headings = [(0, text)]
        elif label == DocItemLabel.SECTION_HEADER:
            text = (getattr(item, "text", "") or "").strip()
            if text:
                level = getattr(item, "level", 1) or 1
                while headings and headings[-1][0] >= level:
                    headings.pop()
                headings.append((level, text))
        else:
            text = (getattr(item, "text", "") or "").strip()
            if text:
                if label == DocItemLabel.LIST_ITEM:
                    text = f"{(getattr(item, 'marker', '') or '-').strip()} {text}"
                elements.append(Element("text", text, page, section()))
    return elements


@lru_cache(maxsize=4)
def _converter(ocr: bool, tables: bool, models_dir: str, timeout_s: int):
    """Building a converter loads the ML models — do it once per option set."""
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    except ImportError as e:
        raise ParserUnavailable(f"Docling is not installed. {_INSTALL_HINT}") from e

    opts = PdfPipelineOptions(do_ocr=ocr, do_table_structure=tables, document_timeout=float(timeout_s))
    if models_dir:
        opts.artifacts_path = models_dir
    return DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
    })


def parse_with_docling(path: Path, *, ocr: bool = True, tables: bool = True,
                       models_dir: str = "", timeout_s: int = 900) -> ParsedDocument:
    if not docling_available():
        raise ParserUnavailable(f"Docling is not installed. {_INSTALL_HINT}")
    from docling.datamodel.base_models import ConversionStatus

    try:
        result = _converter(ocr, tables, models_dir, timeout_s).convert(str(path))
    except ParseError:
        raise
    except ModuleNotFoundError as e:                         # e.g. torch / OCR engine missing for PDFs
        raise ParserUnavailable(f"Docling is installed but a component is missing ({e.name}). {_INSTALL_HINT}") from e
    except Exception as e:
        raise ParseError(f"Docling could not convert the file: {e}") from e

    if result.status == ConversionStatus.FAILURE:
        detail = "; ".join(str(getattr(err, "error_message", err)) for err in (result.errors or [])[:2])
        raise ParseError(f"Docling failed to convert the file. {detail}".strip())

    doc = result.document
    parsed = ParsedDocument(elements_from_docling(doc), parser="docling", n_pages=len(doc.pages) or None)
    if result.status == ConversionStatus.PARTIAL_SUCCESS:
        parsed.warnings.append("Docling converted the file only partially; some content may be missing.")
        logger.warning("Docling partial conversion: %s", path.name)
    return parsed
