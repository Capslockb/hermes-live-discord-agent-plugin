# Validation Matrix — Hermes Live Discord Agent Plugin v2

This matrix documents what is actually wired and testable in the `second-release` branch. Every row is tagged:

- **WORKING** — code exists, is registered, and has been exercised.
- **PARTIAL** — code exists but has known gaps, soft failures, or depends on external auth.
- **PLANNED** — intended for a future release; not shipped yet.
- **RESEARCH** — being evaluated; no production code yet.

---

## 1. Gemini Live API integration

| Feature | Status | Evidence / Notes |
|---|---|---|
| Bidirectional audio WSS | **WORKING** | `plugin/bridge.py` `GeminiLiveBridge._connect()` + `_send_audio()` + `_recv_loop()`. |
| Opus decode / encode | **WORKING** | `VoiceListener`, `LiveAudioSource`; PCM resampling with `numpy`. |
| Function calling inside voice session | **WORKING** | `_handle_tool_call()` dispatches to local tools and returns `toolResponse`. |
| Vision / frame push | **WORKING** | `POST /frame`, `voice_live_frame` tool, image URL fetch. Bots cannot receive native Discord video. |
| Native audio models | **PARTIAL** | Tested on `gemini-3.1-flash-live-preview` and `gemini-2.5-flash-native-audio-preview-*`. `mediaResolution` omitted because current models reject it. |
| Voice selection (Gemini TTS) | **WORKING** | `DISCORD_VOICE_LIVE_VOICE` passed in `voiceConfig`. |
| Model fallbacks | **WORKING** | `GEMINI_LIVE_MODEL_FALLBACKS` comma list. |
| Cost controls | **PARTIAL** | 1 fps cap, 512 KB frame cap, audio gating, idle hangup. Dollar estimates are approximations; your mileage will vary. |

---

## 2. Discord voice integration

| Feature | Status | Evidence / Notes |
|---|---|---|
| Join voice channel | **WORKING** | `voice_live()` in `plugin/__init__.py`; slash command `/voice-live`. |
| Leave voice channel | **WORKING** | `voice_live_leave()`; slash command `/voice-live-leave`. |
| Move between channels | **WORKING** | `current_vc.move_to(target)` when re-triggered in a different channel. |
| Stale rejoin cleanup | **WORKING** | `_active_bridges` stale-entry cleanup in `voice_live()`. |
| Auto-infer user's current channel | **WORKING** | `_infer_user_voice_channel()` for slash command with empty args. |
| Typing SFX | **WORKING** | `_load_typing_sfx_pcm()` in `bridge.py`; env controls. |
| Idle hangup | **WORKING** | `AUTO_LEAVE_QUIET_SECONDS`, idle prompt, grace period. |
| Video state awareness | **WORKING** | `_video_state_watcher()` polls `self_stream`/`self_video`. |
| Discord CDN 4006 quirk | **WORKING workaround** | Known: first ~5 handshakes rejected, ~27–40 s connect time. Patience required; do not restart repeatedly. |
| Multiple concurrent guild bridges | **PARTIAL** | One bridge registry entry per guild; global sidecar uses a single `BRIDGE` and one port, so only one bridge's health/notes are visible at a time. |
| Multi-channel within one guild | **PLANNED** | One voice client per guild today. |

---

## 3. Sidecar HTTP API (local-only)

| Endpoint | Status | Notes |
|---|---|---|
| `GET /health` | **WORKING** | Returns `voice_connected`, `running`, `model`, counters. |
| `POST /frame` | **WORKING** | Pushes image to Gemini; `force=true` supported. |
| `GET /notes` | **WORKING** | Recent call-note events from `NOTES_DIR`. |
| `GET /say?text=…` | **WORKING** | Injects text into the Gemini session. |
| `POST /stop` | **WORKING** | Clean shutdown. |
| Public exposure | **Not supported** | Sidecar binds to `127.0.0.1` only. |

---

## 4. SORA bridge elements (v2 import)

| Tool | Status | Notes |
|---|---|---|
| `sora_bridge_preflight` | **WORKING** | Local diagnostics; redacts secrets; no external calls except local sidecar `/health`. |
| `sora_live_grill` | **WORKING** | Transcript analysis; returns objective/constraints/owner/deadline/risk/next command/verification questions. |
| `sora_goal_synth` | **WORKING** | `/goal` + ranked `/subgoal` generation for weaker models. |
| `sora_redact` | **WORKING** | Secret redaction before Gemini/Discord/logs. |
| SORA → Gemini bridge auto-wiring | **WORKING** | `plugin/__init__.py` imports `register_sora_bridge_tools` inside `register(ctx)`. |
| SORA regression tests | **WORKING** | `scripts/regression_test_sora_elements.py` (19 checks). |

---

## 5. Vapi bridge

| Feature | Status | Notes |
|---|---|---|
| Vapi voice bridge plugin | **PLANNED** | Separate repo exists. Not merged into this release. |
| `voice_vapi` tool | **PLANNED** | Requires porting Vapi WebSocket/audio logic and sidecar. |
| Shared sidecar port (`18944`) | **PLANNED** | Env `DISCORD_VOICE_LIVE_PORT_VAPI=18944` reserved, no listener yet. |

---

## 6. MCP (Model Context Protocol)

| Feature | Status | Notes |
|---|---|---|
| Native Hermes MCP client | **RESEARCH** | Hermes supports MCP servers in `config.yaml`; no MCP server shipped by this plugin yet. |
| SORA MCP orchestration | **RESEARCH** | Evaluating whether to expose bridge health/transcripts as MCP resources. |

---

## 7. Dograh agent / full SORA runtime

| Feature | Status | Notes |
|---|---|---|
| Dograh autonomous agent | **RESEARCH** | No code in this repo yet. |
| SORA goal-stack runtime | **PLANNED** | `sora_goal_synth` is a text helper; the full runtime loop is not wired. |
| SORA progress sidecar for Discord | **PLANNED** | Not imported; would require Discord edit-in-place progress emitter. |
| SORA voice/video production pipeline | **PLANNED** | Out of scope for the Gemini bridge; see SORA project. |

---

## 8. Tool families

| Family | Status | Notes |
|---|---|---|
| Spotify | **WORKING** | 9 tools declared; needs Spotify auth. |
| Web search / extract | **WORKING** | 2 tools; uses Hermes web tools. |
| GitHub | **WORKING** | 7 tools via `gh` CLI; needs `gh` auth. |
| Email | **WORKING** | list/read/send/reply; Gmail API; STT correction. |
| Local helpers | **PARTIAL** | Declared in `bridge.py`; some depend on system commands and may fail if the command is missing. |
| Home Assistant | **PARTIAL** | 4 tools; needs `HASS_TOKEN`. |
| OpenCode CLI | **PARTIAL** | `opencode_run` etc. require `opencode` installed and on PATH. |
| Codex CLI | **PARTIAL** | `codex_run` etc. require `codex` installed and on PATH. |
| System inspect | **WORKING** | Owner-only allowlist. |
| Multi-CLI delegation | **PARTIAL** | `local_delegate_*` wired; some declared platforms are not day-to-day verified. |
| Onboarding | **WORKING** | First-session Q&A persisted to profile. |

---

## 9. Profiles / isolation

| Feature | Status | Notes |
|---|---|---|
| Auto-create per-user profile | **WORKING** | `user_profiles.py`. |
| Owner detection | **WORKING** | `VOICE_OWNER_DISCORD_ID`. |
| Tool allowlist | **PARTIAL** | Implemented; review before exposing the bot to untrusted users. |
| Cross-user session isolation | **PARTIAL** | Opencode registry keyed per user; not formally audited. |
| Cross-call memory via Honcho | **PARTIAL** | Reads Honcho context at connect; writing voice sessions back to Honcho is not yet implemented. |

---

## 10. Observability / ops

| Feature | Status | Notes |
|---|---|---|
| Webhook dispatcher | **WORKING** | 6 event classes; per-class URLs. |
| OpenCode watcher | **WORKING** | Polls tmux log, milestone detection, voice injection. |
| Email reminder poller | **WORKING** | 5-min poll, spam filter, hourly cap. |
| Post-call notes / transcripts | **WORKING** | JSONL notes in `DISCORD_VOICE_LIVE_NOTES_DIR`. |
| Autostart file | **WORKING** | `~/.hermes/voice-live-autostart.json`. |
| `systemctl` service management | **WORKING** | Documented in README and AGENTS.md. |
| SORA preflight diagnostics | **WORKING** | Runs from `sora_bridge_preflight` tool or `installer/enable_sora_bridge_elements.py`. |

---

## How to update this matrix

1. After exercising a feature, change its tag and add evidence.
2. When a **PLANNED** item ships, move it to **WORKING**.
3. When a **RESEARCH** item is rejected, mark it **PLANNED** with a note or remove it.
4. Never mark a feature **WORKING** without a test, command, or code pointer.
