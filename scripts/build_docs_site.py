#!/usr/bin/env python3
"""Build the static docs site from `docs/*.md` into `docs-site/*.html`.

Single source of truth: the .md files. The output is one .html per .md,
plus a hand-written landing `index.html` (which lives outside the .md
source so the marketing is separate from the docs).

Usage:
    python3 scripts/build_docs_site.py
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
DOCS_DIR  = ROOT / "docs"
SITE_DIR  = ROOT / "docs-site"

# nav order matters; matches the landing sidebar
NAV = [
    ("getting-started", "Getting started", [
        ("index.html",        "Overview"),
        ("quickstart.html",   "Quick start"),
        ("architecture.html", "Architecture"),
    ]),
    ("core-systems", "Core systems", [
        ("personality.html",    "Personality"),
        ("fallback-chain.html", "Fallback chain"),
        ("notification.html",   "Notifications"),
        ("email-brief.html",    "Email brief"),
        ("sfx-library.html",    "SFX library"),
        ("sfx-credits.html",    "SFX credits"),
        ("webhooks.html",       "Webhooks"),
        ("video.html",          "Video feeder"),
    ]),
    ("reference", "Reference", [
        ("env-vars.html",       "Environment variables"),
        ("troubleshooting.html","Troubleshooting"),
        ("changelog.html",      "Changelog"),
    ]),
]

# title overrides per .md (so the page title is human, not the file name)
PAGE_TITLES = {
    "architecture":   "Architecture — Hermes Live",
    "personality":    "Personality — Hermes Live",
    "fallback-chain": "Fallback chain — Hermes Live",
    "notification":   "Notification system — Hermes Live",
    "email-brief":    "Email brief — Hermes Live",
    "sfx-library":    "SFX library — Hermes Live",
    "sfx-credits":    "SFX credits — Hermes Live",
    "webhooks":       "Webhooks — Hermes Live",
    "video":          "Video frame feeder — Hermes Live",
    "env-vars":       "Environment variables — Hermes Live",
    "troubleshooting":"Troubleshooting — Hermes Live",
    "changelog":      "Changelog — Hermes Live",
    "quickstart":     "Quick start — Hermes Live",
}

# Pager order = the same as NAV flattened, but with explicit prev/next.
ORDER = [
    "index.html",
    "quickstart.html",
    "architecture.html",
    "personality.html",
    "fallback-chain.html",
    "notification.html",
    "email-brief.html",
    "sfx-library.html",
    "sfx-credits.html",
    "webhooks.html",
    "video.html",
    "env-vars.html",
    "troubleshooting.html",
    "changelog.html",
]

# Page description for the <meta name="description"> tag
META_DESC = {
    "architecture":    "End-to-end audio path, threading model, and lifecycle of the Hermes Live Discord voice bridge.",
    "personality":     "The 14-section system prompt, ping-pong rhythm, boredom switch, and vocal expression cap that govern the agent.",
    "fallback-chain":  "Multi-CLI delegation with a health registry. opencode / codex / numasec / gemini / hermes-api, with automatic fallback.",
    "notification":    "Five delivery channels, scheduled notifications, and AFK delivery for the Hermes Live voice agent.",
    "email-brief":     "Scheduled Gmail digest with 3-bucket importance scoring. AFK delivery via the notification dispatcher.",
    "sfx-library":     "Four-slot UI sound effects library: tool-init, error, notification, transition. Env-driven, lazy PCM cache.",
    "sfx-credits":     "Provenance and license of the default sfx clips bundled with Hermes Live.",
    "webhooks":        "Nine event classes for fanout: voice.transcript, bridge.status, email.sent, tool.called, and more.",
    "video":           "Companion feeder script that captures your local screen and pushes frames to the bridge over HTTP.",
    "env-vars":        "Every DISCORD_VOICE_LIVE_* environment variable, with defaults and descriptions.",
    "troubleshooting": "Common bridge failures and how to fix them: bridge won't start, interrupts lag, sfx not playing, email brief fails.",
    "changelog":       "Hermes Live release history. Load-bearing fixes, reference baselines, and what changed in each version.",
    "quickstart":      "Five commands, two minutes. Install the bridge, restart the gateway, run /voice-live in Discord.",
}

# ─────────────────────────── markdown → HTML (small, GFM-ish) ────────────

def md_to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []
    code_lang = ""

    def flush_code():
        nonlocal code_buf, code_lang
        if code_buf:
            body = html.escape("\n".join(code_buf))
            out.append(f'<div class="pre-wrap"><button type="button" class="copy-btn" aria-label="Copy code" data-copy-btn>copy</button><pre><code class="lang-{html.escape(code_lang)}">{body}</code></pre></div>')
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

        # tables (GFM: | a | b | followed by | - | - |)
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            tbl: list[str] = [line]
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                tbl.append(lines[j]); j += 1
            out.append(_render_table(tbl))
            i = j
            continue

        # headings
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            text = _inline(m.group(2).strip())
            out.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        # blockquote (collect contiguous > lines)
        if line.startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i].lstrip(">").lstrip())
                i += 1
            inner = _inline(" ".join(block))
            out.append(f"<blockquote><p>{inner}</p></blockquote>")
            continue

        # hr
        if re.match(r"^\s*---+\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # unordered list
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])))
                i += 1
            out.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue

        # ordered list
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*\d+\.\s+", "", lines[i])))
                i += 1
            out.append("<ol>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
            continue

        # blank
        if not line.strip():
            out.append("")
            i += 1
            continue

        # paragraph (collect contiguous non-empty, non-special lines)
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not _is_block(lines[i]):
            para.append(lines[i]); i += 1
        out.append(f"<p>{_inline(' '.join(para))}</p>")

    if in_code:
        flush_code()
    return "\n".join(out)


def _is_block(line: str) -> bool:
    s = line.lstrip()
    return (s.startswith("#") or s.startswith("```") or s.startswith(">")
            or s.startswith("|") or s.startswith("---")
            or re.match(r"^[-*]\s+", s) or re.match(r"^\d+\.\s+", s))


def _render_table(rows: list[str]) -> str:
    def split(row: str) -> list[str]:
        s = row.strip()
        if s.startswith("|"): s = s[1:]
        if s.endswith("|"):   s = s[:-1]
        return [c.strip() for c in s.split("|")]
    header = split(rows[0])
    body   = [split(r) for r in rows[1:]]
    h = "".join(f"<th>{_inline(c)}</th>" for c in header)
    b = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


def _inline(s: str) -> str:
    # code first (to protect its content)
    s = re.sub(r"`([^`]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", s)
    # bold **x**
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    # italic *x* or _x_
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<em>\1</em>", s)
    # links [text](url)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


# ─────────────────────────── page assembly ────────────────────────────

def render_page(slug: str, source_md_path: Path) -> str:
    md = source_md_path.read_text(encoding="utf-8")
    body = md_to_html(md)
    title = PAGE_TITLES.get(slug, f"{slug.title()} — Hermes Live")
    desc  = META_DESC.get(slug, "Hermes Live — Discord voice agent documentation.")

    prev, nxt = _pager_for(slug)

    sidebar = _render_sidebar(current=slug + ".html")
    topbar  = _render_topbar(current=slug)
    pager   = _render_pager(prev, nxt)

    return f"""<!doctype html>
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
{sidebar}
  </aside>

  <main class="main">
{topbar}

    <article class="content">
{body}

{pager}

      <div class="foot">
        Hermes Live v0.3.5 · MIT · <a href="https://github.com/Capslockb/hermes-live-discord-agent-plugin">github.com/Capslockb/hermes-live-discord-agent-plugin</a>
      </div>

    </article>

    <aside class="toc" id="toc" aria-label="On this page"></aside>
  </main>

</div>
<script src="nav.js"></script>
</body>
</html>
"""


def _pager_for(slug: str) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    fname = slug + ".html"
    if fname not in ORDER:
        return (None, None)
    idx = ORDER.index(fname)
    prev = ORDER[idx - 1] if idx > 0 else None
    nxt  = ORDER[idx + 1] if idx < len(ORDER) - 1 else None
    def label(f: str) -> str:
        if f == "index.html": return "Overview"
        return PAGE_TITLES.get(f.replace(".html", ""), f.replace(".html", "").replace("-", " ").title())
    return ((prev, label(prev)) if prev else None, (nxt, label(nxt)) if nxt else None)


def _render_pager(prev, nxt) -> str:
    def cell(side, item):
        if not item: return f'<a class="{side}" style="visibility:hidden"></a>'
        f, lbl = item
        arrow = "←" if side == "prev" else "→"
        anchor = "Prev" if side == "prev" else "Next"
        return f'''<a class="{side}" href="{f}">
          <span class="label">{anchor} {arrow}</span>
          <span class="title">{html.escape(lbl)}</span>
        </a>'''
    return '<div class="pager">' + cell("prev", prev) + cell("next", nxt) + "</div>"


def _render_sidebar(current: str) -> str:
    out = ['''    <div class="brand">
      <div class="brand-mark">H</div>
      <div>
        <div class="brand-text">Hermes Live</div>
        <div class="brand-sub">v0.3.5 · VOPI build</div>
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


def _render_topbar(current: str) -> str:
    crumb = current.replace("-", " ").replace(".html", "").title()
    if current == "index": crumb = "Overview"
    return f'''    <div class="topbar">
      <span class="crumb">{html.escape(crumb)}</span>
      <span class="sep">/</span>
      <span>docs-site/</span>
      <div class="right">
        <span class="pill good">v0.3.5</span>
        <span class="pill warn">VOPI build</span>
      </div>
    </div>'''


# ─────────────────────────── entry ────────────────────────────

def main() -> int:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "style.css").write_text((ROOT / "docs-site" / "style.css").read_text(), encoding="utf-8")
    (SITE_DIR / "nav.js"  ).write_text((ROOT / "docs-site" / "nav.js"  ).read_text(), encoding="utf-8")

    slug_to_md = {
        "quickstart":     "quickstart.md",
        "architecture":   "architecture.md",
        "personality":    "personality.md",
        "fallback-chain": "fallback-chain.md",
        "notification":   "notification.md",
        "email-brief":    "email-brief.md",
        "sfx-library":    "sfx-library.md",
        "sfx-credits":    "sfx-credits.md",
        "webhooks":       "webhooks.md",
        "video":          "video.md",
        "env-vars":       "env-vars.md",
        "troubleshooting":"troubleshooting.md",
        "changelog":      "../CHANGELOG.md",
    }

    # ensure quickstart source exists; create a stub if not
    quickstart_src = DOCS_DIR / "quickstart.md"
    if not quickstart_src.exists():
        quickstart_src.write_text(_quickstart_stub(), encoding="utf-8")
        print(f"  + created stub: docs/quickstart.md", file=sys.stderr)

    for slug, md_name in slug_to_md.items():
        src = DOCS_DIR / md_name
        if not src.exists():
            print(f"  ! missing source: {src}", file=sys.stderr)
            continue
        out = SITE_DIR / f"{slug}.html"
        out.write_text(render_page(slug, src), encoding="utf-8")
        print(f"  + {out.relative_to(ROOT)}  ←  docs/{md_name}", file=sys.stderr)

    print("done.", file=sys.stderr)
    return 0


def _quickstart_stub() -> str:
    return """# Quick start

Five commands, two minutes.

## Install

```bash
# 1. Clone
git clone https://github.com/Capslockb/hermes-live-discord-agent-plugin.git
cd hermes-live-discord-agent-plugin

# 2. Install — prompts for DISCORD_BOT_TOKEN, GEMINI_API_KEY, your Discord user ID
./install.sh

# 3. Restart the gateway so the plugin loads
systemctl --user restart hermes-gateway
```

## First session

From Discord, join a voice channel, then in any text channel:

```
/voice-live          # join
/voice-live-leave    # leave
```

That's it. The bridge will:

1. Connect to your voice channel (Discord CDN quirk: first attempt takes ~27s — this is normal, do not restart the gateway)
2. Handshake with Gemini Live
3. Play the `transition` sfx
4. Wait for you to speak — first turn is muted by design

## Verify

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

You should see `"voice_connected": true`, `"running": true`, and a non-zero `audio_in_chunks` after you speak.

## Common pitfalls

- **"Bridge failed to start"** — wait ~30s. The first 5 voice WebSocket handshakes are rejected by the Discord CDN; the bridge retries.
- **First-turn hallucination** ("I see you're sharing your screen") — the system prompt has the guard, but if you see this, the audioStreamEnd mute is missing. Check `bridge.py` for `await self._gemini._ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))` right after `connect()`.
- **No audio in voice** — check `~/.hermes/voice-users/sfx/` exists and the four WAV files are present.

## Next

- [Architecture](architecture.html) — understand the audio path and threading model.
- [Environment variables](env-vars.html) — every `DISCORD_VOICE_LIVE_*` env var.
- [Troubleshooting](troubleshooting.html) — what to do when it doesn't work.
"""


if __name__ == "__main__":
    raise SystemExit(main())
