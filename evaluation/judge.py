"""
Judge LLM for DeepEval.

DeepEval metrics (Faithfulness, Contextual Precision, ...) are LLM-as-a-judge
metrics. By default DeepEval talks to OpenAI only. MediRAG also supports Groq,
so this wrapper lets *any* LangChain chat model act as the judge.

Environment variables (all optional):
    EVAL_JUDGE_PROVIDER   openai | groq      (default: PRIMARY_LLM)
    EVAL_JUDGE_MODEL      model name         (default: provider default)
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional, Type

from deepeval.models import DeepEvalBaseLLM
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from app.core.config import settings


def _build_chat_model() -> tuple[BaseChatModel, str]:
    provider = os.getenv("EVAL_JUDGE_PROVIDER", settings.primary_llm).lower()
    model_override = os.getenv("EVAL_JUDGE_MODEL")

    if provider == "openai" and settings.openai_api_key:
        from langchain_openai import ChatOpenAI

        name = model_override or settings.openai_model
        return ChatOpenAI(model=name, api_key=settings.openai_api_key, temperature=0.0), name

    if settings.groq_api_key:
        from langchain_groq import ChatGroq

        name = model_override or settings.groq_model
        return ChatGroq(model=name, api_key=settings.groq_api_key, temperature=0.0), name

    raise RuntimeError(
        "No judge LLM available. Set OPENAI_API_KEY or GROQ_API_KEY in .env"
    )


def _extract_json(text: str) -> str:
    """Pull the first JSON object out of a model response (handles ``` fences)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


class MediRAGJudge(DeepEvalBaseLLM):
    """Adapter that lets a LangChain chat model be used as a DeepEval judge."""

    def __init__(self) -> None:
        self._chat, self._model_name = _build_chat_model()
        super().__init__(model=self._model_name)

    # -- DeepEvalBaseLLM interface ----------------------------------------
    def load_model(self) -> BaseChatModel:
        return self._chat

    def get_model_name(self) -> str:
        return f"MediRAG-judge ({self._model_name})"

    def generate(self, prompt: str, schema: Optional[Type[BaseModel]] = None):
        if schema is None:
            return self._chat.invoke(prompt).content
        return self._parse(self._chat.invoke(self._json_prompt(prompt, schema)).content, schema)

    async def a_generate(self, prompt: str, schema: Optional[Type[BaseModel]] = None):
        if schema is None:
            return (await self._chat.ainvoke(prompt)).content
        res = await self._chat.ainvoke(self._json_prompt(prompt, schema))
        return self._parse(res.content, schema)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _json_prompt(prompt: str, schema: Type[BaseModel]) -> str:
        return (
            f"{prompt}\n\nReturn ONLY a valid JSON object matching this JSON schema. "
            f"No prose, no markdown fences.\n{json.dumps(schema.model_json_schema())}"
        )

    @staticmethod
    def _parse(content: str, schema: Type[BaseModel]) -> BaseModel:
        return schema.model_validate_json(_extract_json(content))
