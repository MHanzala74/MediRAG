"""
See exactly what a parser extracts from a file — use it on your own PDFs before uploading them.

    python scripts/inspect_document.py report.pdf                 # PARSER from .env (default: auto)
    python scripts/inspect_document.py report.pdf --mode fast     # pypdf only
    python scripts/inspect_document.py report.pdf --mode deep     # Docling (layout, tables, OCR)
    python scripts/inspect_document.py report.pdf --chunks 5      # also print the first 5 chunks

Compare `fast` and `deep` on a scanned or table-heavy document to decide which mode you need.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.chunking import chunk_document          # noqa: E402
from app.rag.parsing import ParseError, parse_document  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--mode", choices=["auto", "fast", "deep"], default=None)
    ap.add_argument("--chunks", type=int, default=3, help="how many chunks to print (default 3)")
    args = ap.parse_args()

    path = Path(args.file)
    try:
        parsed = parse_document(path, args.mode)
    except ParseError as e:
        sys.exit(f"✗ {e}")

    kinds = Counter(e.kind for e in parsed.elements)
    pages = sorted({e.page for e in parsed.elements if e.page})
    print(f"\n{path.name}: parser={parsed.parser} · pages={parsed.n_pages} · elements={dict(kinds)} · {parsed.n_chars:,} chars")
    if pages:
        print(f"  text found on pages: {pages[0]}–{pages[-1]} ({len(pages)} of {parsed.n_pages})")
    for w in parsed.warnings:
        print(f"  ⚠ {w}")
    sections = sorted({e.section for e in parsed.elements if e.section})
    if sections:
        print(f"  {len(sections)} section(s), e.g.: " + " | ".join(sections[:4]))

    chunks = chunk_document(parsed, source=path.name, doc_id="inspect", role="user", uploaded_by="cli")
    sizes = [len(c.page_content) for c in chunks]
    print(f"  → {len(chunks)} chunks (min {min(sizes, default=0)} / max {max(sizes, default=0)} chars), "
          f"{sum(1 for c in chunks if c.metadata['element_type'] == 'table')} table chunk(s)")
    for c in chunks[:args.chunks]:
        m = c.metadata
        print(f"\n  ── chunk {m['chunk_id']} · {m['element_type']} · page {m.get('page', '–')} · {m['section'] or 'no section'}")
        print("  " + c.page_content[:400].replace("\n", "\n  "))


if __name__ == "__main__":
    main()
