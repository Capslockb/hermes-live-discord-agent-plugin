# docs/ — discord-voice plugin documentation

The plugin is a Discord voice bridge backed by the Gemini Multimodal Live API. Beyond basic voice I/O it ships a personality system, a fallback chain for multi-CLI delegation, a proactive notification system, an email brief scheduler, and optional slot-based SFX support for operator-supplied original or explicitly licensed assets.

> **Documentation authority:** this Markdown index and the files under `docs/` are canonical for the current tree. The generated HTML under `docs-site/` is stale and still contains pre-correction repository-identity, licensing, and capability wording. Do not publish or cite the generated site as authoritative until the generator and regeneration work in [Issue #6](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/6) is complete.

## Index

| Doc | What it covers |
|---|---|
| [`architecture.md`](architecture.md) | End-to-end audio path, threading model, lifecycle |
| [`personality.md`](personality.md) | User-visible voice persona, turn-taking, video-awareness boundaries, optional Honcho context, and the camera-transition notification blocker in [Issue #10](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/10) |
| [`fallback-chain.md`](fallback-chain.md) | Multi-CLI delegation health registry, `execute_with_fallback`, `local_delegate_health` |
| [`notification.md`](notification.md) | `local_notify`, scheduling, `/notify`, and the current persistence/authentication blockers in Issues [#13](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/13), [#14](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/14), and [#17](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/17) |
| [`email-brief.md`](email-brief.md) | `local_email_brief`, scheduler, buckets, and current delivery/privacy blockers in [Issue #12](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/12) |
| [`sfx-library.md`](sfx-library.md) | Optional slot-based SFX support, trigger semantics, `local_sfx_test`, operator-supplied asset requirements, the cross-session routing blocker in [Issue #15](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/15), and custom-`HERMES_HOME` path alignment in [Issue #20](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/20) |
| [`sfx-credits.md`](sfx-credits.md) | YouTube source provenance, the current licensing boundary, and the accepted removal/replacement direction in [Issue #16](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/16) |
| [`webhooks.md`](webhooks.md) | Event classes, emit helpers, environment-variable configuration, and the queue/throttle-state defects tracked in [Issue #11](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/11) |
| [`video.md`](video.md) | `/frame` HTTP endpoint, current client blockers in [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9), and bundled feeder synchronization tracked in [Issue #19](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/19) |
| [`env-vars.md`](env-vars.md) | Environment variables, defaults, the accepted fail-closed identity/owner migration boundary in [Issue #18](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/18), and the Google Workspace helper-path limitation in [Issue #24](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/24) |
| [`troubleshooting.md`](troubleshooting.md) | Common bridge failures, the Discord CDN handshake quirk, log locations |
| [`../CHANGELOG.md`](../CHANGELOG.md) | Release history |

## Quick reference

```bash
# Install this cloned checkout
./install.sh --from-local

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

After cloning the repository, use `--from-local` so the installer uses the current checkout. Plain `./install.sh` uses the canonical remote clone target now present on `main`; owner-merged [PR #7](https://github.com/Capslockb/hermes-live-discord-agent-plugin/pull/7) corrected that executable install path. Current-main clean-install, rerun, uninstall-boundary, and custom-`HERMES_HOME` validation are still required under Issue #6.

## Sidecar control API boundary

The listener on `127.0.0.1:18943` is an internal loopback sidecar, not a public API.

- `/stop`, `/say`, `/frame`, and `/notify` are mutating routes protected by `X-API-Secret`. They are normally called through the plugin's internal handlers rather than copied as unauthenticated `curl` commands.
- Current `main` generates a fresh process-scoped `CONTROL_API_SECRET` at process start. Restarting rotates the value. The runtime ignores the historical `DISCORD_VOICE_LIVE_SECRET_FILE` setting and `~/.hermes/voice-live-control-secret` file; existing file contents do not authenticate the sidecar. Issue [#17](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/17) tracks the remaining trusted-client handoff and fail-closed client behavior.
- `/health` is anonymous and read-only.
- `/notes` is anonymous on loopback and can return stored voice events and reconstructed transcript text. Do not publish, proxy, or expose port `18943` beyond the local machine.
- The bundled `/frame` clients do not currently complete the required authenticated request path; see [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).
- The internal `/notify` helper also omits the required control header; keep route protection enabled and track the narrow in-process credential handoff in [Issue #14](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/14). Do not restore a persisted shared-secret default or weaken the route to make the helper work.

## What this plugin does NOT do

- It does not expose a production HTTP service. The sidecar is intended only for local plugin components and trusted local integrations.
- It does not guarantee that text transcripts are ephemeral. Voice events are written under `DISCORD_VOICE_LIVE_NOTES_DIR` (default: `~/.hermes/voice-live-notes`), and `/notes` can return that stored content.
- It does not rely only on Discord user/role permissions for sidecar mutations: mutating HTTP routes also require the internal process secret, while `/health` and `/notes` remain anonymous on loopback.
- It does not yet provide a complete trusted-client credential handoff. Process-start rotation is implemented, but the built-in `/notify` and `/frame` clients still omit the exact current credential and external callers have no approved handoff contract; see Issues #9, #14, and #17.
- It does not currently provide restart-safe, retry-safe scheduled notifications. Live runtime objects are passed into JSON persistence, failed attempts are removed after one try, and missing recipient state does not yet produce the required explicit skipped result; see [Issue #13](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/13).
- It does not currently provide truthful email-brief delivery receipts or backend-failure reporting. Failed notification attempts can consume de-duplication state, recipientless background work still needs a metadata-only skip contract, and bucket payloads can expose email snippets to model-visible history; see [Issue #12](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/12).
- It does not currently guarantee that implicit SFX playback reaches the initiating user or most recent voice session. Source entries can remain strongly retained after lifecycle end, and the fallback selector returns the first registered source; see [Issue #15](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/15).
- It does not establish redistribution rights for the bundled WAV files. Issue #16 selects removal of files without auditable permission and optional operator-supplied original or explicitly licensed replacements; the media, installer, and release changes still require a reviewed PR.
- It does not fully complete Issue #18's identity migration. Owner-merged PR #26 removed repository-embedded recipient and owner-ID fallbacks, but strict snowflake validation, `force_owner` constraints, canonical persisted identity, historical owner-state revocation, authenticated invoker context, and recipientless background skip behavior remain under review in draft PR #27 and Issue #18.

For implementation details and design context, see [`architecture.md`](architecture.md) and the per-file docstrings.
