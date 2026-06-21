#!/usr/bin/env python3
"""Build the static docs site from docs/*.md into docs-site/*.html.

The landing page (docs-site/index.html) remains hand-written because it is a
marketing/release overview. All other pages are generated from markdown.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
SITE_DIR = ROOT / "docs-site"
VERSION = "v0.3.6"
BUILD_LABEL = "Gemini/SORA build"

NAV = [
    ("getting-started", "Getting started", [
        ("index.html", "Overview"),
        ("quickstart.html", "Quick start"),
        ("architecture.html", "Architecture"),
    ]),
    ("sora-release", "SORA release", [
        ("sora-bridge-elements.html", "SORA bridge elements"),
        ("release-readiness.html", "Release truth table"),
    ]),
    ("core-systems", "Core systems", [
        ("personality.html", "Personality"),
        ("fallback-chain.html", "Fallback chain"),
        ("notification.html", "Notifications"),
        ("email-brief.html", "Email brief"),
        ("sfx-library.html", "SFX library"),
        ("sfx-credits.html", "SFX credits"),
        ("webhooks.html", "Webhooks"),
        ("video.html", "Video feeder"),
    ]),
    ("reference", "Reference", [
        ("env-vars.html", "Environment variables"),
        ("troubleshooting.html", "Troubleshooting"),
        ("changelog.html", "Changelog"),
    ]),
]

SLUG_TO_MD = {
    "quickstart": "quickstart.md",
    "architecture": "architecture.md",
    "sora-bridge-elements": "sora-bridge-elements.md",
    "release-readiness": "release-readiness.md",
    "personality": "personality.md",
    "fallback-chain": "fallback-chain.md",
    "notification": "notification.md",
    "email-brief": "email-brief.md",
    "sfx-library": "sfx-library.md",
    "sfx-credits": "sfx-credits.md",
    "webhooks": "webhooks.md",
    "video": "video.md",
    "env-vars": "env-vars.md",
    "troubleshooting": "troubleshooting.md",
    "changelog": "../CHANGELOG.md",
}

PAGE_TITLES = {
    "quickstart": "Quick start — Hermes Live",
    "architecture": "Architecture — Hermes Live",
    "sora-bridge-elements": "SORA bridge elements — Hermes Live",
    "release-readiness": "Release truth table — Hermes Live",
    "personality": "Personality — Hermes Live",
    "fallback-chain": "Fallback chain — Hermes Live",
    "notification": "Notification system — Hermes Live",
    "email-brief": "Email brief — Hermes Live",
    "sfx-library": "SFX library — Hermes Live",
    "sfx-credits": "SFX credits — Hermes Live",
    "webhooks": "Webhooks — Hermes Live",
    "video": "Video frame feeder — Hermes Live",
    "env-vars": "Environment variables — Hermes Live",
    "troubleshooting": "Troubleshooting — Hermes Live",
    "changelog": "Changelog — Hermes Live",
}

META_DESC = {
    "quickstart": "Install the Gemini Live Discord voice bridge, restart Hermes, and run /voice-live.",
    "architecture": "Audio path, sidecar flow, SORA helper layer, and lifecycle of Hermes Live.",
    "sora-bridge-elements": "SORA preflight, transcript grilling, goal synthesis, and redaction helpers.",
    "release-readiness": "Truth table separating working, partial, sibling, and research features.",
    "personality": "The system prompt and behavior contract for the live voice agent.",
    "fallback-chain": "Multi-CLI delegation with health registry and fallback rules.",
    "notification": "Proactive notification delivery across voice, DM, channel, webhook, and auto modes.",
    "email-brief": "Scheduled Gmail digest and importance buckets.",
    "sfx-library": "Slot-based sound effects for tool-init, error, notification, and transition cues.",
    "sfx-credits": "SFX provenance and license information.",
    "webhooks": "Webhook event classes and dispatch configuration.",
    "video": "Manual frame feeder and the Discord screenshare limitation.",
    "env-vars": "Environment variables and defaults.",
    "troubleshooting": "Common bridge failures and fixes.",
    "changelog": "Release history and load-bearing fixes.",
}

ORDER = ["index.html"] + [f"{slug}.html" for slug in SLUG_TO_MD]


def md_to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    in_code = False
    code_lang = ""
    code_buf: list[str] = []

    def flush_code() -> None:
        nonlocal code_buf, code_lang
        body = html.escape("\n".join(code_buf))
        out.append(
            f'<div class="pre-wrap"><button type="button" class="copy-btn" aria-label="Copy code" data-copy-btn>copy</button>'
            f'<pre><code class="lang-{html.escape(code_lang)}">{body}</code></pre></div>'
        )
        code_buf = []
        code_lang = ""

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
                code_lang = line[3:].strip()
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            rows = [line]
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(render_table(rows))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue
        if line.startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i].lstrip(">").lstrip())
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(block))}</p></blockquote>")
            continue
        if re.match(r"^\s*---+\s*$", line):
            out.append("<hr>")
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(inline(re.sub(r"^\s*[-*]\s+", "", lines[i])))
                i += 1
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(inline(re.sub(r"^\s*\d+\.\s+", "", lines[i])))
                i += 1
            out.append("<ol>" + "".join(f"<li>{item}</li>" for item in items) + "</ol>")
            continue
        if not line.strip():
            out.append("")
            i += 1
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not is_block(lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")

    if in_code:
        flush_code()
    return "\n".join(out)


def is_block(line: str) -> bool:
    s = line.lstrip()
    return bool(
        s.startswith("#") or s.startswith("```") or s.startswith(">") or s.startswith("|")
        or s.startswith("---") or re.match(r"^[-*]\s+", s) or re.match(r"^\d+\.\s+", s)
    )


def render_table(rows: list[str]) -> str:
    def split(row: str) -> list[str]:
        s = row.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [cell.strip() for cell in s.split("|")]

    header = split(rows[0])
    body = [split(row) for row in rows[1:]]
    h = "".join(f"<th>{inline(cell)}</th>" for cell in header)
    b = "".join("<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>" for row in body)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def render_sidebar(current: str) -> str:
    out = [f'''    <div class="brand">
      <div class="brand-mark">H</div>
      <div>
        <div class="brand-text">Hermes Live</div>
        <div class="brand-sub">{VERSION} · {BUILD_LABEL}</div>
      </div>
    </div>

    <div class="search">
      <input id="search" type="search" placeholder="Search docs…" autocomplete="off">
    </div>

    <nav class="nav">''']
    for _, section, items in NAV:
        out.append(f'      <div class="nav-section">{html.escape(section)}</div>')
        for href, label in items:
            cls = ' class="active"' if href == current else ""
            out.append(f'      <a href="{href}"{cls}>{html.escape(label)}</a>')
    out.append('''    </nav>

    <div class="sidebar-foot">
      <a href="https://github.com/Capslockb/hermes-live-discord-agent-plugin">GitHub →</a><br>
      MIT licensed · Hermes Agent plugin
    </div>''')
    return "\n".join(out)


def render_topbar(slug: str) -> str:
    crumb = "Overview" if slug == "index" else slug.replace("-", " ").title()
    return f'''    <div class="topbar">
      <span class="crumb">{html.escape(crumb)}</span>
      <span class="sep">/</span>
      <span>docs-site/</span>
      <div class="right">
        <span class="pill good">{VERSION}</span>
        <span class="pill warn">Gemini/SORA</span>
      </div>
    </div>'''


def pager_for(slug: str) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    fname = f"{slug}.html"
    if fname not in ORDER:
        return None, None
    idx = ORDER.index(fname)

    def label(fname_: str) -> str:
        if fname_ == "index.html":
            return "Overview"
        slug_ = fname_.replace(".html", "")
        return PAGE_TITLES.get(slug_, slug_.replace("-", " ").title()).replace(" — Hermes Live", "")

    prev = ORDER[idx - 1] if idx > 0 else None
    nxt = ORDER[idx + 1] if idx < len(ORDER) - 1 else None
    return ((prev, label(prev)) if prev else None, (nxt, label(nxt)) if nxt else None)


def render_pager(slug: str) -> str:
    prev, nxt = pager_for(slug)

    def cell(side: str, item: tuple[str, str] | None) -> str:
        if not item:
            return f'<a class="{side}" style="visibility:hidden"></a>'
        href, title = item
        arrow = "←" if side == "prev" else "→"
        anchor = "Prev" if side == "prev" else "Next"
        return f'''<a class="{side}" href="{href}">
          <span class="label">{anchor} {arrow}</span>
          <span class="title">{html.escape(title)}</span>
        </a>'''

    return '<div class="pager">' + cell("prev", prev) + cell("next", nxt) + '</div>'


def render_page(slug: str, source: Path) -> str:
    body = md_to_html(source.read_text(encoding="utf-8"))
    title = PAGE_TITLES.get(slug, f"{slug.title()} — Hermes Live")
    desc = META_DESC.get(slug, "Hermes Live documentation.")
    current = f"{slug}.html"
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="shell">
  <aside class="sidebar">
{render_sidebar(current)}
  </aside>
  <main class="main">
{render_topbar(slug)}
    <article class="content">
{body}
{render_pager(slug)}
      <div class="foot">Hermes Live {VERSION} · MIT · <a href="https://github.com/Capslockb/hermes-live-discord-agent-plugin">github.com/Capslockb/hermes-live-discord-agent-plugin</a></div>
    </article>
    <aside class="toc" id="toc" aria-label="On this page"></aside>
  </main>
</div>
<script src="nav.js"></script>
</body>
</html>
'''


def main() -> int:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    for asset in ("style.css", "nav.js"):
        src = SITE_DIR / asset
        if src.exists():
            (SITE_DIR / asset).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    for slug, md_name in SLUG_TO_MD.items():
        src = DOCS_DIR / md_name
        if not src.exists():
            print(f"missing source: {src}", file=sys.stderr)
            continue
        out = SITE_DIR / f"{slug}.html"
        out.write_text(render_page(slug, src), encoding="utf-8")
        print(f"+ {out.relative_to(ROOT)} ← {src.relative_to(ROOT)}", file=sys.stderr)
    print("done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
