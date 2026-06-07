# Environment variables

All `DISCORD_VOICE_LIVE_*` env vars the plugin reads. Defaults shown in **bold**.

## Required

| Var | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `GEMINI_API_KEY` | Google Gemini API key |
| `DISCORD_VOICE_LIVE_USER_ID` | Your Discord snowflake — the bridge listens to this user |

## Core

| Var | Default | Description |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.1-flash-live-preview` | Primary Gemini Live model |
| `GEMINI_LIVE_MODEL_FALLBACKS` | — | Comma-separated fallback models, tried in order if primary fails |
| `DISCORD_VOICE_LIVE_VOICE` | `en-US-JennyNeural` | TTS voice (criterion #3 — high-pitched female) |
| `DISCORD_VOICE_LIVE_PORT` | `18943` | Sidecar HTTP control port |
| `DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS` | empty | Comma-separated list of user IDs the bridge accepts audio from. Empty = listen to the channel. |
| `DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS` | `900` | Idle timeout (15 min) before the bridge auto-leaves |
| `DISCORD_VOICE_LIVE_AUTO_LEAVE_MIN_UPTIME_SECONDS` | `30` | Don't auto-leave within the first 30s of a session (avoids killing right after start) |
| `DISCORD_VOICE_LIVE_LEAVE_PHRASES` | (built-in list) | Phrases that trigger `/voice-live-leave` (e.g. "stop", "hang up", "bye", "exit voice") |
| `DISCORD_VOICE_LIVE_GREETING` | — | Optional greeting text on first turn |
| `DISCORD_VOICE_LIVE_CLEAR_ON_INTERRUPT` | `true` | When user interrupts the model, clear the audio queue |
| `DISCORD_VOICE_LIVE_NOTES_DIR` | `~/.hermes/voice-live-notes/` | Where to write per-call notes |
| `DISCORD_VOICE_LIVE_KEEP_AUTOSTART_FILE` | `false` | If true, the autostart file is not deleted after use |
| `DISCORD_VOICE_LIVE_AUTOSTART` | `false` | Auto-join the channel in `voice-live-autostart.json` on gateway boot |
| `DISCORD_VOICE_LIVE_AUTOSTART_FILE` | `~/.hermes/voice-live-autostart.json` | Path to the autostart file |
| `DISCORD_VOICE_LIVE_GUILD_ID` | — | Guild ID for autostart; required if autostart is enabled |
| `DISCORD_VOICE_LIVE_CHANNEL_ID` | — | Voice channel ID for autostart; required if autostart is enabled |

## Voice output

| Var | Default | Description |
|---|---|---|
| `GEMINI_AUDIO_STREAM_IDLE_END_SECONDS` | `0.25` | Time of audio silence before the model considers the user turn ended |
| `DISCORD_VOICE_LIVE_OUTPUT_PREROLL_MS` | `200` | Pre-roll audio before first byte lands in Discord |
| `DISCORD_VOICE_LIVE_OUTPUT_TAIL_PAD_MS` | `120` | Tail padding after last byte (prevents click on natural ends) |
| `DISCORD_VOICE_LIVE_OUTPUT_FADE_IN_MS` | `60` | Fade-in on each chunk (prevents click on joins) |
| `DISCORD_VOICE_LIVE_OUTPUT_READ_WAIT_SECONDS` | `0.05` | How long `LiveAudioSource.read()` blocks waiting for the next chunk |

## Idle prompts

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS` | `120` | Seconds of inactivity before the model generates a nudge |
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS` | `30` | Initial grace period after session start before nudging |
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_TEXT` | (built-in) | The nudge prompt injected after idle timeout |

## SFX library

See `sfx-library.md` for full list. Highlights:

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_SFX_ENABLED` | `true` | Master enable |
| `DISCORD_VOICE_LIVE_SFX_DIR` | `~/.hermes/voice-users/sfx/` | Default sfx directory |
| `DISCORD_VOICE_LIVE_SFX_<SLOT>` | per-slot | Per-slot WAV path override |
| `DISCORD_VOICE_LIVE_SFX_<SLOT>_VOLUME` | per-slot | Per-slot volume (0.0-1.5) |

Slots: `TOOL_INIT`, `ERROR`, `NOTIFICATION`, `TRANSITION`.

## Typing sfx (legacy single-slot)

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_TYPING_SOUND` | `true` | Enable the keyboard click sfx on tool calls |
| `DISCORD_VOICE_LIVE_TYPING_SFX` | `~/.hermes/voice-live-typing.wav` | Path to the WAV |
| `DISCORD_VOICE_LIVE_TYPING_SFX_VOLUME` | `0.35` | Volume |
| `DISCORD_VOICE_LIVE_TYPING_SYNTH_FALLBACK` | `false` | If true and the WAV is missing, generate a synthetic click instead of going silent |

## Video

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_VIDEO_ENABLED` | `true` | Allow video frame input |
| `DISCORD_VOICE_LIVE_VIDEO_MAX_FPS` | `1.0` | Max frames per second |
| `DISCORD_VOICE_LIVE_VIDEO_MAX_BYTES` | `524288` | Max JPEG size (default 512 KB) |
| `DISCORD_VOICE_LIVE_VIDEO_INITIALIZED_QUIET_THRESHOLD_S` | `30` | Seconds of silence before a "video initialized" event fires |
| `DISCORD_VOICE_LIVE_VIDEO_STATE_DETECTION` | `true` | Auto-react to video enable/disable (criterion #4) |
| `DISCORD_VOICE_LIVE_VIDEO_STATE_POLL_INTERVAL_SECONDS` | `2.0` | Poll interval for video state changes |
| `DISCORD_VOICE_LIVE_VIDEO_WHEN_RECENT_AUDIO_SECONDS` | `5` | Don't fire video events when audio is recent |

## Tool enable/disable

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_LOCAL_TOOLS` | `true` | All local tools (umbrella) |
| `DISCORD_VOICE_LIVE_WEB_TOOLS` | `true` | Web search / extract |
| `DISCORD_VOICE_LIVE_SPOTIFY_TOOLS` | `true` | Spotify playback |
| `DISCORD_VOICE_LIVE_GITHUB_TOOLS` | `true` | GitHub repo / issue / PR tools |
| `DISCORD_VOICE_LIVE_HA_TOOLS` | `true` | Home Assistant |
| `DISCORD_VOICE_LIVE_OPENCODE_TOOLS` | `true` | Opencode delegation |
| `DISCORD_VOICE_LIVE_SYSINSPECT_TOOLS` | `true` | System inspection |
| `DISCORD_VOICE_LIVE_EMAIL_TOOLS` | `true` | Email read / send / reply / brief |

## Webhooks

See `webhooks.md`. One env var per event class, all start with `DISCORD_VOICE_LIVE_WEBHOOK_<CLASS>`.

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_WEBHOOK_THROTTLE_SECONDS` | `60` | Default throttle window when `throttle_key` is set |

## Email brief

See `email-brief.md`.

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_EMAIL_BRIEF_ENABLED` | `true` | Enable the scheduler |
| `DISCORD_VOICE_LIVE_EMAIL_BRIEF_INTERVAL_SECONDS` | `1800` | 30 min default |
| `DISCORD_VOICE_LIVE_EMAIL_BRIEF_LIMIT` | `8` | Max emails per brief |

## Per-email reminder loop

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_EMAIL_REMINDER_ENABLED` | `true` | Enable per-email pings |
| `DISCORD_VOICE_LIVE_EMAIL_REMINDER_POLL_SECONDS` | `120` | 2 min poll interval |
| `DISCORD_VOICE_LIVE_EMAIL_REMINDER_MAX_PER_HOUR` | `3` | Cap pings per hour to avoid spam |

## Notification system

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_NOTIFY_TIMEOUT` | `10` | HTTP timeout for webhook delivery (s) |

## Honcho integration

| Var | Default | Description |
|---|---|---|
| `VOICE_LIVE_HONCHO_CONTEXT` | `true` | Inject Honcho context into the system prompt |
| `VOICE_LIVE_HONCHO_MAX_CHARS` | `1200` | Cap Honcho context block size |
| `VOICE_LIVE_HONCHO_PEER` | (user_id) | Override the Honcho peer name |

## Opencode delegation

| Var | Default | Description |
|---|---|---|
| `OPENCODE_BIN` | `~/.local/bin/opencode` | Path to opencode binary |
| `OPENCODE_DEFAULT_MODEL` | (opencode default) | Model passed to opencode |
| `OPENCODE_TMUX_SESSION` | `voice-opencode` | Tmux session name |
| `DISCORD_VOICE_LIVE_OPENCODE_WATCHER` | `true` | Watch opencode tmux sessions for status changes |
| `DISCORD_VOICE_LIVE_OPENCODE_WATCHER_POLL_SECONDS` | `2.0` | Poll interval |
| `DISCORD_VOICE_LIVE_OPENCODE_WATCHER_INITIAL_DELAY_SECONDS` | `5` | Delay before first poll after session start |
| `DISCORD_VOICE_LIVE_OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS` | `10` | Minimum gap between narrations to avoid spam |

## Misc

| Var | Default | Description |
|---|---|---|
| `VOICE_USERS_DIR` | `~/.hermes/voice-users/` | Per-user profile directory |
| `VOICE_OWNER_DISCORD_ID` | (env) | Used for owner-only commands |
| `HERMES_PYTHON` | `python3` | Python interpreter for subprocess calls |
| `GOOGLE_API_BIN` | (auto-detected) | Path to `google_api.py` for email + Google Workspace |
| `HASS_URL` | `http://homeassistant.local:8123` | Home Assistant base URL |
| `HASS_TOKEN` | — | Home Assistant long-lived access token |
