---
title: Injecting External Memory Context into Gemini Live Sessions
skill: gemini-live-spotify-tools
last-updated: 2026-05-27
---

# Context Injection & Memory Integration

Gemini Live accepts `systemInstruction` exactly once — inside the `setup` message sent immediately after WebSocket connect. There is no API to update it mid-session.

## The constraint

```python
# bridge.py — systemInstruction is built here, then FROZEN
setup_payload = {
    "model": f"models/{model}",
    "systemInstruction": {
        "parts": [{"text": "<static prompt>"}]
    },
    "tools": [...],
}
await ws.send(json.dumps({"setup": setup_payload}))
# ← After this point, systemInstruction cannot be changed
```

Three ways to get dynamic context into the model, ranked by quality:

| Approach | When it happens | Audio interruption? | Quality |
|---|---|---|---|
| **Prepend at `_connect_model` time** | Once per session start | No | ✅ Best |
| Force reconnect with new `setup` | On explicit reconnect | ~2s dropout | ⚠️ Acceptable |
| Send text "[Context refresh: ...]" mid-session | Any time | Gemini reads it aloud | ❌ Janky |

## Recommended pattern: async context fetch before setup

Query your memory layer right before `_connect_model` builds the payload, then prepend a distilled summary:

```python
async def _fetch_context(self) -> str:
    """Returns a short context string to prepend to systemInstruction."""
    # This example uses Honcho, but the pattern applies to any memory store.
    # Built-in tools are preferred over raw API calls (see self-host-honcho).
    profile = await self._query_honcho_profile()
    recent = await self._query_honcho_search("recent context")
    # Distill aggressively — Gemini Live system prompts are token-budgeted
    return f"User context:\n{profile}\nRecent activity:\n{recent}"

# In _connect_model, before setup_payload is built:
context = await self._fetch_context()
base_prompt = "You are S0RA, the AI companion of Capslockb..."
full_prompt = f"{context}\n\n{base_prompt}" if context else base_prompt
setup_payload["systemInstruction"]["parts"][0]["text"] = full_prompt
```

## Token budget guidance

The base prompt in the current bridge is ~600 tokens. To stay under Gemini Live's implicit system prompt ceiling:
- **Injected context:** ~300-500 tokens (~1,200 chars)
- **Base prompt:** ~600 tokens
- **Total:** ~900-1,100 tokens

If you need more context, switch to the text-injection approach with a `[Context: ...]` prefix on the first human message instead of the system instruction.

## Self-Hosted Honcho: Direct SDK/HTTP Pattern

When `AUTH_USE_AUTH=true` on a self-hosted Honcho instance, the built-in
Hermes `honcho_profile` / `honcho_search` tools may 401 because the gateway's
memory plugin uses a different auth resolution path than the voice bridge. The
reliable approach is **direct SDK calls from the bridge**.

### SDK import path (bypasses package shadow)

The PyPI package `honcho` (v2.0.0) is the AI SDK but its `__init__.py` only
exports `__version__`. Import from the submodule directly:

```python
from honcho.client import Honcho  # NOT from honcho import Honcho
```

### Building the context string

```python
async def _build_honcho_context() -> str:
    # 1. Resolve credentials from honcho.json (not env vars alone)
    hermes_home = os.path.expanduser("~/.hermes")
    with open(os.path.join(hermes_home, "honcho.json")) as f:
        data = json.load(f)

    host = data.get("hosts", {}).get("hermes", {})
    base_url = host.get("baseUrl") or data.get("baseUrl") or "http://127.0.0.1:8000"
    workspace = host.get("workspace") or data.get("workspace") or "hermes"
    api_key = host.get("apiKey") or data.get("apiKey")
    # honcho.json peerName takes priority over env-derived snowflake IDs
    peer_name = (
        host.get("peerName") or data.get("peerName")
        or host.get("peer_name") or data.get("peer_name")
        or os.getenv("HONCHO_PEER_NAME")
        or os.getenv("DISCORD_VOICE_LIVE_USER_ID")
        or "user"
    )

    # 2. Call the SDK directly (async wrapper for sync SDK)
    h = Honcho(workspace_id=workspace, base_url=base_url, api_key=api_key)
    peer = h.peer(peer_name)

    # 3. Fetch memory layers
    try:
        rep_data = peer.representation()
        representation = rep_data.get("content", rep_data) if isinstance(rep_data, dict) else rep_data
    except Exception as e:
        representation = ""
    try:
        card = peer.card()
    except Exception as e:
        card = ""

    # 4. Format and truncate
    parts = ["--- USER MEMORY CONTEXT ---"]
    if representation:
        parts.append(representation)
    if card:
        parts.append("--- USER CARD ---")
        parts.append(card)
    parts.append("--- END CONTEXT ---")

    full = "\n\n".join(parts)
    max_chars = int(os.getenv("VOICE_LIVE_HONCHO_MAX_CHARS", "1200"))
    return full[:max_chars] if len(full) > max_chars else full
```

### Why direct SDK over built-in tools

| Approach | Auth path | Peer resolution | Works self-hosted? |
|---|---|---|---|
| Built-in `honcho_profile()` | Gateway memory plugin | Discord snowflake | ❌ Often 401 |
| Direct SDK (`honcho.client`) | `honcho.json` `apiKey` | `honcho.json` `peerName` | ✅ Yes |
| Raw `httpx` + JWT | Manual header | Manual URL | ✅ Yes (fallback) |

### Honcho v3 REST endpoints used by SDK

- `POST /v3/workspaces` — list/create workspace (returns `{"items": [...]}`)
- `POST /v3/workspaces/{ws}/peers/{peer_id}/representation` — fetch peer memory
- `GET /v3/workspaces/{ws}/peers/{peer_id}/card` — fetch peer card
- `POST /v3/workspaces/{ws}/peers` — list peers (returns `SyncPage`, use `list()`)

### Critical: Self-Hosted JWT Token Format

Self-hosted Honcho v3's `verify_jwt()` has a type-conflict bug: `exp` as string
→ 401, `exp` as int → 500, `exp` absent → 200. The **only** working admin
JWT is `{"t": "", "ad": True}` with **no `exp` claim**. See
`self-host-honcho` skill Step 2 for the full bug description and workaround.

### Peer name resolution priority

In multi-platform setups (Discord snowflake vs human username), place the
human peer name in `honcho.json`:

```json
{
  "baseUrl": "http://127.0.0.1:8000",
  "workspace": "hermes",
  "apiKey": "<admin-jwt>",
  "peerName": "caps"
}
```

The bridge checks `honcho.json` `peerName` before falling back to env vars, so
the voice bridge targets the human peer (`caps`) with rich memory instead of the
Discord snowflake (`1474100257762578597`).