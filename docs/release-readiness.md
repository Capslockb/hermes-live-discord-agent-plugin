# Release readiness truth table

This page is the cross-exam checklist for the Gemini bridge second release. It separates code that is present in this repository from sibling systems, roadmap items, and integration research.

## Status legend

| Status | Meaning |
|---|---|
| **WORKING** | Code exists in this repository and has an executable verification path. |
| **PARTIAL** | Code exists, but it depends on local services, credentials, CLIs, or a manual feeder. |
| **INCLUDED / VERIFY WIRING** | Code exists, but the install path must be checked so the feature is actually registered at runtime. |
| **SIBLING / NOT BUNDLED** | A separate plugin/service may exist, but it is not shipped in this repository. |
| **RESEARCH** | Mention only as an integration target until code and tests land. |

## Current repository truth table

| Feature / claim | Evidence in repo | Verification command | Status | Release wording |
|---|---|---|---|---|
| Gemini Live Discord voice bridge | `plugin/bridge.py`, `plugin/__init__.py`, `/voice-live` command registration | `/voice-live`, then `curl -s http://127.0.0.1:18943/health` | **WORKING** | “Gemini Live Discord voice bridge.” |
| Discord slash commands | `ctx.register_command(name="voice-live")`, `ctx.register_command(name="voice-live-leave")` | Discord slash picker or gateway logs | **WORKING** | “Discord slash commands for join/leave.” |
| Sidecar API | `DISCORD_VOICE_LIVE_PORT`, `_control_get`, `/health`, `/frame`, `/say`, `/notes`, `/notify` docs/code paths | `curl -s http://127.0.0.1:18943/health` | **WORKING** | “Local control API on 127.0.0.1.” |
| Manual image/frame input | `voice_live_frame`, `/frame`, video feeder docs | `voice_live_frame file_path=...` or `curl -X POST /frame` | **PARTIAL** | “Manual frame feed; not automatic Discord screenshare capture.” |
| Function calling | Gemini Live bridge tool declarations and Hermes tool registration | Watch Gemini `toolCalls` and tool responses in logs | **PARTIAL** | “Function calling; backend tools depend on local auth/install.” |
| Multi-CLI fallback | `delegation_agent.py`, fallback docs | `local_delegate_health` / CLI-specific smoke tests | **PARTIAL** | “Multi-CLI delegation when CLIs are installed.” |
| Notifications | `notification.py`, `/notify`, notification docs | `curl -X POST .../notify` | **PARTIAL** | “Proactive notifications, backend-dependent.” |
| Email brief | `email_brief.py`, email docs | `local_email_brief` with Gmail backend configured | **PARTIAL** | “Email brief when Gmail backend is configured.” |
| Honcho context | `VOICE_LIVE_HONCHO_CONTEXT`, profile/Honcho paths | `sora_bridge_preflight` should report config/peer state | **PARTIAL** | “Honcho context if configured; fail loud when missing.” |
| SORA preflight/grill/goal/redact | `plugin/sora_bridge_elements.py`, `plugin/plugin.yaml`, `installer/enable_sora_bridge_elements.py` | Run patcher, compile, then call the four SORA tools | **INCLUDED / VERIFY WIRING** | “SORA bridge elements are included; run the patcher if not wired.” |
| Vapi bridge | Only sibling references in this repository | Search repo for real `voice_vapi` implementation | **SIBLING / NOT BUNDLED** | “Sibling Vapi transport, not bundled in this repo.” |
| MCP mode | No first-class MCP server/client code found in this repo docs pass | Search for MCP adapter/server code | **RESEARCH** | “MCP adapter target.” |
| Dograh | No Dograh implementation found in this repo docs pass | Search for Dograh bridge/adapter code | **RESEARCH** | “Dograh comparison target, not shipped.” |

## Hard release rules

1. Do not call Vapi, Dograh, or MCP “supported” unless this repository contains working code and a test command.
2. Do not say the model can see Discord screenshare automatically. It cannot; use screenshots or a frame feeder.
3. Do not claim optional backends work without checking their credentials and local binaries.
4. Do not leak secrets in preflight, transcripts, goals, Discord messages, GitHub issues, or docs.
5. The README, docs-site, and raw docs must use the same status labels.

## E2E smoke checklist

```bash
# wire optional SORA helpers into the plugin entrypoint if needed
python3 installer/enable_sora_bridge_elements.py

# syntax smoke test
python3 -m py_compile plugin/*.py installer/enable_sora_bridge_elements.py

# restart runtime
systemctl --user restart hermes-gateway
journalctl --user -u hermes-gateway -n 100 --no-pager

# start from Discord, then check local sidecar
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

Hermes tool checks:

```text
voice_live_status
voice_live_notes limit=10
sora_bridge_preflight
sora_redact text="Authorization: Bearer fake.fake.fake"
sora_live_grill text="Migrate SORA bridge features into Gemini bridge and verify docs/code accuracy."
sora_goal_synth text="Migrate SORA bridge features into Gemini bridge and verify docs/code accuracy."
```

## Release-blocking gaps

- If `sora_bridge_preflight` is not available after restart, the SORA patcher did not wire `plugin/__init__.py` in the deployed copy.
- If `/health` fails after `/voice-live`, the bridge did not start or the sidecar port differs from `DISCORD_VOICE_LIVE_PORT`.
- If video docs imply automatic Discord screenshare vision, fix them before release.
- If Vapi/Dograh/MCP are mentioned as active features without code, downgrade them to sibling/research wording.
