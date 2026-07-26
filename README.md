# Hermes Live — Discord Voice Agent

![Hermes Live Banner](docs/banner.png)

Hermes Live is a self-hosted Hermes Agent plugin that connects Discord voice channels to Google Gemini Multimodal Live. It provides real-time voice conversation, optional tool integrations, notifications, and local bridge controls while running inside an existing Hermes gateway.

## Project status

The core Discord-to-Gemini voice path is available for self-hosted use. Several optional integrations and control paths remain under active development, including bundled frame delivery, scheduled notification recovery, email-brief delivery guarantees, identity migration, and some installer/documentation paths.

Review the open issues before production deployment. Do not expose the local control API beyond loopback.

## Supported capabilities

- Full-duplex Discord voice input and output.
- Gemini Multimodal Live sessions.
- Optional configured tool integrations.
- Optional task delegation to locally configured developer tools.
- Proactive notifications through supported Discord and webhook paths.
- JSONL voice-event records with transcript segments, tool calls, turns, and idle events.
- Optional email-brief generation.
- Optional screen-frame input through the local bridge API.
- Optional integrations such as Home Assistant, Spotify, calendar, mail, search, and GitHub.

Availability depends on the selected providers, credentials, network, gateway configuration, and optional services.

## Requirements

- A working Hermes Agent gateway.
- A Discord bot token with the required voice permissions.
- A Gemini API key for a supported Live model.
- Python and system dependencies used by the Hermes gateway.
- Explicit Discord user and owner identifiers for single-owner deployments.

## Installation from a local checkout

```bash
git clone https://github.com/Capslockb/hermes-live-discord-agent-plugin.git
cd hermes-live-discord-agent-plugin
./install.sh --from-local
systemctl --user restart hermes-gateway
```

In Discord:

```text
/voice-live
/voice-live-leave
```

Use `./install.sh --uninstall` to remove the plugin.

Remote installer behavior is still being corrected. A local checkout with `--from-local` is the recommended installation path until the related installer work is complete.

## Minimum configuration

```bash
DISCORD_BOT_TOKEN=***
GEMINI_API_KEY=***
DISCORD_VOICE_LIVE_USER_ID=<your-discord-user-id>
VOICE_OWNER_DISCORD_ID=<your-discord-user-id>
```

Set both identity values explicitly. See [`docs/env-vars.md`](docs/env-vars.md) for the full configuration reference.

## Basic architecture

```text
Discord Voice
    ↓ Opus decode
48 kHz PCM
    ↓ resample
16 kHz mono
    ↓
Gemini Multimodal Live
    ↓ generated audio
24 kHz PCM
    ↓ Discord audio source
Discord Voice
```

The bridge runs inside the Hermes gateway asyncio loop. Discord, Gemini, and optional integrations remain external services and are subject to their own availability, pricing, and data-handling terms.

See [`docs/architecture.md`](docs/architecture.md) for implementation details.

## Local control API

The bridge exposes a local HTTP interface on `127.0.0.1:18943`.

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET | Local bridge health information |
| `/frame` | POST | Authenticated JPEG or PNG frame input |
| `/stop` | GET | Authenticated bridge stop |
| `/say` | GET | Authenticated text injection |
| `/notes` | GET | Recent local transcript events |
| `/notify` | GET/POST | Authenticated notification delivery |

Keep this interface loopback-only. Mutating routes require `X-API-Secret`. The secret lifecycle, internal notification authentication, anonymous transcript access, and frame-client compatibility have open issues and should be reviewed before deployment.

## Current limitations

- Bundled frame clients are not currently considered operational.
- Scheduled notification persistence and retry behavior are incomplete.
- Email briefs do not yet provide complete backend, delivery, recipient, and de-duplication guarantees.
- Generated static documentation under `docs-site/` is stale and must not be treated as authoritative.
- Some installer and mirrored-source paths remain under correction.
- Owner identity migration and repository-embedded fallback removal remain incomplete.
- Bundled sound-effect redistribution rights have not been fully established.

The current issue tracker is the authoritative source for active limitations and fixes.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — audio path and lifecycle
- [`docs/notification.md`](docs/notification.md) — notifications
- [`docs/email-brief.md`](docs/email-brief.md) — email briefs
- [`docs/sfx-library.md`](docs/sfx-library.md) — sound-effect configuration
- [`docs/webhooks.md`](docs/webhooks.md) — webhook delivery
- [`docs/video.md`](docs/video.md) — frame input
- [`docs/env-vars.md`](docs/env-vars.md) — environment variables
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — troubleshooting

Markdown documentation under `docs/` and this README are canonical until the generated site is rebuilt from corrected sources.

## Security

- Keep the control API bound to loopback.
- Use explicit Discord identity configuration.
- Protect provider and Discord credentials through environment or secret-management tooling.
- Review transcript retention and notification destinations before enabling them.
- Do not assume optional tools are safe for multi-user use without authorization controls.
- Report security concerns privately to the repository owner rather than publishing credentials or exploit details.

## Contributing

Use focused pull requests, preserve truthful capability labels, add tests for behavior changes, and avoid committing credentials, private identifiers, transcripts, or generated artifacts containing sensitive data.

## License

No standalone `LICENSE` file is currently included. Do not assume reuse or redistribution rights until the repository owner adds an explicit license.
