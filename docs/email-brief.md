# Email brief — proactive inbox digest

The voice agent can build a spoken summary of recent inbox mail and fire it through the notification dispatcher. AFK users get a DM; voice users hear it spoken.

## `local_email_brief` tool

```json
{
  "limit": 8,
  "force": false,
  "notify": true,
  "backend": "google"
}
```

- `limit` — max emails to consider. Default 8.
- `force` — skip the de-dup check and always brief. Default false.
- `notify` — when true (default), the brief is fired through `notification.deliver(mode="auto")` in addition to being returned to the model. Set false for a pure read.
- `backend` — `"google"` (default, uses `google_api.py`) or `"himalaya"`. Auto-falls back to himalaya if google_api.py fails.

Returns a payload with three buckets + the per-email list:

```json
{
  "result": {
    "status": "ok",
    "backend": "google",
    "count": 5,
    "brief": "**1 important.**\n• Sarah Chen — URGENT: invoice overdue\n\n**1 FYI.**\n• Alex Kim — Quick question about the deploy\n\n**3 auto** (Promotions, Social, Updates).",
    "buckets": {
      "important": [{"id": "1", "from": "Sarah Chen ...", "subject": "URGENT: ...", "score": 82, ...}],
      "fyi":       [...],
      "auto":      [...]
    },
    "notified": true,
    "delivery": {"status": "ok", "channel": "dm", ...}
  }
}
```

## Importance scoring (0-100)

The scoring formula (see `email_brief.py:_score_email`):

| Signal | Score |
|---|---|
| Recency < 1h | +35 |
| Recency < 6h | +25 |
| Recency < 24h | +15 |
| Recency < 72h | +8 |
| Recency > 72h | +2 |
| Gmail label `IMPORTANT` | +25 |
| Gmail label `STARRED` | +15 |
| Gmail label `CATEGORY_PRIMARY` or `INBOX` | +10 |
| Subject matches urgent/asap/critical/emergency/deadline/overdue/invoice/contract/legal/signature/fwd | +12 |
| Sender contains `noreply` / `no-reply` / `notifications@` | -30 |
| Gmail label `CATEGORY_PROMOTIONS` / `_SOCIAL` / `_UPDATES` / `_FORUMS` / `SPAM` / `TRASH` | -50 |
| Already read | -10 |

Final score is clamped to 0-100. Buckets:
- **Important** (≥55)
- **FYI** (20-54)
- **Auto** (<20 or auto-category)

## Backend fallback

`fetch(limit, prefer)` tries the preferred backend first, then falls back. The google backend uses `google_api.py` (the official Gmail client). The himalaya backend uses the `himalaya` CLI for envelope-only listing (no snippet, no labels).

If both fail, the function returns `{status: "ok", backend: "none", count: 0}` — never crashes. This is the right shape for the agent to handle ("Inbox is empty right now").

## De-dup

State persists at `~/.hermes/voice-users/email-brief-state.json`:

```json
{
  "last_briefed_ids": ["1", "2", "3"],
  "last_brief_at": 1749312456.7
}
```

The scheduler (and on-demand calls with `force=false`) only fires a brief when at least one email ID is not in `last_briefed_ids`. After a successful brief, the IDs are appended (capped at 500).

The de-dup set is **separate from** the per-email reminder loop's seen set (which lives in the bridge's email reminder module). The two can both fire in the same interval — the brief summarises, the per-email pings the highest-importance item — without conflict.

## Background scheduler

`email_brief.py:start_brief_scheduler(get_bridge_fn, interval)` starts a daemon thread. Default interval is 30 min (env: `DISCORD_VOICE_LIVE_EMAIL_BRIEF_INTERVAL_SECONDS`). It is started in `bridge.py:run_sidecar()` next to the notification scheduler.

The scheduler picks up the live bridge via the `get_bridge_fn` callable (which returns `BRIDGE`), so the brief uses the same voice path the user is currently in.

## When to use

| Use case | Tool |
|---|---|
| "What just came in?" on demand | `local_email_brief` with `force=true` |
| Scheduled morning brief | scheduler (default 30 min) — no agent call needed |
| Read a specific email | `local_email_read` (existing tool, not part of brief) |
| Reply to an email | `local_email_reply` (existing tool) |

## Disable

`DISCORD_VOICE_LIVE_EMAIL_BRIEF_ENABLED=false` to disable the scheduler. The on-demand tool still works.
