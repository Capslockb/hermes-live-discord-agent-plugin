# Fallback chain — multi-CLI delegation with health registry

The voice agent can delegate work to opencode, codex, gemini, numasec, or the Hermes API. `execute_with_fallback()` can reroute a request when the requested platform is already marked unhealthy, fails before launch, or produces an early log signature that is classified as a platform failure.

Execution-time fallback is not a general availability check. It reads the persisted health registry and early process output; it does not consult the in-memory hourly rate-limit counters before starting the requested platform.

## The pool

Defined in `delegation_agent.py:PLATFORMS`:

| Platform | Current executable or endpoint | Intended use | Declared limit |
|---|---|---|---|
| opencode | `/home/caps/.local/bin/opencode` | code, refactoring, tests, debugging | 100 requests/hour |
| codex | `/home/caps/.npm-global/bin/codex` | reasoning and multi-file refactors | 50 requests/hour |
| gemini | `/home/caps/.npm-global/bin/gemini` | large context, vision, and audio | 1,000,000 tokens/hour |
| numasec | `/home/caps/.npm-global/bin/numasec` | security analysis and review | 60 requests/hour |
| hermes-api | `API_SERVER_HOST` and `API_SERVER_PORT`, defaulting to `127.0.0.1:8088` | general Hermes tasks | 200 dispatches/hour |

The four CLI paths are currently hard-coded for one home directory. They are not portable `~`-based defaults and do not have environment-variable overrides on the current head.

## The chain

Defined in `delegation_agent.py:_FALLBACK_CHAIN`:

```python
_FALLBACK_CHAIN = {
    "codex":      ["opencode", "hermes-api", "gemini"],
    "opencode":   ["codex", "hermes-api", "gemini"],
    "numasec":    ["opencode", "codex", "hermes-api"],
    "gemini":     ["opencode", "codex", "hermes-api"],
    "hermes-api": ["opencode", "codex", "gemini"],
}
```

`choose_fallback()` returns the first neighbor that is absent from the persisted unhealthy-platform snapshot. It does not check whether the neighbor binary exists or whether its hourly counter is currently exhausted.

## Health registry

Broken-platform state is stored in `~/.hermes/voice-platform-health.json`. The default TTL is 600 seconds. Persisted entries use wall-clock timestamps:

```json
{
  "codex": {
    "reason": "rate limit response in early log output",
    "marked_at": 1749312456.7,
    "expires_at": 1749313056.7,
    "ttl_seconds": 600
  }
}
```

`mark_platform_broken(platform, reason, ttl_seconds=600)` writes an entry. `clear_platform_health(platform=None)` clears one platform or the entire registry. `get_health_snapshot()` prunes expired entries and returns a reduced view:

```json
{
  "codex": {
    "reason": "rate limit response in early log output",
    "expires_in_seconds": 432
  }
}
```

## `execute_with_fallback(prompt, platform, session_id, ...)`

The execution wrapper follows this sequence:

1. **Persisted-health pre-check** — if the requested platform is marked unhealthy, recursively try the first healthy neighbor.
2. **Launch** — call `execute_delegation()` for the selected CLI or Hermes API endpoint.
3. **Immediate error handling** — if launch returns an `error`, mark that platform unhealthy and try a neighbor.
4. **Early-log inspection** — when a `log_path` is returned, sleep for `health_check_delay` (default 5 seconds), then inspect the first 4096 bytes.
5. **Detected failure** — mark the platform unhealthy and recursively start the first healthy neighbor. The result includes `requested_platform`, `active_platform`, and fallback metadata.
6. **No detected failure** — return the original launch result. This does not prove the delegated task later completed successfully.

Early-log detection currently covers HTTP 401/403/429/5xx, rate-limit wording, missing commands or files, permission errors, authentication or credential failures, connection refusal, Ollama/OpenRouter availability errors, free-tier or quota exhaustion, and Python tracebacks. Generic timeout text is not one of the configured patterns.

## `local_delegate_health`

The tool exposes health-registry inspection and clearing. List output reflects `get_health_snapshot()`, so active entries contain `reason` and `expires_in_seconds`; it does not return the persisted `marked_at`, `expires_at`, or `ttl_seconds` fields.

## Rate-limit boundary

`get_all_rate_limits()` and platform suggestion use separate in-memory counters. Those counters reset when the process restarts. `execute_with_fallback()` does not call `_check_rate_limit()` before launch, so a platform can still be selected at execution time unless it has also been marked unhealthy or emits a recognized early failure.

## Validation safety

Do not use `execute_with_fallback()` as a harmless documentation smoke test. It can create tmux sessions, run local CLIs, write logs under `/tmp`, or call the Hermes API endpoint.

Implementation tests should isolate the health file and mock `execute_delegation()`, `detect_broken_log()`, sleeping, tmux/subprocess calls, and HTTP dispatch. Tests should also cover pre-marked platforms, immediate launch errors, early-log failures, no healthy neighbor, metadata preservation, and the separate rate-limit boundary.