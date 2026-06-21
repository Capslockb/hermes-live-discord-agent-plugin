"""
SORA bridge elements for the Gemini Live Discord bridge.

This module is intentionally dependency-light and safe to import from the
Hermes gateway process. It adds the pieces that the standalone SORA wrapper
expects around a live voice bridge:

- redaction before logs/transcripts are injected into Gemini
- local preflight diagnostics for Gemini/Honcho/sidecar/notes
- Live Grill Mode transcript analysis
- deterministic /goal + /subgoal synthesis for weaker follow-up models

It does not open any HTTP server and does not expose ports. It only reads local
env/config files and, when asked, calls the existing localhost sidecar.

Status tags used by this module:
  WORKING   — implemented and wired
  PARTIAL   — implemented with known gaps
  PLANNED   — on the roadmap but not yet shipped
  RESEARCH  — being evaluated, no production code yet
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SECRET_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._\-~+/=]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[A-Za-z0-9._\-~+/=]{12,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(token\s*[=:]\s*)[A-Za-z0-9._\-~+/=]{12,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(jwt\s*[=:]\s*)[A-Za-z0-9._\-~+/=]{20,}"), r"\1[REDACTED]"),
    (re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9._\-]+"), "[REDACTED_DISCORD_WEBHOOK]"),
    (re.compile(r"\bpk_[A-Za-z0-9._\-]{10,}\b"), "pk_[REDACTED]"),
    (re.compile(r"\b(sk|key|ghp|github_pat)_[A-Za-z0-9._\-]{12,}\b"), "[REDACTED_TOKEN]"),
    # Caution: this last pattern is broad and will redact any three-dot word group.
    # It is intentionally last so more specific patterns run first.
    (re.compile(r"\b[A-Za-z0-9_\-.]+\.[A-Za-z0-9_\-.]+\.[A-Za-z0-9_\-.]+\b"), "[REDACTED_JWT]"),
)

_GOAL_HINTS = (
    "build", "fix", "make", "ship", "wire", "integrate", "bridge", "agent",
    "autonomous", "goal", "subgoal", "repo", "vapi", "gemini", "pocket", "honcho",
)

_VAGUE_WORDS = (
    "thing", "stuff", "shit", "somehow", "whatever", "maybe", "later", "soon",
    "make it work", "better", "nice", "smart", "autonomous", "it", "this",
)

_CLASSIFIERS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("gemini_live_bridge", ("gemini", "live", "voice-live", "18943", "bidi", "wss")),
    ("vapi_bridge", ("vapi", "voice-vapi", "18944", "outbound call")),
    ("honcho_memory", ("honcho", "memory", "peer", "workspace", "jwt", "context")),
    ("pocket_import", ("pocket", "recording", "transcript", "summary", "action item")),
    ("discord_delivery", ("discord", "webhook", "channel", "dm", "hermes send")),
    ("repo_repair", ("repo", "patch", "branch", "commit", "test", "fix")),
)


def redact_secrets(value: Any, max_chars: Optional[int] = None) -> str:
    """Return a string safe for Gemini context, Discord messages, and logs."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    if max_chars is not None and len(text) > max_chars:
        return text[: max_chars - 24] + "\n...[TRUNCATED/REDACTED]..."
    return text


def _env_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {"exists": False, "path": str(path)}
        data = json.loads(path.read_text(errors="replace"))
        return {"exists": True, "path": str(path), "keys": sorted(data.keys()) if isinstance(data, dict) else []}
    except Exception as exc:
        return {"exists": True, "path": str(path), "error": f"{type(exc).__name__}: {exc}"}


async def _sidecar_get(port: int, path: str = "/health", timeout: float = 2.0) -> Dict[str, Any]:
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        req = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        writer.write(req.encode("utf-8"))
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        body = raw.split(b"\r\n\r\n", 1)[-1].decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": redact_secrets(body, 2000)}
        return {"reachable": True, "path": path, "body": parsed}
    except Exception as exc:
        return {"reachable": False, "path": path, "error": f"{type(exc).__name__}: {exc}"}


async def build_preflight_report(bridge_mod: Any = None, active_bridges: Optional[Dict[int, Any]] = None) -> Dict[str, Any]:
    """Build a safe SORA/Gemini bridge diagnostic report."""
    home = Path.home()
    notes_dir = Path(os.getenv("DISCORD_VOICE_LIVE_NOTES_DIR", str(home / ".hermes" / "voice-live-notes")))
    port = int(os.getenv("DISCORD_VOICE_LIVE_PORT", "18943"))
    model = os.getenv("GEMINI_MODEL", getattr(bridge_mod, "GEMINI_MODEL", "") if bridge_mod else os.getenv("GEMINI_MODEL", ""))
    fallbacks = os.getenv("GEMINI_LIVE_MODEL_FALLBACKS", getattr(bridge_mod, "GEMINI_MODEL_FALLBACKS", "") if bridge_mod else os.getenv("GEMINI_LIVE_MODEL_FALLBACKS", ""))
    if isinstance(fallbacks, list):
        fallback_list = fallbacks
    else:
        fallback_list = [x.strip() for x in str(fallbacks).split(",") if x.strip()]

    warnings: List[str] = []
    if not _env_present("GEMINI_API_KEY") and not _env_present("GOOGLE_API_KEY"):
        warnings.append("missing GEMINI_API_KEY/GOOGLE_API_KEY")
    if model and ("3.1" in model or "12-2025" in model):
        warnings.append(f"model name looks future/stale; validate against current Gemini Live model list: {model}")
    if not notes_dir.exists():
        warnings.append(f"notes directory does not exist yet: {notes_dir}")
    if os.getenv("VOICE_LIVE_HONCHO_CONTEXT", "true").lower() in {"1", "true", "yes", "on"}:
        if not any(p.exists() for p in (home / ".hermes" / "honcho.json", home / ".honcho" / "config.json")):
            warnings.append("Honcho context enabled but no ~/.hermes/honcho.json or ~/.honcho/config.json found")

    sidecar = await _sidecar_get(port, "/health")
    honcho_configs = [
        _read_json(home / ".hermes" / "honcho.json"),
        _read_json(home / ".honcho" / "config.json"),
    ]

    active = []
    if active_bridges:
        for gid, info in active_bridges.items():
            vc = info.get("vc") if isinstance(info, dict) else None
            active.append({
                "guild_id": str(gid),
                "vc_connected": bool(vc and vc.is_connected()),
                "channel_id": str(getattr(getattr(vc, "channel", None), "id", "")) if vc else "",
                "task_done": bool(info.get("task") and info.get("task").done()) if isinstance(info, dict) else False,
            })

    status = "ok" if not warnings else "warn"
    if not sidecar.get("reachable") and active:
        status = "error"
        warnings.append("active bridge registry exists but sidecar health is unreachable")

    return {
        "status": status,
        "component": "sora-gemini-live-bridge-preflight",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gemini": {
            "api_key_present": _env_present("GEMINI_API_KEY") or _env_present("GOOGLE_API_KEY"),
            "model": model,
            "fallbacks": fallback_list,
            "voice": os.getenv("DISCORD_VOICE_LIVE_VOICE", getattr(bridge_mod, "GEMINI_VOICE_NAME", "Kore") if bridge_mod else "Kore"),
        },
        "honcho": {
            "enabled": os.getenv("VOICE_LIVE_HONCHO_CONTEXT", "true"),
            "peer": os.getenv("VOICE_LIVE_HONCHO_PEER", os.getenv("HONCHO_PEER_NAME", os.getenv("DISCORD_VOICE_LIVE_USER_ID", ""))),
            "configs": honcho_configs,
            "required_behavior": "fail loud on 401/missing workspace/missing peer instead of silent empty context",
        },
        "sidecar": {"port": port, **sidecar},
        "notes": {"dir": str(notes_dir), "exists": notes_dir.exists()},
        "active_bridges": active,
        "warnings": warnings,
    }


def classify_transcript(text: str) -> List[str]:
    lower = text.lower()
    labels = []
    for label, needles in _CLASSIFIERS:
        if any(n in lower for n in needles):
            labels.append(label)
    return labels or ["unknown"]


def grill_transcript(text: str, max_questions: int = 7) -> Dict[str, Any]:
    safe = redact_secrets(text, 12000)
    lower = safe.lower()
    labels = classify_transcript(safe)
    vague_hits = [w for w in _VAGUE_WORDS if w in lower]
    has_goal_hint = any(w in lower for w in _GOAL_HINTS)

    questions = []
    if not has_goal_hint:
        questions.append("What concrete thing should ship at the end of this session?")
    questions.extend([
        "What is the exact objective in one sentence?",
        "What repo/file/service is the owner of the fix?",
        "What constraints are non-negotiable: Tailscale-only, no public ports, no secret leakage, no user prompts?",
        "What is the next command or patch the agent should run?",
        "What would prove this worked: health output, transcript, Discord message, test, or PR?",
        "What should fail loudly instead of silently degrading?",
        "What should be archived as memory versus sent as a compact alert?",
    ])
    if "honcho_memory" in labels:
        questions.insert(1, "Which Honcho workspace/peer must be used, and what should happen on 401?")
    if "pocket_import" in labels:
        questions.insert(1, "Is this a new Pocket cue alert or a long transcript digest?")
    if vague_hits:
        questions.insert(0, f"You used vague terms ({', '.join(vague_hits[:5])}); what do they refer to exactly?")

    return {
        "status": "ok",
        "labels": labels,
        "vague_hits": vague_hits[:10],
        "questions": questions[: max(1, min(max_questions, 12))],
        "redacted_excerpt": safe[:1500],
    }


def synthesize_goal_subgoals(text: str) -> Dict[str, Any]:
    analysis = grill_transcript(text, max_questions=6)
    labels = analysis["labels"]
    focus = ", ".join(labels)
    goal = (
        f"/goal Autonomously turn this transcript/call into an execution-grade SORA plan. "
        f"Focus areas: {focus}. Inspect repo/config/log facts first, redact secrets, keep Tailscale/local-first, "
        f"produce exact fixes, tests, rollback, and Discord-safe summary."
    )
    subgoals = [
        "/subgoal Map the relevant repo files, configs, env vars, sidecars, scripts, and transcript sources. Output path, purpose, risk, and owner.",
        "/subgoal Diagnose bridge context and memory paths. Confirm Gemini/Vapi/SORA/Honcho/Pocket auth sources and fail-loud points.",
        "/subgoal Implement or recommend the smallest safe patch set. Separate repair work, product behavior, delivery rules, and tests.",
        "/subgoal Add Live Grill Mode output: objective, constraints, owner, deadline, risks, next command, verification test, and missing questions.",
        "/subgoal Validate end-to-end with one fake long transcript and send only a redacted compact Discord summary.",
    ]
    if "gemini_live_bridge" in labels:
        subgoals.insert(1, "/subgoal Verify Gemini Live model/config/audio/tool-call setup against current docs and add startup preflight diagnostics.")
    if "vapi_bridge" in labels:
        subgoals.insert(2, "/subgoal Verify Vapi bridge lifecycle, outbound call/WSS config, sidecar health, transcript output, and Discord fallback.")
    if "pocket_import" in labels:
        subgoals.insert(2, "/subgoal Import Pocket cues via MCP/API or existing poller; compact alerts for new cues, digest summaries for long recordings, no raw webhook secrets.")
    return {"status": "ok", "goal": goal, "subgoals": subgoals[:7], "grill": analysis}


async def _handler_preflight(args: Optional[Dict[str, Any]], bridge_mod: Any, active_bridges: Dict[int, Any]) -> str:
    return json.dumps(await build_preflight_report(bridge_mod, active_bridges), ensure_ascii=False)


async def _handler_grill(args: Optional[Dict[str, Any]]) -> str:
    args = args or {}
    text = str(args.get("text") or args.get("transcript") or "")
    if not text.strip():
        return json.dumps({"status": "error", "message": "text or transcript is required"})
    return json.dumps(grill_transcript(text, int(args.get("max_questions") or 7)), ensure_ascii=False)


async def _handler_synth(args: Optional[Dict[str, Any]]) -> str:
    args = args or {}
    text = str(args.get("text") or args.get("transcript") or "")
    if not text.strip():
        return json.dumps({"status": "error", "message": "text or transcript is required"})
    return json.dumps(synthesize_goal_subgoals(text), ensure_ascii=False)


async def _handler_redact(args: Optional[Dict[str, Any]]) -> str:
    args = args or {}
    text = str(args.get("text") or "")
    return json.dumps({"status": "ok", "text": redact_secrets(text, int(args.get("max_chars") or 4000))}, ensure_ascii=False)


def register_sora_bridge_tools(ctx: Any, bridge_mod: Any = None, active_bridges: Optional[Dict[int, Any]] = None) -> None:
    """Register SORA bridge element tools on a Hermes plugin ctx."""
    active_bridges = active_bridges if active_bridges is not None else {}

    ctx.register_tool(
        name="sora_bridge_preflight",
        toolset="hermes",
        schema={
            "name": "sora_bridge_preflight",
            "description": "Run SORA/Gemini Live bridge preflight diagnostics: Gemini env/model, Honcho config paths, sidecar health, notes dir, active bridge registry. Redacts secrets.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        handler=lambda args=None, **kwargs: _handler_preflight(args or kwargs, bridge_mod, active_bridges),
        check_fn=lambda: True,
        is_async=True,
    )

    ctx.register_tool(
        name="sora_live_grill",
        toolset="hermes",
        schema={
            "name": "sora_live_grill",
            "description": "Analyze a transcript/call and produce targeted Live Grill Mode questions that force objective, constraints, owner, deadline, risk, next command, and verification test.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Transcript or call notes text"},
                    "transcript": {"type": "string", "description": "Alias for text"},
                    "max_questions": {"type": "integer", "description": "Max questions to return"},
                },
                "additionalProperties": False,
            },
        },
        handler=lambda args=None, **kwargs: _handler_grill(args or kwargs),
        check_fn=lambda: True,
        is_async=True,
    )

    ctx.register_tool(
        name="sora_goal_synth",
        toolset="hermes",
        schema={
            "name": "sora_goal_synth",
            "description": "Generate a Discord-safe /goal plus ranked /subgoal items from a transcript/call for weaker autonomous models.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Transcript or call notes text"},
                    "transcript": {"type": "string", "description": "Alias for text"},
                },
                "additionalProperties": False,
            },
        },
        handler=lambda args=None, **kwargs: _handler_synth(args or kwargs),
        check_fn=lambda: True,
        is_async=True,
    )

    ctx.register_tool(
        name="sora_redact",
        toolset="hermes",
        schema={
            "name": "sora_redact",
            "description": "Redact API keys, bearer tokens, JWTs, Pocket keys, Discord webhooks, and GitHub tokens before sending text to Gemini/Discord/logs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to redact"},
                    "max_chars": {"type": "integer", "description": "Optional output character cap"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        handler=lambda args=None, **kwargs: _handler_redact(args or kwargs),
        check_fn=lambda: True,
        is_async=True,
    )
