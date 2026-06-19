# SORA bridge elements

Adds SORA-style bridge helpers around the Gemini Live Discord bridge without opening ports or adding external services.

## What it adds

Tools registered by `sora_bridge_elements.register_sora_bridge_tools()`:

- `sora_bridge_preflight` — local diagnostic report for Gemini env/model, Honcho config paths, sidecar health, notes dir, and active bridge registry.
- `sora_live_grill` — analyzes a transcript/call and returns targeted questions for objective, constraints, owner, deadline, risk, next command, and verification test.
- `sora_goal_synth` — turns transcript/call text into a Discord-safe `/goal` plus ranked `/subgoal` items for weaker autonomous models.
- `sora_redact` — redacts API keys, bearer tokens, JWTs, Pocket keys, Discord webhooks, and GitHub tokens before text is sent to Gemini/Discord/logs.

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

## Safety

- No HTTP listener is added.
- No public ports are exposed.
- Sidecar checks use `127.0.0.1` only.
- Secrets are redacted before Discord/Gemini/log usage.
- Honcho is only inspected via local config paths and existing sidecar context.

## Example use

```text
sora_bridge_preflight
sora_live_grill text="long call transcript here"
sora_goal_synth text="long call transcript here"
sora_redact text="Authorization: Bearer ..."
```
