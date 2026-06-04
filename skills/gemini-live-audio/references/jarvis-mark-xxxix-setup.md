# Jarvis Mark-XXXIX — B's Environment Setup

## Server Location

The Jarvis WebUI server lives at `/home/caps/Mark-XXXIX/`. This hosts:

- `main.py` — Core Live API logic, model constant, tool declarations
- `webui.py` — WebSocket server + static file serving
- `launcher.py` — Entry point, picks up API key from environment
- `config/api_keys.json` — Runtime API key store (auto-populated by launcher)
- `core/prompt.txt` — System prompt for the assistant
- `web/` — Static frontend files

## Startup

The launcher reads the API key from environment, writes to config, then spawns `webui.py` which serves on port 8765. The launcher process exits after spawning; the live server is the `webui.py` process.

## Server Access

- Local: `http://localhost:8765/`
- Tailnet: `https://homelab.tailb9e21e.ts.net/` (reverse-proxied)

## Fix History

### Issue 5 (May 2026): Config Completeness — Silent Bot After Config Stripping

**Symptom:** During debugging, switching to a minimal `LiveConnectConfig` (stripping `session_resumption`, `tools`, and transcription configs) caused the model to accept the connection but produce zero audio output. No error, no log — just silence.

**Root cause:** `output_audio_transcription={}`, `input_audio_transcription={}`, `session_resumption=types.SessionResumptionConfig()`, and `tools=[...]` are all required in `_build_config` for audio to flow. Removing any of them silences the response path with no diagnostic.

**Fix:** Restored the full config with all fields. The stripped config was an overcorrection during debugging — only `session_resumption` was potentially problematic with 3.1, but removing it also removed audio output.

### Issue 6 (May 2026): 3.1 Text Commands Don't Produce Audio

**Symptom:** Text commands sent via `send_client_content` on the 3.1 model are processed (model THINKING state) but produce no audio output. The same commands produce audio on the 2.5 model.

**Root cause:** The 3.1 model's `send_client_content` path doesn't trigger TTS audio generation. Audio output only comes from real-time audio input via `send_realtime_input`.

**Workaround:** Keep the 2.5 model for text-to-audio scenarios, or send real-time audio to trigger voice responses.

### Issue 1: Stale Model Name
**Symptom:** Bot silent despite WebSocket connected and mic permission granted.

**Root cause:** The hardcoded model string didn't match any available model in B's account.

**Fix:** Updated the LIVE_MODEL constant to a valid, available model from the models list.

### Issue 2: Missing MIME Sample Rate
**Symptom:** Same — bot quiet after WebSocket connected.

**Root cause:** The audio mime_type in BidiGenerateContentSetup was `"audio/pcm"` without the required sample rate parameter.

**Fix:** Changed to the proper format: `"audio/pcm;rate=16000"`.

### Issue 3: Faster Model
**Change:** Swapped to a newer-gen Live model for lower latency.

### Issue 4: `media`→`audio` Protocol Migration (Gemini SDK 2.6.0 + 3.1 model)
**Symptom:** Bot connects fine, mic picks up audio, but no response. Server logs show `realtime_input.media_chunks is deprecated. Use audio, video, or text instead.` followed by a TaskGroup restart cycle.

**Root cause:** Gemini 3.1+ models no longer accept the deprecated `realtime_input.mediaChunks` WebSocket frame. The `send_realtime_input(media=...)` call in the Google GenAI SDK sends `mediaChunks` internally. The fix is to switch to `send_realtime_input(audio=types.Blob(...))` which sends the new `realtime_input.audio` frame.

**Fix applied in `main.py` `_send_realtime`:**
```python
# OLD:
await self.session.send_realtime_input(media=msg)
# where msg = {"data": ..., "mime_type": "audio/pcm;rate=16000"}

# NEW:
await self.session.send_realtime_input(
    audio=types.Blob(data=msg["data"], mime_type=msg["mime_type"])
)
```

**Diagnostic pattern:** Check process logs for the `media_chunks is deprecated` string. That's the smoking gun for this class of bug.

## Server Config (main.py constants)

- LIVE_MODEL — set to `models/gemini-3.1-flash-live-preview` (latest Live-native model)
- CHANNELS = 1
- SEND_SAMPLE_RATE = 16000
- RECEIVE_SAMPLE_RATE = 24000
- CHUNK_SIZE = 1024

## Port Verification

Check server is listening and healthy using ss and curl.
