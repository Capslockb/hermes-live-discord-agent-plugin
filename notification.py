"""
notification.py — Multi-channel proactive notification dispatcher.

Lets the voice agent break out of "reply only" mode and notify the user
on its own accord via:

  - voice     push text into Gemini Live (next spoken turn picks it up)
  - dm        send a Discord DM via the bot adapter
  - channel   post in a Discord text channel via the bot adapter
  - webhook   fire a webhook event via WebhookDispatcher
  - auto      pick the best path based on what's available

This is the "agent can break out of reply-only" infrastructure (criterion #6).
Called by:
  - local_notify tool (Gemini-callable, runs in worker thread)
  - POST /notify sidecar (any process that can hit 127.0.0.1:18943)
  - local_notify_schedule tool (timer-based, async)
  - opencode watcher on session finish (so AFK user gets a ping)
  - email reminder loop (existing, now uses deliver() instead of send_text())

The dispatcher is thread-safe and non-blocking from the caller's POV —
Discord sends and webhook enqueues are best-effort with short timeouts.
"""

import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("voice-notification")

# Sidecar control port (same as bridge.py HTTP_PORT)
NOTIFY_HTTP_TIMEOUT = float(os.getenv("DISCORD_VOICE_LIVE_NOTIFY_TIMEOUT", "5"))
SCHEDULED_PATH = Path.home() / ".hermes" / "voice-scheduled-notifications.jsonl"
SCHEDULED_LOCK = threading.Lock()


# ── Sidecar dispatch (POST to /notify on the bridge's local HTTP server) ──

def sidecar_notify(payload: Dict[str, Any], host: str = "127.0.0.1",
                   port: Optional[int] = None) -> Dict[str, Any]:
    """POST a notify payload to the running bridge's sidecar /notify endpoint.

    Returns the sidecar's response dict, or an error dict if the bridge
    isn't running. Safe to call from any thread.
    """
    if port is None:
        port = int(os.getenv("DISCORD_VOICE_LIVE_PORT", "18943"))
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}/notify",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "HermesVoice/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=NOTIFY_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"status": "ok", "raw": raw[:300]}
    except urllib.error.HTTPError as exc:
        return {"status": "error", "channel": "sidecar", "http_status": exc.code,
                "message": exc.read().decode("utf-8", errors="replace")[:300]}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"status": "unavailable", "channel": "sidecar", "message": str(exc)}


# ── Direct channel senders (used by sidecar /notify handler) ─────────────

def _send_voice(bridge: Any, text: str) -> Dict[str, Any]:
    """Push text into Gemini Live so the model speaks it on the next turn."""
    if bridge is None:
        return {"status": "error", "channel": "voice", "message": "no bridge"}
    gemini = getattr(bridge, "_gemini", None)
    if gemini is None or not getattr(gemini, "_running", False):
        return {"status": "error", "channel": "voice", "message": "gemini not running"}
    # Schedule the async send_text — the dispatcher runs in a worker thread
    try:
        loop = getattr(gemini, "_loop", None) or getattr(bridge, "_loop", None)
        # VoiceLiveBridge runs the gemini websocket on the bridge's main loop
        bridge_loop = getattr(bridge, "_loop", None)
        if bridge_loop is not None and bridge_loop.is_running():
            import asyncio
            asyncio.run_coroutine_threadsafe(gemini.send_text(text), bridge_loop)
        else:
            # No loop reference — fall back to sidecar /say (always works)
            return sidecar_notify({"mode": "voice", "text": text})
        return {"status": "ok", "channel": "voice", "queued": True}
    except Exception as exc:
        logger.warning("voice notify failed: %s", exc)
        return {"status": "error", "channel": "voice", "message": str(exc)}


def _send_dm(adapter: Any, user_id: str, text: str) -> Dict[str, Any]:
    """Send a DM via the discord bot adapter. Best-effort."""
    if adapter is None or user_id is None:
        return {"status": "error", "channel": "dm", "message": "no adapter/user_id"}
    client = getattr(adapter, "_client", None)
    if client is None:
        return {"status": "error", "channel": "dm", "message": "no discord client"}
    try:
        uid_int = int(user_id)
    except (TypeError, ValueError):
        return {"status": "error", "channel": "dm", "message": f"bad user_id: {user_id}"}
    user = None
    # Try cache first
    user = client.get_user(uid_int)
    if user is None:
        # Fallback: fetch_user (async — we can't await here, but it's
        # almost always in cache for guilds the bot shares with the user)
        return {"status": "error", "channel": "dm", "message": "user not in cache"}
    try:
        # Use a thread to run the async send without blocking the caller
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        async def _do_send():
            ch = user.dm_channel
            if ch is None:
                ch = await user.create_dm()
            sent = await ch.send(text[:2000])
            return sent.id

        # Schedule on the gateway's running loop if we can find it
        bridge_loop = None
        try:
            import gateway.run as gateway_run
            ref = getattr(gateway_run, "_gateway_runner_ref", None)
            runner = ref() if callable(ref) else None
            if runner is not None:
                bridge_loop = getattr(runner, "_gateway_loop", None)
        except Exception:
            bridge_loop = None

        if bridge_loop is not None and bridge_loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(_do_send(), bridge_loop)
            try:
                msg_id = fut.result(timeout=NOTIFY_HTTP_TIMEOUT)
                return {"status": "ok", "channel": "dm", "message_id": msg_id}
            except Exception as exc:
                return {"status": "error", "channel": "dm", "message": f"send failed: {exc}"}
        else:
            # No gateway loop — just run a fresh one in a thread
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(lambda: asyncio.run(_do_send()))
                msg_id = fut.result(timeout=NOTIFY_HTTP_TIMEOUT)
            return {"status": "ok", "channel": "dm", "message_id": msg_id}
    except Exception as exc:
        logger.warning("dm send failed: %s", exc)
        return {"status": "error", "channel": "dm", "message": str(exc)}


def _send_channel(adapter: Any, channel_id: str, text: str) -> Dict[str, Any]:
    """Post a message in a Discord text channel via the bot adapter."""
    if adapter is None or channel_id is None:
        return {"status": "error", "channel": "channel", "message": "no adapter/channel_id"}
    client = getattr(adapter, "_client", None)
    if client is None:
        return {"status": "error", "channel": "channel", "message": "no discord client"}
    try:
        cid_int = int(channel_id)
    except (TypeError, ValueError):
        return {"status": "error", "channel": "channel", "message": f"bad channel_id: {channel_id}"}
    channel = None
    for guild in getattr(client, "guilds", []) or []:
        ch = guild.get_channel(cid_int)
        if ch is not None:
            channel = ch
            break
    if channel is None:
        # Try global fetch
        channel = client.get_channel(cid_int)
    if channel is None:
        return {"status": "error", "channel": "channel", "message": f"channel {channel_id} not found"}
    try:
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        async def _do_send():
            sent = await channel.send(text[:2000])
            return sent.id

        bridge_loop = None
        try:
            import gateway.run as gateway_run
            ref = getattr(gateway_run, "_gateway_runner_ref", None)
            runner = ref() if callable(ref) else None
            if runner is not None:
                bridge_loop = getattr(runner, "_gateway_loop", None)
        except Exception:
            bridge_loop = None

        if bridge_loop is not None and bridge_loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(_do_send(), bridge_loop)
            try:
                msg_id = fut.result(timeout=NOTIFY_HTTP_TIMEOUT)
                return {"status": "ok", "channel": "channel", "message_id": msg_id, "channel_id": channel_id}
            except Exception as exc:
                return {"status": "error", "channel": "channel", "message": f"send failed: {exc}"}
        else:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(lambda: asyncio.run(_do_send()))
                msg_id = fut.result(timeout=NOTIFY_HTTP_TIMEOUT)
            return {"status": "ok", "channel": "channel", "message_id": msg_id, "channel_id": channel_id}
    except Exception as exc:
        logger.warning("channel send failed: %s", exc)
        return {"status": "error", "channel": "channel", "message": str(exc)}


def _send_webhook(event_class: str, sub_event: str, text: str,
                  fields: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Emit a webhook event via the existing WebhookDispatcher."""
    try:
        from webhook_dispatcher import get_dispatcher
        n = get_dispatcher().emit(event_class, sub_event, text, fields=fields, throttle=True)
        return {"status": "ok" if n > 0 else "no_subscribers", "channel": "webhook",
                "event_class": event_class, "sub_event": sub_event, "urls_reached": n}
    except Exception as exc:
        return {"status": "error", "channel": "webhook", "message": str(exc)}


# ── Top-level dispatch ───────────────────────────────────────────────────

def deliver(
    text: str,
    mode: str = "auto",
    *,
    bridge: Optional[Any] = None,
    adapter: Optional[Any] = None,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    event_class: str = "agent.notify",
    sub_event: str = "agent_notification",
    title: Optional[str] = None,
    source: str = "agent",
) -> Dict[str, Any]:
    """Send a proactive notification. mode ∈ {auto, voice, dm, channel, webhook, all}.

    `auto` picks the first available path in this order:
      voice  if bridge is running and gemini is connected and B is in voice
      dm     if bot adapter + B's user_id are available
      channel if a channel_id is provided
      webhook always (last resort, requires webhook URL configured)
    """
    text = (text or "").strip()
    if not text:
        return {"status": "error", "message": "text is required"}
    title = title or "Agent notification"
    fields = [
        {"name": "Source", "value": source, "inline": True},
    ]
    if mode == "auto":
        # Try voice first if bridge is alive
        if bridge is not None and getattr(getattr(bridge, "_gemini", None), "_running", False):
            r = _send_voice(bridge, f"[{title}] {text}")
            if r.get("status") == "ok":
                # Also fire a webhook so the transcript channel sees it
                _send_webhook(event_class, sub_event, text, fields=fields)
                return r
        # Then DM
        if adapter is not None and user_id is not None:
            r = _send_dm(adapter, user_id, f"**{title}**\n{text}")
            if r.get("status") == "ok":
                return r
        # Then channel
        if adapter is not None and channel_id is not None:
            r = _send_channel(adapter, channel_id, f"**{title}**\n{text}")
            if r.get("status") == "ok":
                return r
        # Last resort: webhook
        r = _send_webhook(event_class, sub_event, text, fields=fields)
        return r

    if mode == "voice":
        return _send_voice(bridge, f"[{title}] {text}")
    if mode == "dm":
        return _send_dm(adapter, user_id, f"**{title}**\n{text}")
    if mode == "channel":
        return _send_channel(adapter, channel_id, f"**{title}**\n{text}")
    if mode == "webhook":
        return _send_webhook(event_class, sub_event, text, fields=fields)
    if mode == "all":
        out = {}
        for ch in ("voice", "dm", "channel", "webhook"):
            if ch == "voice":
                out[ch] = _send_voice(bridge, f"[{title}] {text}")
            elif ch == "dm":
                out[ch] = _send_dm(adapter, user_id, f"**{title}**\n{text}")
            elif ch == "channel":
                out[ch] = _send_channel(adapter, channel_id, f"**{title}**\n{text}")
            elif ch == "webhook":
                out[ch] = _send_webhook(event_class, sub_event, text, fields=fields)
        # Mark overall success if at least one channel worked
        ok = any(r.get("status") == "ok" for r in out.values())
        return {"status": "ok" if ok else "partial", "channels": out}
    return {"status": "error", "message": f"unknown mode: {mode}"}


# ── Schedule (deferred delivery) ──────────────────────────────────────────

def schedule_notification(
    fire_at: float,
    text: str,
    mode: str = "auto",
    title: Optional[str] = None,
    source: str = "scheduled",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Queue a notification to fire at fire_at (epoch seconds).

    Persistence: the queue is JSONL at SCHEDULED_PATH. The dispatcher
    is a background thread that reads the file on each tick and fires
    due entries. Survives process restarts.
    """
    SCHEDULED_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": f"n-{int(time.time() * 1000)}-{os.getpid()}",
        "fire_at": float(fire_at),
        "text": text,
        "mode": mode,
        "title": title,
        "source": source,
        "kwargs": kwargs,
        "created_at": time.time(),
    }
    with SCHEDULED_LOCK:
        with SCHEDULED_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"status": "scheduled", "id": entry["id"], "fire_at": fire_at,
            "in_seconds": max(0.0, fire_at - time.time())}


def list_scheduled() -> List[Dict[str, Any]]:
    if not SCHEDULED_PATH.exists():
        return []
    with SCHEDULED_LOCK:
        lines = SCHEDULED_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def cancel_scheduled(notif_id: str) -> bool:
    """Remove a scheduled notification by id. Returns True if removed."""
    if not SCHEDULED_PATH.exists():
        return False
    with SCHEDULED_LOCK:
        lines = SCHEDULED_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = []
    removed = False
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if entry.get("id") == notif_id:
            removed = True
            continue
        kept.append(line)
    if removed:
        with SCHEDULED_PATH.open("w", encoding="utf-8") as fh:
            fh.write("\n".join(kept) + ("\n" if kept else ""))
    return removed


# Background ticker for scheduled notifications
_SCHEDULER_THREAD: Optional[threading.Thread] = None
_SCHEDULER_STOP = threading.Event()


def _scheduler_loop() -> None:
    while not _SCHEDULER_STOP.is_set():
        try:
            now = time.time()
            entries = list_scheduled()
            for entry in entries:
                if entry.get("fire_at", 0) <= now:
                    try:
                        deliver(
                            text=entry.get("text", ""),
                            mode=entry.get("mode", "auto"),
                            title=entry.get("title"),
                            source=entry.get("source", "scheduled"),
                            **entry.get("kwargs", {}),
                        )
                    except Exception as exc:
                        logger.warning("scheduled notify fire failed: %s", exc)
                    cancel_scheduled(entry.get("id", ""))
        except Exception as exc:
            logger.debug("scheduler loop error: %s", exc)
        _SCHEDULER_STOP.wait(2.0)


def start_scheduler() -> None:
    global _SCHEDULER_THREAD
    if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
        return
    _SCHEDULER_STOP.clear()
    _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, name="voice-notify-scheduler", daemon=True)
    _SCHEDULER_THREAD.start()
    logger.info("notification scheduler started (poll=2s)")


def stop_scheduler(timeout: float = 3.0) -> None:
    _SCHEDULER_STOP.set()
    if _SCHEDULER_THREAD is not None:
        _SCHEDULER_THREAD.join(timeout=timeout)
