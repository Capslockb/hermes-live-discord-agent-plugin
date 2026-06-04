# voice_recv Opus Decode Pitfall

## Problem

Manually decoding Discord voice Opus frames with `discord.opus.Decoder()` silently fails in production. The bridge reports "connected" but receives zero usable audio, and downstream APIs (Vapi, Gemini Live) receive no input.

## Root Cause: DAVE Encryption

Discord uses **DAVE** (Discord Audio and Video Encryption) on voice streams. The Opus frames arriving in UDP packets are:
1. **Encrypted** with a negotiated cipher (AES-256-GCM or XSalsa20-Poly1305)
2. **Wrapped** in a Discord-specific RTP header extension
3. Potentially contain **FEC** (Forward Error Correction) redundancy packets

`voice_recv.AudioSink.write(user, data)` with `wants_opus() -> True` gives you the raw decrypted payload bytes. However:
- The bytes are NOT standard Opus frames at this point
- They may contain DAVE frame headers, FEC packets, or padding
- `discord.opus.Decoder` is a thin ctypes wrapper around libopus — it expects clean Opus frames

## Failure Mode

`decoder.decode(bytes(opus), fec=False)` either:
- Returns empty/zeroed PCM (most common — libopus rejects malformed frames)
- Raises `OpusError` (less common — usually caught and logged)
- Corrupts decoder state (rare — subsequent all frames fail)

The bridge logs may show repeated "undecodable Opus frames" or may show nothing at all (empty PCM passes silently).

## Detection

Check `voice_sink_frames` vs `voice_sink_decoded_frames` in metrics. If frames are received but `decoded_frames` stays near zero:

```python
if bridge_info["metrics"].get("voice_sink_frames", 0) > 100 and bridge_info["metrics"].get("voice_sink_decoded_frames", 0) < 10:
    logger.error("Opus decode failure detected — likely DAVE/FEC incompatibility")
```

## Fix

Set `wants_opus() -> False` and receive PCM directly:

```python
class VapiPCMSink(voice_recv.AudioSink):
    def wants_opus(self) -> bool:
        return False  # voice_recv handles DAVE + Opus decode internally

    def write(self, user, data):
        pcm = getattr(data, "pcm", b"") or b""  # 48kHz stereo PCM from voice_recv
        # Resample / downsample as needed
```

This works because `voice_recv` processes the full Discord voice pipeline internally: UDP receive → DAVE decrypt → FEC reconstruction → Opus decode → PCM output.

## When to use `wants_opus() -> True`

Only if you are **re-publishing** the raw Opus stream (e.g. recording to Ogg, forwarding to another Opus consumer). Never when you need PCM for an external API.
