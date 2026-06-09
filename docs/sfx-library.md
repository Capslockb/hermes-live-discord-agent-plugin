# SFX library — multi-slot UI sound effects

A small slot-based system for playing UI sound effects into the active voice session. Each slot maps to a 24kHz mono PCM16 WAV file and fires on a specific bridge event.

## The four slots

| Slot | Triggered by | Typical sound |
|---|---|---|
| `tool_init` | First tool call of a session (one-shot per session) | Soft chime — "I'm ready to work" |
| `error` | Uncaught exception in `_run_local_tool` | Sharp beep — "something went wrong" |
| `notification` | Successful `local_notify` delivery (incl. email brief) | Light ping — "you have a message" |
| `transition` | Session start (after `vc.play()` succeeds) | Pop/swoosh — "we're connected" |

The `tool_init` sfx uses a one-shot guard (`_run_local_tool._tool_init_played`) so it doesn't replay on every tool call.

## File layout

Default directory: `~/.hermes/voice-users/sfx/`

```
~/.hermes/voice-users/sfx/
├── tool_init.wav       # chime (e.g. UI Notification Chimes Pack, first chime)
├── error.wav           # 4x chain of a sharp beep (~2.8s total)
├── notification.wav    # mobile-OS style ping
└── transition.wav      # pop / whoosh
```

All four files are **24 kHz mono PCM16**. The loader auto-resamples if you give it a different format, but cutting directly to the target format keeps the loader's resample path simple.

## Where the clips came from

Cut from a YouTube playlist ("UI Sound Effects for App & Game Development" by Brand Name Audio) using `ffmpeg silencedetect=noise=-30dB:d=0.2`. See `silencedetect` log lines for the `silence_end` timestamps that anchor each cut.

To re-cut or add new slots, see the recipe in `silence-detect-sfx-cutting` skill.

## Environment variables

Per-slot override (path):

```bash
DISCORD_VOICE_LIVE_SFX_TOOL_INIT=/path/to/custom_chime.wav
DISCORD_VOICE_LIVE_SFX_ERROR=/path/to/custom_beep.wav
DISCORD_VOICE_LIVE_SFX_NOTIFICATION=/path/to/custom_ping.wav
DISCORD_VOICE_LIVE_SFX_TRANSITION=/path/to/custom_pop.wav
```

Per-slot volume (0.0 to 1.5, where 1.0 = no scaling):

```bash
DISCORD_VOICE_LIVE_SFX_TOOL_INIT_VOLUME=0.55
DISCORD_VOICE_LIVE_SFX_ERROR_VOLUME=0.45
DISCORD_VOICE_LIVE_SFX_NOTIFICATION_VOLUME=0.50
DISCORD_VOICE_LIVE_SFX_TRANSITION_VOLUME=0.60
```

Global enable:

```bash
DISCORD_VOICE_LIVE_SFX_ENABLED=true    # default
```

Global SFX directory (overrides the default `~/.hermes/voice-users/sfx/`):

```bash
DISCORD_VOICE_LIVE_SFX_DIR=/custom/sfx/dir
```

## `local_sfx_test` tool

The agent can play any slot in the current voice session:

```json
// play a slot
{"slot": "notification"}

// inspect all configured slots
{"action": "list"}
```

Returns:
```json
// play result
{"result": {"status": "played", "slot": "notification", "bytes": 33600, "duration_s": 0.7}}

// list result
{"result": {"slots": {
  "tool_init":    {"path": "...", "exists": true,  "volume": 0.55, "cached_bytes": 33600},
  "error":        {"path": "...", "exists": true,  "volume": 0.45, "cached_bytes": 134400},
  "notification": {"path": "...", "exists": true,  "volume": 0.50, "cached_bytes": 33600},
  "transition":   {"path": "...", "exists": true,  "volume": 0.60, "cached_bytes": 33600}
}}}
```

If no voice session is active, returns `{"status": "no_active_source"}` — the sfx library is voice-only.

## Adding a new slot

1. Add a `sfx_<slot>.wav` file to the sfx dir
2. In `sfx.py`, add the slot name to `DEFAULT_SFX_PATHS` and `DEFAULT_SFX_VOLUMES`
3. Call `play_sfx("<slot>")` from the bridge event you want it to fire on
4. Add the slot name to the `local_sfx_test` tool declaration enum

No need to restart the gateway for step 1 if the cache is invalidated, but steps 2-4 require a gateway restart.

## Cache invalidation

If you swap a WAV file but keep the same path, the in-memory cache still holds the old bytes. Two ways to invalidate:

- Restart the gateway
- Call `invalidate_cache()` (currently no public tool for this; add `local_sfx_invalidate` if needed)
