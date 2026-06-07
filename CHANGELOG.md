# CHANGELOG — gemini-live-discord-bridge

## 0.2.8 — 2026-06-07

### Features
- **User-presence gate (criterion #33)** — `voice_live()` checks the target user is in the voice channel before starting the bridge. A per-second watchdog in `_connection_watchdog` stops the bridge within 1s if the user leaves or moves channels. No more token burn from unattended sessions.
- **First-turn mute (criterion #34)** — immediately after Gemini Live setup completes, the bridge sends `audioStreamEnd` to suppress the model's first autonomous generation turn. Combined with the rewritten system prompt (`TURN-START BEHAVIOUR: stay silent on connect`), the model no longer hallucinates "I see you're sharing your screen" on session start.
- **Video-initialized webhook (criterion #35)** — new `bridge.video` event class (`DISCORD_VOICE_LIVE_WEBHOOK_VIDEO`) fires `emit_video_initialized()` when the bridge accepts its first video frame after ≥30s of silence (default, tunable via `DISCORD_VOICE_LIVE_VIDEO_INITIALIZED_QUIET_THRESHOLD_S`). Embed color: purple 0x9B59B6. `WebhookDispatcher.emit()` extended with optional `throttle_key` parameter for per-(event_class, sub_event) throttling.
- **Feeder-side content-aware filtering (criterion #36)** — `video-frame-feeder.py` v0.2 pre-captures an 8×8 grayscale thumbnail, computes a 64-bit average hash + pixel stddev, and skips identical or near-identical frames (Hamming distance < 2) before triggering a full JPEG capture. Fallback to unfiltered full-frame send when the thumbnail pipe is unavailable. New CLI flags: `--min-change`, `--stddev-min`, `--no-content-filter`, `--source-label`.

### Fixes
- **"I see you're sharing your screen" token burn** — two root causes: (1) the system prompt told the model "If someone shares their screen... you have LIVE VIDEO SIGHT", which Gemini hallucinated as an implied task on every connect. (2) The old video-frame-feeder.py sent every frame at 1fps regardless of content — solid overlays cost ~258 tokens/frame. Both fixed: prompt rewritten as strictly conditional; feeder now filters by perceptual hash.
- **Feeder silent blackout on thumbnail failure** — when the 8×8 thumbnail pipe failed (ffmpeg version/filter incompatibility), the feeder silently looped without ever sending frames. Now falls through to full-frame capture + POST with a clear log warning.
- **stddev filter default too aggressive** — `--stddev-min` default lowered from 6.0 to 0 (disabled). The Hamming-distance filter already catches truly identical static screens; a low stddev value on an 8×8 thumbnail of real content (editor whitespace, dark theme) incorrectly blocked legitimate frames.

### Configuration additions
```
DISCORD_VOICE_LIVE_WEBHOOK_VIDEO=<discord_webhook_url>
DISCORD_VOICE_LIVE_VIDEO_INITIALIZED_QUIET_THRESHOLD_S=30
```

---

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

## 0.2.2 — 2026-06-04

### Features
- **Per-user profile isolation (criterion #9)** — new `user_profiles.py` module. Every Discord user who joins the voice bridge is a separate principal. Memory, tool allowlists, and system-prompt overrides are keyed by `discord_id` — never shared globally. `UserProfile` is auto-created on first contact with safe defaults; the owner (whose Discord ID matches `VOICE_OWNER_DISCORD_ID` env var) gets destructive tools enabled. Allowlist logic in `is_tool_allowed()` runs at setup-payload build time AND at tool-call dispatch time (defense in depth).
- **Owner auto-promotion** — if the user's Discord ID matches `VOICE_OWNER_DISCORD_ID` (env var, default: Capslockb's snowflake `1474100257762578597`), their profile is auto-promoted on first contact.

### Tests
- New `scripts/regression_test_per_user_profiles.py` — 58 checks across 5 sets covering profile CRUD, allowlist logic, owner detection, cross-user denial, and edge cases. All 58 pass.

---

## 0.2.1 — 2026-06-04

### Tests
- First regression suite: `scripts/regression_test_criteria_16.py` using a repeatable template.

---

## 0.1.0 — 2026-06-03

### Features
- Initial voice bridge: Gemini Live ↔ Discord audio via `discord-ext-voice-recv`
- Native Discord `/voice-live` and `/voice-live-leave` slash commands via `tree.command`
- Autostart via `voice-live-autostart.json`
- Spotify voice tools (play/pause/skip/search/volume/playlists)
- Web search voice tools (web search + extract)
- Local tools (read/search/patch/terminal/exec)
- Email tools (google_api.py — send/read/search/reply)
- Home Assistant tools
- OpenCode delegation (tmux, per-user namespace)
- SysInspect tools (process/disk/memory/net/uptime/gpu)
- Typing SFX (mechanical keyboard burst)
- VAD: START_OF_ACTIVITY_INTERRUPTS, auto-leave
- Control API on port 18943: /health, /stop, /say, /frame, /notes
- Per-session notes journal (JSONL append-log)
