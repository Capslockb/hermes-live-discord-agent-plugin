# Notification system — proactive breakout from reply-only

Before criterion #6 the agent could only respond when B spoke. It had no tool to break out and notify on its own. Now it does.

## Three channels

`notification.py:deliver(text, mode, ...)` dispatches via one of five modes:

| Mode | What happens | When to use |
|---|---|---|
| `voice` | Push `text` into Gemini via `bridge._gemini.send_text()` so the model speaks it in the next turn | Voice session is active; B is in the channel |
| `dm` | Send a Discord DM directly to the user via the bot adapter | B is offline / AFK |
| `channel` | Post to a text channel in the same guild | A "voice log" or notes channel exists |
| `webhook` | Emit a `agent.notify` event class webhook | Configure a webhook URL in the channel where you want the brief to land |
| `auto` | Try voice → dm → channel → webhook in order, return first success | Default. The right choice when you don't know B's state. |
| `all` | Fire all four at once | For the "definitely get this to B" case |

## Gemini tools

### `local_notify`

```json
{
  "text": "Hey, codex is back online",
  "mode": "auto",
  "title": "Tool online",
  "source": "health_watcher"
}
```

Returns:

```json
{
  "status": "ok",
  "channel": "voice",
  "results": {"voice": {"status": "ok", "queued": true}, ...},
  "notified_at": 1749312456.7
}
```

### `local_notify_schedule`

```json
{
  "text": "Reminder: standup in 10",
  "delay_seconds": 600,
  "mode": "auto"
}
```

Returns `{scheduled: <id>, fire_at_epoch: <ts>}`. The schedule persists to `~/.hermes/voice-users/notifications-schedule.jsonl` and survives bridge restarts. Polled every 2s by a background thread.

**Other actions** on the same tool:
- `{"list": true}` → list all scheduled
- `{"cancel_id": "abc123"}` → remove one

## Sidecar HTTP endpoint

For callers that aren't in a Gemini session (e.g. cron jobs, subagents):

```bash
curl -X POST -H "Content-Type: application/json" \
     -d '{"text":"inbox is huge","mode":"dm","title":"Brief"}' \
     "http://127.0.0.1:18943/notify"
```

Also accepts GET with `?text=...&mode=...&title=...` for trivial cases. The handler reads `BRIDGE` from the global to find the live `GeminiLiveBridge` for the `voice` mode; falls through to other modes if voice is unavailable.

## AFK ping from opencode watcher

The opencode watcher (which polls long-running `local_delegate_execute` tmux sessions) was extended to call `notification.deliver(mode="auto")` after a session finishes. So when B is AFK and a 5-minute refactor finishes, the bot DMs B "refactor done" automatically.

## Webhook fanout

The notification dispatcher also fires the `agent.notify` event class via `WebhookDispatcher`, which is configurable through env vars:

```
DISCORD_VOICE_LIVE_WEBHOOK_AGENT_NOTIFY=https://discord.com/api/webhooks/...
```

If unset, the dispatcher still tries DM and channel paths. Setting the webhook URL just gives you a third destination.

## Notification sfx

When a notification is delivered, the bridge plays the `notification` sfx slot (see `sfx-library.md`). To disable: `DISCORD_VOICE_LIVE_SFX_ENABLED=false`.

## When NOT to use

- For **task results** (delegate finished, web search returned), prefer the tool's own return value to the model — the model is already in the loop and will narrate.
- For **urgent alerts** that must interrupt B (call coming in, server down), use the webhook directly with `urgent: true` to bypass throttling.
- For **scheduled reminders** the user asked for, use `local_notify_schedule` — it has a clear audit trail.
