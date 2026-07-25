# Architecture

End-to-end audio path, concurrency model, and lifecycle of the Discord voice bridge.

## Audio path

```
Discord Voice (Opus)
    ↓  discord-ext-voice-recv decode
48 kHz PCM stereo (16-bit)
    ↓  GeminiPCMSink conversion
16 kHz PCM mono
    ↓  GeminiLiveBridge queue + WebSocket realtimeInput
Gemini Multimodal Live API (WSS)
    ↓  server audio
24 kHz PCM mono (PCM16)
    ↓  LiveAudioSource conversion
48 kHz PCM stereo
    ↓  discord.AudioSource
Discord Voice (Opus encode)
```

The bridge constants are `DISCORD_SR=48000`, `GEMINI_IN_SR=16000`, and `GEMINI_OUT_SR=24000`. Input is not first converted to 24 kHz and then downsampled inside `GeminiLiveBridge`; `_feed_audio()` already receives 16 kHz mono PCM from `GeminiPCMSink`.

## Concurrency model

There is no fixed three-thread topology.

| Execution context | Owner | Purpose |
|---|---|---|
| **Gateway asyncio loop** | `discord.py` and plugin tasks | Slash/tool handlers, bridge lifecycle, Discord voice state, Gemini WebSocket tasks, watchdogs, and control-server coroutines. |
| **Discord audio thread** | `discord.py` voice playback | Calls `LiveAudioSource.read()`. Cross-thread audio queues must therefore use `threading.Queue`, not `asyncio.Queue`. |
| **Default executor workers** | `asyncio.run_in_executor()` | Run blocking local, web, Spotify, GitHub, system-inspection, notification, and other synchronous operations. Multiple calls may occupy separate executor workers. |
| **Scheduler threads** | notification and email-brief modules | Poll scheduled work in daemon threads when those schedulers are enabled. |
| **Timers/background tasks** | bridge modules | Handle reconnect backoff, idle prompting, playback, video-state polling, and other delayed work. |

A tool worker is not one dedicated long-lived thread. Each blocking tool call is submitted to the event loop's executor, whose size and queueing behavior are controlled by the Python runtime or host application.

## Lifecycle

1. **A caller invokes `/voice-live`, the `voice_live` tool, or autostart.**
2. `__init__.py:voice_live()` resolves the Discord adapter, target guild/channel, and effective configured user. It rejects startup when the target user can be verified as absent from the requested voice channel.
3. A stale disconnected entry in `_active_bridges` is cancelled and removed before a fresh start.
4. `bridge.run_sidecar()` is spawned as an asyncio task and reports readiness through a future.
5. `VoiceLiveBridge.start()`:
   - Disconnects an existing guild voice client when necessary.
   - Calls `channel.connect(cls=VoiceRecvClient, timeout=60.0, reconnect=True, self_deaf=False)` once.
   - Creates `GeminiPCMSink` and `LiveAudioSource`, then starts Discord receive and playback.
   - Plays the `transition` sfx on a best-effort basis.
   - Connects to Gemini Live and waits for `setupComplete`.
   - Sends an initial `audioStreamEnd` signal on a best-effort basis to suppress unsolicited first-turn output.
6. The bridge marks itself running and starts a one-second connection watchdog.
7. The watchdog stops the bridge when Discord disconnects or the configured target user leaves the channel. Its normal idle flow first sends `IDLE_PROMPT_TEXT` after `IDLE_PROMPT_SECONDS`, then stops after `IDLE_PROMPT_GRACE_SECONDS` without renewed activity. The plain `AUTO_LEAVE_QUIET_SECONDS` path is the fallback when idle prompting is disabled.
8. **A caller invokes `/voice-live-leave` or `voice_live_leave`.** The active task is cancelled and the voice client is disconnected.
9. `VoiceLiveBridge.stop()` stops receive/playback, disconnects Gemini and Discord, and resumes the adapter's normal voice receiver. Autostart-file retention is controlled separately by `DISCORD_VOICE_LIVE_KEEP_AUTOSTART_FILE`; `stop()` itself does not delete the file.

Discord connection time varies. The implementation enables reconnect within a 60-second `channel.connect()` call, but it does not encode a rule that the first five handshakes must fail with code 4006 or that success occurs after approximately 27 seconds.

## Key files

| File | Role |
|---|---|
| `__init__.py` | Hermes plugin entry, commands/tools, autostart, control-secret initialization, and video-state awareness. |
| `bridge.py` | Core voice/Gemini bridge, audio I/O, tool declarations and execution, transcript notes, and sidecar HTTP server. |
| `notification.py` | Multi-channel proactive notification dispatcher. |
| `email_brief.py` | Inbox digest (scheduled and on-demand). |
| `sfx.py` | Slot-based UI sound effects library. |
| `delegation_agent.py` | Multi-CLI delegation, health registry, and fallback chain. |
| `user_profiles.py` | Per-user profile, Honcho peer mapping, onboarding, and tool allowlists. |
| `webhook_dispatcher.py` | Event-class webhook fanout. |

## Key env vars (full list in `env-vars.md`)

- `DISCORD_VOICE_LIVE_PORT=18943` — sidecar HTTP control port.
- `DISCORD_VOICE_LIVE_USER_ID=<snowflake>` — configured target user when a caller does not supply another user ID.
- `DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS=900` — fallback quiet timeout.
- `DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS=120` — quiet period before the idle prompt.
- `DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS=60` — response window after the idle prompt.
- `DISCORD_VOICE_LIVE_VOICE=Kore` — Gemini prebuilt voice name.
- `DISCORD_VOICE_LIVE_TYPING_SFX=<path>` — optional typing-feedback WAV path.