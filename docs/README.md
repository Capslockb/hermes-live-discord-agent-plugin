# docs/ — discord-voice plugin documentation

The plugin is a Discord voice bridge backed by the Gemini Multimodal Live API. Beyond basic voice I/O it ships a personality system, a fallback chain for multi-CLI delegation, a proactive notification system, an email brief scheduler, and optional slot-based SFX support for operator-supplied original or explicitly licensed assets.

> **Documentation authority:** this Markdown index and the files under `docs/` are canonical for the current tree. The generated HTML under `docs-site/` is stale and still contains pre-correction repository-identity, licensing, and capability wording. Do not publish or cite the generated site as authoritative until the generator and regeneration work in [Issue #6](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/6) is complete.

## Index

| Doc | What it covers |
|---|---|
| [`architecture.md`](architecture.md) | End-to-end audio path, threading model, lifecycle |
| [`personality.md`](personality.md) | System prompt shape, ping-pong rhythm, boredom switch, vocal expression |
| [`fallback-chain.md`](fallback-chain.md) | Multi-CLI delegation health registry, `execute_with_fallback`, `local_delegate_health` |
| [`notification.md`](notification.md) | `local_notify`, scheduling, `/notify`, and the current persistence/authentication blockers in Issues [#13](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/13), [#14](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/14), and [#17](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/17) |
| [`email-brief.md`](email-brief.md) | `local_email_brief`, scheduler, buckets, and current delivery/privacy blockers in Issue #12 |
| [`sfx-library.md`](sfx-library.md) | Optional slot-based SFX support, trigger semantics, `local_sfx_test`, operator-supplied asset requirements, and the cross-session routing blocker in Issue #15 |
| [`sfx-credits.md`](sfx-credits.md) | YouTube source provenance, the current licensing boundary, and the accepted removal/replacement direction in Issue #16 |
| [`webhooks.md`](webhooks.md) | Event classes, emit helpers, env-var configuration |
| [`video.md`](video.md) | `/frame` HTTP endpoint and current client blockers tracked in Issue #9 |
| [`env-vars.md`](env-vars.md) | Environment variables, defaults, and the accepted fail-closed identity/owner migration boundary in Issue #18 |
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

After cloning the repository, use `--from-local`. Plain `./install.sh` uses the installer's configured remote clone target rather than the current checkout; its repository correction is under review in [PR #7](https://github.com/Capslockb/hermes-live-discord-agent-plugin/pull/7).

## Sidecar control API boundary

The listener on `127.0.0.1:18943` is an internal loopback sidecar, not a public API.

- `/stop`, `/say`, `/frame`, and `/notify` are mutating routes protected by `X-API-Secret`. They are normally called through the plugin's internal handlers rather than copied as unauthenticated `curl` commands.
- The credential is loaded from or persisted to `DISCORD_VOICE_LIVE_SECRET_FILE` (default `~/.hermes/voice-live-control-secret`), so current `main` can reuse it across restarts. Existing file type, ownership, symlink status, and mode are not revalidated; see [Issue #17](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/17). Do not assume a gateway restart rotates the secret on the current implementation.
- `/health` is anonymous and read-only.
- `/notes` is anonymous on loopback and can return stored voice events and reconstructed transcript text. Do not publish, proxy, or expose port `18943` beyond the local machine.
- The bundled `/frame` clients do not currently complete the required authenticated request path; see [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).
- The internal `/notify` helper also omits the required control header; keep route protection enabled and track the authenticated-call correction in [Issue #14](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/14). That fix must implement Issue #17's accepted ephemeral process-scoped secret, restart rotation, narrow in-process handoff, and fail-closed behavior.

## What this plugin does NOT do

- It does not expose a production HTTP service. The sidecar is intended only for local plugin components and trusted local integrations.
- It does not guarantee that text transcripts are ephemeral. Voice events are written under `DISCORD_VOICE_LIVE_NOTES_DIR` (default: `~/.hermes/voice-live-notes`), and `/notes` can return that stored content.
- It does not rely only on Discord user/role permissions for sidecar mutations: mutating HTTP routes also require the internal shared secret, while `/health` and `/notes` remain anonymous on loopback.
- It does not yet implement the accepted process-start rotation contract. Issue #17 selects an ephemeral process-scoped secret that rotates on every start, is handed only to trusted in-process clients, and fails closed when credential state is unavailable or unsafe.
- It does not currently provide restart-safe, retry-safe scheduled notifications. Live runtime objects are passed into JSON persistence, failed attempts are removed after one try, and recipient fallback is not explicit; see [Issue #13](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/13).
- It does not currently provide truthful email-brief delivery receipts or backend-failure reporting. Failed notification attempts can consume de-duplication state, scheduled routing has an embedded user fallback, and bucket payloads can expose email snippets to model-visible history; see [Issue #12](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/12).
- It does not currently guarantee that implicit SFX playback reaches the initiating user or most recent voice session. Source entries can remain strongly retained after lifecycle end, and the fallback selector returns the first registered source; see [Issue #15](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/15).
- It does not establish redistribution rights for the bundled WAV files. Issue #16 selects removal of files without auditable permission and optional operator-supplied original or explicitly licensed replacements; the media, installer, and release changes still require a reviewed PR.
- It does not yet implement Issue #18's accepted identity migration. Embedded identity fallbacks must be removed; persisted profile data is preserved, but owner authorization must be recomputed at load time and is effective only when the profile ID matches an explicitly configured `VOICE_OWNER_DISCORD_ID`. Background delivery must skip rather than guess a recipient.

For implementation details and design context, see [`architecture.md`](architecture.md) and the per-file docstrings.
