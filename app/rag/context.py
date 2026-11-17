"""
Turn retrieved chunks into the context the LLM sees — shared by the chat chain AND the evaluation
pipeline, so evaluation always measures exactly what production sends to the model.

Every passage is tagged `[Source: file, p. N, Section]` so the model can cite filename + page.
"""
from dataclasses import dataclass


@dataclass
class ContextBundle:
    parts: list[str]          # tagged passages (what the model reads; also DeepEval's retrieval_context)
    text: str                 # parts joined, ready for the prompt
    sources: list[dict]       # unique (file, page) citations for the UI


def build_context(docs) -> ContextBundle:
    parts, sources, seen = [], [], set()
    for doc in docs:
        fname = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page")
        section = doc.metadata.get("section") or None
        tag = f"[Source: {fname}" + (f", p. {page}" if page else "") + (f", {section}" if section else "") + "]"
        parts.append(f"{tag}\n{doc.page_content}")
        if (fname, page) not in seen:
            seen.add((fname, page))
            sources.append({
                "filename": fname,
                "doc_id": doc.metadata.get("doc_id", ""),
                "chunk_preview": doc.page_content[:120].replace("\n", " ") + "...",
                "page": page,
                "section": section,
            })
    return ContextBundle(parts=parts, text="\n\n---\n".join(parts), sources=sources)
