FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*
ARG WITH_DOCLING=false
COPY requirements.txt requirements-docling.txt ./
RUN pip install --upgrade pip && pip install --no-cache-dir --prefix=/install -r requirements.txt
# Optional deep document parsing (layout, tables, OCR). CPU-only PyTorch keeps the image far smaller.
RUN if [ "$WITH_DOCLING" = "true" ]; then \
      pip install --no-cache-dir --prefix=/install torch torchvision --index-url https://download.pytorch.org/whl/cpu \
      && pip install --no-cache-dir --prefix=/install -r requirements-docling.txt ; \
    fi

FROM python:3.11-slim AS runtime
LABEL maintainer="MHanzala74"
LABEL description="RAG Medical Assistant — Multi-model, Hybrid Search, Guardrails"
ARG WITH_DOCLING=false
RUN useradd --create-home medrag
# OpenCV (used by Docling) needs these shared libraries
RUN if [ "$WITH_DOCLING" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/* ; \
    fi
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app/        ./app/
COPY evaluation/ ./evaluation/
COPY scripts/    ./scripts/
COPY main.py     .
RUN mkdir -p logs docs_store chroma_db evaluation/reports
# Bake the Docling model weights into the image so parsing works offline (needs internet at BUILD time only).
# The app finds /app/docling_models automatically (see default_models_dir in app/rag/parsing/docling_parser.py).
RUN if [ "$WITH_DOCLING" = "true" ]; then python scripts/download_docling_models.py /app/docling_models; fi
RUN chown -R medrag:medrag /app
USER medrag
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"
EXPOSE 8000
CMD ["python", "main.py"]
