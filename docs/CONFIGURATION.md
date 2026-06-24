# Configuration

All env vars are read from `~/.hermes/.env` (or the active Hermes profile). Secrets go in `.env`; non-secret settings can also live in `~/.hermes/config.yaml`.

Every variable below is annotated **required**, **recommended**, or **optional**. If a default is not listed, the feature is off until you configure it.

---

## Required

```bash
DISCORD_BOT_TOKEN=...
GEMINI_API_KEY=...                 # or GOOGLE_API_KEY
```

The installer validates both tokens live against Discord and Google before finishing.

---

## Model selection

```bash
GEMINI_MODEL=gemini-3.1-flash-live-preview
GEMINI_LIVE_MODEL_FALLBACKS=gemini-3.1-flash-live-preview,gemini-2.5-flash-native-audio-preview-12-2025,gemini-2.5-flash-native-audio-preview-09-2025
DISCORD_VOICE_LIVE_VOICE=Aoede     # Gemini TTS voice name
```

> **Note:** `mediaResolution` is intentionally not sent because current Gemini Live models reject it. Do not add it back without verifying model support.

---

## Networking / sidecar

```bash
DISCORD_VOICE_LIVE_PORT=18943                    # Gemini bridge sidecar (127.0.0.1 only) — WORKING
DISCORD_VOICE_LIVE_PORT_VAPI=18944               # Reserved for future Vapi bridge — PLANNED
DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS=             # comma-separated Discord user IDs; empty = allow all non-bot users (optional)
```

The sidecar HTTP server is **local-only** and is used by the SORA preflight tool. There is no Vapi listener on port 18944 yet.

---

## Auto-leave / idle

```bash
DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS=900
DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS=120
DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS=60
DISCORD_VOICE_LIVE_IDLE_PROMPT_TEXT=Are you still there?
```

Idle hangup is implemented as a two-phase prompt, then auto-leave if the channel remains quiet.

---

## Notes / transcripts

```bash
DISCORD_VOICE_LIVE_NOTES_DIR=~/.hermes/voice-live-notes
```

Transcripts are written locally as JSONL files. This path is also read by SORA preflight.

---

## Autostart

```bash
DISCORD_VOICE_LIVE_AUTOSTART_FILE=~/.hermes/voice-live-autostart.json
DISCORD_VOICE_LIVE_KEEP_AUTOSTART_FILE=true
```

The autostart JSON looks like:

```json
{"guild_id":"YOUR_GUILD_ID","channel_id":"YOUR_CHANNEL_ID","user_id":"YOUR_USER_ID"}
```

Replace all three values with your own Discord IDs. Do not commit this file.

---

## Webhooks

```bash
DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT=...
DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_STATUS=...
DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_TRANSCRIPT=...
DISCORD_VOICE_LIVE_WEBHOOK_EMAIL=...
DISCORD_VOICE_LIVE_WEBHOOK_BRIDGE_STATUS=...
DISCORD_VOICE_LIVE_WEBHOOK_TOOL_CALLED=...
DISCORD_VOICE_LIVE_WEBHOOK_THROTTLE_SECONDS=2
```

All webhook features are **optional**. Provide URLs only for the destinations you want.

---

## Per-user profiles

```bash
VOICE_OWNER_DISCORD_ID=                         # your Discord user ID (optional; used for owner-only tools)
VOICE_USERS_DIR=~/.hermes/voice-users
```

If `VOICE_OWNER_DISCORD_ID` is empty, owner-only tools are still declared but have no owner to gate against until this is set. Review your tool allowlists before exposing the bot to untrusted users.

---

## Video awareness

```bash
DISCORD_VOICE_LIVE_VIDEO_STATE_DETECTION=true
DISCORD_VOICE_LIVE_VIDEO_STATE_POLL_INTERVAL_SECONDS=3
DISCORD_VOICE_LIVE_VIDEO_MAX_BYTES=524288
```

Discord bots cannot receive the actual video stream. When a user turns on camera/screenshare, Gemini is notified that sharing is active and can ask the user to push a frame via `/frame` or `voice_live_frame`.

---

## Typing SFX

```bash
DISCORD_VOICE_LIVE_TYPING_SOUND=true
DISCORD_VOICE_LIVE_TYPING_SFX=~/.hermes/voice-live-typing.wav        # optional typing sound
DISCORD_VOICE_LIVE_TYPING_SFX_VOLUME=0.35
DISCORD_VOICE_LIVE_TYPING_SYNTH_FALLBACK=false
```

Adjust `TYPING_SFX` to the actual path of the bundled WAV on your machine.

---

## Opencode watcher

```bash
DISCORD_VOICE_LIVE_OPENCODE_WATCHER=true
DISCORD_VOICE_LIVE_OPENCODE_WATCHER_POLL_SECONDS=5
DISCORD_VOICE_LIVE_OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS=30
DISCORD_VOICE_LIVE_OPENCODE_WATCHER_INITIAL_DELAY_SECONDS=60
```

Watches OpenCode sessions and reads back status to the voice channel when there is a gap in conversation. Requires OpenCode on PATH.

---

## Email reminders

```bash
DISCORD_VOICE_LIVE_EMAIL_REMINDER_ENABLED=true
DISCORD_VOICE_LIVE_EMAIL_REMINDER_POLL_SECONDS=300
DISCORD_VOICE_LIVE_EMAIL_REMINDER_MAX_PER_HOUR=3
```

Requires a configured email integration.

---

## External integrations (enable only what you use)

### Spotify

```bash
SPOTIPY_CLIENT_ID=...
SPOTIPY_CLIENT_SECRET=...
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

The redirect URI is local-only; it is used during the Spotify OAuth flow.

### Gmail / Google API

```bash
GOOGLE_CLIENT_SECRET_FILE=...
GOOGLE_TOKEN_FILE=...
```

### Home Assistant

```bash
HASS_URL=...
HASS_TOKEN=...
```

### OpenCode / Codex

No env vars required. The bridge finds the binaries on PATH. If you installed them to a non-standard location, make sure the directory is in your shell PATH before starting the gateway.

---

## SORA bridge elements

```bash
DISCORD_VOICE_LIVE_NOTES_DIR=~/.hermes/voice-live-notes   # also used by preflight
VOICE_LIVE_HONCHO_CONTEXT=true                            # enable Honcho memory inspection (PARTIAL)
VOICE_LIVE_HONCHO_PEER=YOUR_HONCHO_PEER_ID                # Honcho peer alias
```

These are read by `sora_bridge_preflight`. The tools themselves need no extra env var to register. Honcho integration reads context at connect time; writing voice memories back is not yet implemented.

---

## Tuning tips

- If `channel.connect()` hangs for ~30 s on first call, wait. Discord's voice CDN can reject the first few handshakes before accepting. Do not repeatedly restart the gateway to "retry" — each restart resets the retry clock.
- Keep `DISCORD_VOICE_LIVE_VIDEO_MAX_BYTES` under your Gemini model's frame-size limit.
- If Gemini rejects the model name, fall back to `gemini-2.5-flash-native-audio-preview-12-2025`.
- To restrict who can trigger the bot, set `DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS` to a comma-separated list of Discord user IDs. Empty means all non-bot users.

---

## Full env list

For an exhaustive list of all declared variables, grep the plugin code:

```bash
grep -R "os.environ.get\|os.getenv" plugin/*.py | sort
```

The matrix in [`../VALIDATION_MATRIX.md`](../VALIDATION_MATRIX.md) tells you which configuration areas are WORKING, PARTIAL, PLANNED, or RESEARCH.
