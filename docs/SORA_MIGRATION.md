# SORA migration / bridge elements

This page explains how the standalone SORA wrapper relates to the Gemini Live Discord bridge, and exactly what is imported in v2.

## What this repo is

This is the **Hermes Live Discord Agent Plugin** — a full-duplex Discord voice bridge to Google Gemini Multimodal Live. That identity has not changed in v2.

## What SORA is

SORA is a broader agent runtime built around:

- voice/video production pipelines,
- autonomous goal/subgoal orchestration,
- multi-bridge federation (Gemini + Vapi),
- MCP tooling,
- Discord-native progress emitters,
- and agents like Dograh.

SORA is a separate project. This repo selectively imports the pieces that make the Gemini bridge safer and more operator-friendly **without** turning it into SORA.

## What got imported (v2)

| SORA element | Gemini bridge form | Status |
|---|---|---|
| `sora_bridge_preflight` | `plugin/sora_bridge_elements.py` | **WORKING** |
| `sora_live_grill` | `plugin/sora_bridge_elements.py` | **WORKING** |
| `sora_goal_synth` | `plugin/sora_bridge_elements.py` | **WORKING** |
| `sora_redact` | `plugin/sora_bridge_elements.py` | **WORKING** |
| Installer patcher | `installer/enable_sora_bridge_elements.py` | **WORKING** |

These tools are registered automatically when the gateway loads the plugin. They only read local env/config files and call the local sidecar `/health` endpoint.

### What each helper does

| Tool | Purpose | Input |
|---|---|---|
| `sora_bridge_preflight` | Verify env, model, Honcho config, sidecar health, and notes dir before trusting a voice session. | none (reads env) |
| `sora_live_grill` | Analyze a transcript and ask hard questions: objective, constraints, owner, deadline, risk, next command, verification test. | transcript text |
| `sora_goal_synth` | Generate a Discord-safe `/goal` plus ranked `/subgoal` items for weak models. | transcript text |
| `sora_redact` | Strip API keys, bearer tokens, JWTs, webhooks, and Discord secrets from text before logs/Discord/Gemini. | text |

## What did **not** get imported

| SORA element | Status in this repo | Reason |
|---|---|---|
| Vapi bridge federation | **PLANNED** | Kept in a separate repo until API and lifecycle stabilize. |
| MCP orchestration server | **RESEARCH** | Hermes already has a native MCP client; evaluating whether this plugin should ship its own server. |
| Dograh agent | **RESEARCH** | No runtime loop in this repo yet. |
| SORA progress sidecar (Discord edit-in-place) | **PLANNED** | Would require new Discord-message editing surface in the bridge. |
| SORA video production pipeline | **PLANNED** | Out of scope for a voice bridge. |

## How to use the imported tools

```text
sora_bridge_preflight
sora_live_grill text="long call transcript here"
sora_goal_synth text="long call transcript here"
sora_redact text="paste text with secrets here"
```

Or directly in Python:

```python
import asyncio, sys
sys.path.insert(0, "plugin")
from sora_bridge_elements import build_preflight_report
print(asyncio.run(build_preflight_report()))
```

## Safety rules

- No public ports are opened by SORA elements.
- No new HTTP listener is added by SORA elements.
- Secrets are redacted before Discord/Gemini/log usage.
- Honcho is only inspected via existing local config paths.
- A failure in `register_sora_bridge_tools()` is caught and logged; it cannot break `voice_live`.

## When to look at SORA instead

If you need:

- autonomous agent loops beyond a single voice session,
- Vapi outbound-call federation,
- SORA video generation or editing,
- MCP-first orchestration,

then you want the SORA project, not this plugin. This plugin will continue to point users toward SORA for those use cases.

## Honesty note

v2 is not "Gemini bridge with SORA pasted on top." It is the Gemini bridge, plus four small, proven, safe SORA helpers that improve operator diagnostics and post-call analysis. Everything else stays in SORA until it is stable enough to bring over.
