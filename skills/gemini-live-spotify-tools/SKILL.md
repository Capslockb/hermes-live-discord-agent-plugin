---
name: gemini-live-spotify-tools
version: 1.0
description: |
  Integrate external data sources — tools, search, content extract, and conversational
  memory (Honcho) — into the Gemini Live voice bridge via native functionDeclarations
  and systemInstruction customization. The bridge passes schemas at WebSocket setup time
  and handles toolCall / toolResponse messages inline, without leaving the low-latency
  audio loop. Memory context is injected at session start by distilling peer
  representations and conclusions into the system prompt before `setup`.

  Built for the Discord Voice Live bridge plugin, but the pattern applies to
  any Gemini Live WebSocket client that wants to expose local tool APIs.
requires: []
pitfalls:
  - sys.path must include the Hermes agent directory for cross-plugin imports to resolve
  - Gemini Live sends toolCall at the top-level of server messages, NOT inside serverContent
  - toolResponse JSON shape is a dict with key toolResponse containing functionResponses array
  - functionDeclarations go under setup.tools as a list of dicts
  - The tool name in the declaration must match the name in toolCall exactly
---

# Gemini Live Spotify Tool Integration

## What it does

During a Discord voice call with Gemini Live, the AI can directly control Spotify:
- **play / pause / skip / previous** — basic playback
- **set_volume** — 0-100
- **get_state** — what's playing, active device, progress
- **search** — find tracks/albums/artists/playlists
- **add_to_queue** — queue a track by URI

All without leaving the voice channel. The bridge handles the tool calls inline inside the WebSocket receive loop.

## How it works

### 1. Declare functions at setup time

In the `setup` message sent after WebSocket connect, add a `tools` key:

```python
setup_payload = {
    "model": f"models/{model}",
    "generationConfig": {...},
    "systemInstruction": {...},
    "tools": [
        {"functionDeclarations": [
            {
                "name": "spotify_play",
                "description": "Start or resume Spotify playback...",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uris": {"type": "array", "items": {"type": "string"}},
                        "context_uri": {"type": "string"},
                        "device_id": {"type": "string"},
                    },
                },
            },
            # ... more declarations
        ]}
    ],
}
```

### 2. Handle incoming `toolCall` messages

Gemini sends a top-level `toolCall` field (NOT inside `serverContent`):

```python
tool_call = msg.get("toolCall")
if tool_call:
    await self._handle_tool_call(tool_call)
```

Structure:
```json
{
  "toolCall": {
    "functionCalls": [
      {"id": "call-001", "name": "spotify_play", "args": {"context_uri": "..."}}
    ]
  }
}
```

### 3. Execute and reply with `toolResponse`

For each function call, run the matching local handler, then send:

```python
payload = {
    "toolResponse": {
        "functionResponses": [
            {
                "id": "call-001",
                "name": "spotify_play",
                "response": {"result": {...}}   # or {"error": "..."}
            }
        ]
    }
}
await ws.send(json.dumps(payload))
```

### 4. Cross-plugin import fallback

The bridge lives in `~/.hermes/plugins/discord-voice/` but imports `plugins.spotify.tools` from `~/.hermes/hermes-agent/`. The import path may not be on `sys.path` in all contexts. The handler tries the direct import first, then falls back to injecting `~/.hermes/hermes-agent` into `sys.path`:

```python
try:
    import plugins.spotify.tools as spotify_tools
except Exception:
    hermes_agent = Path.home() / ".hermes" / "hermes-agent"
    if str(hermes_agent) not in sys.path:
        sys.path.insert(0, str(hermes_agent))
    import plugins.spotify.tools as spotify_tools
```

## Toggle

Set env var to disable:
```
DISCORD_VOICE_LIVE_SPOTIFY_TOOLS=false
```

Default is `true` (enabled if the Spotify plugin is installed and authenticated).

## System prompt hint

Add a short sentence to the Gemini system instruction so the model knows it can ask about Spotify:

> "You can also control Spotify playback during voice calls — play/pause/skip/search/volume — just ask or mention what you want to hear."

## Files touched

- `~/.hermes/plugins/discord-voice/bridge.py` — the bridge itself

## Multi-tool-family setup warning

When two or more tool families register in the same `setup_payload` (e.g. Spotify + web tools), NEVER do this:

```python
# WRONG — silently clobbers any earlier tool declarations
setup_payload["tools"] = [{"functionDeclarations": [...}]  # wipes Spotify!
```

Always append instead:

```python
# CORRECT — preserves every family's declarations
if "tools" not in setup_payload:
    setup_payload["tools"] = []
setup_payload["tools"].append({"functionDeclarations": spotify_decls})
# ... later ...
setup_payload["tools"].append({"functionDeclarations": web_decls})
```

Log the total count of declarations as a cheap sanity check.

## Extending to other tools

The pattern is generic:
1. Build a `_FUNCTION_DECLARATIONS` list for your tools.
2. Append them to `setup_payload["tools"]` (see **Multi-tool-family setup warning** above).
3. Implement `_run_<domain>_tool(name, args)` to dispatch.
4. Wire `_handle_tool_call()` to call the dispatcher by prefix or a registry dict.
5. Send `toolResponse` with the results.

No changes to the Hermes tool registry or gateway are needed — everything stays inside the bridge's WebSocket loop.

## Additional references

- `references/multi-family-function-declarations.md` — full pattern and code for adding non-Spotify tool families (web search, etc.) to the same bridge without clobbering existing declarations.
- `references/honcho-context-injection.md` — how to inject dynamic memory context (Honcho peer profiles, conclusions) into the Gemini Live `systemInstruction` at session start, with token budgeting and pitfall guidance.