---
title: Multi-Family Function Declarations in Gemini Live Bridge
skill: gemini-live-spotify-tools
last-updated: 2026-05-27
---

# Adding Non-Spotify Tools to the Gemini Live Voice Bridge

## Problem

The `setup_payload["tools"]` in bridge.py overwrites itself if one tool family does a plain dict assignment after another.

## Pattern Used: Prefix-Gated Dispatch

Each tool family gets its own `_DECLARATIONS` list and its own `_run_<family>_tool()` runner.

### Step 1 — Declare functions

```python
_WEB_FUNCTION_DECLARATIONS = [
    {
        "name": "web_search",
        "description": "Search the web...",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_extract",
        "description": "Extract full page content...",
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["urls"],
        },
    },
]
```

### Step 2 — Append (do not overwrite) in setup

```python
if SPOTIFY_VOICE_TOOLS_ENABLED:
    if "tools" not in setup_payload:
        setup_payload["tools"] = []
    setup_payload["tools"].append({"functionDeclarations": _SPOTIFY_FUNCTION_DECLARATIONS})
    logger.info("Spotify voice tools registered ... (count=%d)", len(_SPOTIFY_FUNCTION_DECLARATIONS))

if WEB_VOICE_TOOLS_ENABLED:
    if "tools" not in setup_payload:
        setup_payload["tools"] = []
    setup_payload["tools"].append({"functionDeclarations": _WEB_FUNCTION_DECLARATIONS})
    logger.info("Web voice tools registered ... (count=%d)", len(_WEB_FUNCTION_DECLARATIONS))
```

### Step 3 — Runner with graceful fallback

```python
def _ensure_hermes_agent_path() -> None:
    hermes_agent = Path.home() / ".hermes" / "hermes-agent"
    if str(hermes_agent) not in sys.path:
        sys.path.insert(0, str(hermes_agent))

def _run_web_tool(name, args):
    _ensure_hermes_agent_path()
    try:
        import tools.web_tools as web_tools
    except Exception as exc:
        return {"error": f"Web tools not available: {exc}"}
    # ... dispatch to web_search_tool / web_extract_tool, parse JSON
```

### Step 4 — Gateway dispatch by prefix

```python
async def _handle_tool_call(self, tool_call):
    for fc in function_calls:
        name = fc.get("name", "")
        if name.startswith("spotify_"):
            result = _run_spotify_tool(name, args)
        elif name.startswith("web_"):
            result = _run_web_tool(name, args)
        else:
            result = {"error": f"No handler for tool: {name}"}
```

This keeps the bridge extensible: add `homeassistant_`, `lights_`, etc. by following the same shape.
