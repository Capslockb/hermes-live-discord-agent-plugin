# docs/ — Hermes Live documentation

Hermes Live is the Gemini Live Discord voice bridge for Hermes. It ships voice I/O, local sidecar control, manual frame input, function calling, notification/email/SFX systems, and SORA-style operator helpers for preflight, transcript grilling, goal synthesis, and redaction.

This docs tree is intentionally split into **working**, **partial**, and **research/not bundled** language. Do not promote Vapi, Dograh, or MCP to shipped features unless code and tests exist in this repository.

## Index

| Doc | What it covers |
|---|---|
| [`quickstart.md`](quickstart.md) | Install, restart, first `/voice-live` session, local health check |
| [`architecture.md`](architecture.md) | End-to-end audio path, sidecar flow, threading model, lifecycle |
| [`sora-bridge-elements.md`](sora-bridge-elements.md) | SORA preflight, Live Grill Mode, `/goal` synthesis, redaction |
| [`release-readiness.md`](release-readiness.md) | Cross-exam truth table for working/partial/research claims |
| [`personality.md`](personality.md) | System prompt shape, ping-pong rhythm, boredom switch, vocal expression |
| [`fallback-chain.md`](fallback-chain.md) | Multi-CLI delegation health registry, `execute_with_fallback`, `local_delegate_health` |
| [`notification.md`](notification.md) | `local_notify` / `local_notify_schedule` / `POST /notify` / AFK pings |
| [`email-brief.md`](email-brief.md) | `local_email_brief` tool, scheduler, important/fyi/auto buckets |
| [`sfx-library.md`](sfx-library.md) | Slot-based sfx library, `local_sfx_test`, env vars, adding your own clips |
| [`sfx-credits.md`](sfx-credits.md) | YouTube source provenance, license, processing recipe |
| [`webhooks.md`](webhooks.md) | Event classes, emit helpers, env-var configuration |
| [`video.md`](video.md) | `/frame` HTTP endpoint, video-state detection, feeder, Discord screenshare limitation |
| [`env-vars.md`](env-vars.md) | Every `DISCORD_VOICE_LIVE_*` env var, defaults, descriptions |
| [`troubleshooting.md`](troubleshooting.md) | Common bridge failures, the Discord CDN handshake quirk, log locations |
| [`changelog.md`](changelog.md) | Release history; full changelog is `../CHANGELOG.md` |

## Quick reference

```bash
# Install
cd installer
./install.py
# or
./install.sh --from-local

# Wire SORA helpers if not already present in plugin/__init__.py
cd ..
python3 installer/enable_sora_bridge_elements.py
python3 -m py_compile plugin/sora_bridge_elements.py plugin/__init__.py

# Restart gateway to pick up plugin changes
systemctl --user restart hermes-gateway
journalctl --user -u hermes-gateway -f

# Use from Discord
/voice-live              # join your voice channel
/voice-live-leave        # leave

# Check bridge health after a session starts
curl -s http://127.0.0.1:18943/health | jq

# Sidecar control API
curl "http://127.0.0.1:18943/say?text=hello+from+sidecar"
curl -X POST --data-binary @frame.jpg -H "Content-Type: image/jpeg" "http://127.0.0.1:18943/frame?force=true"
curl -X POST -H "Content-Type: application/json" -d '{"text":"inbox is huge","mode":"dm"}' "http://127.0.0.1:18943/notify"
```

## SORA checks

```text
sora_bridge_preflight
sora_redact text="Authorization: Bearer fake.fake.fake"
sora_live_grill text="long call transcript here"
sora_goal_synth text="long call transcript here"
```

## What this plugin does NOT do

- It does not automatically see Discord screenshare/camera streams. Use a screenshot, `voice_live_frame`, or the feeder.
- It does not bundle the Vapi bridge implementation.
- It does not bundle a Dograh bridge implementation.
- It does not expose a first-class MCP server/client mode yet.
- It does not make optional backends work without their own local credentials, CLIs, and service configuration.
- It does not expose the sidecar as public production traffic; the sidecar is local-first on `127.0.0.1`.

For the public release truth table, see [`release-readiness.md`](release-readiness.md).
