# Quick start

Five commands, two minutes.

## Install

```bash
# 1. Clone
git clone https://github.com/Capslockb/hermes-live-discord-agent-plugin.git
cd hermes-live-discord-agent-plugin

# 2. Install this checkout — prompts for DISCORD_BOT_TOKEN, GEMINI_API_KEY, your Discord user ID
./install.sh --from-local

# 3. Restart the gateway so the plugin loads
systemctl --user restart hermes-gateway
```

Use `--from-local` after cloning. Plain `./install.sh` ignores the current checkout and uses the installer's configured remote clone target; correction of that executable path is under review in [PR #7](https://github.com/Capslockb/hermes-live-discord-agent-plugin/pull/7).

## First session

From Discord, join a voice channel, then in any text channel:

```
/voice-live          # join
/voice-live-leave    # leave
```

The bridge will:

1. Ask Discord to connect to your voice channel with reconnect enabled and a 60-second connection timeout.
2. Handshake with Gemini Live.
3. Play the `transition` sfx.
4. Wait for you to speak — first-turn output is suppressed by an `audioStreamEnd` signal.

Startup duration depends on Discord and network conditions. The current code does not establish that the first five handshakes always fail or that every connection succeeds after roughly 27–30 seconds.

## Verify

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

After a successful session starts, the response should show `"voice_connected": true` and `"running": true`. `audio_in_chunks` should become non-zero after accepted voice audio is received.

## Common pitfalls

- **"Bridge failed to start"** — allow the command to finish its connection attempt, then inspect `journalctl --user -u hermes-gateway -n 100 --no-pager`. The bridge returns failure if `channel.connect()` or the Gemini handshake raises; it does not guarantee a later automatic success.
- **First-turn hallucination** ("I see you're sharing your screen") — the current bridge sends `audioStreamEnd` immediately after the Gemini setup completes. Check the gateway log for `sent initial mute audioStreamEnd`; avoid relying on a source-code line-number check.
- **No audio in voice** — check `~/.hermes/voice-users/sfx/` exists and the four WAV files are present.
- **No video/frame input** — the bundled frame clients are currently blocked by the CLI and authentication defects tracked in [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).

## Next

- [Architecture](architecture.html) — understand the audio path and threading model.
- [Environment variables](env-vars.html) — every `DISCORD_VOICE_LIVE_*` env var.
- [Troubleshooting](troubleshooting.html) — what to do when it doesn't work.