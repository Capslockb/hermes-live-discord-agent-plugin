# CHANGELOG — gemini-live-discord-bridge

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
