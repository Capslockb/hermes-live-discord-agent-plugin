"""
webhook_dispatcher.py — Push live bridge events to Discord webhooks.

The voice bridge posts updates to Discord webhooks so the user gets
a written transcript / progress log in channels they can scroll back
through later. Per the user's design preference, webhooks are
**per-event-class** (not per-user) — all users dump to the same
event-class webhook, and the same event-class can have multiple
webhook URLs that all receive the same payload.

Event classes (criterion #17):
  voice.transcript     — every Gemini input/output transcript line
  opencode.status      — opencode started / progress / milestone / finished / stopped
  opencode.transcript  — live opencode log tail (throttled)
  email.sent           — when an email is sent via local_email
  bridge.status        — when the voice bridge connects / disconnects
  tool.called          — when a tool is invoked (sampled, throttled)

Design:
  - Background thread pulls events off a thread-safe queue and POSTs
    to each registered webhook URL for that event class.
  - Per-event-class throttling (default 1 per webhook per 2 seconds).
  - Discord embed format: title (event name), description (text),
    color (event-class), fields (metadata), timestamp.
  - Webhook URLs come from env vars, one per class. Multiple URLs per
    class are comma-separated.
  - Graceful degradation: all webhooks silently fail on network errors.
  - Auto-truncation: 1900 chars max per description (Discord limit).
"""

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("voice-webhook-dispatcher")

WEBHOOK_QUEUE_MAX = 256
WEBHOOK_HTTP_TIMEOUT = 8.0
WEBHOOK_THROTTLE_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_WEBHOOK_THROTTLE_SECONDS", "2"))

# Per-event-class webhook URLs. Each env var is a comma-separated list of
# Discord webhook URLs. Empty = no webhooks for that class.
_WEBHOOK_ENV_VARS = {
    "voice.transcript": "DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT",
    "opencode.status": "DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_STATUS",
    "opencode.transcript": "DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_TRANSCRIPT",
    "email.sent": "DISCORD_VOICE_LIVE_WEBHOOK_EMAIL",
    "bridge.status": "DISCORD_VOICE_LIVE_WEBHOOK_BRIDGE_STATUS",
    "bridge.video": "DISCORD_VOICE_LIVE_WEBHOOK_VIDEO",
    "tool.called": "DISCORD_VOICE_LIVE_WEBHOOK_TOOL_CALLED",
}

# Friendly event-class label shown in the embed
_EVENT_CLASS_LABEL = {
    "voice.transcript": "Voice Transcript",
    "opencode.status": "OpenCode Status",
    "opencode.transcript": "OpenCode Transcript",
    "email.sent": "Email Sent",
    "bridge.status": "Bridge Status",
    "bridge.video": "Bridge Video",
    "tool.called": "Tool Called",
}

# Sub-event colors (mapped from sub-event names)
_SUB_COLORS = {
    "bridge_started": 0x3BA55D,        # green
    "bridge_stopped": 0x747F8D,        # grey
    "voice_input": 0x5865F2,           # blurple
    "voice_output": 0xEB459E,          # pink
    "opencode_started": 0xFEE75C,      # yellow
    "opencode_progress": 0x5865F2,     # blurple
    "opencode_milestone": 0xED4245,    # red
    "opencode_finished": 0x3BA55D,     # green
    "opencode_stopped": 0x747F8D,      # grey
    "email_sent": 0x00B4D8,            # cyan
    "tool_called": 0x99AAB5,           # grey
    "video_initialized": 0x9B59B6,     # purple
    "video_still_noisy": 0xFEE75C,    # yellow (warn — feeder sending low-info frames)
    "info": 0x99AAB5,
    "warning": 0xFEE75C,
    "error": 0xED4245,
}

# Per-sub-event throttling. If a sub-event isn't in this map, default
# to "no throttle" (always send).
_THROTTLE_KEYS = {
    "voice_input": "transcript",
    "voice_output": "transcript",
    "opencode_progress": "opencode_progress",
    "tool_called": "tool",
}


def _load_webhook_urls() -> Dict[str, List[str]]:
    """Return {event_class: [url, ...]} map from env vars.

    Each env var (e.g. DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT) holds a
    comma-separated list of Discord webhook URLs. Empty/missing = no
    webhooks for that class.
    """
    out: Dict[str, List[str]] = {}
    for event_class, env_var in _WEBHOOK_ENV_VARS.items():
        raw = os.getenv(env_var, "").strip()
        if not raw:
            out[event_class] = []
            continue
        urls = [
            u.strip() for u in raw.split(",")
            if u.strip() and ("discord.com/api/webhooks" in u or "discordapp.com/api/webhooks" in u)
        ]
        out[event_class] = urls
    return out


def _add_url(url: str, event_class: str) -> bool:
    """Add a URL to an event class's list. Returns True if it was new."""
    if "discord.com/api/webhooks" not in url and "discordapp.com/api/webhooks" not in url:
        return False
    os.environ.setdefault(_WEBHOOK_ENV_VARS[event_class], "")
    existing = os.environ[_WEBHOOK_ENV_VARS[event_class]]
    urls = [u.strip() for u in existing.split(",") if u.strip()]
    if url in urls:
        return False
    urls.append(url)
    os.environ[_WEBHOOK_ENV_VARS[event_class]] = ",".join(urls)
    return True


class WebhookDispatcher:
    """Background thread that POSTs bridge events to Discord webhooks.

    Thread-safe: any code path can call .emit() from any thread.
    All HTTP work happens on the single background thread; callers
    never block on network.
    """

    def __init__(self):
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=WEBHOOK_QUEUE_MAX)
        self._urls: Dict[str, List[str]] = _load_webhook_urls()
        self._last_sent: Dict[Tuple[str, str], float] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Stats
        self.dispatched = 0
        self.dropped_queue_full = 0
        self.failed_posts = 0
        self.start()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="voice-webhook-dispatcher", daemon=True
        )
        self._thread.start()
        n_urls = sum(len(v) for v in self._urls.values())
        n_classes = sum(1 for v in self._urls.values() if v)
        logger.info(
            "webhook dispatcher started: %d URLs across %d event class(es)",
            n_urls, n_classes,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def urls_for_class(self, event_class: str) -> List[str]:
        return list(self._urls.get(event_class, []))

    def add(self, event_class: str, url: str) -> bool:
        """Add a URL to an event class. Returns True if it was new."""
        if event_class not in _WEBHOOK_ENV_VARS:
            return False
        if not _add_url(url, event_class):
            return False
        # Re-load the env-derived map so subsequent dispatches see it
        self._urls = _load_webhook_urls()
        return True

    def emit(
        self,
        event_class: str,
        sub_event: str,
        text: str,
        *,
        fields: Optional[List[Dict[str, Any]]] = None,
        throttle: bool = True,
        throttle_key: Optional[Tuple[str, str]] = None,
    ) -> int:
        """Enqueue an event for delivery. Returns number of URLs that will receive it.

        throttle_key: optional explicit (key, url) prefix for throttling. Use this
        when you want a non-default throttle bucket (e.g. per-(event_class, sub_event)
        rather than the global per-sub_event default). When provided, it overrides
        the _THROTTLE_KEYS lookup.
        """
        urls = self.urls_for_class(event_class)
        if not urls:
            return 0

        # Throttling: skip if the same (throttle_key, url) was sent
        # within WEBHOOK_THROTTLE_SECONDS. Throttling is per-(sub-event, url).
        effective_key = throttle_key[0] if throttle_key else _THROTTLE_KEYS.get(sub_event)
        if throttle and effective_key:
            now = time.monotonic()
            kept_urls = []
            for url in urls:
                key = (effective_key, url)
                last = self._last_sent.get(key, 0.0)
                if (now - last) >= WEBHOOK_THROTTLE_SECONDS:
                    kept_urls.append(url)
            urls = kept_urls
            if not urls:
                return 0
            for url in urls:
                self._last_sent[(effective_key, url)] = now

        envelope = {
            "event_class": event_class,
            "event_class_label": _EVENT_CLASS_LABEL.get(event_class, event_class),
            "sub_event": sub_event,
            "text": text[:1900] if text else "",
            "color": _SUB_COLORS.get(sub_event, 0x99AAB5),
            "fields": fields or [],
            "ts": time.time(),
        }
        try:
            self._q.put_nowait(envelope)
        except queue.Full:
            self.dropped_queue_full += 1
            return 0
        return len(urls)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                envelope = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._dispatch(envelope)
            except Exception as exc:
                logger.debug("webhook dispatch crashed: %s", exc)

    def _dispatch(self, envelope: Dict[str, Any]) -> None:
        urls = self.urls_for_class(envelope["event_class"])
        if not urls:
            return
        body = self._build_discord_payload(envelope)
        for url in urls:
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json", "User-Agent": "HermesVoice/1.0"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=WEBHOOK_HTTP_TIMEOUT) as resp:
                    if resp.status not in (200, 204):
                        self.failed_posts += 1
                        logger.debug(
                            "webhook returned HTTP %d for %s", resp.status, envelope["sub_event"]
                        )
                    else:
                        self.dispatched += 1
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                self.failed_posts += 1
                logger.debug("webhook POST failed for %s: %s", envelope["sub_event"], exc)
            except Exception as exc:
                self.failed_posts += 1
                logger.debug("webhook dispatch error: %s", exc)

    def _build_discord_payload(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """Build a Discord webhook embed payload."""
        sub_event = envelope["sub_event"]
        text = envelope.get("text", "")
        fields = envelope.get("fields", []) or []
        # Truncate field values to Discord's 1024-char per-field limit
        safe_fields = []
        for f in fields:
            if not isinstance(f, dict):
                continue
            safe_fields.append({
                "name": str(f.get("name", ""))[:256],
                "value": str(f.get("value", ""))[:1024],
                "inline": bool(f.get("inline", False)),
            })

        return {
            "embeds": [{
                "title": f"{envelope['event_class_label']}: {sub_event.replace('_', ' ').title()}",
                "description": text,
                "color": envelope.get("color", 0x99AAB5),
                "fields": safe_fields,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(envelope.get("ts", time.time()))),
            }],
            # Disable @everyone / @here pings
            "allowed_mentions": {"parse": []},
        }


# ── Module-level singleton ──────────────────────────────────────────────
_DISPATCHER: Optional[WebhookDispatcher] = []  # type: ignore[assignment]


def get_dispatcher() -> WebhookDispatcher:
    """Lazy-init singleton. Returns the live dispatcher, creating it on first call."""
    global _DISPATCHER
    if _DISPATCHER is None or _DISPATCHER == []:
        _DISPATCHER = WebhookDispatcher()
    return _DISPATCHER  # type: ignore[return-value]


def shutdown_dispatcher() -> None:
    global _DISPATCHER
    if isinstance(_DISPATCHER, WebhookDispatcher):
        _DISPATCHER.stop()
        _DISPATCHER = None


# ── Convenience wrappers ─────────────────────────────────────────────────
# These are the call-sites the bridge uses.

def emit_voice_input(text: str) -> int:
    if not text.strip():
        return 0
    return get_dispatcher().emit(
        "voice.transcript", "voice_input", text, throttle=True,
    )


def emit_voice_output(text: str) -> int:
    if not text.strip():
        return 0
    return get_dispatcher().emit(
        "voice.transcript", "voice_output", text, throttle=True,
    )


def emit_opencode_status(sub_event: str, session_name: str, detail: str = "",
                         *, fields: Optional[List[Dict[str, Any]]] = None) -> int:
    base_fields = [{"name": "Session", "value": session_name, "inline": True}]
    if fields:
        base_fields.extend(fields)
    return get_dispatcher().emit(
        "opencode.status", sub_event, detail,
        fields=base_fields, throttle=False,
    )


def emit_opencode_transcript(session_name: str, log_tail: str) -> int:
    return get_dispatcher().emit(
        "opencode.transcript", "opencode_transcript",
        log_tail,
        fields=[{"name": "Session", "value": session_name, "inline": True}],
        throttle=True,
    )


def emit_email_sent(to: str, subject: str) -> int:
    return get_dispatcher().emit(
        "email.sent", "email_sent",
        f"**To:** {to}\n**Subject:** {subject}",
        fields=[
            {"name": "Recipient", "value": to, "inline": True},
            {"name": "Subject", "value": subject, "inline": True},
        ],
        throttle=False,
    )


def emit_bridge_status(sub_event: str, detail: str = "") -> int:
    return get_dispatcher().emit(
        "bridge.status", sub_event, detail, throttle=False,
    )


def emit_video_initialized(source: str, frame_bytes: int, accepted_after_silence_s: float) -> int:
    """Announce that the bridge just accepted its first real video frame after a
    quiet period. The user gets a Discord notification via webhook so they know
    their camera/screen is actually flowing.

    Throttled by default to avoid spam if the feeder briefly hiccups and the
    "first frame" event fires repeatedly.
    """
    desc = f"Video feed is live ({frame_bytes} bytes, after {accepted_after_silence_s:.1f}s of silence)"
    if source:
        desc = f"Video feed from `{source}` is live ({frame_bytes} bytes, after {accepted_after_silence_s:.1f}s of silence)"
    return get_dispatcher().emit(
        "bridge.video", "video_initialized", desc,
        throttle=True,
        throttle_key=("bridge.video", "video_initialized"),
        fields=[
            {"name": "Source", "value": str(source) or "unknown", "inline": True},
            {"name": "Frame size", "value": f"{frame_bytes} B", "inline": True},
            {"name": "Quiet before", "value": f"{accepted_after_silence_s:.1f}s", "inline": True},
        ],
    )


def emit_tool_called(tool_name: str, args_summary: str) -> int:
    return get_dispatcher().emit(
        "tool.called", "tool_called",
        f"**{tool_name}**\n{args_summary[:1500]}",
        throttle=True,
    )

