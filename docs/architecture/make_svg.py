"""Architecture diagram for the Wisconsin DOR property tax assistant (1920x1080 SVG).

Regenerate after an architecture change, then render the PNG (2x) with Chrome:

    python3 docs/architecture/make_svg.py
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \\
      --hide-scrollbars --force-device-scale-factor=2 --window-size=1920,1080 \\
      --screenshot=docs/architecture/architecture.png docs/architecture/architecture.svg
"""
from html import escape

W, H = 1920, 1080
INK, MUTED, LINE = "#111827", "#4b5563", "#9ca3af"
ACCENT = "#2563eb"
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"
TINT = {
    "edge": ("#f3f4f6", "#d1d5db"),
    "api": ("#eef2ff", "#c7d2fe"),
    "compute": ("#fff7ed", "#fed7aa"),
    "data": ("#ecfdf5", "#a7f3d0"),
    "ingest": ("#f8fafc", "#cbd5e1"),
    "user": ("#ffffff", "#9ca3af"),
}
out = []


def text(x, y, s, size=16, weight=400, fill=INK, anchor="start", italic=False):
    st = ' font-style="italic"' if italic else ""
    out.append(
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}"{st}>{escape(s)}</text>'
    )


def box(x, y, w, h, kind, title, lines=(), title_size=20, body_size=15, center=False):
    fill, stroke = TINT[kind]
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
    cx = x + w / 2 if center else x + 18
    anchor = "middle" if center else "start"
    ty = y + 32
    text(cx, ty, title, title_size, 700, INK, anchor)
    for i, ln in enumerate(lines):
        text(cx, ty + 26 + i * 22, ln, body_size, 400, MUTED, anchor)


def step(x, y, w, n, title, detail):
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="54" rx="9" fill="#ffffff" stroke="#fdba74" stroke-width="1.2"/>')
    out.append(f'<rect x="{x+16}" y="{y+14}" width="5" height="26" rx="2.5" fill="#ea580c"/>')
    text(x + 34, y + 23, title, 16, 700)
    text(x + 34, y + 44, detail, 14, 400, MUTED)


def arrow(d, color=ACCENT, dashed=False, width=2.4):
    dash = ' stroke-dasharray="7 6"' if dashed else ""
    marker = "url(#ah)" if color == ACCENT else "url(#ahg)"
    out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{dash} marker-end="{marker}"/>')


def badge(x, y, n):
    out.append(f'<circle cx="{x}" cy="{y}" r="15" fill="{ACCENT}" stroke="#ffffff" stroke-width="2.5"/>')
    text(x, y + 5.5, str(n), 15, 700, "#ffffff", "middle")


def label(x, y, s, anchor="start", color=ACCENT):
    text(x, y, s, 14, 600, color, anchor)


out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
out.append(
    "<defs>"
    f'<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{ACCENT}"/></marker>'
    f'<marker id="ahg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker>'
    "</defs>"
)
out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

# Title
text(60, 66, "Wisconsin DOR Property Tax Assistant: Architecture", 36, 700)
text(60, 100, "AWS us-east-1, deployed with CDK · Amazon Bedrock models · numbered arrows trace one question", 18, 400, MUTED)

# AWS boundary
out.append(f'<rect x="300" y="128" width="1590" height="932" rx="18" fill="none" stroke="{LINE}" stroke-width="1.5" stroke-dasharray="10 8"/>')
text(320, 152, "AWS (us-east-1)", 15, 700, MUTED)

# Users
box(60, 320, 200, 140, "user", "DOR staff", ["Web browser", "(signed in)"], center=True)

# Edge & identity
box(330, 175, 250, 125, "edge", "CloudFront", ["Next.js web app", "Chat UI + admin pages"])
box(330, 480, 250, 140, "edge", "Amazon Cognito", ["User sign-in, Admins group", "DOR single sign-on", "ready (SAML / OIDC)"])

# APIs
box(650, 235, 260, 135, "api", "HTTP API", ["API Gateway", "Signed-in users only", "CORS locked, throttled"])
box(650, 430, 260, 140, "api", "WebSocket API", ["API Gateway", "Login token checked", "on connect; streams answers"])

# Compute
box(970, 170, 470, 110, "compute", "Chat API (Lambda)", ["Sessions, history, feedback, admin", "Per-user message rate limit"])
out.append('<rect x="970" y="300" width="470" height="50" rx="25" fill="#f5f3ff" stroke="#c4b5fd" stroke-width="1.5"/>')
text(1205, 331, "Amazon EventBridge: ChatMessageReceived", 16, 700, INK, "middle")
fill, stroke = TINT["compute"]
out.append(f'<rect x="970" y="370" width="470" height="430" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
text(988, 402, "Agentic Retrieval (Lambda)", 20, 700)
steps = [
    ("Seed the search", "FAQ search, vector search, flowchart router"),
    ("Research loop", "Claude Sonnet 4.6 with 14 read-only tools"),
    ("Statute grounding", "Fetches any section the plan cites"),
    ("Adequacy judge", "Claude Haiku 4.5: answer, clarify or decline"),
    ("Answer stream", "Claude Sonnet 4.6, cited; links checked"),
]
for i, (t, d) in enumerate(steps):
    step(986, 420 + i * 72, 438, i + 1, t, d)

# Data
box(1500, 170, 370, 150, "data", "Neptune Analytics", ["Knowledge graph, 9 authority levels", "Documents, chunks, 1024-d vectors", "Citation and hierarchy edges"])
box(1500, 340, 370, 110, "data", "Bedrock Knowledge Base", ["DOR FAQs (OpenSearch Serverless)"])
box(1500, 470, 370, 130, "data", "Amazon S3", ["Source PDFs, case opinions", "Worksheet + flowchart sidecars"])
box(1500, 620, 370, 180, "data", "Amazon DynamoDB", ["Sessions, chat history, feedback", "Prompts (model config)", "Rate-limit counters", "User data retained + PITR"])

# Query flow
arrow("M160,320 V238 H330")                     # load app
badge(160, 280, 1)
label(176, 232, "Open app, sign in")
arrow("M160,460 V550 H330")                     # sign in
arrow("M260,352 H650")                          # ask
badge(455, 352, 2)
label(455, 328, "Ask a question", "middle")
arrow("M910,300 H940 V225 H970")                # HTTP API -> Chat API
arrow("M1205,280 V300")                         # Chat API -> EventBridge
arrow("M1205,350 V370")                         # EventBridge -> retrieval
badge(1250, 360, 3)
arrow("M1440,500 H1470 V245 H1500")             # retrieve: graph
arrow("M1470,395 H1500")                        # KB
arrow("M1470,500 V535 H1500")                   # S3
badge(1470, 455, 4)
arrow("M970,745 H940 V540 H910")                # stream -> WebSocket
badge(940, 640, 5)
arrow("M650,445 H260")                          # WebSocket -> browser
label(455, 435, "Answer streams back", "middle")
arrow("M1440,765 H1500")                        # save history
badge(1470, 765, 6)

# Legend for the numbered path
text(60, 640, "One question, end to end", 16, 700)
legend = [
    "Open the app and sign in",
    "Ask via the HTTP API",
    "Event starts retrieval",
    "Retrieve the sources",
    "Stream the cited answer",
    "Save to chat history",
]
for i, ln in enumerate(legend):
    y = 672 + i * 34
    badge(75, y - 5, i + 1)
    text(100, y, ln, 14, 400, INK)

# Ingestion band
fill, stroke = TINT["ingest"]
out.append(f'<rect x="330" y="830" width="1540" height="210" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
text(350, 862, "Offline ingestion pipeline (runs on ECS Fargate at each corpus refresh)", 18, 700)
stages = [
    ("Document manifest", ["~1,000 public sources:", "statutes, Tax rules, WPAM,", "DOR guides, FAQs, case law"]),
    ("Scraper", ["Downloads sources", "Skips unchanged files"]),
    ("Raw store (S3)", ["PDFs + metadata", "per document"]),
    ("Extract", ["Chunk PDFs (PyMuPDF)", "Classify: Claude Sonnet 4.6", "Aliases: Nova 2 Lite"]),
    ("Embed", ["Titan Text", "Embeddings v2", "(1024-d)"]),
    ("Load", ["Writes nodes, edges", "and vectors to", "Neptune"]),
]
x0, bw, gap = 350, 225, 32
for i, (t, ls) in enumerate(stages):
    x = x0 + i * (bw + gap)
    out.append(f'<rect x="{x}" y="882" width="{bw}" height="136" rx="10" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.2"/>')
    text(x + 14, 910, t, 17, 700)
    for j, ln in enumerate(ls):
        text(x + 14, 936 + j * 21, ln, 14, 400, MUTED)
    if i:
        arrow(f"M{x - gap + 2},950 H{x - 2}", MUTED, width=2)
# Load -> graph
lx = x0 + 5 * (bw + gap) + bw
arrow(f"M{lx},920 H1880 V245 H1870", MUTED, dashed=True, width=2)

out.append("</svg>")
open(__import__("os").path.join(__import__("os").path.dirname(__file__), "architecture.svg"), "w").write("\n".join(out))
print("wrote architecture.svg")
