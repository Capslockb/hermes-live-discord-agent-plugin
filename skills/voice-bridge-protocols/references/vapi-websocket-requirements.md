# Vapi.ai WebSocket Transport Requirements

## API: Transient Calls (WebSocket Mode)

Endpoint: POST `https://api.vapi.ai/call`  
Auth: Bearer token via `Authorization: Bearer {VAPI_API_KEY}` header  
Content-Type: `application/json`

### Request body: `assistantId` mode (preferred)

When a saved Vapi assistant exists (dashboard-created), use `assistantId` to inherit all settings:

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

**Inherits:** voice settings, model, tools, transcriber, fallbacks, first message, start/stop speaking plans, compliance settings.

### Request body: inline assistant config (fallback)

```json
{
  "name": "Discord Voice Bridge",
  "assistant": {
    "voice": {
      "provider": "11labs",
      "voiceId": "cjVigY5qzO86Huf0OWal"
    },
    "model": {
      "provider": "openai",
      "model": "gpt-4o"
    }
  },
  "transport": {
    "provider": "webSocket",
    "webSocket": {
      "client": {
        "audio": {
          "format": "pcm_s16le",
          "container": "raw",
          "sampleRate": 16000
        }
      }
    }
  }
}
```

Key requirements:
- **Audio format** MUST be `"pcm_s16le"` in `"container": "raw"`
- **Sample rate** MUST be `"16000"` Hz (matches Vapi's expected input)
- **Transport provider** MUST be `"webSocket"` or `"vapi.websocket"` (not `"twilio"`, `"telnyx"`, `"plivo"`, `"vonage"`, `"webRtc"`, or `"sip"`)
- **Voice provider** maps to the TTS engine (e.g. `"11labs"`)
- **Model provider** maps to the LLM backend (e.g. `"openai"`)

### Response fields

```json
{
  "id": "call-id-string",
  "transport": {
    "websocketCallUrl": "wss://s0.vapi.ai/calls/{id}/transport"
  }
}
```

## WebSocket Connection

### URL construction

If `websocketCallUrl` does not end with `/transport`, append it:

```python
from urllib.parse import urlparse
ws_url = transport.get("websocketCallUrl", "")
parsed = urlparse(ws_url)
if not parsed.path.endswith("/transport"):
    ws_url = ws_url.rstrip("/") + "/transport"
```

Most URLs returned by the API already include `/transport`, but defensive code prevents duplicate-suffix bugs.

### Connection options

```python
self._ws = await websockets.connect(
    ws_url,
    ping_interval=20,   # library-level WebSocket ping/pong
    ping_timeout=10,
)
```

Do NOT disable pings. Vapi expects standard WebSocket ping/pong for connection liveness.

## Send Format — Raw Binary PCM Only

Vapi's WebSocket transport accepts **only raw binary PCM** frames. Any non-binary message (text/JSON) causes immediate close with code 1005.

### CORRECT

```python
# chunk is signed 16-bit PCM, mono, 16kHz
await self._ws.send(chunk)  # bytes object
```

### WRONG (causes 1005 disconnect)

```python
await self._ws.send(json.dumps({"type": "keepalive"}))  # text frame — REJECTED
await self._ws.send(b'\x00' * 320)  # raw bytes — OK but should be real PCM
```

### Audio format from Discord

Discord gives PCM at **48kHz stereo**. Downsample to **16kHz mono** before sending:

- Sample rate: 48000 → 16000 Hz (divide by 3)
- Channels: 2 → 1 (average or left channel)
- Format: numpy int16 array

Common frame size after resample: ~640 bytes (20ms @ 16kHz mono s16le).

## Receive Format

Vapi sends two message types on the same WebSocket:

### 1. Text frames — JSON control/status

```python
raw = await self._ws.recv()
if isinstance(raw, str):
    msg = json.loads(raw)
    msg_type = msg.get("type")
    if msg_type == "conversation-update":
        status = msg.get("status")
        if status == "interrupted":
            # Clear queued audio; user interrupted the bot
            pass
        elif status == "ended":
            # Conversation ended
            pass
    elif msg_type == "transcript":
        text = msg.get("text", "")
        # Optional: detect leave commands in transcript
```

### 2. Binary frames — PCM audio response

```python
if isinstance(raw, bytes):
    # raw is signed 16-bit PCM, mono, 16kHz (Vapi output format)
    # Upsample to 48kHz stereo for Discord playback
    self._output_source.feed(raw)
```

Vapi PCM output characteristics:
- **Sample rate**: 16000 Hz (mono)
- **Format**: s16le (signed 16-bit little-endian)
- **Packet size**: variable, typically 640 bytes per 20ms frame

## Keepalive

Do NOT implement application-level keepalive. The `websockets` library's built-in `ping_interval=20` handles liveness. Sending JSON/text keepalive messages causes immediate connection drops.

## Pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `1005` close on connect | JSON/text sent instead of binary | Remove any `send(json.dumps(...))` from send loop |
| `1005` ~27s after connect | Vapi timed out waiting during Discord voice connect | Connect Vapi WebSocket BEFORE Discord voice (see ordering reference) |
| Silent on both sides | Opus decode failure in sink | Set `wants_opus() -> False` |
| Bot joins but no response | Wrong sample rate (e.g. 48000 instead of 16000) | Resample to 16kHz mono before sending |
| "Transport URL missing" | API response missing `websocketCallUrl` | Check that `transport.provider == "webSocket"` in request body |
| "corrupted stream" Opus errors | DAVE decrypt failing | Use passthrough fallback on any decrypt error |

## Environment Variables

```bash
VAPI_API_KEY=       # Required. Get from https://dashboard.vapi.ai/
VAPI_ASSISTANT_ID=  # Optional. Saved assistant ID for assistantId mode
VAPI_VOICE_PROVIDER=11labs    # or "openai", "deepgram", etc.
VAPI_VOICE_ID=      # Voice identifier from chosen provider
VAPI_MODEL=gpt-4o   # or "claude-3-5-sonnet", etc.
```

