# Architecture

This doc explains how the Hermes Live Discord Agent Plugin is structured, how audio flows, and where the SORA helpers fit in. It is written for developers and operators; if you just want to install the bridge, start with the [README](../README.md) or the public site.

## Simple mental model

```text
Discord voice channel
        │
        ▼
Hermes Live bridge        (inside the Hermes gateway)
        │
        ▼
Gemini Multimodal Live API
        │
        ▼
Local tools / integrations
```

Three boxes. Discord carries the audio. The bridge converts and routes it. Gemini does the thinking and speaking. The deep pipeline is below.

## High-level dataflow

```text
┌─────────────────┐         UDP Opus          ┌──────────────┐
│  Discord user   │ ◄───────────────────────►│  discord.py  │
│  (voice channel)│                          │ VoiceClient  │
└─────────────────┘                          └──────┬───────┘
                                                   │
          ┌────────────────────────────────────────┘
          ▼
 ┌─────────────────┐      16 kHz PCM       ┌─────────────────┐
 │  Opus decode    │ ─────────────────────►│  Gemini Live    │
 │  (audio in)     │                     │  WebSocket      │
 └─────────────────┘                     │  (serverContent)│
                                          └────────┬────────┘
                                                   │
          ┌────────────────────────────────────────┘
          ▼
 ┌─────────────────┐      24 kHz PCM       ┌─────────────────┐
 │  Opus encode    │ ◄─────────────────────│  Gemini Live    │
 │  (audio out)    │                       │  WebSocket      │
 └─────────────────┘                       │  (realtimeInput)│
                                            └─────────────────┘
                                                   │
                                                   │ functionCalls
          ┌────────────────────────────────────────┘
          ▼
 ┌──────────────────────────────────────────────────────────┐
 │  Local tool executor (thread pool)                       │
 │  Spotify · Web · Email · GitHub · Home Assistant · etc │
 └──────────────────────────────────────────────────────────┘
```

Discord audio is Opus-encoded stereo at 48 kHz. The bridge decodes it, resamples to 16 kHz mono for Gemini input, and encodes Gemini's 24 kHz output back to Opus for Discord.

## Components

| File | Responsibility |
|---|---|
| `plugin/__init__.py` | Hermes plugin registration, slash-command glue, autostart thread, video-state watcher, SORA bridge element wiring. |
| `plugin/bridge.py` | Core audio pipeline, Gemini Live WSS client, function-call dispatch, typing SFX, idle hangup, sidecar HTTP server, notes JSONL. |
| `plugin/user_profiles.py` | Per-Discord-user profile, owner detection, tool allowlist, onboarding state. |
| `plugin/delegation_agent.py` | Multi-CLI platform scoring, prompt assembly, execution dispatcher for OpenCode/Codex/Gemini CLI. |
| `plugin/webhook_dispatcher.py` | Per-event-class Discord webhook delivery with throttling. |
| `plugin/sora_bridge_elements.py` | SORA bridge helpers: preflight, Live Grill, goal/subgoal synthesis, secret redaction. |
| `installer/install.py` | One-shot installer: preflight, API keys, deploy mode, `.env` merge, autostart. |

## SORA bridge elements (v2)

The SORA helpers are imported into the same Hermes gateway process. They do not add listeners on public interfaces:

```text
Hermes gateway process
 ├─ discord-voice plugin
 │   ├─ bridge.py (audio + Gemini)
 │   └─ sora_bridge_elements.py (preflight/grill/synth/redact)
 │       ├─ reads ~/.hermes/.env and ~/.hermes/honcho.json
 │       └─ calls local sidecar /health
 └─ tool results returned to Gemini / Discord / logs
```

A failure in `register_sora_bridge_tools()` is caught and logged; it cannot break `voice_live`.

## Threading model

- `discord.py` calls `AudioSource.read()` from a native thread, so the outbound PCM queue is a `threading.Queue`.
- All Gemini I/O and tool dispatch run on the gateway asyncio loop.
- Tool handlers that perform blocking I/O run in `asyncio.to_thread` or an executor.

## Security notes

- Sidecar binds to `127.0.0.1` only.
- Secret redaction runs before tool output is sent to Gemini, Discord, or logs.
- Owner-only tools are gated by `VOICE_OWNER_DISCORD_ID`. If that variable is unset, owner-only tools have no owner to gate against — review your tool allowlists before exposing the bot to untrusted users.
- The bridge never stores Gemini API keys in notes or transcripts.

## Control API (local only)

The sidecar HTTP server is bound to `127.0.0.1` only and exposes these routes:

| Method | Route | Purpose | Status |
|---|---|---|---|
| GET | `/health` | Bridge state | **WORKING** |
| POST | `/frame` | Push a video frame | **WORKING** |
| GET | `/notes` | Call-note events | **WORKING** |
| GET | `/say` | Inject a text prompt | **WORKING** |
| POST | `/stop` | Stop the bridge | **WORKING** |

These are intended for local diagnostics and helper scripts, not for public exposure. The default port is configurable via `DISCORD_VOICE_LIVE_PORT`.

## Limitations and design constraints

- Discord bots cannot receive the native video stream. Vision works by pushing individual frames via `/frame` or `voice_live_frame`.
- The bridge is currently one voice call per guild. Multi-channel within one guild and multi-guild sidecar visibility are **PARTIAL** / **PLANNED**.
- Persistent cross-call memory reads from Honcho at connect but does not yet write voice sessions back to Honcho.
- Only Gemini Live is supported as the realtime backend.

## Diagrams

See `docs/diagrams/` for ASCII lifecycle, idle-hangup, and audio-pipeline diagrams.
