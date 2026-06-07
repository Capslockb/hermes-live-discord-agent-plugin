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
  2. Framework returns clarifying questions
  3. Gemini asks user in voice
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
    """Analyze the task and suggest the best platform + ETA."""
    rates = get_all_rate_limits()
    available = [pid for pid, r in rates.items() if r.get("available", False)]
    if not available:
        return {"error": "All platforms are rate-limited right now. Please wait.", "rates": rates}

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
