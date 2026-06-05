# CHANGELOG — gemini-live-discord-bridge

## 0.2.5 — 2026-06-05

### Features
- **GitHub repo tracker (criterion #22)** — 6 new voice tools that wrap the existing `gh` CLI (already authenticated as Capslockb):
  - `local_github_repo_list` — list the user's GitHub repos
  - `local_github_issues` — list issues for a specific repo
  - `local_github_prs` — list pull requests for a specific repo
  - `local_github_issue_create` — create a new issue
  - `local_github_note` — append a free-form note to `~/.hermes/voice-users/voice-session-notes.jsonl` (the "leave notes for hermes to update after call" piece)
  - `local_github_notes_read` — read back persisted notes (most recent first)
- **Note persistence** (criterion #22 follow-on) — voice session notes are written to `~/.hermes/voice-users/voice-session-notes.jsonl` in append-only mode so the next Hermes turn (or any future voice session) can pick up the action items.

### Configuration additions
```
DISCORD_VOICE_LIVE_GITHUB_TOOLS=true   (default: true; set false to disable all 6 tools)
```

### Tests
- New `scripts/regression_test_criterion_22.py` — 22 checks across 5 sets covering declarations, real `gh` CLI calls, note roundtrip, and error handling. All 22 pass.

---

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
DISCORD_VOICE_LIVE_EMAIL_REMINDER_ENABLED=true
DISCORD_VOICE_LIVE_EMAIL_REMINDER_POLL_SECONDS=300
DISCORD_VOICE_LIVE_EMAIL_REMINDER_MAX_PER_HOUR=3
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


## 0.2.1 — 2026-06-05

### Fixes
- **Case-insensitive opencode session name lookup** — `opencode_run` sanitizes session names to lowercase/hyphens (`'Refactor'` → `'refactor'`), but `opencode_status` / `opencode_send` / `opencode_stop` were looking up the registry with the raw name. A user could spawn a session with `name='Refactor'` but then could not stop it. New `_opencode_sanitize_name()` helper called from all five opencode_* dispatch paths fixes it.

### Tests
- **New `scripts/regression_test_user_isolation.py`** — 50 checks across 11 sets exercising the full per-user isolation surface (legacy fallback, owner privilege, new-user safe defaults, explicit disable, cross-user opencode denial, profile round-trip, index tracking, auto-owner promotion, "all" override, list API). Run with `python3 scripts/regression_test_user_isolation.py`. All 50 pass.

---


## 0.2.0 — 2026-06-05

Major feature drop: per-user profiles, OpenCode delegation, real keyboard SFX, and the full 40-tool surface for Gemini Live.

### Features

- **Per-user profile isolation** (`user_profiles.py`). Every Discord user gets their own profile at `~/.hermes/voice-users/<discord_id>.yaml`, auto-created on first contact. Each profile owns its Honcho peer name, tool allowlist, system prompt overrides, default workdir, notes dir, and opencode namespace. Owner (default B's snowflake) gets destructive tools on first contact; everyone else gets a safe starter set.
- **OpenCode delegation tools** — 5 new `opencode_*` tools:
  - `opencode_run` — spawns a full OpenCode coding agent in a tmux window, returns a session name
  - `opencode_status` — tails the session log and checks window liveness
  - `opencode_list` — lists tracked sessions for the active user only
  - `opencode_send` — injects a follow-up message into a running session
  - `opencode_stop` — kills the window and forgets the session
  - Per-user tmux namespacing (`oc-<8char_user_hash>-<session>`) so two users can run "refactor" simultaneously without collision. Cross-user access denied at the registry level.
- **Real keyboard typing SFX** — `assets/voice-live-typing.wav` (Mixkit "Hard laptop keyboard typing", CC0). 60 ms single keystroke, EBU-normalized to -16 LUFS, plays while Gemini is running a tool. Set `DISCORD_VOICE_LIVE_TYPING_SFX` to override. Synthetic fallback is opt-in.
- **System file inspection tools** — `local_inspect_read` + `local_inspect_grep` with a hard-coded path allowlist (`~/.hermes`, `/etc/systemd`, `hermes-workspace`, `honcho`, `~/projects`, `/var/log`). Read-only, owner-only by default.
- **Spotify playlists tool** — `spotify_playlists` with `create` / `list` / `get` / `add_items` / `remove_items` / `update_details`. Wires the existing Hermes Spotify plugin.
- **Honcho per-user memory** — the bridge now uses `profile.honcho_peer_name` when fetching peer context, so memory is per-user instead of global. The `local_honcho` tool lets Gemini search past facts mid-conversation.
- **System prompt** — updated with explicit `PROACTIVE ENGAGEMENT` section (asks questions, recommends music, follows up) and a `VIDEO SIGHT` capability line so Gemini knows it can describe incoming video frames.
- **`mediaResolution: LOW`** added to the Gemini Live setup payload. ~100 tokens/frame vs ~258 default — wallet-safe on the Flex tier.
- **Defense-in-depth per-user tool check** in `_handle_tool_call` — even if a tool declaration slips through the filter, dispatch refuses to run it for a user whose profile doesn't allow it.

### Documentation

- README rewritten: new ✨ Features table at the top (per #11 release-table criterion), full tool reference, per-user profile section, SFX section, expanded env-var docs, updated cost section.
- CHANGELOG bumped to 0.2.0.
- All new behavior documented at the section level (no docs left to write).

### Configuration additions
- `VOICE_OWNER_DISCORD_ID` (default: B's snowflake)
- `VOICE_USERS_DIR` (default: `~/.hermes/voice-users`)
- `DISCORD_VOICE_LIVE_TYPING_SFX` (path to .wav)
- `DISCORD_VOICE_LIVE_TYPING_SFX_VOLUME` (0.0 – 1.0)
- `DISCORD_VOICE_LIVE_TYPING_SYNTH_FALLBACK` (opt-in thud)
- `DISCORD_VOICE_LIVE_OPENCODE_BIN` (default: `~/.local/bin/opencode`)
- `DISCORD_VOICE_LIVE_OPENCODE_MODEL` (default: `anthropic/claude-sonnet-4`)
- `DISCORD_VOICE_LIVE_OPENCODE_TMUX_SESSION` (default: `opencode-voice`)
- `DISCORD_VOICE_LIVE_SYSINSPECT_TOOLS` (default: `true`)

### Known issues

- OpenCode approval passthrough not yet implemented (currently uses `-y` for one-shot `opencode run`); see #5 below.
- End-to-end voice test pending (live test requires explicit user opt-in to avoid burning budget on a misconfigured session).

---


## 0.1.0 — 2026-06-04

Initial public release.

### Features
- Full-duplex Discord voice ↔ Gemini Multimodal Live API bridge.
- Synchronous function calling with in-process tool dispatch.
- Idle hangup with two-phase timer (prompt + grace).
- Auto-leave on `LEAVE_PHRASES` and quiet-timeout.
- 1 fps video frame feeder, 512 KB cap, audio-gated (cost ~$0.03–0.06/h).
- Post-call JSONL transcripts at `~/.hermes/voice-live-notes/`.
- Oneshot TUI installer with live network validation of API keys.
- Slash command `/voice-live` and `/voice-live-leave` (auto-infers user voice channel).
- HTTP control API on `127.0.0.1:18943` (`/health`, `/say`, `/leave`).
- Autostart via `voice-live-autostart.json` (deleted on success).

### Fixes
- Stale rejoin: `voice_live()` now detects and replaces a stale `_active_bridges` entry instead of returning "pending" forever.
- Sidecar HTTP server: `_shutdown_watcher` polls `BRIDGE._running` and calls `server.close()` so `serve_forever()` returns cleanly.
- Expanded `VOICE_LEAVE_PHRASES` default to include `bye`, `hang up`, `exit voice`.

### Known issues
See [`docs/KNOWN_BUGS.md`](docs/KNOWN_BUGS.md). Key ones:
- Discord CDN handshake rejection (code 4006) — first ~5 attempts always fail; just wait ~27 s.
- Module import path uses dash→underscore normalization.
- Function-calling handlers must return quickly; long work must go to a background task.


