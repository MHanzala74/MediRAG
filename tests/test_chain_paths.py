"""
The refactored RAG chain: non-streaming `run_rag_chain` and streaming `prepare_rag` + `stream_rag`
must behave identically (answer, sources, usage, persistence). Real PostgreSQL; fake LLM/retriever/rails.
"""
import asyncio
import importlib
import sys

import pytest

from app.utils.exceptions import GuardrailException, RAGException
from tests.fakes import FakeDoc, FakeRails, ScriptedChatModel

# test_api_integration replaces app.rag.chain with a stub; here we need the REAL module.
sys.modules.pop("app.rag.chain", None)
chain = importlib.import_module("app.rag.chain")
from app.db import models  # noqa: E402


@pytest.fixture(scope="module")
def schema(db):
    models.create_tables()


@pytest.fixture
def env(schema, monkeypatch):
    rails, llm = FakeRails(), ScriptedChatModel()

    class FakeRetriever:
        def __init__(self, vs): ...
        def retrieve(self, q, role):
            return [FakeDoc("Diabetes symptoms include thirst. " * 5), FakeDoc("More on thirst. " * 5), FakeDoc("Other file. " * 5, "other.pdf", "d2")]

    class FakeReranker:
        def rerank(self, q, docs):
            return docs[:3]

    monkeypatch.setattr(chain, "nemo_guardrails", rails)
    monkeypatch.setattr(chain, "get_llm", lambda: llm)
    monkeypatch.setattr(chain, "get_vectorstore", lambda: None)
    monkeypatch.setattr(chain, "HybridRetriever", FakeRetriever)
    monkeypatch.setattr(chain, "reranker", FakeReranker())
    return rails, llm


def run(coro):
    return asyncio.run(coro)


async def collect_stream(q, sid):
    prepared = await chain.prepare_rag(q, "user", sid)
    events = [e async for e in chain.stream_rag(q, sid, prepared)]
    return prepared, events


def saved(sid):
    return [(r["role"], r["content"]) for r in models.get_history(sid, limit=50)]


def test_non_streaming_path_unchanged(env):
    r = run(chain.run_rag_chain("what is diabetes?", "user", "c-nonstream"))
    assert r["answer"] == "Diabetes causes increased thirst.\n\n---\nDISCLAIMER"
    assert [s["filename"] for s in r["sources"]] == ["diabetes.pdf", "other.pdf"]        # de-duplicated by file
    assert r["usage"] == {"prompt_tokens": 120, "completion_tokens": 12, "tokens_estimated": False}
    assert r["model_used"] == "gpt-4o-mini-2024-07-18" and r["guardrail_passed"] is True
    assert saved("c-nonstream") == [("user", "what is diabetes?"), ("assistant", r["answer"])]


def test_streaming_path_gives_same_result_and_persistence(env):
    ref = run(chain.run_rag_chain("what is diabetes?", "user", "c-ref"))
    prepared, events = run(collect_stream("what is diabetes?", "c-stream"))
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "done" and kinds.count("token") >= 4 and "replace" not in kinds
    result = events[-1]["result"]
    streamed = "".join(e["text"] for e in events if e["type"] == "token")
    assert streamed == result["answer"]                                                  # what the user saw == what is saved
    for key in ("answer", "sources", "usage", "model_used", "retrieval_method", "guardrail_passed", "provider"):
        assert result[key] == ref[key], key
    assert saved("c-stream") == [("user", "what is diabetes?"), ("assistant", result["answer"])]


def test_second_turn_includes_history(env):
    run(chain.run_rag_chain("first question", "user", "c-hist"))
    prepared, _ = run(collect_stream("second question", "c-hist"))
    roles = [type(m).__name__ for m in prepared.inputs["chat_history"]]
    assert roles == ["HumanMessage", "AIMessage"]
    assert prepared.prompt_chars > 0


def test_input_rail_blocks_before_anything_happens(env):
    rails, _ = env
    rails.block_input = True
    for call in (lambda: chain.run_rag_chain("write python", "user", "c-blocked"),
                 lambda: chain.prepare_rag("write python", "user", "c-blocked")):
        with pytest.raises(GuardrailException):
            run(call())
    assert saved("c-blocked") == []


def test_context_rail_refusal_is_saved_and_has_no_llm_call(env):
    rails, _ = env
    rails.block_context = True
    r = run(chain.run_rag_chain("rare disease", "user", "c-ctx1"))
    assert r["guardrail_passed"] is False and r["model_used"] == "none" and r["answer"] == "Nothing relevant found."
    prepared = run(chain.prepare_rag("rare disease", "user", "c-ctx2"))
    assert prepared.blocked_result["answer"] == "Nothing relevant found." and prepared.inputs is None
    assert saved("c-ctx1") == [("user", "rare disease"), ("assistant", "Nothing relevant found.")]


def test_llm_failure_raises_ragexception_and_saves_nothing(env):
    _, llm = env
    llm.fail = True
    with pytest.raises(RAGException):
        run(chain.run_rag_chain("q", "user", "c-fail1"))
    with pytest.raises(RAGException):
        run(collect_stream("q", "c-fail2"))
    assert saved("c-fail1") == [] and saved("c-fail2") == []


def test_stopping_a_stream_saves_nothing(env):
    async def go():
        prepared = await chain.prepare_rag("what is diabetes?", "user", "c-stop")
        gen = chain.stream_rag("what is diabetes?", "c-stop", prepared)
        first = await gen.__anext__()
        await gen.aclose()
        return first
    assert run(go())["type"] == "token"
    assert saved("c-stop") == []
