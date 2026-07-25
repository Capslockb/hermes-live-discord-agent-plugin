# Notification system — proactive breakout from reply-only

The notification subsystem lets the voice agent attempt delivery outside the current Gemini reply path. Delivery is best-effort: a queued voice send or webhook enqueue is not proof that a person received the message.

## Delivery modes

`notification.py:deliver(text, mode, ...)` accepts six modes:

| Mode | Current behavior | Important boundary |
|---|---|---|
| `voice` | Queues `bridge._gemini.send_text()` when the Gemini bridge reports `_running` | `queued: true` confirms scheduling on the event loop, not spoken playback or user presence |
| `dm` | Sends a Discord DM through the bot adapter when the target user is already available from the Discord client cache | A cache miss returns an error; the implementation does not fetch an uncached user |
| `channel` | Posts to a configured Discord text channel | Requires a live adapter and a resolvable channel ID |
| `webhook` | Enqueues an `agent.notify` event through `WebhookDispatcher` | The returned URL count is an enqueue/target count, not confirmed remote delivery; see [Issue #11](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/11) |
| `auto` | Tries voice, then DM, then channel, then webhook | A successful voice queue also attempts a webhook side effect; that webhook result is not included in the returned voice result |
| `all` | Calls voice, DM, channel, and webhook sequentially and returns every result | The channels are not fired concurrently despite the mode name |

## Gemini tools

### `local_notify`

Example input:

```json
{
  "text": "The delegated task finished",
  "mode": "auto",
  "title": "Task complete",
  "source": "health_watcher"
}
```

The tool returns the direct `deliver()` result under `result`. Typical successful shapes include:

```json
{
  "result": {
    "status": "ok",
    "channel": "voice",
    "queued": true
  }
}
```

For `all`, the response contains a `channels` object with one result per attempted path. Do not interpret `status: "ok"` from voice or webhook as an end-user delivery receipt.

`local_notify` currently falls back to a repository-embedded Discord user ID when neither a live target nor `DISCORD_VOICE_LIVE_USER_ID` is available. Treat DM routing as trusted single-user behavior until the explicit-recipient work in [Issue #13](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/13) is completed.

### `local_notify_schedule`

Example input:

```json
{
  "text": "Reminder: standup in 10 minutes",
  "delay_seconds": 600,
  "mode": "auto"
}
```

The tool accepts either `delay_seconds` or `fire_at_epoch`. A successful schedule call returns an ID, absolute fire time, and remaining seconds. The queue file is:

```text
~/.hermes/voice-scheduled-notifications.jsonl
```

The scheduler polls every two seconds.

Other actions on the same tool:

- `{"list": true}` lists persisted entries.
- `{"cancel_id": "n-..."}` removes one entry.

### Current scheduling blocker

Scheduled notifications are not currently reliable enough to describe as restart-safe:

- the tool passes live bridge and Discord adapter objects into the JSON persistence layer, which can make scheduling fail because those objects are not serializable;
- persisted entries cannot restore those runtime objects after restart;
- every due entry is removed after one attempt, including exceptions and unsuccessful delivery results;
- recipient fallback is not explicit.

See [Issue #13](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/13). Until it is fixed, verify that the schedule entry was actually created and do not rely on this path for urgent, safety-critical, or one-shot reminders.

## Sidecar HTTP endpoint

`/notify` is a mutating route on the loopback control API. Current `main` requires the per-process `X-API-Secret` header.

Do not use the old unauthenticated `curl` example: it returns `401 Unauthorized`. The internal `notification.sidecar_notify()` helper also omits the required header, so its fallback path is currently blocked by [Issue #14](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/14).

Keep port `18943` on loopback. Do not place the secret in a URL, query string, checked-in file, command history, log, returned error, or persisted notification entry. External callers need a reviewed trusted-local credential handoff; route protection must not be weakened to make the example work.

## AFK and background delivery

Long-running delegation, email-brief, and scheduler paths can call the same dispatcher. Their success semantics inherit the boundaries above:

- voice can be queued without proof of playback;
- DM and channel require a live Discord adapter and resolvable target;
- webhook success means enqueue acceptance, not confirmed HTTP delivery;
- scheduled entries currently have the lifecycle defects tracked in Issue #13;
- email-brief backend and de-duplication behavior is tracked separately in [Issue #12](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/12).

## Webhook fanout

The notification dispatcher uses the `agent.notify` event class. Configure targets with:

```text
DISCORD_VOICE_LIVE_WEBHOOK_AGENT_NOTIFY=https://discord.com/api/webhooks/...
```

Webhook messages can contain notification text and source metadata. Treat the destination as a data recipient, verify its retention policy, and do not send transcript, email, credential, or private task content to an untrusted URL.

## Notification sound effect

The `local_notify` tool attempts to play the `notification` sound-effect slot after dispatch, regardless of whether the selected remote path proved end-user delivery. Disable sound effects with:

```text
DISCORD_VOICE_LIVE_SFX_ENABLED=false
```

See [`sfx-library.md`](sfx-library.md).

## When not to use

- Do not use scheduled notifications for urgent or one-shot reminders until Issue #13 is resolved and exact-head tests prove retry-safe delivery.
- Do not treat voice queueing or webhook enqueue counts as delivery receipts.
- Do not expose `/notify` without its control secret or publish the sidecar beyond loopback.
- For task results already returned to Gemini, prefer the tool result unless an additional trusted delivery channel is intentionally required.
