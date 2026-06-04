# WebSocket Loop Lifecycle Patterns

## The problem

When a WebSocket connection to a voice AI provider (Vapi, Gemini Live, etc.) closes, the bridge's internal loops must **gracefully drain and shutdown** instead of crashing or hanging. Without proper lifecycle management:

- Receive loop breaks but `_running` stays `True` → watchdog keeps running, tries to send on dead socket
- Send loop crashes on `ConnectionClosed` → unhandled exception in task, bridge stays "running"
- Producer thread blocks on `_send_q.put()` because the send loop died with data still in queue

## Pattern: propagate `_running = False` on all close paths

```python
async def _receive_loop(self):
    while self._running:
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("WebSocket closed (receive): %s", e)
            self._running = False
            break
        except Exception as e:
            logger.error("Receive error: %s", e)
            self._running = False
            break
        # ... process message ...

async def _send_loop(self):
    while self._running:
        try:
            chunk = self._send_q.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.02)
            continue
        if chunk is None:
            break
        try:
            await self._ws.send(chunk)
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("WebSocket closed (send): %s", e)
            self._running = False
            break
        except Exception as e:
            logger.error("Send error: %s", e)
            self._running = False
            break
    # Drain the queue so producer threads unblock
    while not self._send_q.empty():
        try:
            self._send_q.get_nowait()
        except queue.Empty:
            break
```

### Key elements

1. **`self._running = False` on every exception path** — ensures loops exit, `stop()` doesn't hang, watchdog detects state
2. **`queue.Empty` drain after loop exit** — prevents `feed_audio()` from blocking forever on a full queue
3. **Log the close code** — 1005 (server dropped), 1008 (Gemini GoAway), 1006 (abnormal) tell you which side closed and why

## Pattern: watchdog checks socket state before sending

```python
async def _connection_watchdog(self) -> None:
    while self._running:
        await asyncio.sleep(1.0)
        
        # Check Discord first
        if not self._vc or not self._vc.is_connected():
            if not self._running:
                return
            logger.warning("Discord disconnected. Stopping bridge.")
            await self.stop()
            return

        # Check WebSocket state before any send operation
        if not self._vapi._ws or not self._vapi._running:
            logger.warning("WebSocket dead. Stopping bridge.")
            await self.stop()
            return

        now = time.monotonic()
        idle = now - self._last_activity_at
        
        if idle >= AUTO_LEAVE_SECONDS:
            logger.info("Auto-leave after idle %.0fs", idle)
            await self.stop()
            return
```

### Key elements

1. **Check `_ws` exists and `_running` is still True** before calling `send_text()` — avoids `ConnectionClosed` exceptions in the watchdog itself
2. **Wrap `send_text()` in try/except** — idle prompts are non-critical; a failure should trigger shutdown, not crash

## Pattern: stop() must be idempotent

```python
async def stop(self):
    if not self._running:
        return
    self._running = False
    # Signal queues to drain
    try:
        self._send_q.put_nowait(None)
    except queue.Full:
        pass
    # Cancel tasks gracefully
    for t in self._tasks:
        if not t.done():
            t.cancel()
    # Wait briefly
    if self._tasks:
        await asyncio.gather(*self._tasks, return_exceptions=True)
    # Close WebSocket if still open
    if self._ws:
        try:
            await self._ws.close()
        except Exception:
            pass
    self._ws = None
```

### Key elements

1. **Early return if already stopped** — prevents double-close and cascading errors
2. **Put sentinel (`None`) into queue** — signals send loop to exit cleanly instead of timing out
3. **`return_exceptions=True` on gather** — cancels all tasks even if one raises
4. **Null out `_ws` after close** — prevents subsequent code from using the closed handle

## WebSocket close codes

| Code | Name | Meaning in voice bridges |
|------|------|-------------------------|
| 1000 | Normal | Clean close — usually from `stop()` |
| 1005 | No status | Server dropped us without reason — Vapi timeout, auth reject |
| 1006 | Abnormal | Network kill, process crash, firewall drop |
| 1008 | Policy/GoaWay | Gemini Live session duration exceeded, hard limit hit |
| 1011 | Server error | Provider internal error — retry may work |

### Detection in logs

```
WebSocket closed (receive): received 1005 (no status received [internal])
```

→ **Provider-side timeout** or **auth failure**. Check:
- Connection order (Vapi before Discord?)
- Audio input rate (silence keepalive present?)
- Auth token validity (test with manual curl)

```
WebSocket closed (receive): received 1008 (policy violation)
```

→ **Gemini Live session limit**. Implement `_restart()` with callback-based teardown (see `references/gemini-1008-reconnect.md`).

## Session context

2026-05-28: Vapi bridge connected and immediately closed with 1005. Root causes were stacked:
1. `voice_recv` removed (DAVE incompatibility) → zero user audio → 20s timeout
2. `_receive_loop` broke on `ConnectionClosed` without setting `_running = False` → watchdog crashed trying to send on dead socket
3. `_send_loop` didn't drain queue → potential producer thread block

Fixes applied: added `_keepalive_loop()` with 20ms silence injection, set `_running = False` on all WebSocket exception paths, added queue drain after send loop exit, added `_ws`/`_running` checks in watchdog before any send.
