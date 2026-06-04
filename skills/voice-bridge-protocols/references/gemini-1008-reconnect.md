# Gemini Live 1008 GoAway Reconnect Reference

**Session:** 27 May 2026 — Hermes Discord voice bridge dead-quiet bug after long session
**File:** `~/.hermes/plugins/discord-voice/bridge.py`

## Bug description

After Gemini Live closes with WebSocket code `1008` (session duration exceeded), the bridge reconnects the WebSocket but **keeps the old Opus decoder state**. Every decoded audio packet is garbage (`_has_speech_energy` drops everything, `voice_sink_frames: 0`), while the bridge still reports `voice_connected: true, running: true`.

## Symptoms checklist

| Metric | Healthy | Corrupted |
|---|---|---|
| `voice_connected` | true | true |
| `running` | true | true |
| `playback_active` | true | true |
| `voice_sink_frames` | increasing | **0** |
| `audio_in_chunks` | increasing | **0** |
| `quiet_seconds` | < 10s | **> 100s** |

## Root cause

`GeminiLiveBridge._restart()` reconnects the WebSocket but does **not** recreate the `GeminiPCMSink` / `LiveAudioSource` objects. The Opus decoder inside `discord.ext.voice_recv` retains corrupted state from the interrupted stream.

## Patch points (`bridge.py`)

1. **`GeminiLiveBridge.__init__`** (line ~486):  
   Add `on_reconnect: callable = None` param, store `self._on_reconnect`.

2. **`_restart()`** (line ~682):  
   Add `exponential backoff` (max 30s), clear `_output_turn_open` and `_seen_server_content_shapes`, call `self._on_reconnect()`.

3. **`_receive_loop()` exception handler** (line ~785):  
   Detect `exc.code == 1008`, log `"Gemini Live: detected 1008 GoAway-style close"`, clear state, trigger `_restart()`.

4. **`VoiceLiveBridge.__init__`** (line ~954):  
   Pass `on_reconnect=self._recreate_pcm_sink` to `GeminiLiveBridge(...)`.

5. **`VoiceLiveBridge._recreate_pcm_sink()`** (insert after `_feed_audio`):  
   Stop current `vc.listen()`, create fresh `GeminiPCMSink`, re-register `vc.listen(sink, after=...)`.

## Verification

After patch, run for a session exceeding the Gemini Live duration limit (~15 min free tier) and watch logs for:
```
Gemini Live: detected 1008 GoAway-style close
VoiceLive: PCM sink recreated
```
Post-reconnect, `voice_sink_frames` should resume increasing within ~2s.

## Don't do

- Do NOT put Opus/PCM teardown inside `GeminiLiveBridge` — it should not know about Discord voice.
- Do NOT restart the gateway repeatedly — Discard CDN 4006 retries take ~27s per attempt.
