# SORA bridge elements

SORA bridge elements are the operator-safety and planning helpers imported into the Gemini Live Discord bridge. They do **not** replace the Gemini transport. They add diagnostics, transcript cross-examination, goal/subgoal generation, and secret redaction around the existing bridge.

## Current status

| Item | Status | Notes |
|---|---|---|
| `plugin/sora_bridge_elements.py` | **Included** | Implements all four tools. |
| `plugin/plugin.yaml` | **Advertised** | Lists the four SORA tools under `provides_tools`. |
| `installer/enable_sora_bridge_elements.py` | **Included** | Idempotent patcher for `plugin/__init__.py`. |
| Runtime registration | **Verify after install** | If the deployed `plugin/__init__.py` lacks the marker below, run the patcher and restart Hermes. |

## What it adds

Tools registered by `sora_bridge_elements.register_sora_bridge_tools()`:

| Tool | Purpose |
|---|---|
| `sora_bridge_preflight` | Local diagnostic report for Gemini env/model, Honcho config paths, sidecar health, notes dir, and active bridge registry. |
| `sora_live_grill` | Analyzes a transcript/call and returns targeted questions for objective, constraints, owner, deadline, risk, next command, and verification test. |
| `sora_goal_synth` | Turns transcript/call text into a Discord-safe `/goal` plus ranked `/subgoal` items for weaker autonomous models. |
| `sora_redact` | Redacts API keys, bearer tokens, JWTs, Pocket keys, Discord webhooks, and GitHub tokens before text is sent to Gemini/Discord/logs. |

## Wire into the plugin

Run once from the repo root:

```bash
python3 installer/enable_sora_bridge_elements.py
python3 -m py_compile plugin/sora_bridge_elements.py plugin/__init__.py
systemctl --user restart hermes-gateway
```

The patcher is idempotent. It inserts this block inside `register(ctx)` before slash command registration:

```python
# SORA bridge elements: preflight/grill/goal synthesis/redaction
try:
    from sora_bridge_elements import register_sora_bridge_tools
    register_sora_bridge_tools(ctx, _bridge_mod, _active_bridges)
except Exception as exc:
    logger.warning("SORA bridge elements failed to register: %s", exc)
```

Verify the marker exists:

```bash
grep -n "SORA bridge elements" plugin/__init__.py
```

## Safety contract

- No HTTP listener is added.
- No public ports are exposed.
- Sidecar checks use `127.0.0.1` only.
- Secrets are redacted before Discord/Gemini/log usage.
- Honcho is only inspected via local config paths and existing sidecar context.
- Preflight should warn loudly on missing Gemini key, questionable model names, missing Honcho config, unreachable sidecar, or missing notes dir.

## Example use

```text
sora_bridge_preflight
sora_live_grill text="long call transcript here"
sora_goal_synth text="long call transcript here"
sora_redact text="Authorization: Bearer fake.fake.fake"
```

## Cross-exam checks before release

| Check | Expected result |
|---|---|
| `python3 -m py_compile plugin/sora_bridge_elements.py plugin/__init__.py` | No syntax errors. |
| `sora_redact text="Authorization: Bearer fake.fake.fake"` | Token string replaced by `[REDACTED]`. |
| `sora_live_grill text="migrate SORA into Gemini bridge"` | Returns objective/constraint/owner/test questions. |
| `sora_goal_synth text="migrate SORA into Gemini bridge"` | Returns one `/goal` and ranked `/subgoal` items. |
| `sora_bridge_preflight` | Returns JSON with Gemini, Honcho, sidecar, notes, active bridge, and warnings. |

## What this is not

- Not a Vapi bridge.
- Not a Dograh bridge.
- Not an MCP server.
- Not a public diagnostics endpoint.
- Not an automatic Discord screenshare/video capture layer.

Those belong in separate integration tracks until real code and tests exist.
