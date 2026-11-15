"""Test doubles for the RAG chain."""
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

USAGE = {"input_tokens": 120, "output_tokens": 12, "total_tokens": 132}


class ScriptedChatModel(BaseChatModel):
    """Answers with fixed text. Supports invoke AND stream; reports usage like a real provider."""
    model_name: str = "gpt-4o-mini-2024-07-18"
    text: str = "Diabetes causes increased thirst."
    fail: bool = False
    calls: list = []

    @property
    def _llm_type(self):
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        if self.fail:
            raise RuntimeError("provider down")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.text, usage_metadata=USAGE))])

    def _stream(self, messages, stop=None, run_manager=None, **kw):
        if self.fail:
            raise RuntimeError("provider down")
        for i, w in enumerate(self.text.split(" ")):
            yield ChatGenerationChunk(message=AIMessageChunk(content=w if i == 0 else " " + w))
        yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=USAGE))


class FakeDoc:
    def __init__(self, text, source="diabetes.pdf", doc_id="d1"):
        self.page_content, self.metadata = text, {"source": source, "doc_id": doc_id}


class FakeRails:
    """Stands in for NeMoGuardrailsManager."""
    def __init__(self):
        self.block_input = False
        self.block_context = False

    async def check_input(self, q):
        from app.guardrails.nemo_guardrails import GuardrailResult
        return GuardrailResult(passed=not self.block_input, reason="off-topic", blocked_response="Medical questions only.")

    def check_context(self, chunks):
        from app.guardrails.nemo_guardrails import GuardrailResult
        return GuardrailResult(passed=not self.block_context, reason="no context", blocked_response="Nothing relevant found.")

    def check_output(self, raw):
        return raw + "\n\n---\nDISCLAIMER"

    def hallucination_check(self, raw):
        return True
