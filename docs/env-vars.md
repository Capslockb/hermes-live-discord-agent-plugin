# Environment variables

Environment variables read by the plugin. Defaults shown in **bold**.

## Required

| Var | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `GEMINI_API_KEY` | Google Gemini API key |
| `DISCORD_VOICE_LIVE_USER_ID` | Your Discord snowflake. It is required for slash-command channel inference and is also used as a default recipient by several background paths. The installer currently lets this prompt be skipped, while current runtime code falls back to a repository-embedded ID; set it explicitly and see [Issue #18](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/18). |

For a single-owner installation, also set `VOICE_OWNER_DISCORD_ID` explicitly before enabling owner-only profile tools. Current `main` otherwise falls back to a repository-embedded owner ID; this authorization boundary is tracked in [Issue #18](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/18).

Changing `VOICE_OWNER_DISCORD_ID` does not currently demote a profile already persisted with `is_owner: true`. Existing profile YAML is authorization state, not a cache. Issue #18 now selects the migration rule: preserve profile data, but recompute effective owner authorization at load time and grant owner-only capabilities only when the profile ID matches an explicitly configured `VOICE_OWNER_DISCORD_ID`. That runtime change is not implemented yet, so do not assume an environment-variable change revokes an existing grant on current `main`.

## Core

| Var | Default | Description |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.1-flash-live-preview` | Primary Gemini Live model |
| `GEMINI_LIVE_MODEL_FALLBACKS` | — | Comma-separated fallback models, tried in order if primary fails |
| `DISCORD_VOICE_LIVE_VOICE` | `Kore` | Gemini Live voice name |
| `DISCORD_VOICE_LIVE_PORT` | `18943` | Loopback sidecar HTTP control port |
| `DISCORD_VOICE_LIVE_SECRET_FILE` | `~/.hermes/voice-live-control-secret` | Current file used to load or persist the sidecar control secret. Current `main` reuses an existing value across restarts and applies mode `0600` only when creating a new file. Issue #17 selects replacement with an ephemeral process-scoped secret that rotates on every start and is handed only to trusted in-process clients; that security-sensitive change is not implemented yet. |
| `DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS` | empty | Comma-separated user IDs whose audio is accepted. Empty allows all non-bot speakers in the channel. |
| `DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS` | `900` | Idle timeout (15 min) before the bridge auto-leaves |
| `DISCORD_VOICE_LIVE_AUTO_LEAVE_MIN_UPTIME_SECONDS` | `120` | Minimum session uptime before auto-leave is allowed |
| `DISCORD_VOICE_LIVE_LEAVE_PHRASES` | (built-in list) | Phrases that trigger `/voice-live-leave` (e.g. "stop", "hang up", "bye", "exit voice") |
| `DISCORD_VOICE_LIVE_GREETING` | `I'm here.` | Initial greeting text configured for the bridge |
| `DISCORD_VOICE_LIVE_CLEAR_ON_INTERRUPT` | `true` | When user interrupts the model, clear the audio queue |
| `DISCORD_VOICE_LIVE_NOTES_DIR` | `~/.hermes/voice-live-notes/` | Where to write per-call notes |
| `DISCORD_VOICE_LIVE_KEEP_AUTOSTART_FILE` | `true` | If true, the autostart file is not deleted after use |
| `DISCORD_VOICE_LIVE_AUTOSTART` | `false` | Auto-join the channel in `voice-live-autostart.json` on gateway boot |
| `DISCORD_VOICE_LIVE_AUTOSTART_FILE` | `~/.hermes/voice-live-autostart.json` | Path to the autostart file |
| `DISCORD_VOICE_LIVE_GUILD_ID` | — | Guild ID for autostart; required if autostart is enabled |
| `DISCORD_VOICE_LIVE_CHANNEL_ID` | — | Voice channel ID for autostart; required if autostart is enabled |

## Voice output

| Var | Default | Description |
|---|---|---|
| `GEMINI_AUDIO_STREAM_IDLE_END_SECONDS` | `0.25` | Time of audio silence before the model considers the user turn ended |
| `DISCORD_VOICE_LIVE_OUTPUT_PREROLL_MS` | `320` | Pre-roll audio before first byte lands in Discord |
| `DISCORD_VOICE_LIVE_OUTPUT_TAIL_PAD_MS` | `240` | Tail padding after last byte (prevents click on natural ends) |
| `DISCORD_VOICE_LIVE_OUTPUT_FADE_IN_MS` | `0` | Fade-in applied to output chunks; disabled by default |
| `DISCORD_VOICE_LIVE_OUTPUT_READ_WAIT_SECONDS` | `0.005` | How long `LiveAudioSource.read()` blocks waiting for the next chunk |

## Idle prompts

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS` | `120` | Seconds of inactivity before the model generates a nudge |
| `DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS` | `60` | Initial grace period after session start before nudging |
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
| `DISCORD_VOICE_LIVE_TYPING_SFX` | empty | Optional WAV path; an empty value does not select `~/.hermes/voice-live-typing.wav` automatically |
| `DISCORD_VOICE_LIVE_TYPING_SFX_VOLUME` | `0.35` | Volume |
| `DISCORD_VOICE_LIVE_TYPING_SYNTH_FALLBACK` | `false` | If true and the WAV is missing, generate a synthetic click instead of going silent |

## Video

These variables configure server-side frame handling. They do not make either current frame client operational; see [`video.md`](video.md) and [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_VIDEO_MAX_FPS` | `1` | Server-side cap on accepted feeder frame rate |
| `DISCORD_VOICE_LIVE_VIDEO_MAX_BYTES` | `524288` | Per-frame JPEG size cap (512 KB) |
| `DISCORD_VOICE_LIVE_VIDEO_WHEN_RECENT_AUDIO_SECONDS` | `8` | Drop frames if no voice activity occurred in the last N seconds |
| `DISCORD_VOICE_LIVE_VIDEO_INITIALIZED_QUIET_THRESHOLD_S` | `30` | Webhook announce fires only when a frame is accepted after at least this many seconds of silence |
| `DISCORD_VOICE_LIVE_VIDEO_ENABLED` | `true` | Enable server-side video frame input |
| `DISCORD_VOICE_LIVE_VIDEO_STATE_DETECTION` | `true` | Auto-react to video enable/disable |
| `DISCORD_VOICE_LIVE_VIDEO_STATE_POLL_INTERVAL` | `5` | Poll interval in seconds for video state changes. The implementation does not read the older `_SECONDS` spelling. |

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

See [`webhooks.md`](webhooks.md) for the exact event classes, delivery semantics, and privacy boundary.

| Var | Default | Description |
|---|---|---|
| `DISCORD_VOICE_LIVE_WEBHOOK_THROTTLE_SECONDS` | `2` | Shared throttle window for events with a built-in or explicit throttle key |

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
| `DISCORD_VOICE_LIVE_NOTIFY_TIMEOUT` | `5` | Timeout used by sidecar notification HTTP calls and synchronous Discord send waits; it is not the webhook dispatch timeout |

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
| `VOICE_OWNER_DISCORD_ID` | repository-embedded ID | Identifies the account that currently receives `is_owner=true` and owner-only tools. Set this explicitly; the current executable fallback is unsafe. Issue #18 selects removal of the fallback and load-time recomputation so persisted `is_owner` state is effective only for a profile whose ID matches the explicit configured owner; that runtime migration is not implemented yet. |
| `HERMES_PYTHON` | `python3` | Python interpreter for subprocess calls |
| `HASS_URL` | `http://homeassistant.local:8123` | Home Assistant base URL |
| `HASS_TOKEN` | — | Home Assistant long-lived access token |

`GOOGLE_API_BIN` is not currently read as an environment variable. `email_brief.py` constructs the Google Workspace helper path under `~/.hermes/hermes-agent/skills/productivity/google-workspace/scripts/google_api.py`, regardless of `HERMES_HOME`. Custom Hermes roots and alternate helper installations therefore remain unsupported without a runtime change; see [Issue #24](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/24).