# Changelog — Hermes Live Discord Agent Plugin

This file records notable changes by release. Historical entries describe the repository at the time of each release and are not guarantees that every optional path is currently production-ready.

## Current documentation corrections

- The canonical repository is `Capslockb/hermes-live-discord-agent-plugin`.
- The hand-written `README.md` and `docs/` tree are authoritative. Generated files under `docs-site/` are stale until they are rebuilt from corrected sources; see [Issue #6](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/6).
- No standalone `LICENSE` file is included. Do not assume reuse or redistribution rights.
- Existing bundled WAV files do not have an established redistribution basis. Do not redistribute them as licensed project assets; removal is tracked in [Issue #16](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/16).
- Several optional integrations and control paths have open correctness or security work. Review the current issue tracker before deployment.

## 0.3.5 — 2026-06-09

### Documentation release candidate

- Added a generated documentation site under `docs-site/` and a Markdown quickstart under `docs/quickstart.md`.
- Refreshed the README and documentation navigation.
- Added release notes for the voice bridge, optional notifications, email briefs, SFX slots, video-frame input, webhooks, and contextual integrations.

The generated site is no longer authoritative because it predates later documentation corrections. Use the root README and `docs/` files until Issue #6 is completed.

### Upgrade

For an existing local checkout:

```bash
cd /path/to/hermes-live-discord-agent-plugin
git pull
./install.sh --from-local
systemctl --user restart hermes-gateway
```

The canonical remote clone-target correction landed through [PR #7](https://github.com/Capslockb/hermes-live-discord-agent-plugin/pull/7). Current-main clean remote installation, rerun/no-overwrite behavior, uninstall boundaries, and custom `HERMES_HOME` validation remain tracked in [Issue #6](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/6).

## 0.3.4 — 2026-06-09

### Voice interruption improvements

- Added local speech-energy detection so active generated audio can be cleared without waiting for a remote interruption round trip.
- Tightened Gemini activity-detection timing for the slower interruption path.
- Added interruption metrics and focused regression tests.

## 0.3.3 — 2026-06-09

### Video-state transitions

- Recorded screen-share start and stop transitions in the configured context backend.
- Added an optional notification when screen sharing starts.
- Documented that Discord bots do not receive user video streams directly; frame input requires an explicit supported path.

## 0.3.2 — 2026-06-09

- Added installation of the external frame-feeder helper.
- Added video configuration and troubleshooting documentation.

Current bundled frame clients are not operational without the fixes tracked in [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).

## 0.3.1 — 2026-06-09

- Documented an optional sibling Vapi transport for deployments that configure the separate integration.
- Added discovery metadata for the optional sibling transport.

## 0.3.0 — 2026-06-07

### Features

- Added configurable local delegation with provider fallback and health tracking.
- Added immediate and scheduled notification helpers with voice, Discord, channel, and webhook delivery modes.
- Added email-brief generation with configurable backends and importance grouping.
- Added four optional SFX runtime slots and operator configuration controls.
- Added user-profile onboarding and profile-scoped preferences.
- Added video-state awareness and frame-input helpers.
- Added webhook events for notifications and fallback activity.
- Added an installer with local-checkout, uninstall, and non-interactive modes.
- Added feature documentation under `docs/`.

### Current caveats for features introduced in this release

- Scheduled notification persistence, retry, and restart routing are tracked in [Issue #13](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/13).
- Email-brief backend, delivery, recipient, privacy, and de-duplication behavior is tracked in [Issue #12](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/12).
- SFX registry cleanup and routing are tracked in [Issue #15](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/15).
- SFX path consistency for custom `HERMES_HOME` installations is tracked in [Issue #20](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/20).
- Bundled media removal is tracked in Issue #16. Operators should use original or explicitly licensed replacement files, or disable SFX.

### Tests

Added focused smoke and regression coverage for delegation fallback, notifications, email briefs, SFX routing, interruption behavior, and profile handling. Historical test counts in earlier notes do not replace current exact-head CI evidence.

## 0.2.8 — 2026-06-07

- Added a user-presence gate and a watchdog that stops unattended voice sessions.
- Suppressed the first unsolicited model turn after session setup.
- Added a video-initialized webhook event.
- Added feeder-side content-change filtering and fallback behavior.

Canonical frame-filter changes and bundled-copy synchronization are tracked in [Issue #19](https://github.com/Capslockb/video-frame-feeder/issues/19).

## 0.2.7 — 2026-06-05

- Added video-activity messaging that distinguishes activity awareness from actual frame visibility.
- Added conversational onboarding backed by per-user profile data.
- Added regression coverage for onboarding and video-awareness behavior.

## 0.2.6 — 2026-06-05

- Removed an unsupported Gemini setup field that prevented the reviewed model configurations from connecting.

## 0.2.5 — 2026-06-05

- Added optional GitHub repository, issue, pull-request, and note helpers for explicitly configured local environments.
- Kept repository mutations behind the normal tool and authorization boundaries of the hosting Hermes deployment.

## 0.2.4 — 2026-06-05

- Added configurable Discord webhook delivery for bridge, transcript, tool, email, and local-task events.
- Added spoken-email-address normalization before configured mail delivery.
- Added an optional important-email reminder poller with throttling and persisted seen-message state.

Use the environment-variable names documented in [`docs/env-vars.md`](docs/env-vars.md); older release-note examples may not reflect current configuration.

## 0.2.3 — 2026-06-05

- Added a short typing-feedback audio slot.
- Added progress notifications for configured long-running local tasks.

The historical bundled audio source does not establish redistribution permission. Follow the current media boundary in Issue #16.

## 0.2.2 — 2026-06-04

- Added per-user profile isolation for memory, preferences, and tool allowlists.
- Added owner authorization derived from an explicitly configured `VOICE_OWNER_DISCORD_ID`.

Removal of embedded identity fallbacks and migration of persisted owner state are tracked in [Issue #18](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/18).

## 0.2.1 — 2026-06-04

- Added the first repeatable regression-test suite.

## 0.1.0 — 2026-06-03

### Initial release

- Discord voice input and output through `discord-ext-voice-recv`.
- Gemini Live audio sessions.
- `/voice-live` and `/voice-live-leave` Discord commands.
- Optional local integrations for media, search, mail, home automation, and system inspection.
- Local control endpoints for health, stop, text injection, frame input, and notes.
- Per-session JSONL notes and event records.

Current control-secret handling, frame-client authentication, notification authentication, identity routing, and optional integration limitations are tracked in the open issues rather than guaranteed by this historical entry.
