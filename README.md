# gemini-live-discord-bridge (discord-voice plugin)

Bidirectional Discord voice ↔ Gemini Multimodal Live API bridge. Voice input/output, tool execution, and video frame feed.

## Architecture

```
Discord Voice → Opus Decode → 48kHz PCM → 16kHz Mono → Gemini WSS → Model → Gemini WSS → 24kHz PCM → 48kHz Stereo → Discord AudioSource
```

Lies on `discord-ext-voice-recv` (audio RX) and Gemini Multimodal Live API (WSS).

## Setup

```bash
# Dependencies
pip install -r requirements.txt

# Env (in ~/.hermes/.env or shell)
export DISCORD_BOT_TOKEN=...
export GEMINI_API_KEY=...
export DISCORD_VOICE_LIVE_WEBHOOK_VIDEO=https://discord.com/api/webhooks/...   # optional

# The plugin auto-loads when Hermes gateway starts. Use:
/voice-live          # join your current voice channel
/voice-live-leave    # leave
```

## Presets

| Env | Default | Description |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.1-flash-live-preview` | Gemini Live model name |
| `DISCORD_VOICE_LIVE_VOICE` | `Aoede` | Gemini voice (Aoede, Charon, Puck, Fenrir) |
| `DISCORD_VOICE_LIVE_PORT` | `18943` | Local control API port |
| `DISCORD_VOICE_LIVE_USER_ID` | B's snowflake | Who the bridge listens to |
| `DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS` | `900` | Idle timeout before auto-leave |
| `DISCORD_VOICE_LIVE_VIDEO_ENABLED` | `true` | Allow video frame input |
| `DISCORD_VOICE_LIVE_VIDEO_MAX_FPS` | `1.0` | Max video frames per second |
| `DISCORD_VOICE_LIVE_WEBHOOK_VIDEO` | — | Webhook URL for video-initialized announces |
| `DISCORD_VOICE_LIVE_VIDEO_INITIALIZED_QUIET_THRESHOLD_S` | `30` | Seconds of silence before video announce fires |

## Control API

Runs on `127.0.0.1:18943`:

| Route | Method | Description |
|---|---|---|
| `/health` | GET | Bridge health JSON |
| `/frame` | POST | Send a JPEG/PNG frame (`?force=true` bypasses audio-gate) |
| `/stop` | GET | Stop the bridge |
| `/say` | GET | Inject text into Gemini (`?text=...`) |
| `/notes` | GET | Recent transcript events (`?limit=50`) |

## Video frame feeding

The bridge accepts video frames via the `/frame` HTTP endpoint. Use the
standalone `video-frame-feeder.py` (in `~/.hermes/voice-video-research/`) to
capture your screen and POST frames:

```bash
python ~/.hermes/voice-video-research/video-frame-feeder.py
```

## Webhooks

Event classes (set `DISCORD_VOICE_LIVE_WEBHOOK_<CLASS>`):

| Class | Env var | Fires on |
|---|---|---|
| `voice.transcript` | `DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT` | Every voice input/output line |
| `bridge.status` | `DISCORD_VOICE_LIVE_WEBHOOK_BRIDGE_STATUS` | Bridge start/stop |
| `bridge.video` | `DISCORD_VOICE_LIVE_WEBHOOK_VIDEO` | First frame after ≥30s silence |
| `opencode.status` | `DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_STATUS` | Opencode lifecycle |
| `email.sent` | `DISCORD_VOICE_LIVE_WEBHOOK_EMAIL` | Email sent via voice |
| `tool.called` | `DISCORD_VOICE_LIVE_WEBHOOK_TOOL_CALLED` | Any tool invocation (sampled, throttled) |

## User-presence gates

- **Pre-start**: `/voice-live` checks that the user is in the voice channel before connecting
- **Runtime watchdog**: if the user leaves or moves channels, the bridge stops within 1s
- **First-turn mute**: `audioStreamEnd` sent immediately after Gemini setup to suppress first-turn token burn
