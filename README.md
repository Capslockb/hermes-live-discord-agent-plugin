# gemini-live-discord-bridge (discord-voice plugin)

Bidirectional Discord voice ↔ Gemini Multimodal Live API bridge. Voice input/output, tool execution, video frame feed, multi-CLI delegation with automatic fallback, proactive notifications, scheduled email digest, and a slot-based UI sound effects library.

## Quick start

```bash
# 1. Install
git clone https://github.com/Capslockb/gemini-live-discord-bridge.git
cd gemini-live-discord-bridge
./install.sh                 # full install (prompts for env)
./install.sh --from-local    # use the current working dir
./install.sh --uninstall     # remove

# 2. Restart the gateway
systemctl --user restart hermes-gateway

# 3. From Discord, run:
/voice-live          # join your current voice channel
/voice-live-leave    # leave
```

The installer handles venv, symlinks, env prompts, and SFX directory creation. See `install.sh` for details.

## Architecture

```
Discord Voice → Opus Decode → 48kHz PCM → 16kHz Mono → Gemini WSS → Model → Gemini WSS → 24kHz PCM → 48kHz Stereo → Discord AudioSource
```

Lies on `discord-ext-voice-recv` (audio RX) and Gemini Multimodal Live API (WSS). Full architecture doc: [`docs/architecture.md`](docs/architecture.md).

## Features

| Feature | Doc | What it does |
|---|---|---|
| **Voice I/O** | `docs/architecture.md` | Opus in/out, Gemini Live streaming, sidecar HTTP API on 18943 |
| **Personality system** | `docs/personality.md` | 14-section system prompt, ping-pong rhythm, boredom switch, vocal expression cap |
| **Multi-CLI delegation** | `docs/fallback-chain.md` | opencode / codex / gemini / numasec / hermes-api with health registry + automatic fallback |
| **Proactive notifications** | `docs/notification.md` | `local_notify` tool, scheduler, sidecar `/notify`, AFK DM pings |
| **Email brief** | `docs/email-brief.md` | Scheduled inbox digest, important/fyi/auto buckets, AFK delivery |
| **SFX library** | `docs/sfx-library.md` | 4 slots (tool_init / error / notification / transition), env-driven paths, `local_sfx_test` tool |
| **Webhooks** | `docs/webhooks.md` | 9 event classes, throttle keys, per-class env-var config |
| **Video awareness** | `docs/video.md` (TBD) | `/frame` HTTP endpoint, auto-react to video enable/disable |
| **Onboarding** | — | First-run Q&A for new users, persisted to `~/.hermes/voice-users/<id>.yaml` |
| **Honcho context** | — | Per-user peer memory injected into the system prompt |
| **GitHub tools** | — | 6 voice tools to manage repos / issues / PRs via the `gh` CLI |
| **Home Assistant** | — | Voice-driven HA control |
| **Spotify** | — | Play/pause/skip/search/volume via voice |

## Environment variables

The minimum required:

```bash
DISCORD_BOT_TOKEN=...
GEMINI_API_KEY=...
DISCORD_VOICE_LIVE_USER_ID=1474100257762578597   # your Discord snowflake
```

Full list of every `DISCORD_VOICE_LIVE_*` env var: [`docs/env-vars.md`](docs/env-vars.md).

## Sidecar HTTP control API

Runs on `127.0.0.1:18943`:

| Route | Method | Description |
|---|---|---|
| `/health` | GET | Bridge health JSON |
| `/frame` | POST | Send a JPEG/PNG frame (`?force=true` bypasses audio-gate) |
| `/stop` | GET | Stop the bridge |
| `/say` | GET | Inject text into Gemini (`?text=...`) |
| `/notes` | GET | Recent transcript events (`?limit=50`) |
| `/notify` | GET/POST | Proactive notification breakout (criterion #6) |

## Personality

The system prompt is a 14-section behavioral contract, not documentation. Each section addresses a specific regression. **Do not** add hedging like "be helpful and harmless" — the model interprets that as permission to revert to assistant defaults.

See [`docs/personality.md`](docs/personality.md) for the section index and how to edit.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — end-to-end audio path, threading, lifecycle
- [`docs/personality.md`](docs/personality.md) — system prompt shape and behavioral contracts
- [`docs/fallback-chain.md`](docs/fallback-chain.md) — multi-CLI delegation with health registry
- [`docs/notification.md`](docs/notification.md) — proactive notification breakout
- [`docs/email-brief.md`](docs/email-brief.md) — scheduled inbox digest
- [`docs/sfx-library.md`](docs/sfx-library.md) — slot-based UI sound effects
- [`docs/webhooks.md`](docs/webhooks.md) — event-class webhook fanout
- [`docs/env-vars.md`](docs/env-vars.md) — every env var, defaults, descriptions
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — common bridge failures

## CHANGELOG

See `CHANGELOG.md` for the full release history.
