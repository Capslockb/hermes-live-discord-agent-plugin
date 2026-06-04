# Vapi Silent Audio Bug

## Session context

2026-05-27: Discord Vapi voice bridge. Bot joins voice channel, WebSocket connects, but remains dead silent — no audio in either direction. All connection-level indicators (WebSocket connected, `voice_connected: true`, no errors in logs) were green.

## Root cause

Two separate bugs combined to silently drop all audio:

### Bug 1: `getattr(data, "data")` instead of `getattr(data, "pcm")`

```python
# WRONG — always returns empty bytes
def write(self, user, data):
    pcm = getattr(data, "data", b"") or b""  # WRONG attribute!
```

`voice_recv.VoiceData` stores decoded PCM bytes in the `pcm` attribute, not `data`:

```python
class VoiceData:
    __slots__ = ('packet', 'source', 'pcm')

    @property
    def opus(self) -> Optional[bytes]:
        return self.packet.decrypted_data
    # .pcm is set during AudioSink.dispatch
```

**Detection:** `voice_sink_frames` increases but `voice_sink_decoded_frames` stays at 0.

**Fix:**
```python
pcm = getattr(data, "pcm", b"") or b""  # CORRECT
```

### Bug 2: `wants_opus() -> True`

```python
# WRONG — discord.opus.Decoder cannot handle DAVE-encrypted frames
class MySink(voice_recv.AudioSink):
    def wants_opus(self) -> bool:
        return True  # WRONG

    def write(self, user, data):
        import discord.opus
        decoder = discord.opus.Decoder()
        pcm = decoder.decode(data.opus, fec=False)
```

DAVE encryption means raw decrypted bytes are not valid Opus frames.

**Fix:**
```python
def wants_opus(self) -> bool:
    return False  # CORRECT — get PCM directly from voice_recv
```

### Both bugs together

Even when Bug 2 was fixed (setting `wants_opus() -> False`), Bug 1 still silently dropped all audio because `getattr(data, "data")` returns empty bytes regardless. The Opus→PCM decode was already happening correctly inside `voice_recv`, but the code read the wrong attribute.

## Complete correct sink implementation

```python
if voice_recv is not None:
    class VapiPCMSink(voice_recv.AudioSink):
        def __init__(self, on_pcm_callback):
            super().__init__()
            self._on_pcm = on_pcm_callback
            self._frames = 0

        def wants_opus(self) -> bool:
            return False

        def write(self, user, data) -> None:
            if user is None:
                return
            # voice_recv may pass raw bytes OR a VoiceData object
            if isinstance(data, bytes):
                pcm = data
            else:
                pcm = getattr(data, "pcm", b"") or b""
            if not pcm:
                return
            self._frames += 1
            self._on_pcm(pcm)

        def cleanup(self) -> None:
            pass
```

## Bug 3: Raw `bytes` passed without `.pcm` attribute (Session 2026-05-27)

When `wants_opus() -> False`, `voice_recv` usually passes a `VoiceData` object with a `.pcm` attribute. However, in some configurations or versions, the library passes the raw decoded `bytes` directly instead of wrapping them in a `VoiceData` object.

### The wrong code

```python
def write(self, user, data) -> None:
    pcm = getattr(data, "pcm", b"") or b""  # FAILS when data IS already raw bytes
    if not pcm:
        return
    self._on_pcm(pcm)
```

When `data` is already raw `bytes`, `getattr(data, "pcm", b"")` returns `b""` because `bytes` has no `.pcm` attribute. `b"" or b""` evaluates to `b""`, so the method returns early and drops **100% of audio**.

### Detection

- `voice_connected: true`, `playback_active: true`, green ring visible
- `voice_sink_frames` may increase (frames are "received")
- `voice_sink_decoded_frames` stays at 0 (but depending on counter placement, might also increase)
- **Dead silence — no bot response at all**

### The fix

Always branch on type. Raw bytes ARE the PCM; only use `getattr` when it's actually an object:

```python
def write(self, user, data) -> None:
    if isinstance(data, bytes):
        pcm = data
    else:
        pcm = getattr(data, "pcm", b"") or b""
    if not pcm:
        return
    self._on_pcm(pcm)
```

This is defensive coding: it handles **both** the normal `VoiceData` object path AND the raw-bytes edge case.

### Why this happens

The `voice_recv` library's `AudioSink.dispatch()` implementation sometimes yields raw bytes directly after decoding, especially when the source stream lacks full metadata (e.g. DAVE-encrypted streams where packet reconstruction is delegated). The `write(self, user, data)` signature accepts `data: Any` — the actual type at runtime depends on the library's internal dispatch path.

## Verification script

Use this to check a sink implementation for the correct attribute access:

```python
def audit_sink_write(write_method_source: str) -> list[str]:
    """Scan sink write() for common attribute access mistakes"""
    issues = []
    if 'getattr(data, "data",' in write_method_source:
        issues.append("uses getattr(data, 'data') — should be getattr(data, 'pcm')")
    if 'getattr(data, "opus",' in write_method_source and 'wants_opus' in write_method_source:
        issues.append("reads data.opus but wants_opus() should probably be False")
    if 'decoder.decode(data' in write_method_source or 'decoder.decode(' in write_method_source:
        issues.append("manual opus decoding — voice_recv does this when wants_opus() -> False")
    if "wants_opus" in write_method_source:
        if "return True" in write_method_source.split("wants_opus")[1].split("def")[0]:
            issues.append("wants_opus() returns True — should be False for PCM sinks")
    # NEW: detect missing raw-bytes guard
    if "isinstance(data, bytes)" not in write_method_source and "wants_opus" in write_method_source:
        issues.append("missing isinstance(data, bytes) guard — voice_recv may pass raw bytes directly")
    return issues
```

