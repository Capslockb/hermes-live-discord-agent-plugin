# docs/ — discord-voice plugin documentation

The plugin is a Discord voice bridge backed by the Gemini Multimodal Live API. Beyond basic voice I/O it ships a personality system, a fallback chain for multi-CLI delegation, a proactive notification system, an email brief scheduler, and a slot-based UI sfx library.

## Index

| Doc | What it covers |
|---|---|
| [`architecture.md`](architecture.md) | End-to-end audio path, threading model, lifecycle |
| [`personality.md`](personality.md) | System prompt shape, ping-pong rhythm, boredom switch, vocal expression |
| [`fallback-chain.md`](fallback-chain.md) | Multi-CLI delegation health registry, `execute_with_fallback`, `local_delegate_health` |
| [`notification.md`](notification.md) | `local_notify` / `local_notify_schedule` / `POST /notify` / AFK pings |
| [`email-brief.md`](email-brief.md) | `local_email_brief` tool, scheduler, important/fyi/auto buckets |
| [`sfx-library.md`](sfx-library.md) | Slot-based sfx library, `local_sfx_test`, env vars, adding your own clips |
| [`sfx-credits.md`](sfx-credits.md) | YouTube source provenance, license, processing recipe |
| [`webhooks.md`](webhooks.md) | Event classes, emit helpers, env-var configuration |
| [`video.md`](video.md) | `/frame` HTTP endpoint, video-state detection, feeder |
| [`env-vars.md`](env-vars.md) | Every `DISCORD_VOICE_LIVE_*` env var, defaults, descriptions |
| [`troubleshooting.md`](troubleshooting.md) | Common bridge failures, the Discord CDN handshake quirk, log locations |
| [`changelog.md`](changelog.md) | Release history (full changelog is `../CHANGELOG.md`) |

## Quick reference

```bash
# Install
./install.sh

# Uninstall
./install.sh --uninstall

# Check bridge health
curl -s http://127.0.0.1:18943/health | jq

# Restart gateway to pick up plugin changes
systemctl --user restart hermes-gateway
journalctl --user -u hermes-gateway -f

# Use from Discord
/voice-live              # join your voice channel
/voice-live-leave        # leave

# Sidecar control API
curl "http://127.0.0.1:18943/say?text=hello+from+sidecar"
curl -X POST -F "image=@frame.jpg" "http://127.0.0.1:18943/frame?force=true"
curl -X POST -H "Content-Type: application/json" -d '{"text":"inbox is huge","mode":"dm"}' "http://127.0.0.1:18943/notify"
```

## What this plugin does NOT do

- It does not record calls or persist transcripts to long-term storage (notes are ephemeral unless `DISCORD_VOICE_LIVE_NOTES_DIR` is set, and even then, only manual notes)
- It does not run a separate HTTP server for production traffic — the sidecar on 18943 is for `__init__.py` handlers and the optional `video-frame-feeder.py`, not public use
- It does not implement auth — it relies on the Hermes gateway's existing Discord user/role permissions

For the "why" of design decisions, see `../AGENTS.md` and the per-file docstrings.
