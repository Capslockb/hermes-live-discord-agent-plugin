# Manual DAVE Decryption Path — `davey` and `dave.py`

**Status:** Deprecated for voice bridge sinks. Use `wants_opus() -> False` instead.

## When is manual decryption needed?

Only when your sink absolutely needs **raw encrypted Opus frames** (e.g., recording to Ogg, forwarding to another Opus consumer, analyzing packet headers). For all PCM-consuming use cases (Vapi, Gemini Live, Whisper, etc.), use `voice_recv`'s built-in pipeline.

## Libraries

| Library | Backend | PyPI | Notes |
|---|---|---|---|
| `davey` | Rust (OpenMLS) | `davey` 0.1.5 | Lightweight, but `NoDecryptorForUser` common on bots |
| `dave.py` | C++ (libdave) | `dave.py` | Official Discord C++ bindings, more robust |

## The `davey` path (what Gemini bridge used)

```python
import davey

class MySink(voice_recv.AudioSink):
    def wants_opus(self) -> bool:
        return True  # Get raw UDP payload bytes

    def _maybe_dave_decrypt(self, user_id: int, opus: bytes) -> bytes:
        try:
            vc = self.voice_client
            conn = getattr(vc, "_connection", None)
            dave_session = getattr(conn, "dave_session", None)
        except Exception:
            dave_session = None
        if not dave_session:
            return opus  # Unencrypted channel
        try:
            decrypted = dave_session.decrypt(user_id, davey.MediaType.audio, opus)
            return decrypted
        except ValueError as e:
            if "NoDecryptorForUser" in str(e):
                # Bot not fully enrolled in MLS group — happens often on DAVE channels
                return opus  # Passthrough (will decode as noise, but doesn't crash)
            raise
```

## Failure modes observed in production

### `NoDecryptorForUser`

```
ValueError: Failed to decrypt: NoDecryptorForUser
```

**Cause:** The bot's `dave_session` exists, but no decryption key was derived for the sender user. This happens when:
- The bot joined the channel before DAVE negotiation completed
- The MLS group epoch rotated and the bot missed the transition
- The sender's `user_id` doesn't match any group member key

**Impact:** Every incoming packet throws. The Opus decoder gets garbage or empty bytes. `voice_sink_decoded_frames` stays at 0. Bot is completely deaf.

**Workaround:** Passthrough to opus (return `opus` unchanged). The frames are still encrypted, so the Opus decoder will reject them as "corrupted stream". This is a band-aid — audio still fails, but the pipeline doesn't crash.

### `OpusError: corrupted stream`

After passthrough on `NoDecryptorForUser`, `discord.opus.Decoder` rejects every frame. Logs fill with:

```
WARNING voice-live: VoiceLive: dropped undecodable Opus frame(s), latest=corrupted stream total=52
```

**Cause:** The bot is receiving encrypted frames but treating them as raw Opus. `libopus` sees random bytes and returns error.

**Impact:** Same as `NoDecryptorForUser` — zero usable audio.

## Why `wants_opus() -> False` fixes both

`voice_recv`'s internal `PacketRouter` processes UDP packets at the correct layer:

```
UDP receive → DAVE decrypt (libdave/davey inside discord.py) → FEC reconstruct → Opus decode → PCM
```

By setting `wants_opus() = False`, your sink receives the **output** of this pipeline: clean 48kHz stereo PCM. The DAVE decryption happens inside `discord.py`'s voice connection state machine, which has access to the full MLS group context and handles epoch rotations correctly.

## Migration checklist

- [ ] Remove `wants_opus() = True` from your `AudioSink`
- [ ] Remove `_maybe_dave_decrypt()` method
- [ ] Remove `discord.opus.Decoder` instances and `_decoders` dict
- [ ] Remove Opus silence marker checks (`b"\xF8\xFF\xFE"`)
- [ ] Read `data.pcm` instead of `data.opus`
- [ ] Verify `voice_sink_decoded_frames` increases when user speaks

## See also

- `references/voice-recv-opus-pitfall.md` — the high-level `wants_opus()` recommendation
- `references/vapi-silent-audio-bug.md` — three silent failure modes including DAVE-related ones
