"""
delegation_agent.py — Multi-CLI delegation framework for the voice bridge.

Supports:
  - opencode     (opencode run)
  - codex        (codex exec)
  - gemini       (gemini ...)
  - numasec      (numasec ...)
  - hermes-api   (Hermes API server HTTP)

Flow:
  1. Gemini calls local_delegate_start(goal)
  2. Framework returns at most one clarifying question when ambiguity blocks action
  3. Gemini asks user in voice only if needed
  4. Gemini calls local_delegate_suggest(goal, size, scope, complexity, platform_hint)
  5. Framework checks rate limits, suggests platform, estimates time
  6. User confirms
  7. Gemini calls local_delegate_assemble(goal, subgoals, platform)
  8. Framework builds platform-optimized system prompt
  9. Gemini calls local_delegate_execute(prompt, platform)
  10. Spawns CLI, reports session_id, watcher fires on progress
"""

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("delegation-agent")

# ── Platform definitions ─────────────────────────────────────────────────
PLATFORMS = {
    "opencode": {
        "name": "OpenCode",
        "binary": "/home/caps/.local/bin/opencode",
        "max_context": 128_000,
        "strengths": ["code generation", "refactoring", "test writing", "debugging"],
        "weaknesses": ["web search", "large file IO"],
        "min_tokens": 4000,  # min tokens for a simple prompt
        "max_tokens": 126_000,
        "rate_limit_key": "opencode_requests",
    },
    "codex": {
        "name": "Codex CLI",
        "binary": "/home/caps/.npm-global/bin/codex",
        "max_context": 200_000,
        "strengths": ["reasoning", "complex code changes", "multi-file refactors"],
        "weaknesses": ["small quick edits (overhead)", "streaming"],
        "min_tokens": 8000,
        "max_tokens": 195_000,
        "rate_limit_key": "codex_requests",
    },
    "gemini": {
        "name": "Gemini CLI",
        "binary": "/home/caps/.npm-global/bin/gemini",
        "max_context": 1_000_000,
        "strengths": ["huge context windows", "vision", "audio understanding"],
        "weaknesses": ["code execution", "tool use"],
        "min_tokens": 1000,
        "max_tokens": 900_000,
        "rate_limit_key": "gemini_tokens",
    },
    "numasec": {
        "name": "Numasec",
        "binary": "/home/caps/.npm-global/bin/numasec",
        "max_context": 128_000,
        "strengths": ["security analysis", "code review", "vulnerability scanning"],
        "weaknesses": ["general coding", "web tasks"],
        "min_tokens": 2000,
        "max_tokens": 120_000,
        "rate_limit_key": "numasec_requests",
    },
    "hermes-api": {
        "name": "Hermes API Server",
        "binary": None,  # HTTP, not CLI
        "max_context": None,  # depends on upstream model
        "strengths": ["any task the Hermes agent can do", "tool access", "multi-step planning"],
        "weaknesses": ["no direct voice", "async dispatch"],
        "min_tokens": None,
        "max_tokens": None,
        "rate_limit_key": "hermes_dispatch",
        "api_config": {
            "host": os.getenv("API_SERVER_HOST", "127.0.0.1"),
            "port": int(os.getenv("API_SERVER_PORT", "0") or "0") or 8088,
            "key": os.getenv("API_SERVER_KEY", ""),
        },
    },
}

# ── Rate-limit tracking (per rolling window) ─────────────────────────────
_RATE_LIMITS: Dict[str, List[float]] = {}
_RATE_WINDOW_SECONDS = 3600  # 1 hour
_RATE_LIMIT_CAPS = {
    "opencode_requests": 100,  # requests per hour (estimated)
    "codex_requests": 50,      # requests per hour (conservative, no auth wall)
    "gemini_tokens": 1_000_000,  # tokens per hour (Free tier, ~1500/min)
    "numasec_requests": 60,    # per hour
    "hermes_dispatch": 200,    # per hour
}


# ── Fallback chain (criterion #5: "fix broken tools via neighbors") ──────
# When a platform is marked broken (binary missing, rate-limited, auth
# failed, persistent error in tmux log), the next delegation on that
# platform auto-routes to the first healthy neighbor in this list.
_FALLBACK_CHAIN: Dict[str, List[str]] = {
    "codex":       ["opencode", "hermes-api", "gemini"],
    "opencode":    ["codex", "hermes-api", "gemini"],
    "numasec":     ["opencode", "codex", "hermes-api"],
    "gemini":      ["opencode", "codex", "hermes-api"],
    "hermes-api":  ["opencode", "codex", "gemini"],
}

# Marked-broken platforms persist to disk so the flag survives bridge
# restarts. Format: {pid: {reason, marked_at_monotonic, ttl_seconds}}
_HEALTH_PATH = Path.home() / ".hermes" / "voice-platform-health.json"
_HEALTH_DEFAULT_TTL = 600  # 10 min — re-try after this


def _load_health() -> Dict[str, Dict[str, Any]]:
    try:
        if _HEALTH_PATH.exists():
            return json.loads(_HEALTH_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("voice-platform-health: load failed: %s", exc)
    return {}


def _save_health(health: Dict[str, Dict[str, Any]]) -> None:
    try:
        _HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HEALTH_PATH.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("voice-platform-health: save failed: %s", exc)


def mark_platform_broken(platform: str, reason: str, ttl_seconds: int = _HEALTH_DEFAULT_TTL) -> None:
    """Flag a platform as unhealthy. Persists to disk with a TTL.

    Suggestion / execute flows will skip this platform until the TTL
    expires, and `local_delegate_execute` will auto-route the first
    subsequent call on this platform to the next healthy neighbor.
    """
    health = _load_health()
    health[platform] = {
        "reason": reason[:280],
        "marked_at": time.time(),
        "expires_at": time.time() + ttl_seconds,
        "ttl_seconds": ttl_seconds,
    }
    _save_health(health)
    logger.warning("voice-platform-health: marked %s broken — %s (ttl=%ds)", platform, reason, ttl_seconds)
    # Best-effort webhook so the agent can narrate it
    try:
        from webhook_dispatcher import emit_fallback_event  # type: ignore
        emit_fallback_event(platform, reason[:200], list(_FALLBACK_CHAIN.get(platform, [])))
    except Exception:
        pass


def clear_platform_health(platform: Optional[str] = None) -> None:
    """Clear health flags — pass a platform to clear one, or None to clear all."""
    if platform is None:
        _save_health({})
        return
    health = _load_health()
    health.pop(platform, None)
    _save_health(health)


def get_health_snapshot() -> Dict[str, Any]:
    """Read current health state, pruning expired entries. Returns {pid: {reason, expires_in}}."""
    now = time.time()
    health = _load_health()
    pruned = {}
    expired = []
    for pid, entry in health.items():
        if entry.get("expires_at", 0) <= now:
            expired.append(pid)
            continue
        pruned[pid] = {
            "reason": entry.get("reason", "?"),
            "expires_in_seconds": int(entry.get("expires_at", 0) - now),
        }
    if expired:
        for pid in expired:
            health.pop(pid, None)
        _save_health(health)
    return pruned


def is_platform_healthy(platform: str) -> bool:
    snapshot = get_health_snapshot()
    return platform not in snapshot


def choose_fallback(platform: str) -> Optional[str]:
    """Return the first healthy neighbor in FALLBACK_CHAIN, or None."""
    for neighbor in _FALLBACK_CHAIN.get(platform, []):
        if is_platform_healthy(neighbor):
            return neighbor
    return None


# Patterns in a CLI's tmux log that mean "this platform is broken right now"
# (not just a one-off task failure). Auto-fallback fires when any of these
# appear within the first ~5s of log output.
_BROKEN_LOG_PATTERNS = [
    re.compile(r"\b(?:HTTP\s*|status[: ]?)\s*401\b", re.I),
    re.compile(r"\b(?:HTTP\s*|status[: ]?)\s*403\b", re.I),
    re.compile(r"\b(?:HTTP\s*|status[: ]?)\s*429\b", re.I),
    re.compile(r"\b(?:HTTP\s*|status[: ]?)\s*5\d\d\b", re.I),
    re.compile(r"\brate[- ]limit", re.I),
    re.compile(r"\b(?:command|program)\s+not\s+found\b", re.I),
    re.compile(r"\bno\s+such\s+file\b", re.I),
    re.compile(r"\bpermission\s+denied\b", re.I),
    re.compile(r"\bauth(?:entication|orization)?\s+(?:failed|error)\b", re.I),
    re.compile(r"\b(?:api[_-]?key|token)\s+(?:invalid|expired|missing)\b", re.I),
    re.compile(r"\bconnection\s+refused\b", re.I),
    re.compile(r"\b(?:ollama|openrouter)\s+(?:error|unavailable)\b", re.I),
    re.compile(r"\b(?:free\s*tier|quota)\s+exceeded\b", re.I),
    re.compile(r"^\s*Traceback\s+\(most recent call last\)", re.M),
]


def detect_broken_log(log_path: str, head_bytes: int = 4096) -> Optional[str]:
    """Return a short reason string if the log shows the platform is broken, else None."""
    try:
        p = Path(log_path)
        if not p.exists():
            return None
        head = p.read_text(errors="replace")[:head_bytes]
        for pat in _BROKEN_LOG_PATTERNS:
            m = pat.search(head)
            if m:
                snippet = head[max(0, m.start() - 20):m.end() + 60].strip().replace("\n", " ")
                return f"log matched {pat.pattern!r}: {snippet[:120]}"
    except Exception as exc:
        logger.debug("detect_broken_log: %s", exc)
    return None


def execute_with_fallback(
    prompt: str,
    platform: str,
    session_id: str,
    workdir: Optional[str] = None,
    health_check_delay: float = 5.0,
) -> Dict[str, Any]:
    """Spawn `platform`; if it appears broken within `health_check_delay`
    seconds, auto-respawn on the first healthy neighbor from FALLBACK_CHAIN.

    Returns a composite dict that always includes the original platform,
    the platform that actually ran, and (if a fallback fired) the reason.
    """
    if not is_platform_healthy(platform):
        neighbor = choose_fallback(platform)
        if neighbor:
            inner = execute_with_fallback(prompt, neighbor, session_id, workdir, health_check_delay)
            inner["requested_platform"] = platform
            inner["active_platform"] = inner.get("active_platform", neighbor)
            # If a deeper layer already set fallback_from, keep that chain
            if "fallback_from" not in inner:
                inner["fallback_from"] = platform
            if "fallback_reason" not in inner:
                inner["fallback_reason"] = f"platform '{platform}' was marked broken (pre-check)"
            return inner
        return {
            "error": f"platform '{platform}' is marked broken and no healthy neighbor found",
            "requested_platform": platform,
            "active_platform": platform,
            "health": get_health_snapshot(),
        }

    result = execute_delegation(prompt, platform, session_id, workdir)
    if "error" in result:
        # Hard error before tmux spawn — treat as broken, try neighbor
        mark_platform_broken(platform, f"execute_delegation error: {str(result['error'])[:160]}")
        neighbor = choose_fallback(platform)
        if neighbor:
            inner = execute_with_fallback(prompt, neighbor, session_id, workdir, health_check_delay)
            inner["requested_platform"] = platform
            inner["active_platform"] = inner.get("active_platform", neighbor)
            if "fallback_from" not in inner:
                inner["fallback_from"] = platform
            inner["fallback_reason"] = str(result["error"])[:200]
            return inner
        result["requested_platform"] = platform
        result["active_platform"] = platform
        return result

    result.setdefault("requested_platform", platform)
    result.setdefault("active_platform", platform)

    log_path = result.get("log_path")
    if log_path and health_check_delay > 0:
        # Wait for early signs of trouble
        time.sleep(health_check_delay)
        reason = detect_broken_log(log_path)
        if reason:
            mark_platform_broken(platform, reason)
            neighbor = choose_fallback(platform)
            if neighbor:
                inner = execute_with_fallback(prompt, neighbor, session_id + "-fb", workdir, health_check_delay)
                inner["fallback_from"] = platform
                inner["fallback_reason"] = reason
                inner["original_log_path"] = log_path
                return inner
            # No healthy neighbor — keep the original (still useful) result
            result["health_warning"] = reason

    result.setdefault("requested_platform", platform)
    result.setdefault("active_platform", platform)
    return result


def _check_rate_limit(rate_limit_key: str) -> Tuple[bool, int, int]:
    """Returns (allowed, used_this_hour, cap)."""
    now = time.monotonic()
    window = _RATE_LIMITS.get(rate_limit_key, [])
    # Prune entries outside the 1h window
    window = [t for t in window if (now - t) < _RATE_WINDOW_SECONDS]
    _RATE_LIMITS[rate_limit_key] = window
    cap = _RATE_LIMIT_CAPS.get(rate_limit_key, 9999)
    used = len(window)
    allowed = used < cap
    return allowed, used, cap


def _record_rate_limit(rate_limit_key: str) -> None:
    now = time.monotonic()
    window = _RATE_LIMITS.get(rate_limit_key, [])
    window.append(now)
    _RATE_LIMITS[rate_limit_key] = [t for t in window if (now - t) < _RATE_WINDOW_SECONDS]


def get_all_rate_limits() -> Dict[str, Dict[str, Any]]:
    """Return rate-limit status for all platforms."""
    out = {}
    for pid, info in PLATFORMS.items():
        rlk = info.get("rate_limit_key")
        if not rlk:
            out[pid] = {"available": True, "used": 0, "cap": 9999}
            continue
        allowed, used, cap = _check_rate_limit(rlk)
        out[pid] = {"available": allowed, "used": used, "cap": cap}
    return out


# ── ETA estimation (based on project complexity) ─────────────────────────
# rough multipliers derived from prior builds (calibratable via
# local_delegate_learn_eta)
_ETA_BY_SIZE = {
    "tiny": 60,       # 1 min — single file edit
    "small": 300,     # 5 min — small feature
    "medium": 900,    # 15 min — multi-file refactor
    "large": 3600,    # 1 hr — new feature across many files
    "xlarge": 7200,   # 2 hr — significant project work
}
_USER_ETA_CORRECTION = {}  # {user_id: multiplier}


def estimate_eta(project_size: str, complexity: str, user_id: Optional[str] = None) -> int:
    """Return estimated seconds for a project."""
    base_sec = _ETA_BY_SIZE.get(project_size, 300)
    if complexity == "low":
        base_sec = int(base_sec * 0.6)
    elif complexity == "high":
        base_sec = int(base_sec * 1.8)
    elif complexity == "extreme":
        base_sec = int(base_sec * 3.0)
    if user_id and user_id in _USER_ETA_CORRECTION:
        base_sec = int(base_sec * _USER_ETA_CORRECTION[user_id])
    return min(base_sec, 14400)  # cap at 4 hours


# ── Prompt assembly ──────────────────────────────────────────────────────
def assemble_prompt(
    goal: str,
    subgoals: List[str],
    platform: str,
    project_root: Optional[str] = None,
) -> str:
    """Build a platform-optimized system prompt for the target CLI."""
    platform_info = PLATFORMS.get(platform, {})
    prompt_parts = [
        "# Goal",
        goal.strip(),
    ]
    if subgoals:
        prompt_parts.append("")
        prompt_parts.append("## Sub-goals (in order)")
        for i, sg in enumerate(subgoals, 1):
            prompt_parts.append(f"  {i}. {sg.strip()}")

    prompt_parts.append("")
    prompt_parts.append("## Constraints")
    prompt_parts.append("- Do NOT hallucinate files or dependencies.")
    prompt_parts.append("- Ask before destructive operations (rm, drop table, etc.).")
    prompt_parts.append("- If stuck, explain what you tried and suggest next steps.")
    prompt_parts.append("- Keep commits atomic and messages clear.")

    if project_root:
        prompt_parts.append("")
        prompt_parts.append(f"```\ncd {project_root}\n```")
        prompt_parts.append(f"Project root: {project_root}")

    # Platform-specific optimizations
    if platform == "codex":
        prompt_parts.append("")
        prompt_parts.append("## Codex-specific")
        prompt_parts.append("- Run in full-auto mode with auto-approve where safe.")
        prompt_parts.append("- After each change, verify the file compiles.")
        prompt_parts.append("- Use --yolo for low-risk operations.")
    elif platform == "opencode":
        prompt_parts.append("")
        prompt_parts.append("## OpenCode-specific")
        prompt_parts.append("- Use the 'run' mode for one-shot task execution.")
        prompt_parts.append("- Model: auto-resolved by the CLI.")
    elif platform == "gemini":
        prompt_parts.append("")
        prompt_parts.append("## Gemini CLI-specific")
        prompt_parts.append("- Use the full context window for analysis.")
        prompt_parts.append("- Prefer structured output (JSON).")
    elif platform == "numasec":
        prompt_parts.append("")
        prompt_parts.append("## Numasec-specific")
        prompt_parts.append("- Focus on security analysis, vulnerability scanning.")
        prompt_parts.append("- Output severity-graded findings.")
    elif platform == "hermes-api":
        prompt_parts.append("")
        prompt_parts.append("## Hermes API-specific")
        prompt_parts.append("- The agent has full Hermes tool access.")
        prompt_parts.append("- Use the shortest path to the result.")

    return "\n".join(prompt_parts)


# ── CLI execution ────────────────────────────────────────────────────────
_ACTIVE_DELEGATIONS: Dict[str, Dict[str, Any]] = {}


def execute_delegation(
    prompt: str,
    platform: str,
    session_id: str,
    workdir: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a prompt to the selected CLI and return a session handle.

    For CLI platforms, spawns a subprocess (background) or tmux window.
    For hermes-api, sends an HTTP POST to the Hermes API server.
    """
    platform_info = PLATFORMS.get(platform)
    if not platform_info:
        return {"error": f"Unknown platform: {platform}"}

    workdir = workdir or os.getenv("HOME", "/home/caps")

    if platform == "opencode":
        return _run_opencode(prompt, session_id, workdir, platform_info)
    elif platform == "codex":
        return _run_codex(prompt, session_id, workdir, platform_info)
    elif platform == "gemini":
        return _run_gemini_cli(prompt, session_id, workdir, platform_info)
    elif platform == "numasec":
        return _run_numasec(prompt, session_id, workdir, platform_info)
    elif platform == "hermes-api":
        return _run_hermes_api(prompt, session_id, platform_info)
    return {"error": f"No executor for platform: {platform}"}


def _tmux_exec(session_name: str, window_name: str, cmd: str, log_path: str) -> Dict[str, Any]:
    """Run a command in a tmux window and return session info."""
    window = f"del-{window_name}"
    # Kill prior window with same name
    subprocess.run(["tmux", "kill-window", "-t", f"delegate:{window}"], capture_output=True)
    # Create tmux session if needed
    subprocess.run(["tmux", "has-session", "-t", "delegate"], capture_output=True)
    if subprocess.run(["tmux", "has-session", "-t", "delegate"], capture_output=True).returncode != 0:
        subprocess.run(["tmux", "new-session", "-d", "-s", "delegate", "-n", "_init"], check=False)
    # Spawn in new window
    import shlex
    full_cmd = f"cd {shlex.quote(session_name)} 2>/dev/null; {cmd} 2>&1 | tee {shlex.quote(log_path)}; echo '[delegate] session ended'"
    subprocess.run(["tmux", "new-window", "-d", "-t", "delegate", "-n", window, "bash", "-c", full_cmd], capture_output=True)
    return {"session_id": session_name, "tmux_window": window, "log_path": log_path}


def _run_opencode(prompt: str, session_id: str, workdir: str, info: Dict[str, Any]) -> Dict[str, Any]:
    binary = info["binary"]
    if not Path(binary).exists():
        return {"error": f"opencode not found at {binary}"}
    import shlex
    log_path = f"/tmp/delegate-opencode-{session_id}.log"
    cmd = f"echo {shlex.quote(prompt)} | {binary} run -y 2>&1"
    return _tmux_exec(workdir, f"oc-{session_id}", cmd, log_path)


def _run_codex(prompt: str, session_id: str, workdir: str, info: Dict[str, Any]) -> Dict[str, Any]:
    binary = info["binary"]
    if not Path(binary).exists():
        return {"error": f"codex not found at {binary}"}
    import shlex
    log_path = f"/tmp/delegate-codex-{session_id}.log"
    # Codex exec: pass prompt via stdin echo
    cmd = f"echo {shlex.quote(prompt)} | {binary} exec --full-auto --yolo 2>&1"
    return _tmux_exec(workdir, f"cd-{session_id}", cmd, log_path)


def _run_gemini_cli(prompt: str, session_id: str, workdir: str, info: Dict[str, Any]) -> Dict[str, Any]:
    binary = info["binary"]
    if not Path(binary).exists():
        return {"error": f"gemini CLI not found at {binary}"}
    import shlex
    log_path = f"/tmp/delegate-gemini-{session_id}.log"
    # Gemini CLI takes a prompt as argument
    cmd = f"{binary} chat --text {shlex.quote(prompt)} 2>&1"
    return _tmux_exec(workdir, f"gm-{session_id}", cmd, log_path)


def _run_numasec(prompt: str, session_id: str, workdir: str, info: Dict[str, Any]) -> Dict[str, Any]:
    binary = info["binary"]
    if not Path(binary).exists():
        return {"error": f"numasec not found at {binary}"}
    import shlex
    log_path = f"/tmp/delegate-numasec-{session_id}.log"
    cmd = f"{binary} run {shlex.quote(prompt)} 2>&1"
    return _tmux_exec(workdir, f"ns-{session_id}", cmd, log_path)


def _run_hermes_api(prompt: str, session_id: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """Send a task to the Hermes API server as a fire-and-forget chat request."""
    api_cfg = info.get("api_config", {})
    host = api_cfg.get("host", "127.0.0.1")
    port = api_cfg.get("port", 8088)
    key = api_cfg.get("key", "")
    log_path = f"/tmp/delegate-hermes-{session_id}.log"
    try:
        import requests
        resp = requests.post(
            f"http://{host}:{port}/api/chat",
            json={"message": prompt, "key": key, "source": "voice-delegation"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("response", "") or data.get("message", "")
        with open(log_path, "w") as f:
            f.write(result)
        return {"session_id": session_id, "response": result[:500], "log_path": log_path}
    except Exception as exc:
        with open(log_path, "w") as f:
            f.write(f"[Hermes API dispatch failed: {exc}]")
        return {"session_id": session_id, "error": str(exc), "log_path": log_path}


# ── Tool interface (called from bridge.py) ───────────────────────────────

def suggest_platform(
    goal: str,
    project_size: str,
    scope: str,
    complexity: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyze the task and suggest the best platform + ETA.

    Platforms currently marked broken in the health registry are filtered
    out before scoring. (criterion #5)
    """
    rates = get_all_rate_limits()
    broken = set(get_health_snapshot().keys())
    available = [
        pid for pid, r in rates.items()
        if r.get("available", False) and pid not in broken
    ]
    if not available:
        return {
            "error": "All healthy platforms are either rate-limited or marked broken. Please wait or run local_delegate_health action='clear'.",
            "rates": rates,
            "unhealthy": list(broken),
        }

    # Score each platform
    scores = {}
    for pid in available:
        info = PLATFORMS[pid]
        score = 50
        # Size-based scoring
        if project_size in ("tiny", "small"):
            if pid == "opencode":
                score += 30
            elif pid == "gemini":
                score += 20
        elif project_size in ("large", "xlarge"):
            if pid == "codex":
                score += 30
            elif pid == "gemini":
                score += 25
        # Complexity scoring
        if complexity == "high" and pid == "codex":
            score += 20
        if complexity == "extreme" and pid == "gemini":
            score += 30
        # Scope scoring
        if scope in ("code", "refactor") and pid in ("opencode", "codex"):
            score += 20
        if scope in ("security", "audit") and pid == "numasec":
            score += 35
        if scope in ("research", "analysis") and pid == "gemini":
            score += 25
        scores[pid] = score

    best = max(scores, key=scores.get)
    eta_sec = estimate_eta(project_size, complexity, user_id)
    eta_str = f"{eta_sec // 60}m {eta_sec % 60}s" if eta_sec >= 60 else f"{eta_sec}s"

    return {
        "suggestion": best,
        "reason": _explain_suggestion(best, project_size, complexity, scope, eta_sec),
        "estimated_eta_seconds": eta_sec,
        "estimated_eta_display": eta_str,
        "all_scores": scores,
        "rate_limits": rates,
        "available_platforms": available,
    }


def _explain_suggestion(platform: str, size: str, complexity: str, scope: str, eta_sec: int = 300) -> str:
    info = PLATFORMS.get(platform, {})
    name = info.get("name", platform)
    context = info.get("max_context", "?")
    ctx_str = f"{context // 1000}k tokens" if context else "flexible"
    eta_str = f"{eta_sec // 60}m{eta_sec % 60}s" if eta_sec >= 60 else f"{eta_sec}s"

    reasons = []
    if platform == "opencode":
        reasons.append(f"fastest for {size} tasks like this")
    elif platform == "codex":
        reasons.append(f"handles {complexity} complexity well")
    elif platform == "gemini":
        reasons.append(f"large context window ({ctx_str}) for scope={scope}")
    elif platform == "numasec":
        reasons.append(f"specialized for {scope} analysis")

    return f"I suggest **{name}** ({', '.join(reasons)}). ETA: **{eta_str}**"


def check_context_fit(goal: str, platform: str, project_root: Optional[str] = None) -> Dict[str, Any]:
    """Estimate whether the goal fits the platform's context window."""
    info = PLATFORMS.get(platform, {})
    max_ctx = info.get("max_context")
    if max_ctx is None:
        return {"fit": True, "reason": "platform has no fixed context limit"}
    # Rough heuristic: prompt is ~10 tokens per word
    word_count = len(goal.split())
    prompt_tokens_est = word_count * 10
    # If project_root, add directory listing size
    project_context_est = 0
    if project_root:
        try:
            out = subprocess.run(
                ["find", project_root, "-type", "f", "-name", "*.py", "-size", "-100k"],
                capture_output=True, timeout=5,
            )
            project_context_est = len(out.stdout.decode(errors="replace").splitlines()) * 200  # ~200 tokens per file
        except Exception:
            pass
    total_est = prompt_tokens_est + project_context_est
    fit = total_est <= max_ctx
    return {
        "fit": fit,
        "estimated_total_tokens": total_est,
        "prompt_tokens_est": prompt_tokens_est,
        "project_context_tokens_est": project_context_est,
        "platform_max_context": max_ctx,
        "warnings": [] if fit else [
            f"Estimated {total_est} tokens exceeds {platform}'s ~{max_ctx // 1000}k limit by "
            f"about {(total_est - max_ctx) // 1000}k tokens. Consider breaking the task into "
            f"smaller sub-goals or choosing a platform with a larger context window (Gemini CLI "
            f"supports up to ~1M tokens)."
        ],
    }
