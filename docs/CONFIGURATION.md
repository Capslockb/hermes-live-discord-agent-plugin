# Configuration

> Every env var the hermes-live-discord-agent-plugin understands.

All settings live in `~/.hermes/.env` (chmod 600). The installer writes/merges them automatically. After changing anything, restart the gateway:

```bash
systemctl --user restart hermes-gateway
```

## Required

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `DISCORD_BOT_TOKEN` | secret | `MTI0...X.Vw.A...` | Bot token from Discord Developer Portal. Must have Voice States intent enabled. |
| `GEMINI_API_KEY` | secret | `AIzaSy...` | From [aistudio.google.com/apikey](https://aistudio.google.com/apikey). |

## Model selection

| Key | Default | Notes |
|-----|---------|-------|
| `GEMINI_MODEL` | `models/gemini-2.5-flash-native-audio-preview-09-2025` | The Live multimodal model. Must support `realtimeInput`. |
| `GEMINI_LIVE_MODEL_FALLBACKS` | `models/gemini-2.0-flash-live-001,models/gemini-2.5-flash-live-preview` | Comma-separated. Tried in order if the primary fails the WSS handshake. |

## Networking

| Key | Default | Notes |
|-----|---------|-------|
| `DISCORD_VOICE_LIVE_PORT` | `18943` | Localhost HTTP control port. Bind stays on `127.0.0.1`. |

## Auto-leave

| Key | Default | Notes |
|-----|---------|-------|
| `DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS` | `900` | Hang up after this many seconds of silence. `0` disables auto-leave entirely. |
| `DISCORD_VOICE_LIVE_AUTO_LEAVE_MIN_UPTIME_SECONDS` | `120` | Don't auto-leave during the first N seconds of a call. Prevents accidentally hanging up if join takes long. |
| `DISCORD_VOICE_LIVE_LEAVE_PHRASES` | `leave voice,disconnect from voice,end voice,stop voice,leave the call,disconnect,goodbye hermes,bye,hang up,exit voice` | Comma-separated. If the model outputs any of these, the bridge leaves immediately. |

## Idle prompt ("Are you still there?")

| Key | Default | Notes |
|-----|---------|-------|
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS` | `300` | After this many seconds of silence, send the prompt. `0` disables the prompt and falls back to plain auto-leave. |
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS` | `60` | Wait this long for the user to respond. If they do, both timers reset. |
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_TEXT` | `Are you still there?` | What the assistant says. Inject into Gemini as a user turn. |

## Notes / transcripts

| Key | Default | Notes |
|-----|---------|-------|
| `NOTES_DIR` | `~/.hermes/voice-live-notes/` | Where the JSONL transcript is written. Created on first call if missing. |

## Autostart

Trigger the bridge to auto-join on gateway boot by writing a small JSON file:

```bash
mkdir -p ~/.hermes
cat > ~/.hermes/voice-live-autostart.json <<EOF
{
  "guild_id": "123456789012345678",
  "channel_id": "123456789012345678",
  "user_id": "123456789012345678"
}
EOF
chmod 600 ~/.hermes/voice-live-autostart.json
```

The file is **deleted on successful start** — recreate it to re-trigger. The vapi plugin uses a different file (`voice-vapi-autostart.json`) and won't conflict.

## Tuning tips

- **Lower latency** — keep `IDLE_PROMPT_SECONDS=0` if you don't want the prompt; auto-leave is faster.
- **Lower cost** — set `GEMINI_LIVE_MODEL_FALLBACKS` to cheaper fallbacks like `models/gemini-2.0-flash-live-001`.
- **More reliable hangup** — extend `LEAVE_PHRASES` to include natural disconnects: `goodbye,done,that's all,thanks bye`.
- **Multiple bots** — bind each gateway instance to a different `DISCORD_VOICE_LIVE_PORT` (e.g. 18943, 18953, 18963) and run them under separate Hermes profiles (`~/.hermes-bob/`, etc.).
