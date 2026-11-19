"""
Document upload → background parsing (pypdf / real Docling) → real ChromaDB → retrieval with page numbers.
Real PostgreSQL, real Chroma (temp dir), real auth. Only the embedding model is fake (deterministic vectors).
"""
import importlib
import io
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_community.vectorstores import Chroma
from langchain_core.embeddings import DeterministicFakeEmbedding

from app.core.config import settings
from app.db import models
from app.rag import ingestion, parsing
from app.rag.parsing import ParserUnavailable, docling_available
from app.rag.retriever.hybrid_retriever import HybridRetriever

# test_api_integration replaces app.api.v1.documents / app.rag.chain with stubs; this file needs the REAL ones.
for _name in ("app.api.v1.documents", "app.api.v1.auth"):
    sys.modules.pop(_name, None)
docs_api = importlib.import_module("app.api.v1.documents")
auth_api = importlib.import_module("app.api.v1.auth")

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

API = "/api/v1"


def pdf_bytes(pages):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for lines in pages:
        y = 780
        for ln in lines:
            c.drawString(72, y, ln)
            y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


def image_pdf_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, "PDF")
    return buf.getvalue()


def docx_bytes():
    import docx
    d = docx.Document()
    d.add_heading("Diabetes Guide", 0)
    d.add_heading("Medication", 1)
    t = d.add_table(rows=2, cols=2)
    for i, row in enumerate([("Drug", "Dose"), ("Metformin", "500-2000 mg/day")]):
        for j, c in enumerate(row):
            t.cell(i, j).text = c
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def stack(db, tmp_path_factory):
    models.create_tables()
    base = tmp_path_factory.mktemp("docs")
    store = Chroma(collection_name="doc_tests", embedding_function=DeterministicFakeEmbedding(size=64),
                   persist_directory=str(base / "chroma"))
    mp = pytest.MonkeyPatch()
    mp.setattr(ingestion, "get_vectorstore", lambda: store)
    mp.setattr(settings, "docs_upload_dir", str(base / "uploads"))
    app = FastAPI()
    app.include_router(auth_api.router, prefix=API)
    app.include_router(docs_api.router, prefix=API)
    client = TestClient(app)
    tokens = {}
    for name, role in (("doc_admin", "admin"), ("doc_user", "user")):
        assert client.post(f"{API}/auth/signup", json={"username": name, "password": "password123", "role": role}).status_code == 201
        r = client.post(f"{API}/auth/login", data={"username": name, "password": "password123"})
        tokens[role] = {"Authorization": "Bearer " + r.json()["access_token"]}
    yield client, tokens, store, base
    mp.undo()


def upload(client, tokens, name, data, role="user", wait=True, **form):
    return client.post(f"{API}/documents/upload{'?wait=true' if wait else ''}", headers=tokens["admin"],
                       files={"file": (name, data, "application/octet-stream")}, data={"role": role, **form})


def chunks_of(store, doc_id):
    return store.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])


def wait_status(client, tokens, doc_id, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"{API}/documents/{doc_id}", headers=tokens["admin"]).json()
        if d["status"] != "processing":
            return d
        time.sleep(0.05)
    raise AssertionError("ingestion did not finish")


def test_only_admins_can_manage_documents(stack):
    client, tokens, *_ = stack
    for call in (lambda h: client.get(f"{API}/documents/list", headers=h), lambda h: client.get(f"{API}/documents/capabilities", headers=h),
                 lambda h: client.post(f"{API}/documents/upload", headers=h, files={"file": ("a.pdf", b"%PDF-")}, data={"role": "user"}),
                 lambda h: client.delete(f"{API}/documents/x", headers=h)):
        assert call(tokens["user"]).status_code == 403
        assert call({}).status_code == 401


def test_capabilities(stack):
    client, tokens, *_ = stack
    c = client.get(f"{API}/documents/capabilities", headers=tokens["admin"]).json()
    assert c["docling_installed"] is docling_available() and ".pdf" in c["extensions"]
    assert (".docx" in c["extensions"]) is docling_available()                      # other formats only with Docling
    assert c["modes"] == ["auto", "fast", "deep"] and c["max_upload_mb"] == settings.max_upload_mb


def test_pdf_upload_indexes_chunks_with_pages_and_is_retrievable(stack):
    client, tokens, store, base = stack
    data = pdf_bytes([["Diabetes causes increased thirst and frequent urination."], ["Hypertension target is below 130/80 mmHg."]])
    r = upload(client, tokens, "guide.pdf", data, role="user", parser="fast")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready" and body["chunk_count"] == 2 and body["parser"] == "pypdf"

    got = chunks_of(store, body["doc_id"])
    assert sorted(m["page"] for m in got["metadatas"]) == [1, 2]
    assert all(m["role"] == "user" and m["source"] == "guide.pdf" for m in got["metadatas"])

    hits = HybridRetriever(store).retrieve("what is the blood pressure target", "user", k=4)
    assert {d.metadata["page"] for d in hits} == {1, 2} and all(d.metadata["source"] == "guide.pdf" for d in hits)
    assert HybridRetriever(store).retrieve("anything", "admin", k=4)                       # admins see user docs too

    doc = next(d for d in client.get(f"{API}/documents/list", headers=tokens["admin"]).json()["documents"] if d["doc_id"] == body["doc_id"])
    assert doc["status"] == "ready" and doc["pages"] == 2 and doc["parser"] == "pypdf" and doc["error"] is None


def test_upload_returns_immediately_and_finishes_in_background(stack):
    client, tokens, store, _ = stack
    r = upload(client, tokens, "bg.pdf", pdf_bytes([["Asthma triggers include pollen."]]), wait=False)
    assert r.status_code == 202 and r.json()["status"] == "processing"
    final = wait_status(client, tokens, r.json()["doc_id"])
    assert final["status"] == "ready" and final["chunk_count"] == 1
    assert len(chunks_of(store, r.json()["doc_id"])["ids"]) == 1


@pytest.mark.skipif(not docling_available(), reason="Docling not installed (uv sync --extra docling)")
def test_docx_goes_through_real_docling_with_table_chunk(stack):
    client, tokens, store, _ = stack
    r = upload(client, tokens, "guide.docx", docx_bytes())
    assert r.status_code == 200, r.text
    assert r.json()["parser"] == "docling"
    got = chunks_of(store, r.json()["doc_id"])
    tables = [(d, m) for d, m in zip(got["documents"], got["metadatas"]) if m["element_type"] == "table"]
    assert len(tables) == 1 and "Metformin" in tables[0][0] and tables[0][1]["section"] == "Diabetes Guide > Medication"
    assert all("page" not in m for m in got["metadatas"])                                  # no pages in DOCX


def test_scanned_pdf_without_ocr_fails_with_a_helpful_message(stack, monkeypatch):
    client, tokens, store, _ = stack

    def no_docling(path):
        raise ParserUnavailable("Docling is not installed.")
    monkeypatch.setattr(parsing, "_deep", no_docling)
    r = upload(client, tokens, "scan.pdf", image_pdf_bytes())
    assert r.status_code == 422 and "No text could be extracted" in r.json()["detail"]
    failed = [d for d in client.get(f"{API}/documents/list", headers=tokens["admin"]).json()["documents"] if d["filename"] == "scan.pdf"][0]
    assert failed["status"] == "failed" and failed["chunk_count"] == 0
    assert chunks_of(store, failed["doc_id"])["ids"] == []


def test_scanned_pdf_is_escalated_to_deep_in_auto_mode(stack, monkeypatch):
    client, tokens, store, _ = stack
    from app.rag.parsing import Element, ParsedDocument
    monkeypatch.setattr(parsing, "_deep", lambda p: ParsedDocument([Element("text", "OCR recovered: insulin dosing.", 1)], "docling", 1))
    r = upload(client, tokens, "scan2.pdf", image_pdf_bytes())
    assert r.status_code == 200 and r.json()["parser"] == "docling"
    assert "OCR" in r.json()["note"]


@pytest.mark.parametrize("name,data,status,fragment", [
    ("virus.exe", b"MZ....", 400, "Unsupported file type"),
    ("fake.pdf", b"just some text, not a pdf", 400, "does not look like"),
    ("empty.pdf", b"", 400, "empty"),
    ("notes.png", b"%PDF-1.4", 400, "does not look like" if docling_available() else "Unsupported file type"),
])
def test_bad_uploads_are_rejected(stack, name, data, status, fragment):
    client, tokens, *_ = stack
    r = upload(client, tokens, name, data)
    assert r.status_code == status and fragment in r.json()["detail"]


def test_size_limit_and_filename_sanitising(stack, monkeypatch):
    client, tokens, store, base = stack
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    assert upload(client, tokens, "big.pdf", pdf_bytes([["x"]])).status_code == 413
    monkeypatch.setattr(settings, "max_upload_mb", 50)
    r = upload(client, tokens, "../../etc/evil name.pdf", pdf_bytes([["Safe filename test."]]))
    assert r.status_code == 200 and r.json()["filename"] == "evil name.pdf"
    saved = [p.name for p in (base / "uploads").iterdir()]
    assert any(n.endswith("_evil name.pdf") for n in saved) and not (base / "etc").exists()


def test_validation_of_role_parser_and_deep_without_docling(stack, monkeypatch):
    client, tokens, *_ = stack
    data = pdf_bytes([["x"]])
    assert upload(client, tokens, "a.pdf", data, role="root").status_code == 400
    assert upload(client, tokens, "a.pdf", data, parser="turbo").status_code == 400
    monkeypatch.setattr(docs_api, "docling_available", lambda: False)
    r = upload(client, tokens, "a.pdf", data, parser="deep")
    assert r.status_code == 400 and "pip install docling" in r.json()["detail"]


def test_unexpected_failure_is_recorded_without_leaking_internals(stack, monkeypatch):
    client, tokens, *_ = stack

    def boom():
        raise RuntimeError("secret internal path /srv/chroma")
    monkeypatch.setattr(ingestion, "get_vectorstore", boom)
    r = upload(client, tokens, "boom.pdf", pdf_bytes([["Some text."]]))
    assert r.status_code == 422
    assert "RuntimeError" in r.json()["detail"] and "secret" not in r.json()["detail"]


def test_partial_index_is_cleaned_up_on_failure(stack, monkeypatch):
    client, tokens, store, _ = stack

    class FlakyStore:
        """Adds the chunks, then fails (e.g. while persisting)."""
        _collection = store._collection
        def add_documents(self, docs):
            store.add_documents(docs)
            raise RuntimeError("disk full")
    monkeypatch.setattr(ingestion, "get_vectorstore", lambda: FlakyStore())
    r = upload(client, tokens, "flaky.pdf", pdf_bytes([["Chunk that must not survive."]]))
    assert r.status_code == 422
    doc_id = [d for d in client.get(f"{API}/documents/list", headers=tokens["admin"]).json()["documents"] if d["filename"] == "flaky.pdf"][0]["doc_id"]
    assert chunks_of(store, doc_id)["ids"] == []


def test_delete_removes_chunks_file_and_record(stack):
    client, tokens, store, base = stack
    r = upload(client, tokens, "todelete.pdf", pdf_bytes([["Delete me please."]]))
    doc_id = r.json()["doc_id"]
    assert chunks_of(store, doc_id)["ids"] and list((base / "uploads").glob(f"{doc_id}_*"))
    assert client.delete(f"{API}/documents/{doc_id}", headers=tokens["admin"]).json() == {"deleted": doc_id}
    assert chunks_of(store, doc_id)["ids"] == [] and not list((base / "uploads").glob(f"{doc_id}_*"))
    assert client.get(f"{API}/documents/{doc_id}", headers=tokens["admin"]).status_code == 404
    assert client.delete(f"{API}/documents/{doc_id}", headers=tokens["admin"]).status_code == 404


def test_cannot_delete_while_processing_and_restart_recovery(stack):
    client, tokens, *_ = stack
    models.create_doc_record("stuck-1", "stuck.pdf", "user", "doc_admin")
    assert client.delete(f"{API}/documents/stuck-1", headers=tokens["admin"]).status_code == 409
    assert models.fail_interrupted_ingestions() >= 1                       # what happens at startup
    d = client.get(f"{API}/documents/stuck-1", headers=tokens["admin"]).json()
    assert d["status"] == "failed" and "restart" in d["error"]
    assert client.delete(f"{API}/documents/stuck-1", headers=tokens["admin"]).status_code == 200
