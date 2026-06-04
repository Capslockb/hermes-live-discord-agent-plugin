---
name: voice-bridge-protocols
description: WebSocket audio protocol patterns for Discord voice bridges (Vapi.ai, Gemini Live, and similar bidirectional voice pipelines)
title: Voice Bridge Protocol Patterns
trigger:
  - vapi
  - voice websocket
  - discord audio pipeline
  - opus decode
  - voice bridge protocol
  - wants_opus
  - pcm audio
prerequisites:
  - discord-ext-voice-recv installed
---

# Voice Bridge Protocol Patterns

WebSocket audio protocol patterns for Discord voice bridges. Covers the receive → decode → forward → play pipeline.

## The `wants_opus() -> False` Pattern

When implementing a `voice_recv.AudioSink` subclass for a voice bridge, **set `wants_opus()` to return `False`**. This delegates Opus decoding to `voice_recv`, which handles Discord's DAVE encryption and FEC correctly.

### DAVE Compatibility Warning (2025-06)

Discord's **DAVE** (Discord Audio and Video Encryption) protocol is now enforced on most voice channels as of March 2026. `voice_recv` intercepts UDP packets **before** DAVE decryption. If your sink requests raw Opus (`wants_opus() -> True`), you get encrypted frames that fail to decode and the bridge hears zero audio.

**Detection:** `OpusError: corrupted stream` or `NoDecryptorForUser` in logs + green ring flashing + `voice_sink_decoded_frames: 0` + bot never responds to speech.

**Fix:** Set `wants_opus() -> False` so `voice_recv` handles DAVE decryption + Opus decode internally, then consume `data.pcm`:

```python
class MySink(voice_recv.AudioSink):
    def wants_opus(self) -> bool:
        return False  # voice_recv handles DAVE + Opus internally

    def write(self, user, data):
        pcm = getattr(data, "pcm", b"") or b""  # 48kHz stereo PCM
```

This is the approach used by the **Vapi bridge** — it works reliably because `voice_recv` processes the full pipeline: UDP receive → DAVE decrypt → Opus decode → PCM output.

**Alternative (only if you MUST handle raw Opus):**
Manually decrypt DAVE frames using `davey` or `dave.py` bindings, then decode. See `references/dave-decrypt-manual.md` for the manual path and its failure modes. This is **brittle** — `davey` 0.1.5 often throws `NoDecryptorForUser` on bot-joined channels where the bot is not fully enrolled in the MLS group.

### WRONG (causes silent failures)

```python
class MySink(voice_recv.AudioSink):
    def wants_opus(self) -> bool:
        return True  # WRONG

    def write(self, user, data):
        opus = getattr(data, "opus", b"") or b""
        import discord
        decoder = discord.opus.Decoder()
        pcm = decoder.decode(bytes(opus), fec=False)  # Fails on DAVE frames
```

### CORRECT

```python
class MySink(voice_recv.AudioSink):
    def wants_opus(self) -> bool:
        return False  # CORRECT — voice_recv gives decoded PCM

    def write(self, user, data):
        pcm = getattr(data, "pcm", b"") or b""  # Already decoded PCM
        # Resample as needed (e.g. 48kHz stereo -> 16kHz mono)
```

### Why manual Opus decoding fails

Discord voice streams use **DAVE encryption** (Discord Audio and Video Encryption). The Opus frames arriving in UDP packets are:
1. **Encrypted** with a negotiated cipher (AES-256-GCM or XSalsa20-Poly1305)
2. **Wrapped** in a Discord-specific RTP header extension
3. Potentially contain **FEC** (Forward Error Correction) redundancy packets

`voice_recv.AudioSink.write(user, data)` with `wants_opus() -> True` gives you the raw decrypted payload bytes. However:
- The bytes are NOT standard Opus frames at this point
- They may contain DAVE frame headers, FEC packets, or padding
- `discord.opus.Decoder` is a thin ctypes wrapper around libopus — it expects clean Opus frames

The result: ~100% decode failure rate, and the bridge receives no usable audio.

**Note:** With DAVE fully deployed, even `wants_opus() -> False` may fail if `voice_recv` intercepts at the wrong layer. The definitive fix is using standard `VoiceClient` when input is not needed, or implementing DAVE passthrough when it is.

### Pitfall: `getattr(data, "pcm", b"")` fails when raw bytes are passed

When `voice_recv` passes decoded PCM as raw `bytes` instead of a `VoiceData` object, `getattr(data, "pcm", b"")` returns `b""` because `bytes` has no `.pcm` attribute. The result is a **dead-silent bridge** that shows connected but captures no audio.

**Detection:** Green ring visible, `voice_sink_frames` may increase, but bot never responds to speech.

**Fix:** Always branch on type:

```python
def write(self, user, data) -> None:
    if isinstance(data, bytes):
        pcm = data  # Already decoded PCM
    else:
        pcm = getattr(data, "pcm", b"") or b""
    if not pcm:
        return
    self._on_pcm(pcm)
```

### Pitfall: using `getattr(data, "data")` instead of `getattr(data, "pcm")`

The `voice_recv.VoiceData` class stores decoded PCM in the **`pcm` attribute**, not `data`:

```python
class VoiceData:
    __slots__ = ('packet', 'source', 'pcm')  # NOTE: .pcm, not .data

    @property
    def opus(self) -> Optional[bytes]:
        return self.packet.decrypted_data
```

Reading from `.data` silently returns empty bytes, causing a **dead-silent bridge** with no error logs:

```python
# WRONG — always empty, never raises
def write(self, user, data):
    pcm = getattr(data, "data", b"") or b""  # WRONG! .data does not exist

# CORRECT
def write(self, user, data):
    pcm = getattr(data, "pcm", b"") or b""  # Correct attribute name
```

**Detection:** `voice_sink_frames` increases steadily while `voice_sink_decoded_frames` stays near zero.

### Disabling DAVE is no longer possible

```python
# DEPRECATED — Discord returns 4017 (DAVE required) as of ~2025-06
VoiceConnectionState.max_dave_protocol_version = property(lambda self: 0)
```

Discord now enforces DAVE on voice channels. The `max_dave_protocol_version` hack causes immediate close code `4017` ("E2EE/DAVE protocol required"). Use standard `discord.VoiceClient` (which handles DAVE via `davey` bindings) instead.

## 1008 GoAway-style close — decoder corruption and reconnect

When Gemini Live shuts the WebSocket with code `1008` (session duration exceeded / GoAway), the Discord audio pipeline must be fully reset. Simply reconnecting the WebSocket leaves the Opus decoder state corrupted. Every subsequent packet decodes to garbage, the speech-energy gate drops everything, and the bridge goes **dead quiet** while still showing `voice_connected: true`.

### Symptoms

- `/health` returns `voice_connected: true`, `running: true`, `playback_active: true`
- `voice_sink_frames: 0` despite user speaking
- `audio_in_chunks: 0` after reconnect
- `quiet_seconds` climbs into the 100s
- Last Gemini output before freeze is a fragment like `"in?"`

### Fix — callback-based teardown

Add an `on_reconnect` callback from `GeminiLiveBridge` to `VoiceLiveBridge` so Discord cleanup stays outside the Gemini WebSocket class:

```python
class GeminiLiveBridge:
    def __init__(self, ..., on_reconnect: Callable[[], None] = None):
        self._on_reconnect = on_reconnect
        self._reconnect_count = 0

    async def _restart(self):
        self._reconnect_count += 1
        backoff = min(2 ** (self._reconnect_count - 1), 30)
        # ... sleep, reconnect ws ...
        self._output_turn_open = False
        self._seen_server_content_shapes.clear()
        if self._on_reconnect:
            self._on_reconnect()

    async def _receive_loop(self):
        try:
            while self._running and self._ws:
                msg = await self._ws.recv()
                # ... normal handling ...
        except websockets.exceptions.ConnectionClosed as exc:
            if exc.code == 1008:
                self._output_turn_open = False
                self._seen_server_content_shapes.clear()
                await self._restart()
```

In `VoiceLiveBridge`:

```python
class VoiceLiveBridge:
    def __init__(self, guild, channel, bot, model=...):
        # ...
        self._bridge = GeminiLiveBridge(
            ..., on_reconnect=self._recreate_pcm_sink,
        )

    def _recreate_pcm_sink(self):
        if self._vc.is_listening():
            self._vc.stop_listening()
        self._listener = GeminiPCMSink(self._feed_audio)
        self._vc.listen(self._listener, after=self._on_listen_end)
```

### Pitfall

Voice bridge autostart will retry `channel.connect()` from a fresh gateway start, but each attempt takes ~27s due to Discard CDN 4006 retries before the first successful handshake. **Do not restart the gateway repeatedly** to "retry" — each restart resets the retry clock. The bridge already waits up to 60s for `secret_key` readiness.

## Toggle-Leave Command UX

A second `/voice-live` (or `/voice-vapi`) in the same channel should **leave**, not return "already active".

```python
if guild_id in _active_bridges:
    bridge_info = _active_bridges[guild_id]
    current_vc = bridge_info.get("vc")
    if (current_vc and current_vc.is_connected() and
        current_vc.channel and current_vc.channel.id == target_channel_id):
        return await voice_live_leave(guild_id)
    # Stale or different channel — clean up and start fresh
    old_task = bridge_info.get("task")
    if old_task: old_task.cancel()
    _active_bridges.pop(guild_id, None)
```

## Stale Bridge Entry Detection

When a previous bridge instance crashes (e.g. WebSocket 1005), internal tracking may hold a `vc` that is disconnected. If a new `/voice-live` or `/voice-vapi` command checks only `guild_id in _active_bridges`, it returns "Bridge still starting" indefinitely.

### Detection pattern

```python
if guild_id in _active_bridges:
    bridge_info = _active_bridges[guild_id]
    current_vc = bridge_info.get("vc")
    if current_vc and current_vc.is_connected() and current_vc.channel and current_vc.channel.id == target_channel_id:
        return "Bridge already running in this channel."
    # Stale entry — clean up and start fresh
    old_task = bridge_info.get("task")
    if old_task:
        old_task.cancel()
    _active_bridges.pop(guild_id, None)
```

This pattern must be applied in **every** voice bridge plugin (`discord-voice`, `discord-vapi`, and any future ones).

## Vapi.ai WebSocket Protocol

### Endpoint construction

Vapi transient call API returns a `websocketCallUrl`. Append `/transport` if missing:

```python
from urllib.parse import urlparse
ws_url = transport.get("websocketCallUrl", "")
parsed = urlparse(ws_url)
if not parsed.path.endswith("/transport"):
    ws_url = ws_url.rstrip("/") + "/transport"
```

### Send format

Vapi WebSocket transport expects **raw binary PCM** (signed 16-bit little-endian, mono, 16kHz). Do NOT send JSON messages, control frames, or Opus.

```python
# CORRECT: send raw bytes
await ws.send(chunk)  # chunk is already 16kHz s16le PCM

# WRONG: JSON kills the connection
await ws.send(json.dumps({"type": "keepalive"}))  # Returns 1005
```

### Keepalive

Vapi handles WebSocket ping/pong internally. Do NOT send application-level keepalive messages. The `websockets` library `ping_interval=20` is sufficient.

```python
self._ws = await websockets.connect(ws_url, ping_interval=20, ping_timeout=10)
```

### Receive format

Vapi sends binary PCM for audio responses. JSON control messages (if any) are text frames and should be handled separately:

```python
raw = await self._ws.recv()
if isinstance(raw, str):
    # JSON control/status — handle status/transcript/interrupt
    msg = json.loads(raw)
    ...
    continue
# Binary: PCM audio to play
self._output_source.feed(raw)
```

## Vapi `assistantId` Mode (Saved Assistant Config)

When a saved Vapi assistant (dashboard-created) exists, **prefer `assistantId` over inline config** in the transient call request. This inherits voice settings, model, tools, transcriber, fallbacks, first message, start/stop speaking plans, and compliance settings automatically.

### Request body: `assistantId` mode

```json
{
  "assistantId": "9de5352e-61ea-4b3b-9914-72fba94a009e",
  "transport": {
    "provider": "vapi.websocket",
    "audioFormat": {
      "format": "pcm_s16le",
      "container": "raw",
      "sampleRate": 16000
    }
  }
}
```

### Fallback: inline assistant config

```json
{
  "assistant": {
    "model": { "provider": "openai", "model": "gpt-4o" },
    "voice": { "provider": "11labs", "voiceId": "cjVigY5qzO86Huf0OWal" }
  },
  "transport": { ... }
}
```

### Implementation pattern

```python
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
if VAPI_ASSISTANT_ID:
    payload = {
        "assistantId": VAPI_ASSISTANT_ID,
        "transport": { ... },
    }
else:
    payload = {
        "assistant": { ... },
        "transport": { ... },
    }
```

**Environment variable:**
```bash
VAPI_ASSISTANT_ID=9de5352e-61ea-4b3b-9914-72fba94a009e  # optional, falls back to inline
```

## Connection Order: Vapi First, Discord Voice Second

Vapi transient call servers give up the WebSocket after ~20 seconds of inactivity. Discord voice connect on Amsterdam CDN takes ~27 seconds due to 4006 handshake retries. **Connect Vapi WebSocket first, then join Discord voice.**

### Correct order

```python
async def start(self):
    # 1. Create transient call + connect Vapi WebSocket (~1s)
    await self._vapi.connect()
    # 2. Join Discord voice (~27s on Amsterdam CDN)
    self._vc = await self._channel.connect(cls=voice_recv.VoiceRecvClient, timeout=60.0)
    # 3. Start audio I/O
    self._listener = VapiPCMSink(self._feed_audio)
    self._vc.listen(self._listener)
    self._vc.play(self._audio_source)
```

### Wrong order (causes 1005 disconnect)

```python
async def start(self):
    # 1. Discord voice first — takes 27s
    self._vc = await self._channel.connect(...)
    # 2. Vapi WebSocket — server already timed out
    await self._vapi.connect()  # ← 1005, dead quiet
```

## Keepalive Silence Loop (When Input Path is Broken)

When the Discord inbound audio path is unavailable (DAVE broke `voice_recv`, or the user has no mic), Vapi's WebSocket times out after ~20 seconds of receiving zero audio. This causes a `1005` close shortly after Discord voice connects.

### Detection

- Bot connects, green ring appears, then ~20s later: disconnects with code `1005`
- Manual test script with synthetic audio works fine (proving Vapi API and WebSocket are healthy)
- Logs show `WebSocket closed: 1005` in `_receive_loop` or `_connection_watchdog`

### Fix: inject synthetic silence

Feed 20ms of zero-PCM at 50Hz into the send queue when no real audio is available:

```python
class VapiBridge:
    async def _keepalive_loop(self):
        import struct
        sr = int(self._sample_rate)  # 16000
        frame_size = sr // 50  # 320 samples
        silence = struct.pack('<' + 'h' * frame_size, *([0] * frame_size))
        while self._running:
            await asyncio.sleep(0.02)  # 20ms
            # Only inject if send queue is empty (real audio takes priority)
            if self._send_q.empty():
                try:
                    self._send_q.put_nowait(silence)
                except asyncio.QueueFull:
                    pass
```

Start alongside send/receive loops:

```python
self._tasks = [
    asyncio.create_task(self._send_loop()),
    asyncio.create_task(self._receive_loop()),
    asyncio.create_task(self._keepalive_loop()),
]
```

**Impact:** negligible bandwidth (~64KB/minute), keeps WebSocket alive indefinitely.

**Note:** This is a band-aid for a broken/missing input path. Remove it once `voice_recv` is restored or when a proper mic capture is implemented.

## WebSocket loop lifecycle fixes

When WebSocket closes, the receive/send loops must propagate `_running = False` so the watchdog and caller clean up gracefully:

```python
# _receive_loop
except websockets.exceptions.ConnectionClosed as e:
    logger.warning("WebSocket closed (receive): %s", e)
    self._running = False
    break
except Exception as e:
    logger.error("Receive error: %s", e)
    self._running = False
    break
```

Without `self._running = False`, the watchdog crashes trying to `send_text()` on a dead connection instead of shutting down cleanly.

### `serve_forever()` hang on bridge stop

The bridge's `run_sidecar()` starts an asyncio HTTP server. If `BRIDGE.stop()` is called, the `run_sidecar()` task may stay alive because `server.serve_forever()` never exits. This prevents `_active_bridges` cleanup via the task's `done_callback`, so the next `/voice-live` returns "Bridge still starting" forever.

**Fix:** Add a `_shutdown_watcher` async task that polls `BRIDGE._running` and calls `server.close()` once it goes `False`, breaking `serve_forever()` cleanly:

```python
async def run_sidecar(vc, adapter, ready_future=None):
    global BRIDGE
    BRIDGE = VoiceLiveBridge(...)
    # ... server setup ...

    async def _shutdown_watcher():
        while BRIDGE and BRIDGE._running:
            await asyncio.sleep(1.0)
        logger.info("VoiceLive: shutting down control server")
        if server:
            server.close()

    shutdown_task = asyncio.create_task(_shutdown_watcher())
    async with server:
        await server.serve_forever()
    shutdown_task.cancel()
```

### `_starting` tombstone leak

When the bridge exits (auto-leave, manual leave, disconnect), `voice_live_leave()` must also purge `_starting[guild_id]`. If it doesn't, subsequent `/voice-live` calls return `"Bridge is being started"` forever. The fix is `_starting.pop(guild_id_int, None)` alongside `_active_bridges.pop()` in every exit path (manual leave, stale-bridge purge, and exception cleanup).

### Connection watchdog must catch ConnectionClosed too

```python
async def _connection_watchdog(self):
    while self._running:
        try:
            if self._ws and self._ws.open:
                await self._ws.send_text(json.dumps({"type": "ping"}))
            await asyncio.sleep(30)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Watchdog detected closed connection")
            self._running = False
            break
        except Exception as e:
            logger.error("Watchdog error: %s", e)
            self._running = False
            break
```

## Cross-Plugin Mutual Disconnect

When multiple voice plugins (`discord-voice` for Gemini Live, `discord-vapi` for Vapi) coexist in the same guild, they **must evict any existing voice client before connecting**. `discord.py` allows only one `VoiceClient` per guild; starting a second one corrupts both.

### The pattern in every voice plugin's `__init__.py`

```python
async def _disconnect_existing_vc(guild) -> None:
    vc = guild.voice_client
    if vc and vc.is_connected():
        try:
            await vc.disconnect(force=True)
            await asyncio.sleep(0.5)  # settle time
        except Exception as e:
            logger.warning("Error disconnecting existing VC: %s", e)
```

Call this **before** `channel.connect()` in both `voice_vapi()` and `voice_live()`:

```python
async def voice_vapi(guild_id, channel_id=None, user_id=None):
    # ... resolve guild and channel ...
    await _disconnect_existing_vc(guild)  # ← CRITICAL: evict Gemini or stale bridge
    # ... proceed with connect ...
```

### Autostart collision prevention

On gateway boot, each voice plugin must check if another plugin already owns the voice client:

```python
async def _maybe_autostart(discord_adapter):
    # If another voice plugin already connected, skip autostart
    for guild in discord_adapter.client.guilds:
        if guild.voice_client and guild.voice_client.is_connected():
            logger.info("Autostart skipped: another voice client already connected")
            return
```

### Autostart file per plugin

| Plugin | Autostart file |
|---|---|
| Gemini Live (`discord-voice`) | `~/.hermes/voice-live-autostart.json` |
| Vapi (`discord-vapi`) | `~/.hermes/voice-vapi-autostart.json` |

Never share autostart files between plugins. Delete the appropriate autostart file on `leave()` to prevent rejoin loops.

## DAVE Decrypt: Passthrough on Failure

When `davey` (DAVE encryption library) version mismatches the gateway's discord.py, or when the session is unencrypted, `dave_session.decrypt()` throws exceptions that corrupt the Opus stream. **Always fall back to passthrough on any decrypt error.**

```python
def _maybe_dave_decrypt(self, user_id, opus):
    try:
        vc = self.voice_client
        conn = getattr(vc, "_connection", None)
        dave_session = getattr(conn, "dave_session", None)
        if not dave_session:
            return opus
        import davey
        return dave_session.decrypt(user_id, davey.MediaType.audio, opus)
    except Exception as e:
        msg = str(e)
        if "Unencrypted" in msg or "not encrypted" in msg.lower():
            self._dave_passthrough += 1
        else:
            # Log once per 10s to avoid spam
            now = time.monotonic()
            if not getattr(self, "_dave_error_logged", 0) or now - self._dave_error_logged > 10.0:
                logger.warning("DAVE decrypt failed (%s), falling back", msg)
                self._dave_error_logged = now
        return opus  # ALWAYS fall through — never raise
```

**However, the preferred fix is avoiding manual decryption entirely.** Set `wants_opus() -> False` and let `voice_recv` handle DAVE internally. Manual decryption should only be used when you absolutely need raw Opus frames (e.g., recording to Ogg, forwarding to another Opus consumer). See the DAVE Compatibility Warning above and `references/dave-decrypt-manual.md` for details.

## In-Process Tools vs HTTP Control Port

Voice bridge plugins should register **in-process tools** (reading internal state directly) rather than exposing a local HTTP control server that agents query. Benefits:

- No port conflicts or firewall issues
- Works across all platforms (SSH, Telegram, Discord) without tunneling
- Direct access to live bridge metrics and methods

### Implementation pattern

Store the bridge module reference in `_active_bridges`:

```python
_active_bridges[guild_id] = {
    "vc": vc,
    "task": task,
    "bridge_mod": bridge_module,  # module containing BRIDGE instance
}
```

Access in-process from plugin `__init__.py`:

```python
async def _voice_vapi_status_handler(args=None, **kwargs):
    results = []
    for gid, info in list(_active_bridges.items()):
        mod = info.get("bridge_mod")
        br = getattr(mod, "BRIDGE", None) if mod else None
        if not br:
            continue
        results.append({
            "guild_id": gid,
            "connected": bool(br._vc and br._vc.is_connected()),
            "call_id": getattr(br._vapi, "_call_id", None),
            **br._vapi.health(),
        })
    return json.dumps(results, indent=2)
```

This replaces the old `requests.get("http://127.0.0.1:18944/health")` pattern.

## Autostart File Collisions Between Voice Plugins

When multiple voice plugins (`discord-voice`, `discord-vapi`) coexist, **each must use its own autostart file** and check for collisions on boot.

### File naming convention

| Plugin | Autostart file |
|---|---|
| Gemini Live (discord-voice) | `~/.hermes/voice-live-autostart.json` |
| Vapi (discord-vapi) | `~/.hermes/voice-vapi-autostart.json` |

### Boot check: prevent cross-plugin collision

```python
async def _maybe_autostart(discord_adapter):
    # If another voice plugin already connected, skip autostart
    for guild in discord_adapter.client.guilds:
        if guild.voice_client and guild.voice_client.is_connected():
            logger.info("Autostart skipped: another voice client already connected in guild %s", guild.id)
            return
    # Now safe to autostart this plugin
    # ...
```

### Cleanup on leave

```python
async def _voice_vapi_leave(guild_id):
    # ... disconnect logic ...
    # Remove autostart file so next boot doesn't rejoin
    try:
        os.remove(os.path.expanduser("~/.hermes/voice-vapi-autostart.json"))
    except FileNotFoundError:
        pass
```

## Compilation Check

After editing any bridge/plugin file:

```bash
python3 -m py_compile ~/.hermes/plugins/discord-vapi/bridge.py
python3 -m py_compile ~/.hermes/plugins/discord-voice/bridge.py
```

## PCM Resampling: Windowed-Sinc FIR Filters

Discord delivers audio at **48kHz stereo** (PCM s16le). AI voice providers like Vapi and Gemini Live typically expect **16kHz mono**. The resample path is a critical quality gate — naive filters produce audible "fuzzy" or "garbled" audio.

### The right approach: windowed-sinc FIR

Pre-compute a lowpass filter at module load using pure numpy (no scipy/soxr needed):

```python
_RESAMPLE_LP_3 = _design_lowpass(1.0/3.0, 63)  # cutoff at 1/3 Nyquist
```

**Downsample (48k → 16k):** Anti-alias FIR → decimate by 3
**Upsample (16k → 48k):** Zero-stuff by 3 → FIR → **scale by 3.0** (required gain correction)

### Common pitfalls

- **Missing upsampling gain correction**: zero-stuffing drops signal amplitude by the interpolation factor. `raw * 3.0` is NOT optional.
- **Too few filter taps**: 3-tap boxcar gives ~12dB rejection — sibilants (4-8kHz) alias back into the passband, producing "fuzzy" audio. 63-tap gives ~160dB rejection.
- **Channel conversion timing**: convert channels at source rate, not after resampling — avoids double-conversion artifacts.
- **Gibbs overshoot**: FIR filters can peak above int16 range. Always `np.clip(..., -32768, 32767)` before casting.

### Detection of bad resampling

Symptoms in the voice bridge:
- Audio is **intelligible but sounds "blurry" or "fuzzy"** — spectral aliasing from insufficient anti-imaging
- User can tell *something is being said* but cannot make out the words
- No errors in logs, no dropped frames — the resampler silently corrupts the audio

See `references/pcm-resampling-fir.md` for detailed filter design, alternatives comparison, and verification.

## References

- `references/gemini-live-setup-gotchas.md` — Gemini Live API `setup` payload schema pitfalls: `mediaResolution` string/object mismatch (WebSocket 1007), `turnCoverage` defaults by model version, model naming conventions, cost-control checklist
- `references/pcm-resampling-fir.md` — PCM resampling with windowed-sinc FIR: filter design, pitfalls, and alternatives comparison
- `references/voice-recv-opus-pitfall.md` — DAVE encryption detail and Opus decode failure mode
- `references/vapi-websocket-requirements.md` — Vapi transient call API quirks (assistantId mode + raw PCM transport)
- `references/vapi-silent-audio-bug.md` — three silent-failure modes: wrong attribute, missing raw-bytes guard, and wants_opus() -> True
- `references/vapi-connection-ordering.md` — full reproduction of the 1005 timeout caused by wrong connect ordering
- `references/gemini-1008-reconnect.md` — handling GoAway-style 1008 closes and decoder state corruption