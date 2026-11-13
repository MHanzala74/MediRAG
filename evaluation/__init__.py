"""
MediRAG — RAG evaluation suite built on DeepEval.

Modules
-------
judge     : DeepEval-compatible judge LLM (OpenAI or Groq)
metrics   : factory for every DeepEval metric used by MediRAG
dataset   : golden dataset loader
pipeline  : side-effect-free RAG runner that exposes retrieval context
runner    : orchestrates generation + evaluation + reporting
report    : JSON / Markdown report writers
"""

import os as _os

# DeepEval sends anonymous usage telemetry by default. MediRAG evaluates medical
# text, so opt out unless the operator explicitly enables it.
_os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
