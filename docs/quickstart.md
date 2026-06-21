# Quick start

Install the Gemini Live Discord voice bridge, wire SORA bridge elements if needed, restart Hermes, then start `/voice-live` from Discord.

## Install

```bash
# 1. Clone
git clone https://github.com/Capslockb/hermes-live-discord-agent-plugin.git
cd hermes-live-discord-agent-plugin

# 2. Install — prompts for DISCORD_BOT_TOKEN, GEMINI_API_KEY, your Discord user ID
cd installer
./install.py
# or, from the repo checkout:
./install.sh --from-local

# 3. Wire SORA helper tools if the deployed entrypoint is not already patched
cd ..
python3 installer/enable_sora_bridge_elements.py
python3 -m py_compile plugin/sora_bridge_elements.py plugin/__init__.py

# 4. Restart the gateway so the plugin loads
systemctl --user restart hermes-gateway
```

## First session

From Discord, join a voice channel, then in any text channel:

```text
/voice-live          # join
/voice-live-leave    # leave
```

The bridge will:

1. Connect to your voice channel.
2. Handshake with Gemini Live.
3. Stream Discord audio to Gemini and Gemini audio back to Discord.
4. Wait for you to speak; first-turn silence is intentional.

## Verify

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

You should see bridge health after `/voice-live` has started a session. If the bridge is not running yet, the sidecar health request can fail normally.

## Verify SORA bridge elements

```text
sora_bridge_preflight
sora_redact text="Authorization: Bearer fake.fake.fake"
sora_live_grill text="migrate SORA bridge features into Gemini bridge"
sora_goal_synth text="migrate SORA bridge features into Gemini bridge"
```

If those tools are unavailable, run the patcher again in the deployed plugin directory and restart Hermes:

```bash
python3 installer/enable_sora_bridge_elements.py
grep -n "SORA bridge elements" plugin/__init__.py
systemctl --user restart hermes-gateway
```

## Common pitfalls

- **`/voice-live` cannot infer your channel** — join a voice channel first with the configured `DISCORD_VOICE_LIVE_USER_ID` account.
- **`/health` fails** — start a session first or check `DISCORD_VOICE_LIVE_PORT`.
- **SORA tools missing** — the SORA module exists, but the runtime entrypoint may not be patched. Run `installer/enable_sora_bridge_elements.py`.
- **The model cannot see screenshare** — Discord bots do not automatically receive screenshare/camera video. Use a screenshot, `voice_live_frame`, or the frame feeder.
- **Optional tools fail** — mail, GitHub, Spotify, Home Assistant, and CLI delegation require their own local credentials/install.

## Next

- [Architecture](architecture.md) — understand the audio path and SORA helper layer.
- [SORA bridge elements](sora-bridge-elements.md) — preflight, grill, goal synthesis, redaction.
- [Release truth table](release-readiness.md) — working vs partial vs research claims.
- [Environment variables](env-vars.md) — every `DISCORD_VOICE_LIVE_*` env var.
- [Troubleshooting](troubleshooting.md) — what to do when it does not work.
