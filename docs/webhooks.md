# Webhooks — event-class fanout

The plugin can enqueue bridge events for delivery to one or more Discord webhooks. The implementation lives in `webhook_dispatcher.py` and performs HTTP delivery asynchronously on a background thread.

Webhook delivery is best-effort. A successful `emit*()` return value means that one or more target URLs were selected and the event was queued; it does **not** confirm that Discord accepted the request.

## Event classes

Each event class has its own environment variable containing a comma-separated list of Discord webhook URLs:

| Class | Environment variable | Current emitters |
|---|---|---|
| `voice.transcript` | `DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT` | `voice_input` and `voice_output` transcript events |
| `opencode.status` | `DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_STATUS` | OpenCode lifecycle and progress events |
| `opencode.transcript` | `DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_TRANSCRIPT` | Throttled OpenCode log-tail events |
| `email.sent` | `DISCORD_VOICE_LIVE_WEBHOOK_EMAIL` | Recipient and subject metadata after a voice email send |
| `bridge.status` | `DISCORD_VOICE_LIVE_WEBHOOK_BRIDGE_STATUS` | Bridge start and stop events |
| `bridge.video` | `DISCORD_VOICE_LIVE_WEBHOOK_VIDEO` | `video_initialized` events after an accepted frame and the configured quiet threshold |
| `tool.called` | `DISCORD_VOICE_LIVE_WEBHOOK_TOOL_CALLED` | Sampled or throttled tool invocation summaries |
| `agent.notify` | `DISCORD_VOICE_LIVE_WEBHOOK_AGENT_NOTIFY` | Immediate and scheduled agent notifications |
| `platform.fallback` | `DISCORD_VOICE_LIVE_WEBHOOK_PLATFORM_FALLBACK` | Delegation platforms marked unhealthy |

An empty or missing variable disables fanout for that class. `email.received` and a separate received-email webhook class are not implemented in the current dispatcher.

Current frame clients are blocked by [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9), so configuring `bridge.video` does not make frame delivery operational.

The dispatcher also has unresolved queue and throttle-state defects: explicit tuple throttle keys lose their second component, selected target URLs are not preserved in the queued envelope, and a queue-full drop can still consume the throttle window. Until [Issue #11](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/11) is fixed, the target count returned at enqueue time and the URLs later selected by the worker can diverge.

## Payload shape

The dispatcher sends a Discord embed and disables automatic mention parsing:

```json
{
  "embeds": [
    {
      "title": "Bridge Status: Bridge Stopped",
      "description": "Reason: user requested /voice-live-leave",
      "color": 7634829,
      "fields": [
        {"name": "user", "value": "1474100257762578597", "inline": true},
        {"name": "uptime_s", "value": "342.5", "inline": true}
      ],
      "timestamp": "2026-06-07T12:34:56Z"
    }
  ],
  "allowed_mentions": {"parse": []}
}
```

Titles are generated from the event-class label and sub-event name. Descriptions are truncated to 1,900 characters; field names and values are truncated to Discord's per-field limits. The current payload does not add a custom webhook username or embed footer.

## Throttling

`DISCORD_VOICE_LIVE_WEBHOOK_THROTTLE_SECONDS` controls the shared throttle window and defaults to **2 seconds**.

`WebhookDispatcher.emit()` accepts:

- `throttle`: enable or disable throttling for the event;
- `throttle_key`: an optional tuple whose first value selects the throttle bucket.

There is no per-call `throttle_seconds` argument. The following sub-events have built-in throttle buckets: `voice_input`, `voice_output`, `opencode_progress`, and `tool_called`. Some wrappers, including video, fallback, and agent notification emitters, supply an explicit bucket.

The video quiet threshold (`DISCORD_VOICE_LIVE_VIDEO_INITIALIZED_QUIET_THRESHOLD_S`) decides when a video-initialized event is created; it is separate from the webhook throttle window.

## Emit helpers

Current convenience helpers include:

```python
from webhook_dispatcher import (
    emit_voice_input,
    emit_voice_output,
    emit_opencode_status,
    emit_opencode_transcript,
    emit_email_sent,
    emit_bridge_status,
    emit_video_initialized,
    emit_tool_called,
    emit_fallback_event,
    emit_agent_notify,
)
```

The helpers return the number of target URLs selected at enqueue time, or `0` when no subscriber was selected, the event was throttled, or the queue was full. Network failures occur later on the background thread and increment dispatcher failure counters; callers do not receive a delivery receipt.

## Privacy and secret handling

Discord webhook URLs are credentials. Anyone who obtains one can normally post to its channel until the webhook is rotated or deleted.

Transcript events, tool summaries, email recipient/subject metadata, session names, source labels, and fallback reasons may contain sensitive information. Configure only the event classes needed, keep webhook URLs out of logs and screenshots, and route private event classes only to channels with an appropriate retention and access policy.

## Notes file

Independent of webhooks, the plugin writes call notes to `~/.hermes/voice-live-notes/` by default, configurable through `DISCORD_VOICE_LIVE_NOTES_DIR`. The sidecar `/notes` endpoint can read recent transcript events from that directory. Treat both the files and the local control API as transcript-bearing surfaces.
