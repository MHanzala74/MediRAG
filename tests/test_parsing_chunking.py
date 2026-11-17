"""Parsing (pypdf + Docling adapter) and structure-aware chunking. No network, no LLM, no DB."""
import io

import pytest

from app.core.config import settings
from app.rag import parsing
from app.rag.chunking import chunk_document, split_table
from app.rag.parsing import Element, ParsedDocument, ParserUnavailable, parse_document
from app.rag.parsing.pypdf_parser import parse_pdf_fast

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402


def make_pdf(path, pages):
    c = canvas.Canvas(str(path), pagesize=A4)
    for lines in pages:
        y = 780
        for ln in lines:
            c.drawString(72, y, ln)
            y -= 18
        c.showPage()
    c.save()
    return path


def make_image_only_pdf(path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (600, 400), "white")
    ImageDraw.Draw(img).text((40, 40), "scanned text that is only pixels", fill="black")
    img.save(path, "PDF")
    return path


def meta(**kw):
    return dict(source="d.pdf", doc_id="id1", role="user", uploaded_by="admin", **kw)


# ─────────────────────────── pypdf (fast path) ───────────────────────────
def test_fast_parser_keeps_page_numbers(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", [["Diabetes causes thirst.", "It also causes fatigue."], ["Hypertension raises risk."], []])
    parsed = parse_pdf_fast(pdf)
    assert parsed.parser == "pypdf" and parsed.n_pages == 3
    assert [(e.page, e.kind) for e in parsed.elements] == [(1, "text"), (2, "text")]     # blank page 3 dropped
    chunks = chunk_document(parsed, **meta())
    assert [(c.metadata["page"], c.metadata["page_end"]) for c in chunks] == [(1, 1), (2, 2)]   # pages never merged
    assert "thirst" in chunks[0].page_content and "Hypertension" in chunks[1].page_content


def test_fast_parser_errors_are_clean(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not really a pdf")
    with pytest.raises(parsing.ParseError):
        parse_pdf_fast(bad)


# ─────────────────────────── auto mode / scanned detection ───────────────────────────
def test_auto_keeps_fast_result_for_text_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(parsing, "_deep", lambda p: pytest.fail("deep parser must not run for a text PDF"))
    pdf = make_pdf(tmp_path / "t.pdf", [["x" * 300]])
    assert parse_document(pdf, "auto").parser == "pypdf"


def test_auto_escalates_scanned_pdf_to_deep(tmp_path, monkeypatch):
    pdf = make_image_only_pdf(tmp_path / "scan.pdf")
    assert parsing.looks_scanned(parse_pdf_fast(pdf))
    deep = ParsedDocument([Element("text", "OCR text", 1)], parser="docling", n_pages=1)
    monkeypatch.setattr(parsing, "_deep", lambda p: deep)
    out = parse_document(pdf, "auto")
    assert out.parser == "docling" and any("OCR" in w for w in out.warnings)


def test_auto_falls_back_with_warning_when_deep_unavailable(tmp_path, monkeypatch):
    pdf = make_image_only_pdf(tmp_path / "scan.pdf")
    def boom(p):
        raise ParserUnavailable("Docling is not installed.")
    monkeypatch.setattr(parsing, "_deep", boom)
    out = parse_document(pdf, "auto")
    assert out.parser == "pypdf" and out.n_chars < 20 and "not installed" in out.warnings[0]


def test_mode_routing(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / "t.pdf", [["hello world"]])
    calls = []
    monkeypatch.setattr(parsing, "_deep", lambda p: calls.append(p.name) or ParsedDocument([], parser="docling"))
    assert parse_document(pdf, "fast").parser == "pypdf" and not calls
    assert parse_document(pdf, "deep").parser == "docling" and calls == ["t.pdf"]
    docx = tmp_path / "x.docx"
    docx.write_bytes(b"PK")
    parse_document(docx, "fast")                         # non-PDF formats always go to Docling
    assert calls == ["t.pdf", "x.docx"]


# ─────────────────────────── Docling adapter ───────────────────────────
docling = pytest.importorskip("docling", reason="Docling not installed")


def test_docling_real_conversion_of_docx(tmp_path):
    import docx
    d = docx.Document()
    d.add_heading("Diabetes Guide", 0)
    d.add_heading("2. Symptoms", 1)
    d.add_paragraph("Common symptoms include increased thirst and fatigue.")
    d.add_heading("2.1 Medication", 2)
    t = d.add_table(rows=3, cols=2)
    for i, row in enumerate([("Drug", "Dose"), ("Metformin", "500-2000 mg/day"), ("Glipizide", "2.5-20 mg/day")]):
        for j, cell in enumerate(row):
            t.cell(i, j).text = cell
    path = tmp_path / "guide.docx"
    d.save(path)

    parsed = parse_document(path)                         # non-PDF → real Docling
    assert parsed.parser == "docling"
    kinds = [(e.kind, e.section) for e in parsed.elements]
    assert ("text", "Diabetes Guide > 2. Symptoms") in kinds
    table = next(e for e in parsed.elements if e.kind == "table")
    assert table.section == "Diabetes Guide > 2. Symptoms > 2.1 Medication"
    assert "| Metformin" in table.text and "500-2000 mg/day" in table.text

    chunks = chunk_document(parsed, **meta())
    tchunk = next(c for c in chunks if c.metadata["element_type"] == "table")
    assert tchunk.page_content.startswith("Diabetes Guide > 2. Symptoms > 2.1 Medication\n|")
    assert "page" not in tchunk.metadata                  # DOCX has no pages → key omitted, never None


def test_docling_markdown_and_html(tmp_path):
    md = tmp_path / "n.md"
    md.write_text("# Hypertension\n\n## Targets\n\nBP target is below 130/80 mmHg.\n\n- Reduce salt\n- Exercise\n")
    parsed = parse_document(md)
    texts = [e.text for e in parsed.elements]
    assert any("130/80" in t for t in texts) and any(t.endswith("Reduce salt") for t in texts)
    html = tmp_path / "p.html"
    html.write_text("<h1>Asthma</h1><h2>Triggers</h2><p>Pollen and dust mites.</p>")
    assert any("dust mites" in e.text and e.section == "Asthma > Triggers" for e in parse_document(html).elements)


def test_adapter_keeps_page_numbers_and_headings():
    """Synthetic multi-page DoclingDocument — the same structure Docling produces for a PDF."""
    from docling_core.types.doc import (BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size,
                                        TableCell, TableData)
    from app.rag.parsing.docling_parser import elements_from_docling

    doc = DoclingDocument(name="x")
    for n in (1, 2, 3):
        doc.add_page(page_no=n, size=Size(width=600, height=800))
    prov = lambda n: ProvenanceItem(page_no=n, bbox=BoundingBox(l=0, t=0, r=10, b=10), charspan=(0, 5))  # noqa: E731
    doc.add_title("Cardiology Handbook", prov=prov(1))
    doc.add_heading("Hypertension", level=1, prov=prov(1))
    doc.add_text(label=DocItemLabel.TEXT, text="Target is below 130/80.", prov=prov(1))
    doc.add_text(label=DocItemLabel.PAGE_HEADER, text="RUNNING HEADER", prov=prov(2))
    doc.add_text(label=DocItemLabel.TEXT, text="Continues on page two.", prov=prov(2))
    doc.add_heading("Drugs", level=2, prov=prov(3))
    cells = [TableCell(text=t, start_row_offset_idx=r, end_row_offset_idx=r + 1, start_col_offset_idx=c,
                       end_col_offset_idx=c + 1, column_header=(r == 0))
             for r, row in enumerate([("Drug", "Dose"), ("Lisinopril", "10 mg")]) for c, t in enumerate(row)]
    doc.add_table(data=TableData(table_cells=cells, num_rows=2, num_cols=2), prov=prov(3))
    doc.add_list_item("Reduce salt", prov=prov(3))

    els = elements_from_docling(doc)
    assert [(e.kind, e.page) for e in els] == [("text", 1), ("text", 2), ("table", 3), ("text", 3)]
    assert els[0].section == "Cardiology Handbook > Hypertension"
    assert els[2].section == "Cardiology Handbook > Hypertension > Drugs"
    assert "Lisinopril" in els[2].text and "RUNNING HEADER" not in "".join(e.text for e in els)
    assert els[3].text.endswith("Reduce salt")
    chunks = chunk_document(ParsedDocument(els, parser="docling", n_pages=3), **meta())
    first = chunks[0]                                     # page-1 and page-2 paragraphs share a heading → one chunk
    assert (first.metadata["page"], first.metadata["page_end"]) == (1, 2)
    assert first.metadata["section"] == "Cardiology Handbook > Hypertension"


def test_deep_mode_without_models_is_a_clear_error(tmp_path):
    """In this environment Docling's ML stack may be missing; the error must say what to install."""
    pdf = make_pdf(tmp_path / "t.pdf", [["hello"]])
    try:
        import torch  # noqa: F401
        pytest.skip("full Docling stack present")
    except ImportError:
        with pytest.raises(ParserUnavailable) as e:
            parse_document(pdf, "deep")
        assert "pip install docling" in str(e.value)


# ─────────────────────────── chunker ───────────────────────────
def test_chunks_do_not_cross_sections_and_carry_heading():
    els = [Element("text", "A" * 200, 1, "Intro"), Element("text", "B" * 200, 1, "Intro"),
           Element("text", "C" * 200, 2, "Dosage")]
    chunks = chunk_document(ParsedDocument(els, "docling"), **meta())
    assert [c.metadata["section"] for c in chunks] == ["Intro", "Dosage"]
    assert chunks[0].page_content.startswith("Intro\nAAAA") and "BBBB" in chunks[0].page_content
    assert "CCCC" not in chunks[0].page_content


def test_packing_respects_chunk_size(monkeypatch):
    monkeypatch.setattr(settings, "chunk_size", 500)
    els = [Element("text", "x" * 300, 1, "S"), Element("text", "y" * 300, 1, "S"), Element("text", "z" * 100, 1, "S")]
    chunks = chunk_document(ParsedDocument(els, "docling"), **meta())
    assert len(chunks) == 2 and all(len(c.page_content) <= 600 for c in chunks)


def test_oversized_element_is_split_with_overlap(monkeypatch):
    monkeypatch.setattr(settings, "chunk_size", 200)
    monkeypatch.setattr(settings, "chunk_overlap", 40)
    text = " ".join(f"word{i}" for i in range(200))
    chunks = chunk_document(ParsedDocument([Element("text", text, 5)], "pypdf"), **meta())
    assert len(chunks) > 3 and all(c.metadata["page"] == 5 for c in chunks)
    assert chunks[0].page_content[-15:].split()[-1] in chunks[1].page_content            # overlap


def test_table_split_repeats_header():
    rows = "\n".join(f"| drug{i} | {i} mg |" for i in range(60))
    table = f"Table 1: Doses\n| Drug | Dose |\n|---|---|\n{rows}"
    parts = split_table(table, 400)
    assert len(parts) > 2
    for p in parts:
        assert p.startswith("Table 1: Doses\n| Drug | Dose |\n|---|---|") and len(p) <= 460
    assert sum(p.count("| drug") for p in parts) == 60                                    # no row lost or duplicated
    assert split_table("| a | b |\n|---|---|\n| 1 | 2 |", 400) == ["| a | b |\n|---|---|\n| 1 | 2 |"]


def test_tables_are_separate_chunks_with_page():
    els = [Element("text", "Intro text.", 1, "Drugs"), Element("table", "| a | b |\n|---|---|\n| 1 | 2 |", 2, "Drugs"),
           Element("text", "After the table.", 2, "Drugs")]
    chunks = chunk_document(ParsedDocument(els, "docling"), **meta())
    assert [c.metadata["element_type"] for c in chunks] == ["text", "table", "text"]
    assert chunks[1].metadata["page"] == 2 and chunks[1].page_content.startswith("Drugs\n| a | b |")


def test_metadata_is_chroma_safe_and_sequential():
    els = [Element("text", "t" * 50, 1, "S"), Element("table", "| a |\n|---|\n| 1 |", None, "")]
    chunks = chunk_document(ParsedDocument(els, "docling"), **meta())
    assert [c.metadata["chunk_id"] for c in chunks] == [0, 1]
    for c in chunks:
        assert all(isinstance(v, (str, int, float, bool)) for v in c.metadata.values())   # Chroma rejects None / lists
        assert {"source", "doc_id", "role", "uploaded_by", "parser", "element_type", "section"} <= set(c.metadata)
    assert "page" not in chunks[1].metadata


def test_default_models_dir(tmp_path, monkeypatch):
    from app.rag.parsing.docling_parser import default_models_dir
    monkeypatch.setattr(settings, "docling_models_dir", "")
    monkeypatch.chdir(tmp_path)
    assert default_models_dir() == ""                                   # nothing downloaded → Docling's own cache
    (tmp_path / "docling_models").mkdir()
    assert default_models_dir() == ""                                   # empty folder doesn't count
    (tmp_path / "docling_models" / "layout").mkdir()
    assert default_models_dir() == str(tmp_path / "docling_models")     # found automatically
    monkeypatch.setattr(settings, "docling_models_dir", "/custom")
    assert default_models_dir() == "/custom"                            # explicit setting wins
