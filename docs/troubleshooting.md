# Troubleshooting

Common bridge failures and how to fix them.

## "Bridge failed to start"

Most common cause: **Discord CDN handshake quirk** (criterion #38, see `AGENTS.md`). The voice WebSocket endpoint rejects the first 5 handshakes with code 4006 before accepting. A single `channel.connect()` takes ~27 seconds of internal retries.

**Do not** restart the gateway repeatedly to "retry" — each restart resets the retry clock and you'll hit the rate limit harder.

**What to do**: wait ~30 seconds. The bridge will connect on its own.

## "Bridge still starting" hangs on `/voice-live`

If a previous bridge is in `_active_bridges` but `vc.is_connected()` is False, the new `/voice-live` will hang with "Bridge still starting" instead of starting fresh.

**Fix**: the `__init__.py:voice_live()` handler should detect stale entries (vc disconnected) and pop them. If you see this hang in production, restart the gateway (`systemctl --user restart hermes-gateway`) and the cleanup will run on boot.

## First-turn "I see you're sharing your screen" hallucination

Two root causes (criterion #34):
1. The system prompt told the model "if someone shares their screen..." — Gemini hallucinated this as an implied task on every connect.
2. The first-turn mute (`audioStreamEnd` after setup) wasn't being sent.

**Verify the fix is in**: `bridge.py` should have an `await self._gemini._ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))` right after `await self._gemini.connect()`.

## Token burn on connect

The bridge sends `setupComplete` → `audioStreamEnd` immediately to prevent the model from starting a first autonomous turn. If you see logs showing model output within 100ms of `setupComplete`, the audioStreamEnd is not being sent or the model is generating despite it.

**Check**: `journalctl --user -u hermes-gateway -n 50 --no-pager | grep setupComplete`

## Voice CDN rate limit (4006 after 5 attempts)

A long-running session can hit the rate limit. The bridge handles it by waiting up to 60s for `secret_key` readiness. If it still fails, the gateway returns an error. Wait 5-10 minutes before retrying.

## Tool calls hang

`_run_local_tool` runs in a thread-pool. Long-running tools (delegation, web search) should return control immediately and let the user poll for status. If a tool blocks the worker thread, **all** subsequent tool calls queue up.

**Watch for**: tools that don't return a `dict` within 30 seconds. They might be stuck on a network call. Check `journalctl --user -u hermes-gateway -n 20 --no-pager` for the traceback.

## Fallback chain always picks opencode

`FALLBACK_CHAIN["codex"]` is `["opencode", "hermes-api", "gemini"]` — opencode is the first fallback by design. If you want codex to fall back to a different neighbor, edit `delegation_agent.py:FALLBACK_CHAIN`.

**Verify the health registry is working**: call `local_delegate_health(action="list")` from voice or `~/.hermes/voice-platform-health.json` from the shell. If a platform is in there with `seconds_remaining > 0`, it's still considered broken.

## Sfx not playing

1. Check the WAV files exist: `ls -la ~/.hermes/voice-users/sfx/`
2. Check the volumes: `DISCORD_VOICE_LIVE_SFX_<SLOT>_VOLUME=0.5` (default)
3. Test manually: `local_sfx_test(action="list")` returns the configured slots
4. Test playback: `local_sfx_test(slot="notification")` — should return `status: "played"`. If `no_active_source`, no voice session is running.

If the sfx is loaded but inaudible, the source's audio queue might be stuck. Restart the gateway to reset.

## Email brief returns "no backend"

Both backends (`google_api.py` and `himalaya`) failed. Common causes:
- `google_api.py` not authenticated: run `python ~/.hermes/hermes-agent/skills/productivity/google-workspace/scripts/google_api.py auth`
- `himalaya` not configured: check `~/.config/himalaya/config.toml`

The brief is **graceful** when both fail — returns `{status: "ok", backend: "none", count: 0}` instead of crashing.

## Notification not delivered

`local_notify` returns `{status, channel, results, notified_at}`. Check `channel`:
- `voice` — the model will speak it in the next turn
- `dm` — sent via bot DM
- `channel` — posted in a text channel
- `webhook` — fired a webhook event
- `no_subscribers` — no webhook URL configured for `agent.notify`

If `channel: "no_subscribers"` and you expected a DM, the bot might not share a guild with the target user. Verify the bot is in the same server.

## Log locations

- **Gateway**: `journalctl --user -u hermes-gateway -f` or `~/.hermes/logs/gateway.log`
- **Plugin**: same journalctl, with `VoiceLive:` prefix in messages
- **Errors**: `~/.hermes/logs/errors.log`
- **Opencode tmux logs**: `/tmp/delegate-<platform>-<timestamp>.log`

## Voice bridges down after gateway restart

Voice bridges (ports 18943 and 9232) are consistently down after gateway restart. The gateway needs to bind the sockets, then ~30s for the Discord CDN handshake. Don't restart repeatedly — let one connect cycle complete.
