# Known bugs & quirks

> This is the canonical bug list. If you hit something not on here, please open an issue with the reproduction steps.

## Critical

### 1. Discord CDN handshake rejection (`code 4006`)

**Symptom:** `channel.connect()` takes ~27 seconds to complete. The first ~5 handshakes are rejected with code 4006; the 6th succeeds.

**Root cause:** This machine's Discord voice WebSocket endpoint (`c-ams08.discord.media` / `c-ams07`) has been observed to always reject initial handshakes. It's a Discord infrastructure quirk, not a bridge bug — a standalone test proves it.

**Workaround:** The bridge waits up to 60 s for the secret key to be ready. Just be patient.

**DO NOT** keep restarting the gateway to "retry" — every restart resets the retry clock and you'll hit the rate limit harder.

### 2. Stale rejoin — "Bridge still starting" hang

**Symptom:** Calling `/voice-live` after a previous disconnect sometimes returns `pending: "Bridge is being started"` forever.

**Root cause:** `_active_bridges[guild_id]` still has an entry, but `voice_client.is_connected()` returns False. The plugin used to return "pending" in this case.

**Fix (shipped in `__init__.py:voice_live()`):**

```python
if guild_id_int in _active_bridges:
    bridge_info = _active_bridges[guild_id_int]
    current_vc = bridge_info.get("vc")
    if current_vc and current_vc.is_connected() and current_vc.channel:
        # ... (happy path)
    else:
        # Stale entry — clean up and start fresh
        old_task = bridge_info.get("task")
        if old_task and not old_task.done():
            old_task.cancel()
            try: await asyncio.wait_for(old_task, timeout=1.0)
            except (TimeoutError, CancelledError): pass
        _active_bridges.pop(guild_id_int, None)
        _starting.pop(guild_id_int, None)
```

### 3. Sidecar HTTP server hangs `serve_forever()`

**Symptom:** `bridge.py:run_sidecar()` uses `http.server.HTTPServer.serve_forever()` which never returns on its own. Without a shutdown signal, you can't cleanly stop the bridge.

**Fix (shipped in `bridge.py:run_sidecar()`):**

```python
async def _shutdown_watcher():
    while BRIDGE._running:
        await asyncio.sleep(0.5)
    server.shutdown()  # breaks serve_forever()

asyncio.create_task(_shutdown_watcher())
server.serve_forever()
```

## Workarounds (not yet "fixed")

### 4. Playback restarts only on new audio

**Symptom:** If playback stops during a 2-second natural silence, the green ring turns off. New audio from the user restarts it via `_wake_playback`.

**Why not just loop?** The old `_ensure_playback` callback created a tight restart loop (empty source → silence → play() → empty → ...). The new code intentionally only restarts when there's new Gemini audio to play.

### 5. Module import path is dash-normalized

The plugin directory is `discord-voice`, but the Python import is `discord_voice`. If you're importing it manually, use the underscore form.

### 6. Discord voice rate limits

If you call `/voice-live` repeatedly without waiting, Discord will throttle. The bridge has a `_starting` guard that returns `pending` for 30 s, but be patient.

## Performance

### 7. Opus decoder state corruption under packet loss

**Symptom:** `undecodable Opus frame` errors in the logs.

**Root cause:** Decoder state corruption. Self-heals on the next valid frame. **>100 errors in 5 seconds = real network issue**, not normal background noise.

## Compatibility

### 8. Older Gemini Live models don't support `mediaResolution: LOW`

If `GEMINI_MODEL` is set to a non-2.5 model, the session config will be rejected. Either upgrade the model or remove the override.

### 9. Function calling requires the executor to be non-blocking

If a custom `functionCalls` handler blocks for >10 seconds, the WSS times out. Keep tool handlers quick or push long work to a background task.

## Reporting new bugs

When opening an issue, include:

1. **Hermes gateway version** — `hermes --version`
2. **Plugin version** — `cat plugin/plugin.yaml | head -3`
3. **Gemini model** in use
4. **Last 100 lines of `journalctl --user -u hermes-gateway --since '5 min ago' --no-pager -o cat`**
5. **Health JSON** — `curl -s http://127.0.0.1:18943/health | python3 -m json.tool`
6. **Repro steps** — `/voice-live` then `...` then expected vs actual
