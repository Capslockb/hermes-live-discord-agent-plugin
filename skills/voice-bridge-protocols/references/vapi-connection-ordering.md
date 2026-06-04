# Vapi Connection Ordering Pitfall

## Session context

2026-05-28: Vapi voice bridge joins successfully, WebSocket connects, but remains dead quiet. Logs show WebSocket 1005 ("no status received") and "dropped undecodable Opus frames".

## Root cause

Discord voice connect on Amsterdam CDN (`c-ams08.discord.media` / `c-ams07`) takes ~27 seconds due to the known 4006 handshake rejection quirk (server rejects first ~5 handshakes before accepting). During this 27s window, Vapi's transient call WebSocket times out after ~20 seconds of inactivity. The result: WebSocket 1005 disconnect, then Opus decode errors from garbage data.

**The fix:** Connect Vapi WebSocket FIRST (fast, ~1s), THEN join Discord voice (~27s).

## Timeline

| Time | Event | State |
|---|---|---|
| T+0s | `/voice-vapi` dispatched | — |
| T+0s | `_create_transient_call()` POST to api.vapi.ai | transient call created |
| T+~1s | WebSocket connect succeeds | ws_url connected |
| T+1s–28s | `self._vc = await self._channel.connect(...)` | 27s of internal retries |
| T+~28s | Discord voice finally connected | both sides ready |
| T+30s | User speaks → audio flows to Vapi | Sora responds |

## Wrong order (produces 1005)

```python
# WRONG — Discord voice first
async def start(self):
    # Takes 27s — Vapi server gives up at ~20s
    self._vc = await self._channel.connect(cls=voice_recv.VoiceRecvClient, timeout=60.0)
    # WebSocket already timed out — 1005
    await self._vapi.connect()
```

## Correct order

```python
# CORRECT — Vapi first
async def start(self):
    # Fast: transient call + WebSocket (~1s)
    await self._vapi.connect()
    # Slow but tolerant: Discord voice connect (~27s)
    self._vc = await self._channel.connect(cls=voice_recv.VoiceRecvClient, timeout=60.0)
    # Audio I/O
    self._listener = VapiPCMSink(self._feed_audio)
    self._vc.listen(self._listener)
    self._vc.play(self._audio_source)
```

## Cleanup on Discord failure

If Discord voice connect fails after Vapi is already connected, the dangling WebSocket must be cleaned up:

```python
    try:
        self._vc = await self._channel.connect(...)
    except Exception as e:
        logger.error("Discord voice connect failed: %s", e)
        await self._vapi.disconnect()  # clean up dangling websocket
        return False
```

## Log signatures

- `Vapi send error: received 1005 (no status received [internal])` after ~27s uptime
- `Vapi receive error: received 1005` paired with Opus undecodable frames
- Bridge shows `voice_connected: true` shortly before the 1005
- Call ID exists but no `assistant_response` events

## Mitigation: keepalive silence injection

If `voice_recv`/`davey` is removed from the path (DAVE-incompatible `discord.py` version), the bridge sends **no audio input** to Vapi. Vapi's WebSocket still times out after ~20s of zero-user-audio even when connection order is correct.

### Keepalive loop pattern

Add an async keepalive task in `VapiBridge.connect()` alongside send/receive loops:

```python
async def _keepalive_loop(self):
    sr = int(VAPI_SR)  # 16000
    silence = struct.pack('<' + 'h' * (sr // 50), *([0] * (sr // 50)))  # 20ms
    while self._running:
        await asyncio.sleep(0.02)  # 20ms = 50 chunks/sec
        if self._send_q.empty():
            self.feed_audio(silence)
```

This injects silence at Vapi's expected chunk rate so the server sees an "active" call while Discord connects. The silence bytes flow through the same `_send_loop()` — no special-casing needed. Rate must match provider expectations (~20ms chunks for 16kHz PCM).

**Important:** The keepalive is a band-aid for missing user input, not a replacement for proper voice_recv. When DAVE/davey is fixed, remove the keepalive and restore real mic input.

## Applies to

Any voice bridge where the external WebSocket provider has a shorter timeout than Discord voice connect:
- Vapi.ai transient calls
- Any future provider with ~20s WebSocket idle timeout
- DAVE-incompatible `discord.py` versions where user audio input is unavailable
