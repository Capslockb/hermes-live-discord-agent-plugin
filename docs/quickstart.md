# Quick start

Five commands, two minutes.

## Install

```bash
# 1. Clone
git clone https://github.com/Capslockb/hermes-live-discord-agent-plugin.git
cd hermes-live-discord-agent-plugin

# 2. Install — prompts for DISCORD_BOT_TOKEN, GEMINI_API_KEY, your Discord user ID
./install.sh

# 3. Restart the gateway so the plugin loads
systemctl --user restart hermes-gateway
```

## First session

From Discord, join a voice channel, then in any text channel:

```
/voice-live          # join
/voice-live-leave    # leave
```

That's it. The bridge will:

1. Connect to your voice channel (Discord CDN quirk: first attempt takes ~27s — this is normal, do not restart the gateway)
2. Handshake with Gemini Live
3. Play the `transition` sfx
4. Wait for you to speak — first turn is muted by design

## Verify

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

You should see `"voice_connected": true`, `"running": true`, and a non-zero `audio_in_chunks` after you speak.

## Common pitfalls

- **"Bridge failed to start"** — wait ~30s. The first 5 voice WebSocket handshakes are rejected by the Discord CDN; the bridge retries.
- **First-turn hallucination** ("I see you're sharing your screen") — the system prompt has the guard, but if you see this, the audioStreamEnd mute is missing. Check `bridge.py` for `await self._gemini._ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))` right after `connect()`.
- **No audio in voice** — check `~/.hermes/voice-users/sfx/` exists and the four WAV files are present.

## Next

- [Architecture](architecture.html) — understand the audio path and threading model.
- [Environment variables](env-vars.html) — every `DISCORD_VOICE_LIVE_*` env var.
- [Troubleshooting](troubleshooting.html) — what to do when it doesn't work.
