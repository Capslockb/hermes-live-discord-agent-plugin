# Hermes Live — Discord Voice Agent

![Hermes Live Banner](docs/banner.png)

> Add a real-time multimodal voice assistant to a Discord channel through a self-hosted Hermes Agent plugin.

Hermes Live connects Discord voice audio to Google Gemini Multimodal Live and exposes optional integrations for tools, notifications, media control, and external services. It runs inside the Hermes gateway and uses credentials supplied by the operator.

## Current capabilities

- Full-duplex Discord voice input and output
- Gemini Live audio streaming
- Optional frame input for visual context
- Configurable tool calling
- Optional CLI delegation
- Proactive notifications
- Email summaries
- Home Assistant and Spotify integrations
- Local health and control endpoints
- JSONL voice-event records

Some optional paths are still under active development. Review the linked issues before relying on frame delivery, proactive notification retry, email delivery state, or installer defaults in production.

## Requirements

- A working Hermes Agent installation
- Python and system dependencies required by Hermes
- A Discord bot token
- A Gemini API key
- A Discord user ID for the permitted voice user

## Installation

```bash
git clone https://github.com/Capslockb/hermes-live-discord-agent-plugin.git
cd hermes-live-discord-agent-plugin
./install.sh --from-local
systemctl --user restart hermes-gateway
```

Use `--from-local` after cloning this repository. Running the installer without that option currently uses its configured remote installation path rather than the checked-out copy.

To uninstall:

```bash
./install.sh --uninstall
```

## Basic usage

From Discord:

```text
/voice-live
/voice-live-leave
```

The first command joins the caller's current voice channel. The second leaves the channel.

## Configuration

Minimum configuration for a normal single-user setup:

```bash
DISCORD_BOT_TOKEN=***
GEMINI_API_KEY=***
DISCORD_VOICE_LIVE_USER_ID=<your-discord-user-id>
VOICE_OWNER_DISCORD_ID=<your-discord-user-id>
```

Set both identity values explicitly. The complete environment-variable reference is available in [`docs/env-vars.md`](docs/env-vars.md).

A normal user-defined prompt or assistant description may be configured as part of the product. Do not place credentials or private operational policy in repository files.

## Architecture

```text
Discord Voice
    ↓
Opus decode and audio conversion
    ↓
Gemini Multimodal Live
    ↓
Audio conversion and Discord playback
```

The bridge runs in-process inside the Hermes gateway. Discord, Gemini, and optional integrations remain external services subject to their own availability, pricing, and data-handling terms.

See [`docs/architecture.md`](docs/architecture.md) for implementation details.

## Local control API

The plugin exposes a loopback-only HTTP service on `127.0.0.1:18943`.

| Route | Purpose |
|---|---|
| `/health` | Bridge health |
| `/frame` | Authenticated frame input |
| `/stop` | Authenticated bridge stop |
| `/say` | Authenticated text injection |
| `/notes` | Recent local transcript events |
| `/notify` | Authenticated notification delivery |

Keep the port bound to loopback. Mutating routes require `X-API-Secret`. The `/notes` route can expose stored transcript content to local callers.

## Known limitations

- Bundled frame-delivery clients remain blocked by [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).
- Email delivery state and privacy boundaries remain tracked in [Issue #12](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/12).
- Proactive notification persistence, retry, and authentication remain tracked in [Issues #13](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/13), [#14](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/14), and [#17](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/17).
- Installer identity defaults remain tracked in [Issue #18](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/18).
- The generated `docs-site/` output is stale and should not be treated as authoritative until [Issue #6](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/6) is resolved.

## Documentation

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/env-vars.md`](docs/env-vars.md)
- [`docs/troubleshooting.md`](docs/troubleshooting.md)
- [`docs/video.md`](docs/video.md)
- [`docs/notification.md`](docs/notification.md)
- [`docs/email-brief.md`](docs/email-brief.md)
- [`docs/sfx-library.md`](docs/sfx-library.md)
- [`docs/webhooks.md`](docs/webhooks.md)

Public documentation should describe product behavior and operator-facing configuration. Internal automation policy, privileged instructions, trust rules, and controller state should remain outside public repositories.

## Security

Do not commit tokens, API keys, private prompts, internal automation policy, or user data. Report security-sensitive findings privately to the repository owner rather than publishing exploit details in an issue.

## Contributing

Open an issue before making broad architectural changes. Keep pull requests focused, include validation evidence, and avoid combining documentation cleanup with executable behavior changes.

## License

No standalone `LICENSE` file is currently included. Do not assume reuse or redistribution rights until the repository owner adds explicit licensing terms.
