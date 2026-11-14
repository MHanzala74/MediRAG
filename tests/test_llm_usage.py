from langchain_core.messages import AIMessage

from app.rag.llm_usage import message_text, token_usage


def test_provider_reported_usage():
    m = AIMessage(content="hi", usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
    assert token_usage(m, prompt_chars=9999, answer="hi") == {
        "prompt_tokens": 120, "completion_tokens": 30, "tokens_estimated": False}


def test_legacy_response_metadata_usage():
    m = AIMessage(content="hi", response_metadata={"token_usage": {"prompt_tokens": 50, "completion_tokens": 7}})
    assert token_usage(m, 0, "hi") == {"prompt_tokens": 50, "completion_tokens": 7, "tokens_estimated": False}


def test_estimate_when_provider_reports_nothing():
    m = AIMessage(content="x" * 40)
    u = token_usage(m, prompt_chars=400, answer="x" * 40)
    assert u == {"prompt_tokens": 100, "completion_tokens": 10, "tokens_estimated": True}


def test_message_text_handles_block_lists():
    assert message_text(AIMessage(content="plain")) == "plain"
    assert message_text(AIMessage(content=[{"type": "text", "text": "a"}, "b"])) == "ab"
