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
| [`../CHANGELOG.md`](../CHANGELOG.md) | Release history |

## Quick reference

```bash
# Install
./install.sh

# Uninstall
./install.sh --uninstall

# Check bridge health (read-only, loopback only)
curl -s http://127.0.0.1:18943/health | jq

# Restart gateway to pick up plugin changes
systemctl --user restart hermes-gateway
journalctl --user -u hermes-gateway -f

# Use from Discord
/voice-live              # join your voice channel
/voice-live-leave        # leave
```

## Sidecar control API boundary

The listener on `127.0.0.1:18943` is an internal loopback sidecar, not a public API.

- `/stop`, `/say`, `/frame`, and `/notify` are mutating routes protected by a per-process `X-API-Secret`. They are normally called through the plugin's internal handlers rather than copied as unauthenticated `curl` commands.
- `/health` is anonymous and read-only.
- `/notes` is anonymous on loopback and can return stored voice events and reconstructed transcript text. Do not publish, proxy, or expose port `18943` beyond the local machine.

## What this plugin does NOT do

- It does not expose a production HTTP service. The sidecar is intended only for local plugin components and trusted local integrations.
- It does not guarantee that text transcripts are ephemeral. Voice events are written under `DISCORD_VOICE_LIVE_NOTES_DIR` (default: `~/.hermes/voice-live-notes`), and `/notes` can return that stored content.
- It does not rely only on Discord user/role permissions for sidecar mutations: mutating HTTP routes also require the internal shared secret, while `/health` and `/notes` remain anonymous on loopback.

For implementation details and design context, see [`architecture.md`](architecture.md) and the per-file docstrings.
