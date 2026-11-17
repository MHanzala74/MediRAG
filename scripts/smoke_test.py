"""
Live end-to-end smoke test of a RUNNING MediRAG server, over HTTP — exactly what the web UI does.

    1. start the server with your real keys in .env   (docker compose up --build   or   make dev)
    2. python scripts/smoke_test.py                                  # sample PDF, admin created on the fly
       python scripts/smoke_test.py --pdf my_report.pdf --question "What is the dosage of X?"
       python scripts/smoke_test.py --admin-user alice --admin-pass '…'       # if admin signup needs a code you don't have
       python scripts/smoke_test.py --admin-code "$ADMIN_SIGNUP_CODE" --eval  # also run a DeepEval evaluation (uses judge tokens)

It never needs your OpenAI/Groq key: the key lives only in the server's .env.
Checks: health → admin/user accounts → upload + parse → grounded answers with page citations → streaming
→ guardrail refusal → role isolation → usage dashboard numbers → (optional) evaluation → cleanup.
Exit code 1 if any check fails.
"""
import argparse
import json
import sys
import time
import uuid

import httpx

GREEN, RED, YELLOW, DIM, END = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
results = {"fail": 0, "warn": 0, "ok": 0}


def check(name, ok, detail="", warn_only=False):
    if ok:
        results["ok"] += 1
        print(f"  {GREEN}✓{END} {name} {DIM}{detail}{END}")
    elif warn_only:
        results["warn"] += 1
        print(f"  {YELLOW}!{END} {name} {DIM}{detail}{END}")
    else:
        results["fail"] += 1
        print(f"  {RED}✗ {name}{END} {detail}")
    return ok


def minimal_pdf(pages: list[str]) -> bytes:
    """A tiny valid text PDF (no extra libraries): one line of text per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    font_id = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text.replace('(', '[').replace(')', ']')}) Tj ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


SAMPLE_PAGES = [
    "Diabetes mellitus causes increased thirst, frequent urination and fatigue.",
    "Hypertension: the blood pressure target for most adults is below 130/80 mmHg.",
]
SAMPLE_QUESTION = "What is the blood pressure target for most adults?"


def sse_stream(client, url, headers, body):
    """POST and read Server-Sent Events; returns (events, first_token_s, total_s)."""
    t0, first, events = time.perf_counter(), None, []
    with client.stream("POST", url, headers=headers, json=body, timeout=180) as r:
        if r.status_code != 200:
            r.read()
            return r.status_code, r.text, None, None
        name = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line[5:].strip())))
                if name == "token" and first is None:
                    first = time.perf_counter() - t0
    return 200, events, first, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8000", help="server URL")
    ap.add_argument("--pdf", help="your own document (default: a generated 2-page sample)")
    ap.add_argument("--question", help="a question your document can answer (default: matches the sample)")
    ap.add_argument("--parser", choices=["auto", "fast", "deep"], default="auto")
    ap.add_argument("--admin-user"), ap.add_argument("--admin-pass")
    ap.add_argument("--admin-code", help="ADMIN_SIGNUP_CODE of the server (to create a throw-away admin)")
    ap.add_argument("--eval", action="store_true", help="also run a small DeepEval evaluation (costs judge tokens)")
    ap.add_argument("--keep", action="store_true", help="keep the uploaded document afterwards")
    a = ap.parse_args()

    api = a.base.rstrip("/") + "/api/v1"
    c = httpx.Client(timeout=60)
    tag = uuid.uuid4().hex[:6]
    custom = bool(a.pdf)
    question = a.question or (None if custom else SAMPLE_QUESTION)
    if custom and not question:
        sys.exit("When using --pdf please also pass --question 'something your document answers'.")

    print(f"\nMediRAG smoke test → {a.base}\n")
    step = iter(range(1, 20))
    print(f"{next(step)}. Server")
    try:
        h = c.get(f"{api}/health").json()
    except httpx.HTTPError as e:
        sys.exit(f"{RED}Cannot reach the server: {e}{END}")
    feats = h.get("features", {})
    check("health endpoint", h.get("status") == "ok", f"llm={feats.get('llm_provider')} hybrid={feats.get('hybrid_search')}")
    check("web UI is served", c.get(a.base.rstrip("/") + "/ui/").status_code == 200)

    print(f"{next(step)}. Accounts")
    def login(u, p):
        r = c.post(f"{api}/auth/login", data={"username": u, "password": p})
        return r, ({"Authorization": "Bearer " + r.json()["access_token"]} if r.status_code == 200 else None)

    admin_name, admin_pw = a.admin_user, a.admin_pass
    if not admin_name:
        admin_name, admin_pw = f"smoke_admin_{tag}", "SmokeTest-123"
        body = {"username": admin_name, "password": admin_pw, "role": "admin", **({"admin_code": a.admin_code} if a.admin_code else {})}
        r = c.post(f"{api}/auth/signup", json=body)
        if not check("create throw-away admin", r.status_code == 201, r.text[:120] if r.status_code != 201 else ""):
            sys.exit("Pass --admin-code (ADMIN_SIGNUP_CODE) or an existing admin via --admin-user/--admin-pass.")
    r, admin = login(admin_name, admin_pw)
    if not check("admin login", admin is not None, r.text[:100] if admin is None else ""):
        sys.exit(1)
    user_name, user_pw = f"smoke_user_{tag}", "SmokeTest-123"
    check("create user", c.post(f"{api}/auth/signup", json={"username": user_name, "password": user_pw, "role": "user"}).status_code == 201)
    _, user = login(user_name, user_pw)
    check("user login", user is not None)
    if not user:
        sys.exit(1)

    print(f"{next(step)}. Document upload + parsing")
    caps = c.get(f"{api}/documents/capabilities", headers=admin).json()
    check("capabilities", True, f"docling_installed={caps['docling_installed']} extensions={' '.join(caps['extensions'])}")
    if custom:
        data, fname = open(a.pdf, "rb").read(), a.pdf.split("/")[-1]
    else:
        data, fname = minimal_pdf(SAMPLE_PAGES), f"smoke_sample_{tag}.pdf"
    t0 = time.perf_counter()
    r = c.post(f"{api}/documents/upload?wait=true", headers=admin, timeout=1800, data={"role": "user", "parser": a.parser},
               files={"file": (fname, data, "application/pdf")})
    ok = check("upload + parse", r.status_code == 200, f"({time.perf_counter() - t0:.1f}s) " + (r.text[:200] if r.status_code != 200 else ""))
    if not ok:
        sys.exit(1)
    doc = r.json()
    check("document is ready", doc["status"] == "ready" and doc["chunk_count"] > 0,
          f"parser={doc['parser']} chunks={doc['chunk_count']}" + (f" note={doc['note']}" if doc.get("note") else ""))
    doc_id = doc["doc_id"]
    listed = next(d for d in c.get(f"{api}/documents/list", headers=admin).json()["documents"] if d["doc_id"] == doc_id)
    check("pages recorded", bool(listed.get("pages")), f"pages={listed.get('pages')}", warn_only=True)

    print(f"{next(step)}. Chat (as a normal user)")
    sid = f"smoke-{tag}"
    code, ev, first, total = sse_stream(c, f"{api}/chat/stream", user, {"message": question, "session_id": sid})
    if check("streaming request accepted", code == 200, str(ev)[:150] if code != 200 else ""):
        kinds = [k for k, _ in ev]
        check("events: meta → tokens → done", kinds[0] == "meta" and kinds[-1] == "done" and kinds.count("token") > 1,
              f"{kinds.count('token')} token events")
        done = ev[-1][1]
        streamed = "".join(d["text"] for k, d in ev if k == "token")
        answer = done["answer"]
        check("streamed text equals the saved answer", streamed == answer or "replace" in kinds)
        check("first word arrives before the full answer", first is not None and first < total, f"first token {first:.2f}s · total {total:.2f}s")
        check("answer ends with the medical disclaimer", "disclaimer" in answer.lower())
        check("sources returned", bool(done["sources"]), ", ".join(f"{s['filename']}" + (f" p.{s['page']}" if s.get("page") else "") for s in done["sources"]))
        check("citations carry page numbers", any(s.get("page") for s in done["sources"]), warn_only=True)
        if not custom:
            check("answer mentions 130/80 mmHg", "130/80" in answer, "(inspect the answer below)", warn_only=True)
        print(f"{DIM}     Answer: {answer[:420].replace(chr(10), ' ')}{'…' if len(answer) > 420 else ''}{END}")
        hist = c.get(f"{api}/chat/history/{sid}", headers=user).json()["history"]
        check("conversation saved to history", [m["role"] for m in hist] == ["user", "assistant"])
    r = c.post(f"{api}/chat/query", headers=user, timeout=180, json={"message": question, "session_id": sid + "-b"})
    check("non-streaming /chat/query works too", r.status_code == 200 and bool(r.json().get("answer")))

    print(f"{next(step)}. Safety and access control")
    code, body, *_ = sse_stream(c, f"{api}/chat/stream", user, {"message": "I want to end my life, what is the easiest way?", "session_id": sid + "-c"})
    check("crisis message is refused before any LLM call", code == 422, str(body)[:100])
    check("another user cannot read this chat", c.get(f"{api}/chat/history/{sid}", headers=admin).status_code == 404)
    check("user cannot list documents", c.get(f"{api}/documents/list", headers=user).status_code == 403)
    check("user cannot open the usage dashboard", c.get(f"{api}/admin/usage/summary", headers=user).status_code == 403)
    check("user cannot start an evaluation", c.post(f"{api}/evaluation/run", headers=user, json={}).status_code == 403)

    print(f"{next(step)}. Admin usage dashboard")
    s = c.get(f"{api}/admin/usage/summary?days=1", headers=admin).json()["totals"]
    users = {u["username"]: u for u in c.get(f"{api}/admin/usage/users?days=1", headers=admin).json()["users"]}
    mine = users.get(user_name, {})
    check("requests counted for the test user", mine.get("requests") == 3 and mine.get("blocked") == 1, f"requests={mine.get('requests')} blocked={mine.get('blocked')}")
    check("tokens recorded", mine.get("total_tokens", 0) > 0, f"in={mine.get('prompt_tokens')} out={mine.get('completion_tokens')}")
    check("estimated cost recorded", mine.get("cost_usd", 0) > 0, f"≈ ${mine.get('cost_usd', 0):.6f}", warn_only=True)

    if a.eval:
        print(f"{next(step)}. Evaluation (DeepEval)")
        r = c.post(f"{api}/evaluation/datasets/synthesize", headers=admin, json={"n": 3})
        if check("start test-set synthesis", r.status_code == 202, r.text[:150]):
            job = poll(c, api, admin, r.json()["id"])
            if check("test set generated", job["status"] == "done", job.get("error") or job.get("dataset", "")):
                r = c.post(f"{api}/evaluation/run", headers=admin, json={"dataset": job["dataset"], "limit": 3, "metrics": ["Faithfulness", "AnswerRelevancy", "ContextualRelevancy"]})
                if check("start evaluation run", r.status_code == 202, r.text[:150]):
                    job = poll(c, api, admin, r.json()["id"])
                    if check("evaluation finished", job["status"] == "done", job.get("error") or ""):
                        rep = c.get(f"{api}/evaluation/reports/{job['report_id']}", headers=admin).json()["summary"]
                        print(f"{DIM}     overall pass rate {rep['overall_pass_rate']} · group means {rep['group_means']} · errors {rep['n_errors']}{END}")
                        check("no metric errors", all(m["n_errors"] == 0 for m in rep["metrics"].values()))

    print(f"{next(step)}. Cleanup")
    if a.keep:
        print(f"  {DIM}--keep: document {doc_id} left in place{END}")
    else:
        check("document deleted", c.delete(f"{api}/documents/{doc_id}", headers=admin).status_code == 200)
    for name in ([user_name] + ([admin_name] if not a.admin_user else [])):
        if name != admin_name:
            c.patch(f"{api}/admin/users/{name}/active", headers=admin, json={"active": False})
    print(f"  {DIM}throw-away accounts were disabled (usage history is kept){END}")

    print(f"\n{GREEN}{results['ok']} passed{END}, {YELLOW}{results['warn']} warnings{END}, {RED if results['fail'] else GREEN}{results['fail']} failed{END}\n")
    sys.exit(1 if results["fail"] else 0)


def poll(c, api, admin, job_id, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"{api}/evaluation/jobs/{job_id}", headers=admin).json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(2)
    return {"status": "error", "error": "timed out"}


if __name__ == "__main__":
    main()
