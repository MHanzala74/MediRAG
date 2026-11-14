"""
Token streaming core, independent of retrieval/guardrail wiring so it can be tested on its own.

`stream_llm` iterates a LangChain runnable (prompt | chat-model) with `astream` and yields events:

    {"type": "token",   "text": "..."}      incremental answer text
    {"type": "replace", "text": "..."}      full text, when the output rail rewrote the answer
    {"type": "end",     "raw": str, "final": str, "message": AIMessageChunk | None}

Output guardrail: `finalize(raw)` (NeMo `check_output`) needs the COMPLETE answer, so it runs after the
last token. In the normal case it only appends the medical disclaimer → the disclaimer is streamed as one
extra token. If it rewrote the beginning (e.g. a safety warning prefix) a `replace` event carries the final text.
"""
from typing import AsyncIterator, Callable

from app.rag.llm_usage import message_text


async def stream_llm(chain, inputs: dict, finalize: Callable[[str], str]) -> AsyncIterator[dict]:
    parts: list[str] = []
    full = None                                     # chunks add up, summing usage_metadata
    async for chunk in chain.astream(inputs):
        text = message_text(chunk) if hasattr(chunk, "content") else str(chunk)
        if hasattr(chunk, "content"):
            full = chunk if full is None else full + chunk
        if text:
            parts.append(text)
            yield {"type": "token", "text": text}

    raw = "".join(parts)
    final = finalize(raw)
    if final.startswith(raw):
        extra = final[len(raw):]
        if extra:
            yield {"type": "token", "text": extra}
    else:
        yield {"type": "replace", "text": final}
    yield {"type": "end", "raw": raw, "final": final, "message": full}
