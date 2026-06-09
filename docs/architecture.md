# Architecture

End-to-end audio path, threading model, and lifecycle of the Discord voice bridge.

## Audio path

```
Discord Voice (Opus)
    ↓  discord-ext-voice-recv decode
48 kHz PCM stereo (16-bit)
    ↓  VoiceListener._feed_audio
16 kHz PCM mono  (24 kHz internally, then downsampled in GeminiLiveBridge)
    ↓  WebSocket binary frame
Gemini Multimodal Live API  (WSS)
    ↓  WebSocket binary frame
24 kHz PCM mono  (PCM16)
    ↓  LiveAudioSource.feed
48 kHz PCM stereo (upsample_for_discord)
    ↓  discord.AudioSource
Discord Voice (Opus encode)
```

## Threading model

The bridge runs **three** long-lived threads plus a small pool of timers.

| Thread | Owner | Purpose |
|---|---|---|
| **Gateway event loop** | `discord.py` | Owns the bot, voice client, and all `LiveAudioSource` playback. Async. |
| **Gemini receive loop** | `GeminiLiveBridge._ws_recv_loop` | Reads WSS frames, dispatches audio / tool calls / transcripts. Runs `_run_in_executor` for any blocking work. |
| **Tool worker** | `_run_local_tool` (module-level) | Synchronous; called from a thread-pool for each Gemini `toolCalls` event. |
| **Schedulers** | `notification.start_scheduler`, `email_brief.start_brief_scheduler` | Daemon threads, JSONL-polling, ~1-2s tick. |

## Lifecycle

1. **User runs `/voice-live` in Discord**
2. `__init__.py:voice_live()` checks the user is in the target voice channel (user-presence gate, criterion #33). If not, it rejects with a "you're not in voice" message.
3. If a stale bridge is in `_active_bridges` (vc disconnected), it's cleaned up.
4. A new `VoiceLiveBridge` is created and `run()` is spawned as a background task.
5. `VoiceLiveBridge.start()`:
   - Calls `channel.connect()` (Discord CDN handshake quirk: first 5 attempts fail with code 4006; takes ~27s)
   - Creates `LiveAudioSource` (the audio-output queue)
   - Creates `GeminiLiveBridge` and registers it in `sfx.register_active_source` for cross-bridge sfx
   - Wires `vc.listen()` (RX) and `vc.play()` (TX)
   - Plays the `transition` sfx into the audio source
   - Calls `await gemini.connect()` (sends setup message, waits for `setupComplete`)
   - Sends `audioStreamEnd` immediately after `setupComplete` to mute first-turn
   - Returns
6. **Connection watchdog** polls `vc.is_connected()` and the user's voice-channel membership every 1s. If either fails, calls `stop()`.
7. **Auto-leave** kicks in if `AUTO_LEAVE_QUIET_SECONDS` passes with no audio.
8. **User runs `/voice-live-leave`** → `__init__.py:voice_live_leave()` calls `bridge.stop()`.
9. `stop()` cancels the recv loop, ends the WSS, disconnects from the voice channel, deletes the autostart file (if any).

## Key files

| File | Role |
|---|---|
| `__init__.py` | Hermes plugin entry. Registers `/voice-live`, `/voice-live-leave`, autostart mechanism, video frame control. |
| `bridge.py` | Core. `VoiceLiveBridge`, `GeminiLiveBridge`, `LiveAudioSource`, audio I/O, all tool definitions, sidecar HTTP server. |
| `notification.py` | Multi-channel proactive notification dispatcher. |
| `email_brief.py` | Inbox digest (scheduled + on-demand). |
| `sfx.py` | Slot-based UI sound effects library. |
| `delegation_agent.py` | Multi-CLI delegation with health registry and fallback chain. |
| `user_profiles.py` | Per-user Honcho peer mapping, onboarding. |
| `webhook_dispatcher.py` | Event-class webhook fanout. |

## Key env vars (full list in `env-vars.md`)

- `DISCORD_VOICE_LIVE_PORT=18943` — sidecar HTTP control port
- `DISCORD_VOICE_LIVE_USER_ID=<b_snowflake>` — who the bridge listens to
- `DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS=900` — idle timeout
- `DISCORD_VOICE_LIVE_VOICE=en-US-JennyNeural` — TTS voice
- `DISCORD_VOICE_LIVE_TYPING_SFX=~/.hermes/voice-live-typing.wav` — keyboard click sfx
