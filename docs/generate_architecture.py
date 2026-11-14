"""Regenerates docs/architecture.svg (research-paper style figure).

    python docs/generate_architecture.py
    python -c "import cairosvg; cairosvg.svg2png(url='docs/architecture.svg', write_to='docs/architecture.png', scale=2)"
"""
from xml.sax.saxutils import escape

W, H = 1260, 975
FONT = "'Times New Roman', Times, 'Liberation Serif', 'Nimbus Roman', serif"
INK = "#1a1a1a"

# soft, print-friendly palette: (fill, stroke)
PAL = {
    "neutral": ("#F4F4F4", "#555555"),
    "guard":   ("#FBE9E7", "#A8433A"),
    "retr":    ("#E7EFF9", "#35588A"),
    "gen":     ("#E6F3E8", "#3C7A4B"),
    "store":   ("#FFF5D6", "#8F741B"),
    "eval":    ("#EEE8F7", "#65499B"),
}

out = []
def add(s): out.append(s)

def txt(x, y, s, size=12, weight="normal", style="normal", anchor="middle", fill=INK):
    add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" font-style="{style}" '
        f'text-anchor="{anchor}" fill="{fill}">{s}</text>')

def lines(x, y, ls, size=11, lh=13, **kw):
    for i, l in enumerate(ls):
        txt(x, y + i * lh, l, size=size, **kw)

def sym(base, sub=None, size=12):
    """italic math symbol with optional subscript (tspan dy based, renderer-safe)"""
    s = f'<tspan font-style="italic">{base}</tspan>'
    if sub is not None:
        s += f'<tspan font-size="{size-3}" dy="3">{sub}</tspan><tspan dy="-3"> </tspan>'
    return s

def rect(x, y, w, h, fill, stroke, rx=5, sw=1.1, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

def arrow(pts, color="#222", dash=None, marker="a", sw=1.3, both=False):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    p = " ".join(f"{x},{y}" for x, y in pts)
    ms = f' marker-end="url(#{marker})"'
    if both: ms += f' marker-start="url(#{marker})"'
    add(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="{sw}"{d}{ms}/>')

def cylinder(cx, ytop, w, h, fill, stroke):
    rx, ry = w / 2, 9
    add(f'<path d="M{cx-rx},{ytop+ry} L{cx-rx},{ytop+h-ry} A{rx},{ry} 0 0 0 {cx+rx},{ytop+h-ry} '
        f'L{cx+rx},{ytop+ry} Z" fill="{fill}" stroke="{stroke}" stroke-width="1.1"/>')
    add(f'<ellipse cx="{cx}" cy="{ytop+ry}" rx="{rx}" ry="{ry}" fill="{fill}" stroke="{stroke}" stroke-width="1.1"/>')

# ───────────────────────────── canvas ─────────────────────────────
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">')
add('<defs>'
    '<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    '<path d="M0,1 L10,5 L0,9 z" fill="#222"/></marker>'
    '<marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    '<path d="M0,1 L10,5 L0,9 z" fill="#A8433A"/></marker>'
    '<marker id="ag" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    '<path d="M0,1 L10,5 L0,9 z" fill="#666"/></marker>'
    '</defs>')
add(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

def panel(y0, y1, label, desc):
    rect(14, y0, W - 28, y1 - y0, "none", "#9a9a9a", rx=8, sw=1.0, dash="5 4")
    txt(30, y0 + 22, f'<tspan font-weight="bold">{label}</tspan>', size=14, anchor="start")
    txt(30 + 8 + len(label) * 7.2, y0 + 22, desc, size=13, style="italic", anchor="start", fill="#333")

# ═══════════════ (a) OFFLINE INDEXING ═══════════════
panel(14, 200, "(a)", "Offline: document ingestion and indexing")
ya, ha = 62, 88
steps_a = [
    ("PDF upload", ["admin only; role tag", "{user, admin}"], "neutral"),
    ("Parsing", ["PyPDFLoader", "page-level text"], "neutral"),
    ("Chunking", ["RecursiveCharacter-", "TextSplitter (600 / 120)"], "retr"),
    ("Embedding", ["OpenAI text-emb-3-small", "or MiniLM-L6-v2 (HF)"], "retr"),
]
xa = [50, 254, 458, 662]
for (t, sub, pk), x in zip(steps_a, xa):
    f, s = PAL[pk]
    rect(x, ya, 160, ha, f, s)
    txt(x + 80, ya + 24, t, size=13, weight="bold")
    lines(x + 80, ya + 47, sub, size=11, lh=14)
for i in range(3):
    arrow([(xa[i] + 160, ya + ha / 2), (xa[i + 1], ya + ha / 2)])
txt((xa[2]) - 22, ya + ha / 2 - 6, sym("c", "i", 11), size=11)
txt((xa[3]) - 22, ya + ha / 2 - 6, sym("c", "i", 11), size=11)
# chroma cylinder
cx_chroma = 905
cylinder(cx_chroma, ya - 6, 140, ha + 12, *PAL["store"])
txt(cx_chroma, ya + 28, "ChromaDB", size=13, weight="bold")
lines(cx_chroma, ya + 46, ["vectors + metadata", "(role, doc_id, source)"], size=10.5, lh=13)
arrow([(822, ya + ha / 2), (cx_chroma - 70, ya + ha / 2)])
txt(846, ya + ha / 2 - 7, sym("e", "i", 11), size=11)
# legend
lx, ly = 1010, 40
rect(lx, ly, 224, 148, "#ffffff", "#9a9a9a", rx=4, sw=0.9)
txt(lx + 10, ly + 17, "Legend", size=12, weight="bold", anchor="start")
for i, (k, lab) in enumerate([("guard", "Safety guardrail"), ("retr", "Retrieval / indexing"),
                              ("gen", "Generation"), ("store", "Data store"), ("eval", "Evaluation")]):
    yy = ly + 30 + i * 17
    rect(lx + 10, yy - 9, 20, 11, *PAL[k], rx=2, sw=1)
    txt(lx + 38, yy, lab, size=11, anchor="start")
arrow([(lx + 10, ly + 122), (lx + 32, ly + 122)])
txt(lx + 38, ly + 126, "data flow", size=11, anchor="start")
arrow([(lx + 112, ly + 122), (lx + 134, ly + 122)], color="#666", dash="4 3", marker="ag")
txt(lx + 140, ly + 126, "read / reuse", size=11, anchor="start")
rect(lx + 10, ly + 131, 22, 11, "#ffffff", "#555555", rx=2, sw=0.9, dash="3 2")
txt(lx + 38, ly + 141, "intermediate output", size=11, anchor="start")

# ═══════════════ (b) ONLINE INFERENCE ═══════════════
panel(214, 556, "(b)", "Online: guarded hybrid-RAG inference")
yb, hb, wb = 262, 226, 160
xs = [50 + i * 204 for i in range(6)]
stages = [
    ("1  Query &amp; Auth", "neutral", [
        ("FastAPI  /chat/query", None), ("JWT (OAuth2 Bearer)", None),
        ("RBAC: role {user, admin}", None), ("session id for history", None)]),
    ("2  Input Guardrail G1", "guard", [
        ("NeMo Guardrails\n+ rule fallback", None), ("off-topic / harmful", None),
        ("crisis -> emergency info", None), ("fail: safe refusal (422)", "red")]),
    ("3  Hybrid Retrieval", "retr", [
        ("Dense: ChromaDB top-k\nrole-filtered", None), ("Sparse: BM25 re-scoring\nof dense candidates", None),
        ("Reciprocal Rank Fusion\n(k = 60)", None), ("output: C_k  (k = 6)", "io")]),
    ("4  Context G2 + Rerank", "retr", [
        ("Context sufficiency check", "guard"), ("Cross-encoder rerank\nms-marco-MiniLM-L-6", None),
        ("keep top-n  (n = 3)", None), ("output: C_n", "io")]),
    ("5  Generation", "gen", [
        ("Prompt: rules + C_n\n+ chat history", None), ("LLM router  (T = 0)", None),
        ("primary: GPT-4o-mini", None), ("fallback: Llama-3.3-70B\n(Groq)", None)]),
    ("6  Output G3 / G4", "guard", [
        ("Strip risky advice", None), ("Hallucination signals", None),
        ("Append disclaimer", None), ("output: answer a\n+ cited sources", "io")]),
]
for x, (title, pk, items) in zip(xs, stages):
    f, s = PAL[pk]
    rect(x, yb, wb, hb, f, s, rx=7, sw=1.3)
    txt(x + wb / 2, yb + 22, title, size=13, weight="bold")
    add(f'<line x1="{x+10}" y1="{yb+30}" x2="{x+wb-10}" y2="{yb+30}" stroke="{s}" stroke-width="0.8"/>')
    for j, (label, kind) in enumerate(items):
        yy = yb + 40 + j * 44
        fill, stroke = "#ffffff", "#8a8a8a"
        if kind == "red": fill, stroke = PAL["guard"][0], PAL["guard"][1]
        if kind == "guard": fill, stroke = PAL["guard"][0], PAL["guard"][1]
        if kind == "io": fill, stroke = "#ffffff", s
        rect(x + 10, yy, wb - 20, 36, fill, stroke, rx=4, sw=0.9, dash="3 2" if kind == "io" else None)
        ls = label.split("\n")
        style = "italic" if kind == "io" else "normal"
        col = "#8a2f27" if kind == "red" else INK
        if len(ls) == 1:
            txt(x + wb / 2, yy + 22, ls[0].replace("->", "&#8594;"), size=11, style=style, fill=col)
        else:
            lines(x + wb / 2, yy + 15, [l.replace("->", "&#8594;") for l in ls], size=10.5, lh=12.5, style=style, fill=col)
# inter-stage arrows + data labels
labels = [sym("q"), sym("q"), sym("C", "k"), sym("C", "n"), '<tspan font-style="italic">a</tspan>&#8242;', None]
for i in range(5):
    xa_, xb_ = xs[i] + wb, xs[i + 1]
    arrow([(xa_, yb + 112), (xb_, yb + 112)])
    txt((xa_ + xb_) / 2, yb + 100, labels[i], size=12)
# input arrow and output arrow
arrow([(24, yb + 112), (xs[0], yb + 112)])
txt(26, yb + 100, sym("q"), size=12, anchor="start")
txt(19, yb + 128, "Web UI", size=9.5, style="italic", anchor="start", fill="#555")
txt(19, yb + 140, "or API", size=9.5, style="italic", anchor="start", fill="#555")
# postgres strip
ys = 506
rect(50, ys, 1180, 28, *PAL["store"], rx=4, sw=1.1)
txt(468, ys + 19, "PostgreSQL", size=12.5, weight="bold", anchor="start")
txt(548, ys + 19, "&#8212;  users  &#183;  chat history  &#183;  document registry", size=12, anchor="start")
for i, d in ((0, True), (4, True), (5, False)):
    cx = xs[i] + wb / 2
    arrow([(cx, yb + hb), (cx, ys)] if not d else [(cx, yb + hb), (cx, ys)], color="#666", dash="4 3", marker="ag", sw=1.1, both=True)
# chroma read arrow into stage 3 (from panel a)
s3 = xs[2] + wb / 2
arrow([(cx_chroma, ya + ha + 6), (cx_chroma, 236), (s3, 236), (s3, yb)], color="#666", dash="4 3", marker="ag", sw=1.2)
txt((cx_chroma + s3) / 2 + 10, 230, "dense index (read)", size=11, style="italic", fill="#555")
# final output
txt(xs[5] + wb / 2, yb + hb + 0, "", size=1)

# ═══════════════ (c) EVALUATION ═══════════════
panel(570, 868, "(c)", "Evaluation with DeepEval")
yc, hc = 620, 150
boxes_c = [
    (50, 160, "Golden dataset", "neutral", ["curated JSON, or synthesized", "from ChromaDB chunks:", "(q, y*, role, expect_block)"]),
    (254, 160, "Pipeline run", "gen", ["same retriever, reranker,", "LLM and guardrails as (b);", "no DB side effects"]),
    (458, 160, "LLMTestCase", "neutral", ["input = q", "actual_output = a", "expected_output = y*", "retrieval_context = C_n"]),
]
for x, w, t, pk, sub in boxes_c:
    f, s = PAL[pk]
    rect(x, yc, w, hc, f, s, rx=7, sw=1.3)
    txt(x + w / 2, yc + 26, t, size=13, weight="bold")
    add(f'<line x1="{x+10}" y1="{yc+35}" x2="{x+w-10}" y2="{yc+35}" stroke="{s}" stroke-width="0.8"/>')
    lines(x + w / 2, yc + 58, sub, size=11, lh=16)
arrow([(210, yc + 75), (254, yc + 75)]); txt(232, yc + 67, sym("q"), size=12)
arrow([(414, yc + 75), (458, yc + 75)]); txt(436, yc + 67, "(q, a, C)", size=10.5, style="italic")
# metrics box
mx, mw = 662, 372
rect(mx, yc, mw, hc, *PAL["eval"], rx=7, sw=1.3)
txt(mx + mw / 2, yc + 26, "DeepEval metrics  (14)", size=13, weight="bold")
add(f'<line x1="{mx+10}" y1="{yc+35}" x2="{mx+mw-10}" y2="{yc+35}" stroke="{PAL["eval"][1]}" stroke-width="0.8"/>')
cols = [
    ("Retrieval", ["Contextual Precision", "Contextual Recall", "Contextual Relevancy"]),
    ("Generation", ["Faithfulness", "Answer Relevancy", "Hallucination", "G-Eval: Correctness", "G-Eval: Citation"]),
    ("Safety", ["G-Eval: Med. Safety", "Non-Advice", "Misuse", "Bias", "Toxicity", "PII Leakage"]),
]
cw = (mw - 20) / 3
for k, (ct, items) in enumerate(cols):
    cxm = mx + 10 + cw * k + cw / 2
    rect(mx + 14 + cw * k, yc + 44, cw - 8, 98, "#ffffff", "#8a8a8a", rx=4, sw=0.9)
    txt(cxm, yc + 59, ct, size=11.5, weight="bold")
    for j, it in enumerate(items):
        txt(cxm, yc + 73 + j * 11.2, it, size=9.6)
arrow([(618, yc + 75), (mx, yc + 75)]); txt(640, yc + 67, sym("T"), size=12)
# judge box under metrics
jx, jy = mx + 36, yc + hc + 22
rect(jx, jy, mw - 72, 40, *PAL["eval"], rx=5, sw=1.1)
txt(jx + (mw - 72) / 2, jy + 17, "Judge LLM  (MediRAGJudge)", size=12, weight="bold")
txt(jx + (mw - 72) / 2, jy + 32, "OpenAI or Groq via LangChain", size=10.5, style="italic")
arrow([(mx + mw / 2, jy), (mx + mw / 2, yc + hc)], color="#666", dash="4 3", marker="ag", sw=1.2)
# reports box
rx0, rw = 1074, 156
rect(rx0, yc, rw, hc, *PAL["neutral"], rx=7, sw=1.3)
txt(rx0 + rw / 2, yc + 26, "Reports", size=13, weight="bold")
add(f'<line x1="{rx0+10}" y1="{yc+35}" x2="{rx0+rw-10}" y2="{yc+35}" stroke="{PAL["neutral"][1]}" stroke-width="0.8"/>')
lines(rx0 + rw / 2, yc + 58, ["per-case scores + reasons", "JSON / CSV / Markdown", "pass rates, group means", "web dashboard (/ui)", "pytest / CI gate"], size=11, lh=16)
arrow([(mx + mw, yc + 75), (rx0, yc + 75)]); txt((mx + mw + rx0) / 2, yc + 67, "scores", size=10.5, style="italic")
# link from (b) to (c)
arrow([(334, 556), (334, yc)], color="#666", dash="4 3", marker="ag", sw=1.2)
txt(344, 596, "re-uses the components of (b)", size=11, style="italic", anchor="start", fill="#555")

# ═══════════════ caption ═══════════════
cap = [
    '<tspan font-weight="bold">Figure 1.</tspan> Architecture of MediRAG. (a) Offline: admin-uploaded PDFs are parsed, chunked and embedded; chunks <tspan font-style="italic">c</tspan><tspan font-size="9" dy="3">i</tspan><tspan dy="-3"> </tspan>with role metadata are indexed in ChromaDB.',
    '(b) Online: a query <tspan font-style="italic">q</tspan> from the web UI or API is authenticated, screened by an input guardrail (G1), matched by dense retrieval re-scored with BM25 and fused with RRF, checked for context sufficiency (G2),',
    'reranked by a cross-encoder, answered by a multi-provider LLM router, and post-processed by output and hallucination guardrails (G3, G4). (c) A golden dataset is run through the same components',
    'and scored by 14 DeepEval metrics (retrieval, generation, safety) using a judge LLM; blocked cases are scored on safety metrics only.',
]
for i, l in enumerate(cap):
    txt(36, 898 + i * 16, l, size=12.5, anchor="start")
add('</svg>')
open(__import__('pathlib').Path(__file__).with_name('architecture.svg'), 'w', encoding='utf-8').write("\n".join(out))
print("written")
