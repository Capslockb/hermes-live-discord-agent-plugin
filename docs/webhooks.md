# Webhooks — event-class fanout

The plugin can fan out events to Discord webhooks for any number of independent channels. The system is built on `webhook_dispatcher.py`.

## Event classes

Each event class has its own env var (a comma-separated list of Discord webhook URLs):

| Class | Env var | Fires on |
|---|---|---|
| `voice.transcript` | `DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT` | Every voice input/output line |
| `bridge.status` | `DISCORD_VOICE_LIVE_WEBHOOK_BRIDGE_STATUS` | Bridge start/stop |
| `bridge.video` | `DISCORD_VOICE_LIVE_WEBHOOK_VIDEO` | First video frame after ≥30s silence |
| `opencode.status` | `DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_STATUS` | Opencode lifecycle |
| `email.sent` | `DISCORD_VOICE_LIVE_WEBHOOK_EMAIL` | Email sent via voice |
| `email.received` | (same env) | Important email received |
| `tool.called` | `DISCORD_VOICE_LIVE_WEBHOOK_TOOL_CALLED` | Any tool invocation (sampled, throttled) |
| `delegation.fallback` | `DISCORD_VOICE_LIVE_WEBHOOK_PLATFORM_FALLBACK` | Platform fallback triggered |
| `agent.notify` | `DISCORD_VOICE_LIVE_WEBHOOK_AGENT_NOTIFY` | Agent-initiated notification fired |

Setting a webhook URL = opt in. Empty env = no fanout for that class.

## Embed shape

Each webhook fires a Discord embed with a consistent shape:

```json
{
  "username": "S0RA Bridge",
  "embeds": [{
    "title": "Bridge stopped",
    "description": "Reason: user requested /voice-live-leave",
    "color": 0x747F8D,
    "fields": [
      {"name": "user", "value": "1474100257762578597", "inline": true},
      {"name": "uptime_s", "value": "342.5", "inline": true}
    ],
    "footer": {"text": "sub_event: bridge_stopped | event_class: bridge.status"},
    "timestamp": "2026-06-07T12:34:56Z"
  }]
}
```

Sub-events get specific embed colors (`_SUB_COLORS` in `webhook_dispatcher.py`).

## Throttling

`WebhookDispatcher.emit()` accepts a `throttle_key` parameter. When set, the event is dropped if the same key fired within `throttle_seconds` (default = `DISCORD_VOICE_LIVE_WEBHOOK_THROTTLE_SECONDS`, default 60s).

Use case: `bridge.video` should not fire 30 times in a row if 30 frames arrive in quick succession. Set `throttle_key="bridge.video"` and `throttle_seconds=30`.

## Emit helpers

`webhook_dispatcher.py` provides typed emit helpers for each event class:

```python
from webhook_dispatcher import (
    emit_bridge_started, emit_bridge_stopped,
    emit_video_initialized,
    emit_opencode_status,
    emit_email_sent, emit_email_received,
    emit_tool_called,
    emit_fallback_event,
    emit_agent_notify,
)
```

All return the number of webhooks the event was delivered to (0 if no subscribers).

## Notes file

Independent of webhooks, the plugin writes call notes to `~/.hermes/voice-live-notes/` (configurable via `DISCORD_VOICE_LIVE_NOTES_DIR`). Each note is a JSONL line with timestamp, speaker, text, and metadata. The `/notes` sidecar endpoint reads them back for replay or summarization.
