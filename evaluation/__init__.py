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
