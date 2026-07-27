# Troubleshooting

Common bridge failures and how to investigate them.

## "Bridge failed to start"

`VoiceLiveBridge.start()` makes one `channel.connect()` call with `reconnect=True` and a 60-second timeout. If Discord voice connection, Discord receive/playback setup, or the Gemini handshake raises, startup returns failure.

The implementation does **not** establish that Discord always rejects the first five handshakes with code 4006, that a connection normally takes 27–30 seconds, or that a failed command will later connect on its own.

**What to do:** let the current command finish, then inspect the gateway log:

```bash
journalctl --user -u hermes-gateway -n 100 --no-pager
```

Look for `Discord voice connect failed`, `Failed to start Discord voice I/O`, or `Gemini connect failed`. Avoid repeatedly restarting the gateway while one connection attempt is still active.

## "Bridge is being started" remains pending

`__init__.py` tracks startup in `_STARTING` and automatically expires that marker after 180 seconds. It also removes a disconnected stale bridge from `_active_bridges` before attempting a fresh start.

A pending response during an active start is expected. A pending state that continues beyond the 180-second TTL indicates that the running process or deployed checkout does not match the documented code, or that another layer is repeatedly starting the bridge. Check the gateway log and confirm the installed plugin path before restarting.

## First-turn "I see you're sharing your screen" output

The current bridge sends `audioStreamEnd` immediately after Gemini setup completes on a best-effort basis. It also includes a system-prompt guard against claiming unseen visual input.

Check for this log entry:

```text
VoiceLive: sent initial mute audioStreamEnd to suppress first turn
```

Absence of the entry can mean Gemini connection failed before the signal or that sending the signal raised. Do not rely on a fixed source line number as a deployment check.

## Unexpected model output immediately after connect

If output arrives before the user speaks, inspect logs around Gemini `setupComplete` and the initial `audioStreamEnd`. The signal is best-effort and its exception is currently suppressed, so a missing log entry is more useful than assuming the mute succeeded.

## Discord voice errors, including code 4006

Treat 4006 as a connection failure to diagnose, not as a mandatory five-attempt warm-up sequence. Confirm the bot token, voice permissions, gateway state, existing guild voice client, and network path. The plugin disconnects an existing guild voice client before starting and allows Discord's connect call up to 60 seconds.

If repeated attempts fail, stop issuing new starts, inspect the full gateway error, and wait before retrying when Discord appears to be rate-limiting the bot.

## Tool calls hang

Blocking tool runners are submitted through `run_in_executor()`, but `_handle_tool_call()` awaits each function call in the current Gemini tool-call message sequentially before sending the combined response. A stuck network or subprocess operation can therefore stall that tool-call response and occupy an executor worker.

It does not follow that one slow call monopolizes a single global tool thread or forces every unrelated executor task to queue behind it. Executor capacity depends on the host event loop and other work in the process.

Check recent logs for the specific tool name and traceback. The current handler does not apply a universal 30-second timeout to every tool runner.

## Fallback chain always picks opencode

The private mapping `delegation_agent.py:_FALLBACK_CHAIN` defines the first fallback for `codex` as `opencode`, followed by `hermes-api` and `gemini`.

`choose_fallback()` checks the persisted health registry only. It does not verify the neighbor's binary path or hourly rate-limit counter before selecting it. The current CLI paths are also hard-coded under `/home/caps`, so another installation may fail immediately until portability is addressed.

Use `local_delegate_health(action="list")` or inspect `~/.hermes/voice-platform-health.json`. Tool list output uses `expires_in_seconds`; persisted entries contain `marked_at`, `expires_at`, and `ttl_seconds`. See [Fallback chain](fallback-chain.md) for the exact schemas and execution boundary.

## SFX not playing

1. Check the WAV files exist: `ls -la ~/.hermes/voice-users/sfx/`
2. Check the volumes: `DISCORD_VOICE_LIVE_SFX_<SLOT>_VOLUME=0.5` (default)
3. Test manually: `local_sfx_test(action="list")` returns the configured slots.
4. Test playback: `local_sfx_test(slot="notification")`. A `no_active_source` result means no active voice audio source is registered.

If the files load but remain inaudible, inspect the gateway log for playback or queue errors before restarting.

## Email brief returns "no backend"

Both backends (`google_api.py` and `himalaya`) failed. Common causes:

- `google_api.py` is not authenticated. Run `python ~/.hermes/hermes-agent/skills/productivity/google-workspace/scripts/google_api.py auth`.
- `himalaya` is not configured. Check `~/.config/himalaya/config.toml`.

The current brief converts total backend failure into an empty result rather than raising. Do not interpret `backend: "none"` and `count: 0` as evidence that the inbox was successfully checked.

## Notification not delivered

`local_notify` returns delivery status and results. Check the selected destination and per-destination result rather than relying only on a top-level timestamp.

- `voice` — delivery requires an active bridge.
- `dm` — delivery requires a resolvable Discord user and permission to DM.
- `channel` — delivery requires a configured channel and send permission.
- `webhook` — delivery requires a matching configured webhook subscriber.

See [Notifications](notification.md) for the current `auto` ordering, scheduled-delivery path, and authentication limitations.

## Log locations

- **Gateway and plugin:** `journalctl --user -u hermes-gateway -f`
- **Optional gateway file log:** `~/.hermes/logs/gateway.log`, when configured by the host installation.
- **Optional error log:** `~/.hermes/logs/errors.log`, when configured by the host installation.
- **Delegation logs:** `/tmp/delegate-<platform>-<session>.log` for CLI paths that launched successfully.

Do not assume every listed file exists; systemd journal output is the primary evidence for the user service.

## Bridge unavailable after gateway restart

This plugin's control port defaults to `18943`. Port `9232` is not defined by this repository and should not be used as a health signal for this plugin.

After restart, wait for the plugin to load and then invoke or autostart the voice bridge. The sidecar can bind before Discord and Gemini are ready. Use `/health`, the command result, and gateway logs rather than a fixed 30-second success assumption.
