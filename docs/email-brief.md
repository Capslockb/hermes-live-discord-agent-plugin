# Email brief — proactive inbox digest

The plugin can build a spoken summary of recent inbox mail and pass it to the notification dispatcher. This path is **best-effort and currently has unresolved delivery-state, recipient-routing, and privacy defects** tracked in [Issue #12](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/12).

Do not treat a returned `notified: true` value as proof that a voice message, DM, channel post, or webhook was delivered.

## `local_email_brief` tool

```json
{
  "limit": 8,
  "force": false,
  "notify": true,
  "backend": "google"
}
```

- `limit` — maximum emails to consider. Default: 8.
- `force` — bypass the saved email-ID de-duplication check. Default: false.
- `notify` — when true, the implementation attempts `notification.deliver(mode="auto")` in addition to returning the brief to the model. Set false to build and return the brief without that notification attempt.
- `backend` — preferred backend: `"google"` or `"himalaya"`. The implementation tries the other backend if the preferred one raises an error.

## Returned data and privacy boundary

A successful build returns the rendered brief, counts, backend name, bucket data, and a per-email list. The current `buckets` entries retain the Google backend's email dictionaries, including `snippet` text.

That means tool results can place inbox content in model-visible conversation history even though the spoken brief itself is rendered from sender and subject. Use this feature only where the model/session retention boundary is acceptable. Issue #12 tracks returning a minimized payload by default.

A representative shape is:

```json
{
  "result": {
    "status": "ok",
    "backend": "google",
    "count": 5,
    "brief": "**1 important.**\n• Sarah Chen — URGENT: invoice overdue\n\n**1 FYI.**\n• Alex Kim — Quick question about the deploy\n\n**3 auto** (Promotions, Social, Updates).",
    "buckets": {
      "important": [{"id": "1", "from": "Sarah Chen ...", "subject": "URGENT: ...", "snippet": "...", "_score": 82}],
      "fyi": [],
      "auto": []
    },
    "emails": [{"id": "1", "from": "Sarah Chen ...", "subject": "URGENT: ...", "score": 82}],
    "notified": true,
    "delivery": {"status": "ok", "channel": "dm"}
  }
}
```

The exact delivery object depends on the selected notification path. Inspect `delivery.status` and `delivery.channel`; do not infer success from the top-level `notified` field.

## Importance scoring (0–100)

The scoring formula is implemented in `email_brief.py:_score_email`:

| Signal | Score |
|---|---:|
| Recency < 1h | +35 |
| Recency < 6h | +25 |
| Recency < 24h | +15 |
| Recency < 72h | +8 |
| Recency ≥ 72h | +2 |
| Unparseable date | +5 |
| Gmail label `IMPORTANT` | +25 |
| Gmail label `STARRED` | +15 |
| Gmail label `CATEGORY_PRIMARY` or `INBOX` | +10 |
| First subject match for urgent/asap/critical/emergency/deadline/overdue/action-required/eod/eow/invoice/payment/billing/charged/contract/legal/signature/fwd | +12 |
| Sender contains `noreply`, `no-reply`, or `notifications@` | -30 |
| Gmail label `CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, `CATEGORY_UPDATES`, `CATEGORY_FORUMS`, `SPAM`, or `TRASH` | -50 |
| Already read | -10 |

The final score is clamped to 0–100. Buckets are:

- **Important:** score ≥55, unless an auto-category label is present.
- **FYI:** score 20–54.
- **Auto:** score <20 or an auto-category label is present.

## Backend fallback status

`fetch(limit, prefer)` tries the preferred backend and then the other backend:

- Google uses `~/.hermes/hermes-agent/skills/productivity/google-workspace/scripts/google_api.py` and can include Gmail labels and snippets.
- Himalaya uses the `himalaya` CLI and returns envelope data without snippets or Gmail labels.

If both backends fail, `fetch()` currently returns an empty list with backend `"none"`. `build_brief()` then reports `status: "ok"`, `count: 0`, and the same text used for a genuinely empty inbox.

**Current limitation:** an empty inbox cannot be distinguished from missing credentials, a missing executable, a timeout, malformed backend output, or total backend failure. Treat `backend: "none"` as an unavailable/error state rather than evidence that the inbox is empty. See Issue #12.

## De-duplication and delivery status

State persists at `~/.hermes/voice-users/email-brief-state.json`:

```json
{
  "last_briefed_ids": ["1", "2", "3"],
  "last_brief_at": 1749312456.7
}
```

With `force=false`, a notification attempt is skipped when every current email ID already appears in `last_briefed_ids`.

The current implementation calls `mark_briefed()` after the delivery attempt regardless of whether `notification.deliver()` returned an error, found no subscribers, or raised an exception. It then returns `notified: true` unconditionally. A failed delivery can therefore suppress the same emails on later scheduler ticks.

The email-brief state is separate from the bridge's per-email reminder state, so both mechanisms can act on the same messages.

## Background scheduler

`email_brief.py:start_brief_scheduler(get_bridge_fn, interval)` starts a daemon thread. The default interval is 30 minutes and is configured with `DISCORD_VOICE_LIVE_EMAIL_BRIEF_INTERVAL_SECONDS`.

The scheduler obtains the current bridge and adapter through `get_bridge_fn`. Recipient selection currently falls back in this order:

1. the live bridge's `_target_user_id`;
2. `DISCORD_VOICE_LIVE_USER_ID`;
3. a repository-embedded Discord user ID.

The final fallback is not a safe multi-user default. Keep scheduled briefs disabled unless the intended recipient is explicitly configured and verified. Issue #12 tracks removing the embedded account fallback.

## When to use

| Use case | Current path |
|---|---|
| Build an on-demand brief without notification | `local_email_brief` with `notify=false` |
| Attempt an immediate brief notification | `local_email_brief` with `notify=true`; inspect `delivery`, not only `notified` |
| Scheduled brief | Background scheduler; use only with an explicitly verified recipient |
| Read a specific email | `local_email_read` |
| Reply to an email | `local_email_reply` |

## Disable

Set:

```bash
DISCORD_VOICE_LIVE_EMAIL_BRIEF_ENABLED=false
```

This disables the background scheduler. It does not disable the on-demand tool.