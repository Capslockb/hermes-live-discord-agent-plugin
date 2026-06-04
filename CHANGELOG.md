# CHANGELOG — gemini-live-discord-bridge

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
