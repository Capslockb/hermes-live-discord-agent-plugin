# Live Agent Activity Feed — Session JSON Watcher

**Session:** 27 May 2026
**Purpose:** Stream agent tool calls to a Discord channel for real-time observability

## Problem

Monitoring what an agent is doing during a long session requires visibility into tool calls. Session JSON files (`~/.hermes/sessions/session_*.json`) contain structured `tool_calls` arrays on `role: assistant` messages, followed by `role: tool` responses.

This pattern builds a background watcher that parses the active session file and posts batches to a Discord webhook.

## Script: `live_transcription.py`

Core logic:
- `guess_session_path()` — newest `session_*.json` by mtime, excluding `sessions.json` index and cron files
- `extract_tool_calls(path, since)` — parse `assistant` → `tool` pairs, extract `function.name` + `arguments`
- `send_tool_call_batch(calls)` — format as Discord markdown and post via webhook
- `watch_loop()` — poll every N seconds, track `~/.hermes/.live_transcription_last_tool` timestamp

## Key pitfall: `sessions.json` glob collision

`SESSIONS_DIR.glob("session_*.json")` matches both `session_2026*.json` files AND `sessions.json` (the index). The index file is ~101KB and has zero `messages`, causing empty results. **Always exclude `sessions.json` explicitly:**

```python
candidates = [p for p in SESSIONS_DIR.glob("session_*.json")
              if p.name != "sessions.json" and "cron" not in p.name]
```

## Key pitfall: unknown timestamps

Session JSON `timestamp` fields may use different formats. A robust parser handles both ISO 8601 and epoch fallbacks:

```python
try:
    msg_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
except Exception:
    msg_time = time.time()  # fallback for safety
```

## Key pitfall: webhook response swallowed silently

Both `requests.post` and curl will fail silently if you don't check HTTP status:

```python
r = requests.post(WEBHOOK_URL, json=payload, timeout=5)
if r.status_code not in (200, 204):
    print(f"Webhook failed: HTTP {r.status_code} — {r.text[:200]}", file=sys.stderr)
```

## Running as a systemd user service (optional)

```ini
# ~/.config/systemd/user/live-transcription.service
[Unit]
Description=Hermes Live Transcription Feed
After=hermes-gateway.service

[Service]
Type=simple
ExecStart=%h/.hermes/hermes-agent/venv/bin/python %h/.hermes/scripts/live_transcription.py --watch --interval 5
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

## Reuse

Replace `WEBHOOK_URL` and point `SESSIONS_DIR` at any Hermes sessions directory. The watcher is Hermes-agnostic — it consumes standard `messages` arrays with `tool_calls`.
