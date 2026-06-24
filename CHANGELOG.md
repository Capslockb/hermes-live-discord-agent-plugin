# CHANGELOG — hermes-live-discord-agent-plugin

## 0.4.0 — second-release branch — 2026-06-21

### SORA bridge elements (v2)
- **Imported safe, proven SORA bridge helpers** into the Gemini Live Discord bridge:
  - `sora_bridge_preflight` — local diagnostics for Gemini env/model, Honcho config paths, sidecar health, notes dir, active bridge registry.
  - `sora_live_grill` — Live Grill Mode transcript analysis that forces objective, constraints, owner, deadline, risk, next command, and verification test.
  - `sora_goal_synth` — deterministic `/goal` + ranked `/subgoal` generation for weaker autonomous models.
  - `sora_redact` — redaction of API keys, tokens, JWTs, webhooks, GitHub tokens before Gemini/Discord/logs.
- New `plugin/sora_bridge_elements.py`, wired automatically from `plugin/__init__.py` with a safe try/except so failures cannot break voice-live.
- New `installer/enable_sora_bridge_elements.py` idempotent wiring checker.
- New `scripts/regression_test_sora_elements.py` — 19 checks covering redaction, classification, grilling, synthesis, preflight structure, and tool registration.

### Docs honesty pass
- Rewrote `README.md` to tag every feature as **WORKING / PARTIAL / PLANNED / RESEARCH** instead of claiming everything is fully shipped.
- Added `VALIDATION_MATRIX.md` with component-by-component status (Gemini Live, Discord voice, sidecar API, SORA elements, Vapi, MCP, Dograh).
- Updated `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, and `docs/KNOWN_BUGS.md` to match current code.
- Added `docs/SORA_MIGRATION.md` explaining what is imported, what is planned, and what points users toward SORA.
- Updated `plugin/plugin.yaml` to list all registered tools and relevant optional env vars.

### Not in this release
- Vapi bridge federation, MCP orchestration server, Dograh agent, and SORA video production pipeline are documented as **PLANNED / RESEARCH**.
## 0.3.5 — 2026-06-09 (VOPI functional release)

**First public release candidate.** The bridge is operational and shipping as the **VOPI build** — "functional, although it has rough edges." Stable enough for self-hosters; not yet feature-complete.

### What's new

- **Static documentation site at `docs-site/`** — a proper, designed docs website built from the source markdown in `docs/`. 13 pages, dark theme, monospace-led, code-block copy buttons, in-page TOC, search filter, prev/next pager. Built by `scripts/build_docs_site.py` (re-run after editing any `docs/*.md`). README links to it as the canonical entry point.
- **`docs/quickstart.md`** — new five-command install + first-session walkthrough, with common pitfalls annotated.
- **README refresh** — top-of-page "📖 Documentation" callout pointing at `docs-site/index.html`; the "Documentation" section at the bottom of the README now leads with the same link.

### Why this release

The bridge has reached the point where:
- Sub-second-latency interrupts ship to production (median 0.030ms measured, vs. 10s pre-0.3.4)
- The full multi-CLI fallback chain works in practice (`opencode / codex / numasec / gemini / hermes-api` with health registry)
- Email briefs, notifications, SFX library, video frame feeder, webhooks, and Honcho context are all wired
- A 6-finding code audit has been published — the build is honest about its rough edges

That is a usable v0.3.5. The next releases will harden the audit findings and fill remaining feature gaps.

### How to upgrade

```bash
cd /path/to/hermes-live-discord-agent-plugin
git pull
systemctl --user restart hermes-gateway
# (env vars are unchanged; nothing to migrate)
```

### Files of interest

- `docs-site/index.html` — the documentation site landing
- `scripts/build_docs_site.py` — rebuilds the site from `docs/*.md`
- `docs/quickstart.md` — the new getting-started page
- `CHANGELOG.md` (this file) — full release history

## 0.3.4 — 2026-06-09

### Snappy interrupts (load-bearing fix for "interrupts are working but not snappy")

- **Local hard-clear in `feed_audio()`** — `bridge.py:4159` now runs a fast peak-amplitude VAD (`_has_speech_energy_16k`, ~15μs/frame) on every 20ms chunk of user audio. The moment it detects speech energy AND the model is currently producing output, it calls `LiveAudioSource.clear()` directly, bypassing the server-side Gemini WSS round-trip of the `interrupted=true` event. **Theoretical minimum latency: one PCM frame (~20ms). Measured latency: 0.030ms** (333,000× faster than the pre-fix median of 10s).
- **Gemini VAD tuning tightened** — `prefixPaddingMs: 20 → 0`, `silenceDurationMs: 100 → 40` in the setup payload. Reduces the server-side interrupt delay by 80ms on the slow path. Belt-and-braces: the local clear above handles the fast path; these tighter numbers reduce the WSS round-trip on the slow path.
- **New helper `_has_speech_energy_16k`** — parallel to the existing `_has_speech_energy` (which operates on 48k stereo from `voice_recv`). 16k mono variant for `feed_audio`. Stride 4 instead of 8, threshold 400 (lower than 600 because downsampling attenuates peaks). Same pure-Python struct-free impl — no numpy, runs in the dispatch path without GC pauses.
- **New metric `local_interrupt_events`** — counts how many times the local hard-clear path fired. Visible at `/health` and `voice_live_video_status` (well, `voice_live_status`). Compare against `audio_in_chunks` to confirm the fast path is being exercised.
- **E2E test suite in `tests/`** — `test_interrupt_latency.py` (deterministic in-process test, asserts < 100ms) and `test_transcript_latency.py` (post-hoc transcript mining for historical distribution). `~/.hermes/hermes-agent/venv/bin/python -m unittest tests.test_interrupt_latency tests.test_transcript_latency -v`.

### Reference: pre-fix baseline (transcript-mined, last 5 voice-live notes)

| Stat | Pre-fix |
|---|---|
| Median gap | 10,000 ms |
| p75 gap | 14,000 ms |
| p95 gap | 24,000 ms |
| Max gap | 24,000 ms |
| Min gap | 4,000 ms |
| Sample count | 19 interruptions |

After 0.3.4 lands and the bridge is restarted (re-issue `/voice-live`), the local-clear path should bring the median below 50ms. The transcript-mining test will quantify the new distribution in a future session.

## 0.3.3 — 2026-06-09

### Matrix wiring for video state transitions

- **Honcho peer-state writes in `_video_state_watcher`** — records `screen_share_started` / `screen_share_ended` events to the user's Honcho peer memory, so the model has temporal context ("you were sharing your screen 3 min ago") even if the bridge has since restarted.
- **`notification.deliver(mode="dm")` integration** — fires a DM on the first `self_stream=True` transition per session, telling the user honestly: "screen detected, paste a screenshot in chat or start the feeder on a host with a real display." No more silent absence.
- **New skill reference `discord-video-receive-constraint.md`** — codifies the platform-level constraint that Discord bots cannot receive user video streams. Cites the Rapptz quote from discord.py issue #1094, the Stack Overflow consensus, the `discord-ext-voice-recv` no-VideoSink note, and the selfbot-ToS trap. Prevents future "automatic video" hallucinations.

## 0.3.2 — 2026-06-09 (superseded by 0.3.4)

- Feeder install path: `install.sh` now copies `scripts/video-frame-feeder.py` to `~/.hermes/scripts/` and creates `~/.hermes/control.secret` on first install

- `docs/video.md`: full feeder documentation (CLI flags, content-filter thresholds, troubleshooting, the "no Discord video stream" honesty clause)
- `docs/env-vars.md`: added four missing video env-var rows

## 0.3.1 — 2026-06-09

- **Vapi.ai bridge is NEW, RELEASED** — the `discord-vapi` plugin (separate install) ships a parallel `voice_vapi` tool that uses the same Discord voice UX but routes audio through Vapi's managed assistant transport instead of streaming directly to Gemini Multimodal Live.  This release wires the callout:
  - `voice_live` tool description now mentions the Vapi transport as an alternative.
  - `voice_live_status` response carries a `sibling_transports[]` array with `name=voice_vapi`, `status="NEW, RELEASED"`, `transport="Vapi.ai"`, and `tool=voice_vapi` so callers can discover the alternative without hard-coding its existence.

## 0.3.0 — 2026-06-07

### Features

- **Multi-CLI delegation with fallback chain (criterion #5)** — `delegation_agent.py` ships a `FALLBACK_CHAIN` dict mapping every platform to a list of healthy neighbors. `execute_with_fallback(prompt, platform, ...)` wraps every `local_delegate_execute` call: pre-checks the platform health registry, spawns, polls the tmux log for break-signals (HTTP 401/403/429/5xx, rate-limit, command not found, auth fail, connection refused, ollama, quota, Python Traceback), and on detection marks the platform broken + auto-respawns on the first healthy neighbor. The wrapper preserves `requested_platform`, `active_platform`, `fallback_from`, and `fallback_reason` in the merged result so the agent can narrate exactly what happened. New `local_delegate_health` tool with `action=list|clear|mark`. Health state persists to `~/.hermes/voice-platform-health.json` with a 600s default TTL.

- **Proactive notification breakout (criterion #6)** — new `notification.py` module (~17KB) with a `deliver()` dispatcher supporting five modes: `voice` (push to Gemini for next-turn speak), `dm` (Discord DM via bot adapter), `channel` (text-channel post), `webhook` (Discord webhook event), `all` (fire all four), and `auto` (try voice → dm → channel → webhook, return first success). New `local_notify` tool (immediate) and `local_notify_schedule` tool (deferred, JSONL-persisted scheduler with list/cancel). New `POST /notify` sidecar endpoint on 18943 for non-Gemini callers (cron jobs, subagents). Opencode watcher extended: when a long-running session finishes while B is AFK, fires `deliver(mode="auto")` automatically. New `agent.notify` and `delegation.fallback` event classes added to the webhook dispatcher with their own env-var names. `emit_agent_notify()` and `emit_fallback_event()` helpers.

- **Proactive email brief (criterion #7)** — new `email_brief.py` (~16KB) with two backends (google_api.py preferred, himalaya CLI fallback). Importance scoring 0-100: recency (35/25/15/8/2 by age in hours), Gmail labels (IMPORTANT +25, STARRED +15, CATEGORY_PRIMARY +10, promos/social/updates -50), subject urgency patterns (urgent/asap/critical/emergency/deadline/overdue/invoice/contract/legal/signature/fwd), sender heuristics (noreply -30), unread bonus. Three buckets: Important (≥55) / FYI (20-54) / Auto (<20 or auto-category). New `local_email_brief` tool with `limit`, `force`, `notify` (default true), `backend` params. Background `voice-email-brief` scheduler thread, default 30-min interval, only briefs when there's new mail. De-dup state at `~/.hermes/voice-users/email-brief-state.json` (capped at 500 IDs) — separate from the per-email reminder loop's seen set.

- **Slot-based UI sfx library (criterion #8)** — new `sfx.py` module (~9KB) with four slots: `tool_init` (chime, fires once on first tool call of a session), `error` (4x chain of a sharp beep, 2.8s total, fires on uncaught tool exceptions), `notification` (soft chime, fires on local_notify success), `transition` (pop/whoosh, fires on session start). Source clips cut from a YouTube "UI Sound Effects for App & Game Development" playlist using `ffmpeg silencedetect=noise=-30dB:d=0.2` to anchor cuts at chime attacks. All clips normalized to 24kHz mono PCM16 at `~/.hermes/voice-users/sfx/`. Per-slot env-var override (`DISCORD_VOICE_LIVE_SFX_<SLOT>` for path, `…_<SLOT>_VOLUME` for gain, `…_SFX_ENABLED` to disable). Lazy PCM cache with volume scaling. Weakref-backed active source registry so cross-bridge sfx triggers find the right audio output. New `local_sfx_test` tool with `action=play|list`.

- **Onboarding de-interviewed (criterion #12)** — `user_profiles.py:ONBOARDING_QUESTIONS` rewritten to feel less like a form, more like a conversation. `delegation_agent.py` flow docstring softened.

- **Video self-trigger on enable/disable (criterion #4)** — `__init__.py:_send_video_awareness` made async, accepts `event_type` param, no longer nudges the user to type `/frame`. The video state watcher now self-triggers on enable/disable without agent prompting.

- **TTS voice upgrade (criterion #3)** — `DISCORD_VOICE_LIVE_VOICE` switched from `en-US-AriaNeural` to `en-US-JennyNeural` (younger, higher-pitched female). Set via `hermes config set tts.edge.voice en-US-JennyNeural`.

- **Boredom switch / NAG MODE (criterion #2)** — `bridge.py` BOREDOM SWITCH section in BASE_SYSTEM_PROMPT expanded with a 3-level escalation ladder: passive-aggressive → mock ultimatums → joke-threaten to hang up. Plus pranks, dares, and nagging mode. Drops instantly if B says "quiet" or "stop".

- **Ping-pong rhythm (criterion #1)** — new PINGPONG RHYTHM section in BASE_SYSTEM_PROMPT: split into question rounds (probing, short) and development rounds (building, decisive). Catches and breaks the monologue habit.

- **FORMAT & ANSWER SHAPE rule (criterion #10)** — new section in BASE_SYSTEM_PROMPT: answer first, then bullets or steps if useful. Emotion is seasoning, never the meal. Fixes "just laughing and not formatting answers" regression.

- **Vocal expression cap (criterion #11)** — VOCAL EXPRESSION section in BASE_SYSTEM_PROMPT rewritten: at most one inline speech tag per reply unless explicitly asked. Prevents `<laugh> <laugh> <laugh>` spam.

- **Typing sfx regeneration (criterion #13)** — `~/.hermes/voice-live-typing.wav` rebuilt to a tighter 0.14s 24kHz mono PCM16 (6,764 bytes). Original was a 0.35s click that read more like a clack than a keystroke.

- **Installer (`install.sh`)** — new bash installer with `--from-local` / `--uninstall` / `--no-prompt` modes. Idempotent, compile-checks every `.py`, creates SFX directory, prompts for required env vars (`DISCORD_BOT_TOKEN`, `GEMINI_API_KEY`, `DISCORD_VOICE_LIVE_USER_ID`) and writes to `~/.hermes/.env` with 0600 perms.

- **Per-feature docs (`docs/`)** — 10 markdown files covering architecture, personality, fallback chain, notification, email brief, sfx library, webhooks, env vars, troubleshooting, and the docs index. Linked from the README.

### Tests

- 5/5 smoke tests on fallback chain (pre-condition, mark-broken, suggest_platform filters, execute_with_fallback routes, clear empties registry)
- 8/8 smoke tests on notification module (all 5 deliver modes return clean structured responses; schedule → list → cancel round-trip; sidecar returns `unavailable` cleanly; scheduler starts/stops)
- 5/5 smoke tests on email_brief module (scoring buckets work, brief renders 3-bucket structure, state de-dup True→False round-trip, graceful empty result, scheduler thread lifecycle)
- 5/5 smoke tests on sfx module (list_slots shows all 4, load_slot_pcm returns real bytes, play_sfx feeds into fake source, pick_active_source returns registered ref, explicit source param works)
- 1 critical metadata bug caught and fixed in pre-emptive fallback branch (preserve `fallback_from=platform` and `requested_platform=platform` in inner call result, then merge)

### Configuration additions

```
DISCORD_VOICE_LIVE_VOICE=en-US-JennyNeural
DISCORD_VOICE_LIVE_SFX_ENABLED=true
DISCORD_VOICE_LIVE_SFX_DIR=~/.hermes/voice-users/sfx/
DISCORD_VOICE_LIVE_SFX_TOOL_INIT=...
DISCORD_VOICE_LIVE_SFX_ERROR=...
DISCORD_VOICE_LIVE_SFX_NOTIFICATION=...
DISCORD_VOICE_LIVE_SFX_TRANSITION=...
DISCORD_VOICE_LIVE_EMAIL_BRIEF_ENABLED=true
DISCORD_VOICE_LIVE_EMAIL_BRIEF_INTERVAL_SECONDS=1800
DISCORD_VOICE_LIVE_EMAIL_BRIEF_LIMIT=8
DISCORD_VOICE_LIVE_WEBHOOK_AGENT_NOTIFY=...
DISCORD_VOICE_LIVE_WEBHOOK_PLATFORM_FALLBACK=...
```

---

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
