import asyncio

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.prompts import ChatPromptTemplate

from app.rag.streaming import stream_llm

DISCLAIMER = "\n\n---\nDISCLAIMER"


def chain_for(text, **kw):
    prompt = ChatPromptTemplate.from_messages([("human", "{q}")])
    msgs = iter([AIMessage(content=text, **kw)])
    return prompt | GenericFakeChatModel(messages=msgs)


async def collect(gen):
    return [e async for e in gen]


def test_tokens_stream_then_disclaimer_is_appended():
    events = asyncio.run(collect(stream_llm(chain_for("Diabetes causes thirst and fatigue."), {"q": "x"}, lambda r: r + DISCLAIMER)))
    kinds = [e["type"] for e in events]
    assert kinds.count("token") > 2 and kinds[-1] == "end" and "replace" not in kinds     # really incremental
    streamed = "".join(e["text"] for e in events if e["type"] == "token")
    assert streamed == "Diabetes causes thirst and fatigue." + DISCLAIMER
    assert events[-1]["raw"] == "Diabetes causes thirst and fatigue." and events[-1]["final"] == streamed
    assert events[-2] == {"type": "token", "text": DISCLAIMER}                              # disclaimer streamed last


def test_replace_event_when_output_rail_rewrites_the_start():
    warn = lambda r: "WARNING\n\n" + r + DISCLAIMER
    events = asyncio.run(collect(stream_llm(chain_for("take exactly 50 mg"), {"q": "x"}, warn)))
    replaces = [e for e in events if e["type"] == "replace"]
    assert len(replaces) == 1 and replaces[0]["text"] == "WARNING\n\ntake exactly 50 mg" + DISCLAIMER
    assert events[-1]["final"] == replaces[0]["text"]


def test_no_guardrails_passthrough():
    events = asyncio.run(collect(stream_llm(chain_for("plain"), {"q": "x"}, lambda r: r)))
    assert "".join(e["text"] for e in events if e["type"] == "token") == "plain"


class UsageAtEndModel(BaseChatModel):
    """Streams words and reports token usage on the LAST chunk only, like real providers do."""

    @property
    def _llm_type(self):
        return "usage-at-end"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        raise NotImplementedError

    def _stream(self, messages, stop=None, run_manager=None, **kw):
        for w in ["a ", "b ", "c"]:
            yield ChatGenerationChunk(message=AIMessageChunk(content=w))
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="", usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}))


def test_usage_metadata_is_merged_from_chunks():
    chain = ChatPromptTemplate.from_messages([("human", "{q}")]) | UsageAtEndModel()
    ev = asyncio.run(collect(stream_llm(chain, {"q": "x"}, lambda r: r)))
    msg = ev[-1]["message"]
    assert msg.usage_metadata["input_tokens"] == 11 and msg.usage_metadata["output_tokens"] == 3
    assert ev[-1]["raw"] == "a b c"


def test_early_close_stops_generation():
    async def run():
        gen = stream_llm(chain_for("one two three four five six"), {"q": "x"}, lambda r: r)
        first = await gen.__anext__()
        await gen.aclose()                      # what happens when the client disconnects
        return first
    assert asyncio.run(run())["type"] == "token"
