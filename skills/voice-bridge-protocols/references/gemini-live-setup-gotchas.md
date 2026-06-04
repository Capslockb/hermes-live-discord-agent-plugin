# Gemini Live API Setup Gotchas

**Session:** 28 May 2026 — WebSocket 1007 on bridge startup due to invalid `generationConfig` field
**File:** `~/.hermes/plugins/discord-voice/bridge.py`

## mediaResolution: String vs Object (WebSocket 1007)

**The bug:** `bridge.py` set `"mediaResolution": "MEDIA_RESOLUTION_LOW"` inside `setup.generationConfig`.

**Why it fails:** The Gemini Live API `BidiGenerateContentSetup.generationConfig.mediaResolution` field is an **`object`** (`MediaResolution` message type), not a string. Passing the string enum `"MEDIA_RESOLUTION_LOW"` causes the server to reject the setup message with WebSocket close code **1007** — `Invalid value at 'setup.generation_config.media_resolution'`.

**The `generateContent` API is different:** That API accepts `"media_resolution": "MEDIA_RESOLUTION_LOW"` as a string inside `GenerationConfig`. The Live API is NOT the generateContent API. The setup payload schemas diverge.

**Fix:** Remove `mediaResolution` from `generationConfig` entirely in the Live API setup payload. The bridge already handles video cost optimization via:
- 1fps frame-rate gating in `feed_video_frame()`
- `turnCoverage: TURN_INCLUDES_ONLY_ACTIVITY` (bills only turns with speech activity)
- 512KB frame size limit
- Audio-gating before video send

These mechanisms make the per-frame `mediaResolution` setting unnecessary. Cost stays at ~$0.03–0.06/hour on Flex tier without it.

**Code fix (bridge.py):**
```python
# REMOVE these lines from _connect_model():
# setup_payload["generationConfig"]["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
# logger.info("Video mediaResolution set to LOW (~100 tokens/frame)")
```

**Where it lives:** `bridge.py` lines ~1420 and ~1461 (duplicate — one in initial dict, one overwritten later).

**Detection:** Startup logs show WebSocket 1007 immediately after `await self._ws.send(json.dumps(setup))`. No `setupComplete` ever arrives.

---

## turnCoverage: Default Changed in 3.1 Flash Live

**Gemini 2.5 Flash Live default:** `TURN_INCLUDES_ONLY_ACTIVITY` — bills only turns with detected audio activity. Silent video-only turns are NOT billed.

**Gemini 3.1 Flash Live default:** `TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO` — bills every frame sent, even in silent turns. This is the opposite of cost-optimal.

**Fix:** Explicitly set `turnCoverage: TURN_INCLUDES_ONLY_ACTIVITY` in `realtimeInputConfig` for ALL models, not just 2.5. The bridge already does this at `bridge.py:1429`.

```python
"realtimeInputConfig": {
    "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
    "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",  # ← required for cost control
    ...
}
```

**Why it matters:** Without this override, 3.1 Flash Live bills ~$0.50–1.00/hour just from silent video frames. With it, cost stays ~$0.03–0.06/hour.

---

## Model Name Conventions

**Current valid names (May 2026):**
- `gemini-3.1-flash-live-preview` — primary, low-latency, audio-to-audio
- `gemini-2.5-flash-native-audio-preview-12-2025` — deprecated March 19, 2026
- `gemini-2.5-flash-native-audio-preview-09-2025` — deprecated March 19, 2026

**The bridge's default model** at `bridge.py:49` is `gemini-3.1-flash-live-preview`.

**Fallback chain:** The bridge tries the primary model first, then falls back through `GEMINI_LIVE_MODEL_FALLBACKS`. If ALL models fail, it raises `RuntimeError("No Gemini Live model could start")`.

**Model-specific behavior differences:**

| Feature | 3.1 Flash Live | 2.5 Flash Live |
|---|---|---|
| Thinking | `thinkingLevel` (minimal/low/medium/high) | `thinkingBudget` (token count) |
| Response delivery | Multi-part events (audio + transcript simultaneously) | Single-part events |
| `clientContent` usage | Only for initial history seeding | Supported throughout session |
| Async function calling | NOT supported | Supported (`NON_BLOCKING`) |
| Proactive audio | NOT supported | Supported (v1alpha) |
| Affective dialogue | NOT supported | Supported (v1alpha) |

**Migration note:** When switching from 2.5 to 3.1, review any code that relies on `clientContent` after the first turn — 3.1 requires `send_realtime_input(text=...)` instead.

---

## Setup Payload Schema (Authoritative Excerpt)

From [ai.google.dev/api/live](https://ai.google.dev/api/live) (March 2026):

```json
{
  "setup": {
    "model": string,
    "generationConfig": {
      "candidateCount": integer,
      "maxOutputTokens": integer,
      "temperature": number,
      "topP": number,
      "topK": integer,
      "presencePenalty": number,
      "frequencyPenalty": number,
      "responseModalities": [string],
      "speechConfig": object,
      "mediaResolution": object   // ← object, NOT string
    },
    "systemInstruction": string,
    "tools": [object],
    "realtimeInputConfig": { ... },
    "inputAudioTranscription": {},
    "outputAudioTranscription": {},
    "sessionResumption": { "handle": string }
  }
}
```

**Critical:** `setup` is sent as the FIRST message after WebSocket open. You cannot update config mid-session. The only way to change parameters is to disconnect and reconnect.

---

## Cost-Control Checklist

For a voice bridge running Gemini 3.1 Flash Live with video frames:

1. ✅ Remove `mediaResolution` from `generationConfig` (prevents 1007)
2. ✅ Set `turnCoverage: TURN_INCLUDES_ONLY_ACTIVITY` (prevents silent video billing)
3. ✅ Set `mediaResolution: LOW` at the **per-part level** for individual video frames (v1alpha only, Gemini 3 only)
4. ✅ Gate video to 1fps maximum in bridge code
5. ✅ Audio-gate video sends (only send frames during active speech turns)
6. ✅ Cap frame size at 512KB in bridge code
7. ✅ Use `mediaResolution: LOW` in `generateContent` API calls (if any) — this API accepts the string

**Target cost:** ~$0.03–0.06/hour on Flex tier.
