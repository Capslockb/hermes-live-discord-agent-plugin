---
name: gemini-live-audio
description: Setup, configuration, and troubleshooting for Gemini Live Audio API (bidiGenerateContent).
category: mlops
tags:
  - google
  - gemini
  - live-api
  - bidi-generate-content
  - websocket
  - voice-assistant
  - jarvis
  - audio-streaming
triggers:
  - 'user asks about jarvis, voice assistant, voice UI, or Gemini audio streaming'
  - 'bot is quiet / mic works but no response (silent-bot syndrome)'
  - 'WebSocket connection succeeds but speech output fails'
  - 'model not found or bad model name'
  - 'audio format errors from Gemini API'
  - 'debugging voice assistant server setup'
---

# Gemini Live Audio API

## Architecture

Gemini Live Audio uses **bidiGenerateContent** — a bidirectional WebSocket streaming API. The client sends audio chunks in real-time, Gemini processes them and streams back audio + text responses.

```
User Mic → PCM16 chunks → WebSocket → Gemini Live API → Audio response → Speaker
```

## Finding Live-Capable Models

Query the Gemini API for models that support `bidiGenerateContent`. Use the `models.list` endpoint with your `GEMINI_API_KEY` passed as query param `?key=`:

Open `https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY` in a browser or curl it and filter for models whose name includes "live" or "audio".

To check a specific model supports bidiGenerateContent, look for `"supportedGenerationMethods": ["bidiGenerateContent"]` in the model metadata.

### Known Live Models (as of May 2026)

| Model | Type | Notes |
|-------|------|-------|
| `models/gemini-3.1-flash-live-preview` | Flash (fast) | Latest gen, best latency |
| `models/gemini-2.5-flash-native-audio-latest` | Flash (fast) | Previous gen, stable |
| `models/gemini-2.5-flash-native-audio-preview-12-2025` | Flash (fast) | Older, may go stale |
| `models/gemini-2.5-flash-native-audio-preview-09-2025` | Flash (fast) | Older, may go stale |

`gemini-3.1-flash-live-preview` is the current recommended choice — newest architecture, same Flash tier latency, purpose-built for bidiGenerateContent.

## Critical: Audio MIME Type

**This is the #1 cause of "mic works but bot is quiet".**

Gemini's Live API requires the sample rate in the audio MIME type for incoming PCM audio:

```
# WRONG — bot will accept connection but never respond
"audio/pcm"

# RIGHT — bot processes your speech
"audio/pcm;rate=16000"
```

Check your server code for the MIME type string when configuring the `types.BidiGenerateContentSetup` object. The send sample rate is typically 16000 Hz (16kHz mono PCM).

## Model Naming Convention

Gemini Live models follow two naming patterns:

- **Live-native (recommended):** `models/gemini-X.Y-flash-live-preview` — explicit Live/bidi models
- **Audio-preview:** `models/gemini-X.Y-flash-native-audio-preview-YYYY` — audio-capable but older naming

Always prefer the `*-live-*` naming pattern when available — these are purpose-built for the bidirectional streaming API.

## Server Verification

After starting the server, verify it's listening:

```bash
ss -tlnp | grep <PORT>
curl -s -o /dev/null -w "%{http_code}" http://localhost:<PORT>/
```

## API Key Setup

The Gemini API key must be set. Priority order:
1. `GEMINI_API_KEY` env var
2. `GOOGLE_API_KEY` env var
3. Config file at `config/api_keys.json` (in the project directory)

A typical launcher flow reads env vars and writes them to a JSON config file for the WebUI server to consume.

## `send_realtime_input`: `media`→`audio` Protocol Migration

**Gemini 3.1+ models deprecated the `realtime_input.media_chunks` WebSocket frame.** The old `send_realtime_input(media=...)` call silently sends `mediaChunks` under the hood, which now causes a WebSocket close with:

```
realtime_input.media_chunks is deprecated. Use audio, video, or text instead.
```

### What changed

| Parameter | Old (2.5 models) | New (3.1+ models) |
|-----------|------------------|-------------------|
| API call | `send_realtime_input(media={"data": ..., "mime_type": "..."})` | `send_realtime_input(audio=types.Blob(data=..., mime_type="..."))` |
| WebSocket frame | `realtime_input.mediaChunks[...]` | `realtime_input.audio[...]` |
| Dict vs Blob | Passes a raw dict | Requires `types.Blob` wrapper |

### Migration example

```python
# BEFORE (works on 2.5, breaks on 3.1+)
await session.send_realtime_input(media=msg)
# where msg = {"data": audio_bytes, "mime_type": "audio/pcm;rate=16000"}

# AFTER (works on all Live models)
await session.send_realtime_input(
    audio=types.Blob(data=msg["data"], mime_type=msg["mime_type"])
)
# or directly:
await session.send_realtime_input(
    audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
)
```

### Detection

When a 2.5-era codebase hits a 3.1 model, the symptom is:
1. WebSocket connects successfully (server says ✅ Connected.)
2. User speaks → mic sends audio → **no response**
3. After a few seconds, the server reconnects
4. Server logs show the `media_chunks is deprecated` error

The error propagates as a WebSocket `1007` close code, which the Google GenAI SDK wraps in an `APIError`, which then crashes the TaskGroup containing `_receive_audio`.

## Common Failure Modes

### Silent Bot (mic works, connection succeeds, no response)
1. **MIME type missing sample rate** ← Most common fix (see above)
2. **Stale model name** — the model string in the server doesn't match an available model
3. **API key expired or invalid** — check `config/api_keys.json`
4. **`media`→`audio` protocol mismatch** — Gemini 3.1+ models deprecated `realtime_input.media_chunks`. Check server logs for:
   ```
   realtime_input.media_chunks is deprecated. Use audio, video, or text instead.
   ```
   If found, the `send_realtime_input(media=...)` call needs to switch to `send_realtime_input(audio=...)` (see migration section below).

### Bot gets stuck "Thinking" (state cycles LISTENING↔THINKING)
The "stuck thinking" user experience is often a **reconnection loop**: the bot connects successfully (LISTENING), receives audio or text (THINKING), then a task crashes → TaskGroup tears down → server calls `set_state("THINKING")` → 3s delay → reconnects (LISTENING) → repeat. The user sees "THINKING" most of the time because the listen window is brief.

1. **Check server logs** — `realtime_input.media_chunks is deprecated` means the `send_realtime_input(media=...)` protocol version mismatch (Gemini 3.1+). The error code is WebSocket `1007 (invalid frame payload data)`. See the `media`→`audio` migration section below.
2. **`ExceptionGroup` / TaskGroup crashes** — one of the four parallel tasks (`_send_realtime`, `_receive_audio`, `_listen_audio`, `_play_audio`) crashed, causing the entire TaskGroup to tear down. Identify which task failed from the traceback.
3. **Model or config mismatch** — if no task crash occurs but THINKING persists without response, the model may be ignoring the config (e.g. `response_modalities`, system instruction, or tools schema issue). Try `response_modalities=["AUDIO","TEXT"]` temporarily to verify the model produces output at all.

### Model Not Found
The model string must include the `models/` prefix: `"models/gemini-3.1-flash-live-preview"` not `"gemini-3.1-flash-live-preview"`.

### The Config Completeness Trap (Silent Response, No Error)

**Symptom:** Server connects to Gemini successfully, mic audio is received and forwarded to Gemini (you can see send_realtime logging), but the model produces ZERO audio response. No error, no crash, no WebSocket close — just silence.

**Root cause:** `LiveConnectConfig` fields that seem optional are actually required for audio output. Removing any of these silences the model with no diagnostic:

- `output_audio_transcription={}` — missing → model doesn't generate output audio
- `input_audio_transcription={}` — missing → no input processing trigger
- `session_resumption=types.SessionResumptionConfig()` — missing → model ignores audio entirely
- `tools=[...]` — missing → same silent behavior

The working `_build_config` must include ALL of these:

```python
return types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    output_audio_transcription={},
    input_audio_transcription={},
    system_instruction="\n".join(parts),
    tools=[{"function_declarations": TOOL_DECLARATIONS}],
    session_resumption=types.SessionResumptionConfig(),
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name="Charon"
            )
        )
    ),
)
```

**Diagnosis:** Add debug logging to `_send_realtime` to confirm audio bytes are reaching Gemini. If they are but no response returns, the config is missing one of the required fields above. Add them back one at a time until audio resumes.

### `send_client_content` (Text Commands) vs `send_realtime_input` (Audio)

**Symptom:** Typed/pasted text commands (sent via WebSocket `{"type": "text"}`) are processed by the model — state changes to THINKING — but produce NO audio on Gemini 3.1 models. The same text command produces audio on Gemini 2.5 models.

**Root cause:** `send_client_content(turns=...)` and `send_realtime_input(audio=...)` are different API paths. On 3.1 models, text-only turns via `send_client_content` do not trigger TTS audio generation even with `response_modalities=["AUDIO"]`. Audio output only occurs when the model receives real-time audio input via `send_realtime_input`.

**Workaround:** If audio output from text input is needed, stick with Gemini 2.5 Flash models (which handle text→audio correctly), or send a short dummy audio chunk via `send_realtime_input` immediately after the text command to trigger the model's voice output path.

### CRITICAL PITFALL: `mediaResolution` in `generationConfig` breaks Live API

**DO NOT add `media_resolution` or `mediaResolution` to the `generationConfig` dict of a `BidiGenerateContent` WebSocket setup.** This field belongs to the `generateContent` REST API, NOT the Live/bidi API. Adding it causes ALL models to fail with:

```
WebSocket close 1007: Invalid value at 'setup.generation_config.media_resolution'
```

The field is referenced in the API reference for `BidiGenerateContentSetup` as `mediaResolution: object`, but this appears to be a documentation bug or a field only valid in the SDK's protobuf layer (where it's a typed `MediaResolution` object, not a raw string), not in raw WebSocket JSON.

**Lesson:** If you want video cost control in the bridge, the correct approach is client-side gating (1fps, audio-gated, 512KB cap) + `turnCoverage: TURN_INCLUDES_ONLY_ACTIVITY` in `realtimeInputConfig`. Do NOT attempt to set media resolution in the setup.

**Symptom pattern:** Bridge fails to start, logs show 1007 errors on all fallback models, port 18943 never comes up. Removal of the single `media_resolution` line fixes it immediately.

### `turnCoverage` placement
- Check port binding: `ss -tlnp | grep 8765`
- Check Tailscale/network: Is the host reachable from client?
- Check CORS headers in WebSocket upgrade response

## Video Input & Cost Control

Gemini Live accepts video frames via the same `send_realtime_input` WebSocket path as audio. Each frame is tokenized and billed. **Cost control is mandatory** — ungated video can burn 900K+ tokens/hour.

### Critical billing parameters

These two fields in the `LiveConnectConfig` setup payload control video cost:

| Field | Default | Safe value | Tokens/frame | Impact |
|---|---|---|---|---|
| `mediaResolution` | `MEDIUM` | `LOW` | ~100 vs ~258 | 60% cheaper |
| `turnCoverage` | `TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO` | `TURN_INCLUDES_ONLY_ACTIVITY` | Bills every frame | Only bills when audio+video active |

```python
# CRITICAL: mediaResolution and turnCoverage do NOT go inside generationConfig
# for the Live API BidiGenerateContent. See pitfall below.
#
# CORRECT raw WebSocket setup (discord-voice bridge pattern):
{
    "generationConfig": {
        "responseModalities": ["AUDIO"],
        "speechConfig": { ... }
    },
    "realtimeInputConfig": {
        "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",
        ...
    }
}
# mediaResolution has NO valid placement in BidiGenerateContentSetup.
# It is a generateContent API parameter only — see pitfall.
```

### Frame gating rules (client-side)

Before calling `send_realtime_input(video=...)`, enforce:
1. **1fps max** — cap at 1 frame per second (Gemini hard limit is 1fps anyway)
2. **512KB max frame size** — JPEG/PNG only, reject oversized
3. **Audio-gated sending** — only send frames when speech was detected within last 8 seconds; silent video-only turns bill unnecessarily even with `TURN_INCLUDES_ONLY_ACTIVITY`
4. **Graceful degradation** — if frame capture fails, skip the frame rather than crash the stream

### Cost reality check

| Scenario | Tokens/hour | Free tier | Paid (Standard) |
|---|---|---|---|
| 1fps low-res + audio-gated | ~140K | ✅ Fits | ~$0.04/hr |
| 1fps default + audio-gated | ~300K | ✅ Fits | ~$0.08/hr |
| 1fps default, no gating | ~928K | ⚠️ Tight | ~$0.26/hr |

### External frame feeder pattern

For video sources that aren't native to the client (screen capture, webcam via FFmpeg), use a lightweight external feeder that POSTs frames to a local HTTP endpoint:

```python
# Feeder captures screen region via FFmpeg x11grab
# POSTs JPEG to http://127.0.0.1:18943/frame
# The receiving endpoint applies all gating before forwarding to Gemini
```

Key principle: **The bridge/endpoint is the gatekeeper.** The feeder can be spammy; the bridge enforces 1fps + size + audio-gating. This separates capture concerns from billing concerns.

### Video state awareness without video payload

Discord bots cannot receive video streams (platform restriction). The only signals available are voice-state flags (`self_stream`, `self_video`). Polling these and sending contextual text to Gemini provides awareness without burning tokens:

- *"Someone started screen sharing. I can't see the shared screen, but I know it's active."*
- *"Someone turned on their camera. I can't see the video feed, but I know it's on."*

This is zero-token video awareness — Gemini responds verbally to the text cue.

See also `discord-voice-ops` skill for the full bridge operational context (voice state polling implementation, autostart, post-call analysis).

## Verification Checklist

- [ ] Model name includes `models/` prefix
- [ ] Audio MIME type includes `;rate=16000`
- [ ] `turnCoverage` set to `TURN_INCLUDES_ONLY_ACTIVITY` in `realtimeInputConfig` (NOT in `generationConfig`)
- [ ] Frame rate gated to ≤1fps client-side
- [ ] Audio-gating prevents silent-video-only billing
- [ ] Do NOT add `mediaResolution`/`media_resolution` to `generationConfig` — it's invalid in BidiGenerateContent
- [ ] API key is set and valid
- [ ] Server is listening on expected port
- [ ] Client can reach server via network (Tailscale, localhost, etc.)
- [ ] WebSocket handshake completes
- [ ] Audio chunks are 16-bit PCM, 16kHz, mono

## Diagnostics: Debug Instrumentation Pattern

When tracing silent-bot or stuck-thinking issues, add `print(... flush=True)` at each stage of the audio pipeline:

1. **`push_audio`** — confirm bytes arrive from the browser (see script at `scripts/test-websocket-audio.py`)
2. **`_listen_audio` (override)** — confirm bytes leave the queue and enter `out_queue`
3. **`_send_realtime`** — confirm bytes are forwarded to Gemini via `send_realtime_input`
4. **`_receive_audio`** — log each `response.data` / `server_content` / `tool_call` event

```python
# Pattern: add to _send_realtime
print(f"[JARVIS] 📤 send_realtime {len(msg['data'])} bytes mime={msg['mime_type']}", flush=True)

# Pattern: add to _receive_audio receive loop
print(f"[JARVIS] 📥 recv: data={response.data is not None} server_content={response.server_content is not None}", flush=True)
```

If bytes reach `_send_realtime` but no response comes back, the issue is in what Gemini receives (wrong model, missing config fields, bad mime_type). If bytes don't reach `_send_realtime`, the issue is in the browser→server WebSocket path.

### Exec Pitfall (Output Capture)

When starting the server with `exec python3 ...` (via `PYTHONUNBUFFERED=1 exec python3 ... 2>&1`), the background process output capture may show an empty log even though the process is running fine. This is because `exec` replaces the shell process, and the tracking mechanism may follow the replaced PID differently.

**Prefer:** Starting without `exec`, or redirecting to a file:
```bash
python3 -u webui.py --host 0.0.0.0 --port 8765 > /tmp/jarvis.log 2>&1
```

**Verification:** Always confirm with `ss -tlnp | grep <PORT>` and `curl` regardless of what the process log shows.

## Scripts

- `scripts/test-websocket-audio.py` — Standalone WebSocket test client. Sends tone/text and verifies audio response. Usage: `python3 scripts/test-websocket-audio.py --mode tone`

## References
