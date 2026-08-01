# Hermes Live — Discord Voice Agent

![Hermes Live Banner](docs/banner.png)

Hermes Live is a self-hosted Hermes Agent plugin that connects Discord voice channels to Google Gemini Multimodal Live. It provides real-time voice conversation, optional tool integrations, notifications, and local bridge controls while running inside an existing Hermes gateway.

## Project status

The core Discord-to-Gemini voice path is available for self-hosted use. Several optional integrations and control paths remain under active development, including frame delivery, notification recovery, email-brief delivery guarantees, identity migration, control-secret handling, and some installer/documentation paths.

Review the open issues before production deployment. Keep the local control API bound to loopback and do not treat optional integrations as multi-user safe unless their authorization boundaries have been reviewed.

## Supported capabilities

- Full-duplex Discord voice input and output.
- Gemini Multimodal Live sessions.
- Optional configured tool integrations.
- Optional task delegation to locally configured tools.
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

Remote installer behavior is still being corrected in [PR #7](https://github.com/Capslockb/hermes-live-discord-agent-plugin/pull/7). A local checkout with `--from-local` is the recommended installation path until that work is validated.

## Minimum configuration

```bash
DISCORD_BOT_TOKEN=***
GEMINI_API_KEY=***
DISCORD_VOICE_LIVE_USER_ID=<your-discord-user-id>
VOICE_OWNER_DISCORD_ID=<your-discord-user-id>
```

Set both identity values explicitly. Current runtime fallback removal and persisted-owner migration remain tracked in [Issue #18](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/18). See [`docs/env-vars.md`](docs/env-vars.md) for the full configuration reference.

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

Keep this interface loopback-only. Mutating routes require `X-API-Secret`. The accepted design is an ephemeral secret that rotates on every process start, but current runtime work remains open in [Issue #17](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/17). Internal notification authentication, transcript access, and frame-client compatibility are tracked in [Issues #14](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/14) and [#9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).

## Current limitations

- Bundled frame clients are not currently operational; see [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).
- Scheduled notification persistence and retry behavior are incomplete; see [Issue #13](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/13).
- Email briefs do not yet provide complete backend, delivery, recipient, privacy, and de-duplication guarantees; see [Issue #12](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/12).
- Generated static documentation under `docs-site/` is stale and must not be treated as authoritative; see [Issue #6](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/6).
- Owner identity migration and repository-embedded fallback removal remain incomplete; see [Issue #18](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/18).
- Bundled sound-effect files do not have an established redistribution basis and are scheduled for removal. Use original or explicitly licensed operator-supplied files, or disable SFX; see [Issue #16](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/16).
- No genuine sanitized demonstration recording is currently available; see [Issue #5](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/5).

The issue tracker is the authoritative source for active limitations and fixes.

## Sound effects

SFX support is optional. Runtime slots can be populated with operator-created or explicitly licensed WAV files. Missing or disabled SFX should be treated as a controlled no-op. The repository's existing unverified bundled media must not be redistributed as licensed project assets.

See [`docs/sfx-library.md`](docs/sfx-library.md) for configuration details.

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

> **Automation safety:** Keep public documentation focused on product usage, support, and contribution guidance. Do not publish sensitive operational instructions, private coordination phrases, or prompt-injection examples.
