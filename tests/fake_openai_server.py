"""
A tiny OpenAI-compatible server for tests (runs in a background thread on a free port).

  * POST /v1/chat/completions   streaming + non-streaming; answers by quoting the "[Source: …]" tags in the
                                prompt (so tests can see which passages reached the model); usage 120 in / 12 out
  * GET  /v1/models, POST /v1/embeddings
  * records every request (headers + JSON body) in `.requests`
  * `expected_key`: when set, any other API key gets OpenAI's 401 `invalid_api_key` error
"""
import asyncio
import json
import re
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


class FakeOpenAIServer:
    def __init__(self, expected_key: str | None = None):
        self.expected_key = expected_key
        self.requests: list[dict] = []
        self.models = ["gpt-4o-mini", "gpt-4o", "text-embedding-3-small", "whisper-1", "gpt-4.1-mini"]
        self.app = self._build()
        self._server = None
        self._thread = None
        self.url = ""

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self._server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                break
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{port}/v1"
        return self

    def stop(self):
        if self._server:
            self._server.should_exit = True
            self._thread.join(timeout=5)

    def reset(self):
        self.requests.clear()

    def chat_requests(self):
        return [r for r in self.requests if r["path"].endswith("/chat/completions")]

    # ── app ──────────────────────────────────────────────────────────
    def _build(self):
        app = FastAPI()

        async def record(request: Request):
            body = {}
            if request.method == "POST":
                try:
                    body = await request.json()
                except Exception:
                    body = {}
            auth = request.headers.get("authorization", "")
            self.requests.append({"path": request.url.path, "auth": auth, "body": body})
            if self.expected_key is not None and auth != f"Bearer {self.expected_key}":
                return body, JSONResponse(status_code=401, content={"error": {
                    "message": f"Incorrect API key provided: {auth.replace('Bearer ', '')[:8]}***.",
                    "type": "invalid_request_error", "param": None, "code": "invalid_api_key"}})
            return body, None

        @app.get("/v1/models")
        async def models(request: Request):
            _, err = await record(request)
            return err or {"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]}

        @app.post("/v1/embeddings")
        async def embeddings(request: Request):
            body, err = await record(request)
            if err:
                return err
            inputs = body.get("input", [])
            inputs = [inputs] if isinstance(inputs, str) else inputs
            data = [{"object": "embedding", "index": i, "embedding": [float((len(str(t)) + j) % 7) for j in range(8)]}
                    for i, t in enumerate(inputs)]
            return {"object": "list", "data": data, "model": body.get("model", ""),
                    "usage": {"prompt_tokens": 1, "total_tokens": 1}}

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            body, err = await record(request)
            if err:
                return err
            prompt = "\n".join(str(m.get("content", "")) for m in body.get("messages", []))
            tags = re.findall(r"\[Source: ([^\]]+)\]", prompt)
            text = ("Based on the documents: " + "; ".join(tags) + ".") if tags else "I could not find this in the documents."
            usage = {"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132}
            model = body.get("model", "")

            if not body.get("stream"):
                return {"id": "c1", "object": "chat.completion", "created": int(time.time()), "model": model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                        "usage": usage}

            def frame(delta, finish=None, with_usage=False):
                d = {"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                     "choices": [] if with_usage else [{"index": 0, "delta": delta, "finish_reason": finish}]}
                if with_usage:
                    d["usage"] = usage
                return f"data: {json.dumps(d)}\n\n"

            async def gen():
                yield frame({"role": "assistant", "content": ""})
                for i, w in enumerate(text.split(" ")):
                    await asyncio.sleep(0.005)
                    yield frame({"content": w if i == 0 else " " + w})
                yield frame({}, finish="stop")
                if (body.get("stream_options") or {}).get("include_usage"):
                    yield frame({}, with_usage=True)
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")

        return app
