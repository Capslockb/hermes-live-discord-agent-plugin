# CHANGELOG — hermes-live-discord-agent-plugin

## 0.3.0 — 2026-06-07

### Brand + public release
- **Renamed** to **Hermes Live Discord Agent Plugin** (`hermes-live-discord-agent-plugin`). Internal plugin directory `discord-voice` kept untouched to avoid refactoring ~1.5k lines of cross-references.
- **Public release** — repo is now public.
- **Promo site** at `docs/index.html`, auto-deployed to GitHub Pages via `.github/workflows/pages.yml`. Live at `https://capslockb.github.io/hermes-live-discord-agent-plugin/`.
- Installer banner + status strings updated to the new public name; REPO_NAME constant and `.env` keys unchanged so existing installs keep working.

## 0.2.7 — 2026-06-05

### Features
- **Video awareness messaging (criterion #31)** — when the user starts screen sharing or turns on their camera, the bridge now tells Gemini explicitly that the user can share a frame via the `/frame` command (the `voice_live_frame` tool + `/frame` HTTP endpoint) or push frames automatically via `video-frame-feeder.py`. Until they do, Gemini just knows the video activity is happening, not what's on screen — and can ask the user to describe verbally. Discord bots can't receive video streams natively; this is the practical workaround.
- **New-user onboarding Q&A (criterion #32)** — when a brand-new user first runs `/voice-live`, the plugin detects `needs_onboarding()=True` and appends a one-time system reminder telling Gemini to walk the user through 6 questions (name, timezone, work, interests, communication style, pet peeves). The user answers in voice; the agent calls `local_user_onboarding_answer` for each; answers are persisted to `~/.hermes/voice-users/<id>.yaml` and mirrored to the top-level profile fields. New module state in `user_profiles.py` (`ONBOARDING_QUESTIONS`, `UserProfile.needs_onboarding()`, `mark_onboarding_complete()`) and two new tools in `bridge.py` (`local_user_onboarding_get_questions`, `local_user_onboarding_answer`). Honours criterion #28 (mirroring speech/style) by capturing `communication_style` and `pet_peeves` during onboarding.

### Tests
- New `scripts/regression_test_criteria_31_32.py` — 43 checks across 5 sets covering ONBOARDING_QUESTIONS shape, `mark_onboarding_complete` round-trip, `needs_onboarding()` state transitions, `UserProfile` field coverage, and #31 video awareness message content. All 43 pass.
- **Total regression coverage: 178/180 across 5 test files.** The 2 failures in `regression_test_voice_loop.py` are pre-existing test-script mock-fidelity issues, not code regressions.

---

## 0.2.6 — 2026-06-05

### Fixes
- **Bridge loop / "Bridge failed to start"** (regression of #14) — the Gemini Live API rejects the speculative `mediaResolution` field with `Unknown name "mediaResolution" at 'setup': Cannot find field.` (WebSocket 1007) on the current model lineup (3.1-flash-live-preview and 2.5-flash-native-audio-preview-*). The field exists in the docs for "native audio" models but is NOT accepted on these specific model names. Removing it entirely restores the v0.1.0 setup shape and the bridge connects. Frame-size cost is still controlled at the bridge level (1 fps cap + 512 KB max, audio-gated, 350ms SFX cap) — the `mediaResolution` optimization was speculative and not load-bearing.

---

## 0.2.5 — 2026-06-05

### Features
- **GitHub repo tracker tools (criterion #22)** — 6 new voice tools for the live agent to manage GitHub on the user's behalf. Wraps the existing `gh` CLI which is already authenticated as Capslockb.
  - `local_github_repo_list` — list the user's GitHub repos (read-only)
  - `local_github_issues` — list issues for a specific repo
  - `local_github_prs` — list pull requests for a specific repo
  - `local_github_issue_create` — create a new issue (write — use sparingly)
  - `local_github_note` — append a free-form note to `~/.hermes/voice-users/voice-session-notes.jsonl` so the next Hermes turn or future voice session can pick it up
  - `local_github_notes_read` — read back persisted notes (most recent first)

---

## 0.2.4 — 2026-06-05

### Features
- **Webhook dispatcher (criterion #17)** — per-event-class Discord webhook delivery. New module `webhook_dispatcher.py` with a background thread, per-class throttling, Discord embed formatting, and `allowed_mentions: {parse: []}` to disable @everyone pings. Event classes: `voice.transcript`, `opencode.status`, `opencode.transcript`, `email.sent`, `bridge.status`, `tool.called`. Each class has its own env var (`DISCORD_VOICE_LIVE_WEBHOOK_<CLASS>=url[,url2,...]`). Wire-up points emit on bridge start/stop, every transcript line, every tool call, opencode lifecycle events (start/progress/milestone/finished/stopped), and email sends.
- **Email address auto-correction + send fix (criterion #18)** — new `_autocorrect_email_address()` helper handles common STT errors when users speak addresses in voice (`"at"` → `@`, `"dot"` → `.`, case-insensitive, whitespace/double-space cleanup, lowercase). `local_email_send` now applies the correction and surfaces the change notes in the tool result so the agent can confirm with the user. Bails and returns the original if the result still doesn't look like an email, so the agent can ask for character-by-character spelling.
- **Email reminder poller (criterion #19)** — new background `asyncio` task that runs while the bridge is up. Polls the Gmail inbox via `google_api.py` every `DISCORD_VOICE_LIVE_EMAIL_REMINDER_POLL_SECONDS` (default 300s = 5 min) and voice-reminds the user about important non-spam emails. Filter `_should_remind_email()` rejects automated senders (noreply, no-reply, GitHub/GitLab/Stripe/PayPal/etc. domains), newsletter/receipt/invoice/Pull-Request keywords, and GitHub-style `[repo] PR #123:` patterns. Throttled to `DISCORD_VOICE_LIVE_EMAIL_REMINDER_MAX_PER_HOUR` (3 by default) to avoid nagging. Seen-IDs persisted to `~/.hermes/voice-users/email-reminder-seen.json` so restarts don't re-nag on already-seen emails.
- **Voice to Aoede (criterion #20)** — already the default (`GEMINI_VOICE_NAME = os.getenv("DISCORD_VOICE_LIVE_VOICE", "Aoede")`). No code change needed.

### Fixes
- **Email compose/send broken** (#18) — was a missing auto-correction path; the spoken email address was passed verbatim to `google_api.py` which would fail on STT artifacts. The new `_autocorrect_email_address()` runs before send.

### Tests
- New `scripts/regression_test_criteria_17_18_19.py` — 50 checks across 3 sets covering webhook dispatcher routing/throttling/embed format, `_autocorrect_email_address` common cases, `_should_remind_email` spam filtering. All 50 pass.

### Configuration additions
```
DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT=...
DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_STATUS=...
DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_TRANSCRIPT=...
DISCORD_VOICE_LIVE_WEBHOOK_EMAIL=...
DISCORD_VOICE_LIVE_WEBHOOK_BRIDGE_STATUS=...
DISCORD_VOICE_LIVE_WEBHOOK_TOOL_CALLED=...
DISCORD_VOICE_LIVE_WEBHOOK_THROTTLE_SECONDS=2
DISCORD_VOICE_LIVE_EMAIL REMINDER_ENABLED=true
DISCORD_VOICE_LIVE_EMAIL REMINDER_POLL_SECONDS=300
DISCORD_VOICE_LIVE_EMAIL REMINDER_MAX_PER_HOUR=3
```

---

## 0.2.3 — 2026-06-05

### Fixes
- **Keyboard typing SFX** — replaced the thin 60ms Mixkit single-click SFX with a real 180ms mechanical keyboard click (sourced from YouTube, "Keyboard Single Strokes SOUND Effect" by SennaFoxy / VirtualZero). Downmixed to mono, resampled to 24 kHz, band-passed 100 Hz - 8 kHz, EBU-normalized to -18 LUFS. Now reads as a natural staccato of real mechanical clicks when looped, not a click track.

### Features
- **OpenCode progress watcher (criterion #16)** — long-running opencode tasks now keep the user informed via voice updates. A background asyncio task on the gateway event loop polls the opencode log every 5s, throttles voice updates to at most one per 30s, and emits an immediate update on milestone events (error, exception, test pass/fail, compile success/fail, build success/fail, done, complete, commit, push, ✓/✗). Sends a final summary when the tmux window dies. Respects "user is currently speaking" gate — drops updates rather than barging in. Per-session weak-ref registry lets the watcher call back into the bridge's send_text() from a different code path. New env vars: `DISCORD_VOICE_LIVE_OPENCODE_WATCHER` (master toggle), `..._POLL_SECONDS`, `..._MIN_VOICE_GAP_SECONDS`, `..._INITIAL_DELAY_SECONDS`.

### Tests
- New `scripts/regression_test_opencode_watcher.py` — 13 checks across 6 sets covering the watcher end-to-end. All 13 pass.

---
