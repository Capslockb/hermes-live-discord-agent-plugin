# Fallback chain — multi-CLI delegation with health registry

The voice agent delegates coding tasks to a pool of CLIs (opencode, codex, gemini, numasec, hermes-api). When one is broken, the agent shouldn't be stuck — `execute_with_fallback` automatically reroutes to a healthy neighbor.

## The pool

Defined in `delegation_agent.py:PLATFORMS`:

| Platform | Binary | Best for | Tokens | Rate limit |
|---|---|---|---|---|
| opencode | `~/.local/bin/opencode` | code/refactor/test/debug | 126k | 100/h |
| codex | `~/.npm-global/bin/codex` | reasoning/multi-file refactors | 195k | 50/h |
| gemini | `~/.npm-global/bin/gemini` | huge context/vision/audio | 900k | 1M tok |
| numasec | `~/.npm-global/bin/numasec` | security/review | 120k | 60/h |
| hermes-api | HTTP 127.0.0.1:8088 | general | — | 200/h |

## The chain

Defined in `delegation_agent.py:FALLBACK_CHAIN`:

```python
FALLBACK_CHAIN = {
    "codex":    ["opencode", "hermes-api", "gemini"],
    "opencode": ["codex", "hermes-api", "gemini"],
    "numasec":  ["opencode", "codex", "hermes-api"],
    "gemini":   ["opencode", "codex", "hermes-api"],
    "hermes-api": ["opencode", "codex", "gemini"],
}
```

The chain is **bidirectional** between opencode/codex (they substitute for each other most often) and falls through to gemini last (because it has the most tokens but is also the most likely to be rate-limited).

## Health registry

Broken platforms are persisted to `~/.hermes/voice-platform-health.json` with a TTL (default 600s = 10 min). After TTL expires, the platform is automatically considered healthy again — the assumption being that transient failures (rate limits, OAuth expiry) clear themselves.

```json
{
  "codex": {
    "marked_broken_at": 1749312456.7,
    "reason": "rate_limit: 429 from openai.com",
    "ttl_seconds": 600
  }
}
```

`is_platform_healthy(platform)` reads this file. `mark_platform_broken(platform, reason, ttl)` writes it. `clear_platform_health(platform=None)` wipes one or all entries.

## `execute_with_fallback(prompt, platform, ...)`

The wrapper that every `local_delegate_execute` call goes through:

1. **Pre-check** — if the platform is already in the broken registry, recurse to the first healthy neighbor with `fallback_from=<original>` and `requested_platform=<original>`.
2. **Spawn** — call the platform's `_run_<name>` executor (each is fire-and-forget, returns the tmux window name + log file).
3. **Poll the log** for ~5 seconds for break-signals:
   - HTTP 401 / 403 / 429 / 5xx
   - "rate limit", "rate-limit", "quota"
   - "command not found"
   - "auth", "auth fail", "unauthorized"
   - "connection refused", "timeout", "ollama", "Ollama"
   - Python "Traceback"
4. **If a break-signal is detected**: mark the platform broken, recurse to the first healthy neighbor, return a merged result with `fallback_from` and `fallback_reason` populated.
5. **Otherwise**: return the original result with `fallback_from=null` and `active_platform == requested_platform`.

## `local_delegate_health` tool

Lets the agent (or `__init__.py` handlers) inspect and reset the registry without touching the file directly.

```json
// local_delegate_health(action="list")
{
  "result": {
    "health": {
      "codex": {"marked_broken_at": "...", "reason": "...", "ttl_seconds": 600, "seconds_remaining": 432}
    }
  }
}

// local_delegate_health(action="clear", platform="codex")
{"result": {"cleared": ["codex"]}}
```

## Why defense in depth

`execute_with_fallback` is the safe wrapper, but `suggest_platform` is also called from elsewhere (e.g. for ETA estimation, voice narration). It also filters broken platforms out of `available_platforms` before scoring. Two layers, no single point of failure.

## Smoke test

The fallback chain has a real-execution smoke test (see `delegation_agent.py` docstring):

```bash
~/.hermes/hermes-agent/venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from delegation_agent import mark_platform_broken, suggest_platform, execute_with_fallback, get_health_snapshot
mark_platform_broken('codex', 'rate_limit', ttl=60)
print(suggest_platform('complex refactor'))  # should NOT return 'codex'
print(execute_with_fallback('test', 'codex'))  # should route to opencode
"
```

The smoke test was the test that caught the `fallback_from` metadata bug in pre-emptive branching (see git log around 1bd8906).
