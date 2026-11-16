"""
Structure-aware chunking.

Compared with splitting raw text every N characters this:
  * never mixes two sections (chunks stay under one heading) and prefixes each chunk with its
    heading path, so the embedding "knows" what the passage is about;
  * keeps every TABLE as its own chunk (Markdown with caption); oversized tables are split by rows
    and the header row is repeated in each part, so numbers always travel with their column names;
  * records the page range, section and element type of each chunk → page-level citations.

Elements are packed greedily up to `chunk_size`; an element larger than that is split with a
recursive splitter (with overlap).
"""
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings
from app.rag.parsing.models import Element, ParsedDocument


def split_table(text: str, limit: int) -> list[str]:
    """Split a Markdown table by rows, repeating caption + header row in every part."""
    if len(text) <= limit:
        return [text]
    lines = text.split("\n")
    first_row = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("|")), None)
    if first_row is None or first_row + 2 >= len(lines):
        return RecursiveCharacterTextSplitter(chunk_size=limit, chunk_overlap=0).split_text(text)
    head, rows = lines[:first_row + 2], lines[first_row + 2:]        # caption + header + separator
    head_len = len("\n".join(head))
    parts, current, size = [], [], head_len
    for row in rows:
        if current and size + len(row) + 1 > limit:
            parts.append("\n".join(head + current))
            current, size = [], head_len
        current.append(row)
        size += len(row) + 1
    if current:
        parts.append("\n".join(head + current))
    return parts


def chunk_document(parsed: ParsedDocument, *, source: str, doc_id: str, role: str, uploaded_by: str) -> list[Document]:
    size, overlap = settings.chunk_size, settings.chunk_overlap
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size, chunk_overlap=overlap, separators=["\n\n", "\n", ". ", " ", ""])
    chunks: list[Document] = []

    def add(text: str, kind: str, section: str, pages: list[int]):
        body = f"{section}\n{text}" if section else text
        meta = {"source": source, "doc_id": doc_id, "role": role, "uploaded_by": uploaded_by,
                "chunk_id": len(chunks), "element_type": kind, "section": section,
                "parser": parsed.parser}
        if pages:                                   # Chroma rejects None → only set when known
            meta["page"], meta["page_end"] = min(pages), max(pages)
        chunks.append(Document(page_content=body, metadata=meta))

    buf: list[Element] = []
    buf_key = None

    def buffered_len() -> int:
        return sum(len(e.text) for e in buf) + 2 * max(len(buf) - 1, 0)

    def flush():
        if buf:
            add("\n\n".join(e.text for e in buf), "text", buf[0].section, [e.page for e in buf if e.page])
            buf.clear()

    for el in parsed.elements:
        if el.kind == "table":
            flush()
            for part in split_table(el.text, settings.table_chunk_size):
                add(part, "table", el.section, [el.page] if el.page else [])
            continue
        # unstructured text (no headings, e.g. pypdf) is kept per page so page numbers stay exact
        key = (el.section, el.page if not el.section else None)
        if buf and (key != buf_key or buffered_len() + len(el.text) + 2 > size):
            flush()
        buf_key = key
        if len(el.text) > size:                     # oversized paragraph / page → recursive split
            flush()
            for part in splitter.split_text(el.text):
                add(part, "text", el.section, [el.page] if el.page else [])
        else:
            buf.append(el)
    flush()
    return chunks
