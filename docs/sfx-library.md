# SFX library — multi-slot UI sound effects

A small slot-based system for playing UI sound effects through a registered voice output source. Each slot maps to a 24 kHz mono PCM16 WAV file and is invoked from specific bridge events.

> **Media boundary:** the current tree still contains four legacy WAV files whose redistribution rights are not established. Issue [#16](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/16) records the accepted direction: remove files without auditable permission and keep SFX optional through operator-supplied original or explicitly licensed assets. Until that reviewed media/installer change lands, do not redistribute or treat the bundled files as licensed project assets; configure replacement paths or set `DISCORD_VOICE_LIVE_SFX_ENABLED=false`.

## The four slots

| Slot | Triggered by | Typical sound |
|---|---|---|
| `tool_init` | First local tool call after the gateway process starts | Soft chime — "I'm ready to work" |
| `error` | Uncaught exception escaping `_run_local_tool`'s inner dispatch | Sharp beep — "something went wrong" |
| `notification` | Completion of a `local_notify` attempt, regardless of confirmed delivery | Light ping — "a notification path ran" |
| `transition` | Session start (after `vc.play()` succeeds) | Pop/swoosh — "we're connected" |

The `tool_init` guard is stored on the module-level `_run_local_tool` function as `_tool_init_played`. It is process-global and is not reset for each Discord user or voice session; restarting the gateway resets it.

A notification sound is not a delivery receipt. The current code calls `play_sfx("notification")` after `_notify_deliver()` returns without requiring a successful, acknowledged, or subscriber-backed result.

## File layout

Current runtime default directory: `~/.hermes/voice-users/sfx/`

```text
~/.hermes/voice-users/sfx/
├── tool_init.wav
├── error.wav
├── notification.wav
└── transition.wav
```

These filenames are runtime slots, not a grant to use or redistribute the legacy files currently present in the repository. Populate them only with assets you created or are explicitly licensed to use and redistribute where applicable.

All four files are **24 kHz mono PCM16**. The loader auto-resamples if you give it a different format, but cutting directly to the target format keeps the loader's resample path simple.

## Legacy bundled-file provenance

The files currently checked into the repository were cut from a YouTube playlist ("UI Sound Effects for App & Game Development" by Brand Name Audio) using `ffmpeg silencedetect=noise=-30dB:d=0.2`. That provenance does not establish redistribution permission. See [`sfx-credits.md`](sfx-credits.md) and Issue [#16](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/16) for the rights boundary and accepted removal direction.

Do not re-cut, repackage, publish, or release those source-derived clips without auditable permission. For new slots, use original or explicitly redistributable media.

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
DISCORD_VOICE_LIVE_SFX_ENABLED=true    # current runtime default
```

Set this to `false` when no approved operator-supplied assets are configured.

Global SFX directory (overrides the default `~/.hermes/voice-users/sfx/`):

```bash
DISCORD_VOICE_LIVE_SFX_DIR=/custom/sfx/dir
```

## `local_sfx_test` tool

The agent can ask the SFX module to play a slot through its implicit active-source registry:

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

With an empty registry, playback returns `{"status": "no_active_source"}`. That result does not reliably prove that no Discord voice session is active, because the registry's current lifecycle and selection behavior is defective.

## Current session-routing limitation

`GeminiLiveBridge` registers each output source under a session identifier, but the registry currently stores both a weak reference and a strong reference to the same source. The strong reference prevents the advertised garbage-collection cleanup, and bridge disconnect does not explicitly unregister the entry.

`pick_active_source()` also returns the first live dictionary entry; it does not track or select the most recently active session. `local_sfx_test`, `tool_init`, `notification`, and local-tool error paths call `play_sfx()` without an explicit source. In a multi-user process, or after a stale source remains registered, a sound can therefore be fed to an older or unrelated voice session.

Until [Issue #15](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/15) is fixed:

- treat implicit SFX playback as single-active-session only;
- do not use a sound as evidence that a notification reached its intended recipient;
- restart the gateway to clear stale in-memory source registrations;
- avoid `local_sfx_test` while multiple voice sessions are active.

## Adding a new slot

1. Add an original or explicitly licensed `sfx_<slot>.wav` file to the SFX directory.
2. In `sfx.py`, add the slot name to `DEFAULT_SFX_PATHS` and `DEFAULT_SFX_VOLUMES`.
3. Call `play_sfx("<slot>")` from the bridge event you want it to fire on.
4. Add the slot name to the `local_sfx_test` tool declaration enum.

No need to restart the gateway for step 1 if the cache is invalidated, but steps 2-4 require a gateway restart.

## Cache invalidation

If you swap a WAV file but keep the same path, the in-memory cache still holds the old bytes. Two ways to invalidate:

- Restart the gateway
- Call `invalidate_cache()` (currently no public tool for this; add `local_sfx_invalidate` if needed)
