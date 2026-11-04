FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim AS runtime
LABEL maintainer="MHanzala74"
LABEL description="RAG Medical Assistant — Multi-model, Hybrid Search, Guardrails"
RUN useradd --create-home medrag
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app/     ./app/
COPY main.py  .
RUN mkdir -p logs docs_store chroma_db && chown -R medrag:medrag /app
USER medrag
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"
EXPOSE 8000
CMD ["python", "main.py"]
