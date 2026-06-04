# Architecture

> A deep dive into the audio pipeline, threading model, and lifecycle of the Gemini Live Discord bridge.

## High-level data flow

```
                                  ┌─────────────────────────────────────────────┐
                                  │             Gemini Live API                 │
                                  │  ┌─────────────┐    ┌─────────────────────┐  │
                                  │  │  multimodal │    │  function-calling   │  │
                                  │  │  reasoning  │◄──►│  (synchronous)      │  │
                                  │  └──────┬──────┘    └──────────┬──────────┘  │
                                  └─────────┼──────────────────────┼─────────────┘
                                            │ WSS                 │ toolResponse
                                  ▲ audio   │                     │
                                  │         │                     ▼
              24 kHz mono         │         │            ┌─────────────────┐
        ┌────────────────────────┐│         │            │ FunctionCalls   │
        │  GeminiLiveBridge      ││         │            │ dispatcher      │
        │  (async)               ├┘         │            │ (in-process)    │
        └────────────────────────┘          │            └─────────────────┘
                                            │
                                  ▲ 16 kHz  │  base64
                                  │ mono    │  PCM chunks
        ┌────────────────────────┐│         │
        │  VoiceListener         ├┘         │
        │  (rx thread)           │          │
        └────────────────────────┘          │
                                            │
              Opus 48 kHz                   │
        ┌────────────────────────┐         │
        │  Discord VoiceClient   │         │
        │  (UDP + NaCl decrypt)  ├─────────┘
        └────────────────────────┘
              ▲
              │  user microphone
              │
        ┌────────────────────────┐
        │  Discord voice channel │
        └────────────────────────┘
```

## Audio pipeline (detailed)

### Receive path (Discord → Gemini)

1. **Discord VoiceClient** receives Opus packets from the voice UDP socket and decrypts them with the **NaCl secret-box** session key negotiated at handshake.
2. **VoiceListener** runs in its own thread, reading decoded PCM frames at 48 kHz stereo.
3. Frames are converted with **numpy** to 16 kHz mono (Discord uses 48 kHz; Gemini realtime input expects 16 kHz PCM-16LE).
4. Each chunk is base64-encoded and pushed to Gemini via `realtimeInput.mediaChunks`.
5. When the user stops speaking, the listener idles. No audio chunks are sent.

### Send path (Gemini → Discord)

1. Gemini's `serverContent.modelTurn.parts[].inlineData` arrives over WSS as base64 PCM at 24 kHz mono.
2. **GeminiLiveBridge** decodes, then **upsamples** to 48 kHz stereo using linear interpolation (numpy).
3. Frames are pushed to a **thread-safe queue** inside `LiveAudioSource` (a `discord.AudioSource` subclass).
4. **VoiceClient.play()** pulls from the queue and sends Opus-encoded packets back to Discord.

### Why a thread-safe queue?

`AudioSource.read()` is called from discord.py's voice send thread. The Gemini WSS receive loop is on the gateway's asyncio loop. They can never share a Python object directly. The queue is the synchronisation point.

## Threading model

| Thread / task | Owner | Role |
|---------------|-------|------|
| Gateway asyncio loop | Hermes | spawns bridge, handles slash commands, owns Gemini WSS |
| discord.py voice rx | internal | decrypts + decodes inbound Opus |
| discord.py voice tx | internal | encodes + sends outbound Opus |
| `_shutdown_watcher` (poll task) | bridge | polls `BRIDGE._running` and closes the sidecar HTTP server |

The bridge is **cooperatively async**. The only blocking work is Opus encoding/decoding, which discord.py does off-thread for us.

## Lifecycle

```
slash command                voice_live()
    │                              │
    ▼                              ▼
disconnect any existing    infer channel
voice client in guild          │
    │                          ▼
    ▼                  spawn bridge.py:run_sidecar(channel, ...)
disconnect                 │ in sidecar:
    │                      │   channel.connect()       ← may take 27s
    ▼                      │   VoiceClient.play(LiveAudioSource())
wait ~30s                  │   open Gemini WSS
    │                      │   start VoiceListener thread
    ▼                      ▼
ready future fires     register in _active_bridges[guild_id]
    │                      │
    ▼                      ▼
return success         bridge runs until:
                           - /voice-live-leave
                           - idle hangup
                           - gateway stop
```

## Idempotency

The plugin guards against:

- **Stale entries** — if a guild's `voice_client` is disconnected but `_active_bridges` still has an entry, the new call cancels the old task, pops the entry, and starts fresh.
- **Same-channel no-op** — calling `/voice-live` while already connected to that channel returns `"success": "Voice bridge is ready"` instead of reconnecting.
- **Cross-channel move** — calling `/voice-live` for a different channel in the same guild calls `vc.move_to()` instead of disconnect+reconnect.

## Control API (HTTP)

The sidecar HTTP server runs on `127.0.0.1:18943` (configurable via `DISCORD_VOICE_LIVE_PORT`).

| Endpoint | Method | Body | Effect |
|----------|--------|------|--------|
| `/health` | GET | — | Returns the full `BRIDGE.health()` JSON |
| `/say?text=...` | GET | — | Injects text into the Gemini session; the model speaks it |
| `/leave` | GET | — | Stops the bridge, disconnects |
| `/note` | GET | — | Returns the most recent transcript entry |

The server is bound to **loopback only** — it is not exposed to the network. Use a Tailscale / SSH tunnel to access it remotely.

## Function calling

Gemini Live (3.1 Flash) supports **synchronous** function calling. The client must dispatch `functionCalls` manually and send `toolResponse` back. The WSS stays open during execution; the receive loop runs in an executor so it never blocks on the tool call.

Example of the message round-trip (in `bridge.py`):

```python
async def _on_function_call(self, function_calls):
    # dispatch to in-process tools (run Hermes-side skills, etc.)
    responses = await self._dispatch(function_calls)
    await self._send_tool_response(responses)
```

The receive loop never awaits long-running work — anything slow goes through `loop.run_in_executor()`.

## Cost control

The default session config:

```python
SESSION_PARAMS = {
    "mediaResolution": "LOW",                 # ~100 tokens/frame vs ~258 default
    "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",  # don't bill silent video
}
```

Combined with the bridge-level 1 fps frame gating and audio-gating (no video frames are sent when the user is silent for > 1s), a typical hour of full-duplex audio costs **$0.03–$0.06 on the Flex tier**.

## Failure modes

| Symptom | Where it happens | Recovery |
|---------|------------------|----------|
| `channel.connect()` hangs ~27s | Discord CDN handshake rejection (code 4006) | Wait it out. Don't restart the gateway. |
| WSS drops mid-call | Network blip / Gemini server restart | `run_sidecar` reconnects automatically with backoff |
| Playback stops silently | Natural silence + 2s timeout in `LiveAudioSource` | Speak again — `_wake_playback` restarts on new audio |
| Notes file missing | `~/.hermes/voice-live-notes/` not writable | Create the dir, set `NOTES_DIR` env var |
| "Bridge still starting" hangs | Stale entry in `_active_bridges` | Plugin auto-cleans; if not, `pkill -9` the gateway and restart |
