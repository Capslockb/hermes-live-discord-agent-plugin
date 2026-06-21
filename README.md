# Hermes Live Discord Agent Plugin — v2

> Talk to Google Gemini Live in any Discord voice channel. Self-hosted, open source, and honest about what works today.

Hermes Live is a [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that bridges Discord voice to Google's Gemini Multimodal Live API. It provides real-time, full-duplex audio, optional vision frames, tool calling, idle hangup, local transcripts, and a set of safe SORA bridge helpers. Features are tagged **WORKING**, **PARTIAL**, **PLANNED**, or **RESEARCH** so you know what is real today.

- **Public site:** https://capslockb.github.io/hermes-live-discord-agent-plugin/
- **GitHub:** https://github.com/Capslockb/hermes-live-discord-agent-plugin

---

## What this is

A self-hosted Discord voice bot that lets you talk to Gemini Live hands-free. You join a voice channel, run `/voice-live`, and the bot listens and replies in real time. You can ask it to play music, check your inbox, control smart-home devices, run a web search, or look at an image you share.

This repo remains the **Gemini Live Discord bridge**. v2 imports a small set of safe helpers from the broader SORA runtime, but it is not a full SORA port.

---

## Who it is for

- **Discord communities** that want a hands-free AI co-host in voice.
- **Developers and operators** who already run Hermes Agent and want a self-hosted voice bridge they can extend.
- **AI experimenters** who want to test real-time multimodal voice + vision inside a familiar chat app.

---

## What works today

| Feature | Status | What it means |
|---|---|---|
| Real-time full-duplex voice | **WORKING** | Two-way audio in Discord with low latency. |
| Vision / frame push | **WORKING** | Send images or single frames to Gemini while in voice. Discord's native video stream is not received by bots; frames are pushed via a command or local helper. |
| Tool calling from voice | **WORKING** | Gemini can call tools during the call: Spotify, email, GitHub, weather, web search, Home Assistant, and more. |
| Idle hangup | **WORKING** | Prompt after silence, then auto-leave if nobody responds. |
| Local transcripts | **WORKING** | Every call writes a timestamped transcript to a local directory. |
| Local control API | **WORKING** | A 127.0.0.1-only HTTP surface for health, frames, text injection, and notes. |
| Per-user profiles | **PARTIAL** | Auto-created profiles, owner detection, onboarding Q&A. Strict allowlists exist but should be reviewed before exposing the bot to untrusted users. |
| Multi-CLI delegation | **PARTIAL** | Framework can suggest/spawn OpenCode or Codex from voice. Those CLIs must be installed separately; some declared platforms are not verified. |
| Cost controls | **PARTIAL** | Frame-rate, frame-size, and audio-gating caps are enforced. Dollar figures are rough estimates; your mileage will vary. |
| SORA bridge helpers | **WORKING** | Preflight diagnostics, Live Grill transcript questions, goal/subgoal synthesis, secret redaction. Local-only. |
| Vapi bridge / MCP / Dograh | **PLANNED / RESEARCH** | Not imported into v2. See SORA migration docs. |

---

## SORA helpers (v2 import)

The broader SORA runtime includes video production, multi-bridge federation, MCP orchestration, and autonomous agents like Dograh. v2 imports only these four safe, local helpers:

| Tool | Status | Purpose |
|---|---|---|
| `sora_bridge_preflight` | **WORKING** | Local diagnostics: env/model, Honcho paths, sidecar health, notes dir. |
| `sora_live_grill` | **WORKING** | Analyze a transcript and return hard questions (objective, constraints, owner, deadline, risk, next command, verification). |
| `sora_goal_synth` | **WORKING** | Generate a Discord-safe `/goal` plus ranked `/subgoal` items. |
| `sora_redact` | **WORKING** | Strip secrets from text before logs/Discord/Gemini. |

No public ports. No new HTTP listeners. A failure in the SORA helper registration is caught and logged so it cannot break voice calls.

---

## Quick install

Requirements: Python 3.10+, a Discord bot token, a Gemini API key, and (recommended) Hermes Agent.

```bash
git clone https://github.com/Capslockb/hermes-live-discord-agent-plugin.git
cd hermes-live-discord-agent-plugin
python3 installer/install.py
```

The installer checks your tokens live, deploys the plugin, writes a private `.env` file, and optionally restarts the gateway.

---

## First run

1. Restart the Hermes gateway so the plugin loads.
2. In Discord, join a voice channel the bot can access.
3. Run `/voice-live`.
4. Wait up to ~30 seconds on first connect — Discord's voice network sometimes rejects the first few handshakes before accepting. Do not restart repeatedly.
5. Talk normally. Ask questions, share frames, or request tools.

---

## Verify it is working

```bash
# 1. Check the local sidecar health
curl -s http://127.0.0.1:18943/health | python3 -m json.tool

# 2. Run the SORA wiring check
python3 installer/enable_sora_bridge_elements.py

# 3. Compile-check the plugin
python3 -m py_compile plugin/*.py installer/*.py scripts/*.py
```

Look for `voice_connected: true` and `running: true` in the health JSON.

---

## Configuration

All settings live in `~/.hermes/.env`.

### Required

```bash
DISCORD_BOT_TOKEN=...
GEMINI_API_KEY=...
```

### Common optional settings

```bash
GEMINI_MODEL=gemini-3.1-flash-live-preview
GEMINI_LIVE_MODEL_FALLBACKS=gemini-3.1-flash-live-preview,gemini-2.5-flash-native-audio-preview-12-2025
DISCORD_VOICE_LIVE_VOICE=Aoede
DISCORD_VOICE_LIVE_PORT=18943
DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS=            # comma-separated Discord user IDs; empty = allow all non-bot speakers
DISCORD_VOICE_LIVE_NOTES_DIR=~/.hermes/voice-live-notes
VOICE_OWNER_DISCORD_ID=                         # your Discord user ID for owner-only tools
```

See [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for the full list.

---

## Architecture

```
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

The bridge runs in-process inside the Hermes gateway. Discord audio is decoded and resampled in a worker thread; Gemini I/O and tool dispatch run on the asyncio loop. The sidecar HTTP server binds to `127.0.0.1` only.

Deep architecture, threading model, and security notes are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Tool families available to Gemini

| Family | Tools | Notes |
|---|---|---|
| Spotify | play, pause, next, previous, search, queue, playlists, volume | Needs Spotify auth. |
| Web | web_search, web_extract | Uses Hermes web tools. |
| Email | local_email_list/read, local_email_send/reply | Gmail API auth. |
| GitHub | repo list, issues, PRs, issue create, notes | Needs `gh` CLI auth. |
| Home Assistant | call_service, etc. | Needs `HASS_TOKEN`. |
| Local helpers | weather, translate, time, remind, systemd, docker, tailscale, notes, disk, calc, uptime, news, youtube, honcho | Best-effort; some depend on system commands. |
| System inspect | local_inspect_read, local_inspect_grep | Owner-only. |
| OpenCode / Codex | opencode_run, list, status, send, stop | Owner-only for run/stop; CLI must be installed. |
| Delegation | local_delegate_suggest, assemble, execute, eta | PARTIAL — OpenCode/Codex verified; other platforms declared but not day-to-day tested. |
| Onboarding | local_user_onboarding_get_questions, answer | First-session Q&A persisted to profile. |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Bot takes ~30 s to join voice on first connect | Normal. Discord's voice network can reject the first few handshakes. Wait it out; do not restart the gateway repeatedly. |
| Bot joins but does not speak | Check gateway logs, sidecar health, and that your Gemini API key/model support native audio. Try the fallback models. |
| Tool returns auth error | That tool needs external auth (Spotify, Gmail, Home Assistant, GitHub). Configure it or use auth-free tools. |
| Sidecar health fails | The bridge is not running or the gateway stopped. Re-run `/voice-live` and check logs. |
| SORA tools missing | Run `python3 installer/enable_sora_bridge_elements.py` and restart the gateway. |

Full edge cases are in [`docs/KNOWN_BUGS.md`](docs/KNOWN_BUGS.md).

---

## Validation matrix

See [`VALIDATION_MATRIX.md`](VALIDATION_MATRIX.md) for the full component-by-component status table (Gemini Live, Discord voice, sidecar API, SORA elements, Vapi, MCP, Dograh, tool families, profiles).

---

## Roadmap

- **v2 (now)** — Honest status tags, SORA helper import, refreshed docs.
- **Next** — Vapi bridge federation, MCP server surface.
- **Later** — Multi-channel party mode, persistent cross-call memory, Dograh agent loop.

---

## License

MIT. Bundled `voice-live-typing.wav` is from Mixkit (CC0-equivalent).

---

## Credits

- Discord voice protocol — [discord.py](https://github.com/Rapptz/discord.py) + [discord-ext-voice-recv](https://github.com/imayhaveborkedit/discord-ext-voice-recv)
- Gemini Live — [Google AI Studio](https://aistudio.google.com)
- Honcho memory — [Honcho](https://github.com/plastic-labs/honcho)
- Hermes Agent — [Nous Research](https://nousresearch.com)
- Built and battle-tested at [capslock.nl](https://capslock.nl)
