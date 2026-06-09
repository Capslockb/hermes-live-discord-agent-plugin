"""
email_brief.py — Proactive inbox digest (criterion #7).

Fetches the latest N emails, scores them by importance, groups them
into Important / FYI / Auto, and produces a short spoken brief. The
brief is returned to the calling model AND fired through the
notification dispatcher so AFK users still get pinged.

Backends (tried in order):
  1. google_api.py (skills/productivity/google-workspace/scripts/) — preferred,
     richer data (Gmail labels, snippet, importance)
  2. himalaya CLI — envelope-only fallback

Importance heuristics:
  - Recency (last 24h scores higher)
  - Sender is in important_contacts (or matches important patterns)
  - Subject contains urgent/asap/deadline/invoice/etc.
  - Gmail label "IMPORTANT" if available
  - Not in blocklist domains/keywords (delegated to bridge._should_remind_email)
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("voice-email-brief")

# Defaults — env-overridable so the user can tune without code changes
DEFAULT_LIMIT = int(os.getenv("DISCORD_VOICE_LIVE_EMAIL_BRIEF_LIMIT", "8"))
DEFAULT_INTERVAL_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_EMAIL_BRIEF_INTERVAL_SECONDS", "1800"))  # 30 min
DEFAULT_ENABLED = os.getenv("DISCORD_VOICE_LIVE_EMAIL_BRIEF_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

# Persisted state
STATE_PATH = Path.home() / ".hermes" / "voice-users" / "email-brief-state.json"
STATE_LOCK = threading.Lock()

# Words in the subject that push importance up
URGENT_SUBJECT_PATTERNS = [
    re.compile(r"\b(urgent|asap|critical|emergency)\b", re.I),
    re.compile(r"\b(deadline|overdue|action required|eod|eow)\b", re.I),
    re.compile(r"\b(invoice|payment|billing|charged)\b", re.I),
    re.compile(r"\b(contract|legal|sign(ed|ing)?|signature)\b", re.I),
    re.compile(r"\b(fw(d)?\s*:\s*)\b", re.I),  # Fwd: chains get a small bump
]

# Auto-from senders (matches existing blocklist) — these go in the Auto bucket
AUTO_LABEL_HINTS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS",
                    "SPAM", "TRASH"}


# ── Backend fetchers ──────────────────────────────────────────────────────

def _google_api_path() -> Optional[Path]:
    p = (Path.home() / ".hermes" / "hermes-agent" / "skills" / "productivity"
         / "google-workspace" / "scripts" / "google_api.py")
    return p if p.exists() else None


def fetch_google(limit: int) -> List[Dict[str, Any]]:
    """Fetch latest inbox entries via google_api.py. Returns list of dicts.

    Each dict has at least: id, from, subject, date, snippet, labels (list).
    """
    bin_path = _google_api_path()
    if bin_path is None:
        raise FileNotFoundError("google_api.py not found")
    out = subprocess.run(
        [os.getenv("HERMES_PYTHON", "python3"), str(bin_path),
         "gmail", "search", "in:inbox", "--max", str(limit)],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        raise RuntimeError(f"google_api.py failed: {out.stderr[:200]}")
    items = json.loads(out.stdout)
    if not isinstance(items, list):
        return []
    out_list = []
    for it in items:
        labels = it.get("labels", []) or []
        out_list.append({
            "id": str(it.get("id", "")),
            "from": str(it.get("from", "") or it.get("sender", "")),
            "subject": str(it.get("subject", "")),
            "date": str(it.get("date", "") or it.get("internalDate", "")),
            "snippet": str(it.get("snippet", "") or it.get("body", ""))[:300],
            "labels": labels,
            "unread": "UNREAD" in labels or bool(it.get("unread")),
            "source": "google",
        })
    return out_list


def fetch_himalaya(limit: int) -> List[Dict[str, Any]]:
    """Fallback: fetch via himalaya. Envelope-only — no snippet, no labels."""
    out = subprocess.run(
        ["himalaya", "--quiet", "-o", "json", "envelope", "list",
         "--page-size", str(limit)],
        capture_output=True, text=True, timeout=15,
    )
    if out.returncode != 0:
        raise RuntimeError(f"himalaya failed: {out.stderr[:200]}")
    data = json.loads(out.stdout)
    messages = data if isinstance(data, list) else data.get("response", data.get("results", []))
    out_list = []
    for msg in messages:
        sender = msg.get("from", {})
        if isinstance(sender, list):
            sender = sender[0] if sender else {}
        from_str = sender.get("addr") or sender.get("address") or sender.get("name") or "unknown"
        flags = msg.get("flags", {})
        seen = (
            flags.get("seen")
            if isinstance(flags, dict)
            else ("Seen" in flags or "\\Seen" in flags if isinstance(flags, list) else False)
        )
        out_list.append({
            "id": str(msg.get("id", "")),
            "from": from_str,
            "subject": str(msg.get("subject", "(no subject)")),
            "date": str(msg.get("date", "")),
            "snippet": "",
            "labels": [],
            "unread": not bool(seen),
            "source": "himalaya",
        })
    return out_list


def fetch(limit: int = DEFAULT_LIMIT, prefer: str = "google") -> Tuple[List[Dict[str, Any]], str]:
    """Try the preferred backend, then fall back. Returns (emails, backend_used)."""
    backends = ([fetch_google, fetch_himalaya] if prefer == "google"
                else [fetch_himalaya, fetch_google])
    for fn in backends:
        try:
            return fn(limit), fn.__name__.replace("fetch_", "")
        except Exception as exc:
            logger.debug("email backend %s failed: %s", fn.__name__, exc)
            continue
    return [], "none"


# ── Importance scoring ────────────────────────────────────────────────────

def _score_email(email: Dict[str, Any], now_epoch: float) -> int:
    """Return an integer 0-100; higher = more important."""
    score = 0
    # Recency — Gmail dates are RFC 2822 strings; fall back to 0
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(email.get("date", ""))
        epoch = dt.timestamp()
        age_h = max(0.0, (now_epoch - epoch) / 3600.0)
        if age_h < 1:
            score += 35
        elif age_h < 6:
            score += 25
        elif age_h < 24:
            score += 15
        elif age_h < 72:
            score += 8
        else:
            score += 2
    except Exception:
        score += 5
    # Gmail importance label
    labels = {l.upper() for l in email.get("labels", []) or []}
    if "IMPORTANT" in labels:
        score += 25
    if "STARRED" in labels:
        score += 15
    if "CATEGORY_PRIMARY" in labels or "INBOX" in labels:
        score += 10
    if labels & AUTO_LABEL_HINTS:
        score -= 50  # demote promotions/social/etc.
    if not email.get("unread", True):
        score -= 10
    # Subject urgency
    subj = email.get("subject", "")
    for pat in URGENT_SUBJECT_PATTERNS:
        if pat.search(subj):
            score += 12
            break
    # Sender heuristic: short domain, no noreply
    sender = (email.get("from") or "").lower()
    if "noreply" in sender or "no-reply" in sender or "notifications@" in sender:
        score -= 30
    # Clamp
    return max(0, min(100, score))


def _bucket(emails_scored: List[Tuple[Dict[str, Any], int]]) -> Dict[str, List[Dict[str, Any]]]:
    out = {"important": [], "fyi": [], "auto": []}
    for email, score in emails_scored:
        labels = {l.upper() for l in email.get("labels", []) or []}
        if labels & AUTO_LABEL_HINTS or score < 20:
            out["auto"].append({**email, "_score": score})
        elif score >= 55:
            out["important"].append({**email, "_score": score})
        else:
            out["fyi"].append({**email, "_score": score})
    return out


# ── Brief rendering ───────────────────────────────────────────────────────

def render_brief(emails: List[Dict[str, Any]], max_chars: int = 1800) -> str:
    """Render a concise spoken brief. Keeps it short enough for TTS."""
    if not emails:
        return "Your inbox is empty. No new mail right now."
    now = time.time()
    scored = [(e, _score_email(e, now)) for e in emails]
    # Sort by score desc so important stuff floats up
    scored.sort(key=lambda x: -x[1])
    buckets = _bucket(scored)
    parts: List[str] = []
    if buckets["important"]:
        parts.append(f"**{len(buckets['important'])} important.**")
        for e in buckets["important"][:3]:
            f = e.get("from", "?")
            # Truncate sender name to first 30 chars
            short_from = f.split("<")[0].strip()[:30] or f[:30]
            subj = e.get("subject", "(no subject)")[:80]
            parts.append(f"• {short_from} — {subj}")
    if buckets["fyi"]:
        parts.append(f"\n**{len(buckets['fyi'])} FYI.**")
        for e in buckets["fyi"][:3]:
            short_from = (e.get("from", "?").split("<")[0].strip() or e.get("from", "?"))[:30]
            subj = e.get("subject", "(no subject)")[:80]
            parts.append(f"• {short_from} — {subj}")
        if len(buckets["fyi"]) > 3:
            parts.append(f"  +{len(buckets['fyi']) - 3} more.")
    if buckets["auto"]:
        labels_seen: set = set()
        for e in buckets["auto"]:
            for l in e.get("labels", []):
                if l.startswith("CATEGORY_"):
                    labels_seen.add(l.replace("CATEGORY_", "").title())
        if labels_seen:
            parts.append(f"\n**{len(buckets['auto'])} auto** ({', '.join(sorted(labels_seen))}).")
        else:
            parts.append(f"\n**{len(buckets['auto'])} auto** (filtered).")
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


# ── State persistence (de-dup) ────────────────────────────────────────────

def load_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("email-brief state load: %s", exc)
    return {"last_briefed_ids": [], "last_brief_at": 0.0}


def save_state(state: Dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("email-brief state save: %s", exc)


def has_new_mail(emails: List[Dict[str, Any]]) -> bool:
    """Return True if there's at least one email we haven't briefed yet."""
    state = load_state()
    seen = set(state.get("last_briefed_ids", []))
    return any(e.get("id") and e.get("id") not in seen for e in emails)


def mark_briefed(emails: List[Dict[str, Any]]) -> None:
    state = load_state()
    seen = list(state.get("last_briefed_ids", []))
    for e in emails:
        eid = e.get("id")
        if eid and eid not in seen:
            seen.append(eid)
    # Cap history at 500 IDs
    state["last_briefed_ids"] = seen[-500:]
    state["last_brief_at"] = time.time()
    save_state(state)


# ── Main entrypoints ──────────────────────────────────────────────────────

def build_brief(
    limit: int = DEFAULT_LIMIT,
    backend: str = "google",
) -> Dict[str, Any]:
    """Fetch + score + bucket + render. Returns a dict suitable for the
    model and the notification dispatcher.
    """
    emails, backend_used = fetch(limit=limit, prefer=backend)
    if not emails:
        return {
            "status": "ok",
            "backend": backend_used,
            "count": 0,
            "brief": "Your inbox is empty. No new mail right now.",
            "buckets": {"important": [], "fyi": [], "auto": []},
            "emails": [],
        }
    brief_text = render_brief(emails)
    now = time.time()
    scored = [(e, _score_email(e, now)) for e in emails]
    scored.sort(key=lambda x: -x[1])
    buckets = _bucket(scored)
    return {
        "status": "ok",
        "backend": backend_used,
        "count": len(emails),
        "brief": brief_text,
        "buckets": buckets,
        "emails": [{"id": e.get("id"), "from": e.get("from"),
                    "subject": e.get("subject"), "date": e.get("date"),
                    "score": s, "labels": e.get("labels", []),
                    "unread": e.get("unread", True)} for e, s in scored],
    }


def build_and_notify(
    *,
    limit: int = DEFAULT_LIMIT,
    backend: str = "google",
    force: bool = False,
    bridge: Optional[Any] = None,
    adapter: Optional[Any] = None,
    user_id: Optional[str] = None,
    source: str = "email_brief",
) -> Dict[str, Any]:
    """Build a brief; fire it through the notification dispatcher (criterion #6)
    so AFK users get pinged. If force=False, skip when nothing new since the
    last brief.
    """
    payload = build_brief(limit=limit, backend=backend)
    if payload["count"] == 0:
        return {**payload, "notified": False, "skipped": "empty inbox"}
    if not force and not has_new_mail(payload["emails"]):
        return {**payload, "notified": False, "skipped": "no new mail since last brief"}
    # Fire notification
    title = f"Inbox brief — {payload['count']} new"
    notified: Dict[str, Any] = {"status": "skipped", "channel": None}
    try:
        from notification import deliver
        notified = deliver(
            text=payload["brief"],
            mode="auto",
            bridge=bridge,
            adapter=adapter,
            user_id=user_id,
            title=title,
            source=source,
            event_class="agent.notify",
            sub_event="agent_notification",
        )
    except Exception as exc:
        logger.debug("email brief notify failed: %s", exc)
    # Mark as briefed so the per-email reminder loop / next tick dedupes
    mark_briefed(payload["emails"])
    return {**payload, "notified": True, "delivery": notified}


# ── Background ticker (driven by the bridge) ─────────────────────────────

_BRIEF_THREAD: Optional[threading.Thread] = None
_BRIEF_STOP = threading.Event()


def _brief_loop(get_bridge_fn, interval: float) -> None:
    while not _BRIEF_STOP.is_set():
        try:
            bridge = get_bridge_fn()
            adapter = getattr(bridge, "_adapter", None) if bridge is not None else None
            user_id = (
                getattr(bridge, "_target_user_id", None) if bridge is not None else None
            ) or os.getenv("DISCORD_VOICE_LIVE_USER_ID", "1474100257762578597")
            build_and_notify(bridge=bridge, adapter=adapter, user_id=user_id, source="email_brief_scheduler")
        except Exception as exc:
            logger.debug("email brief tick failed: %s", exc)
        _BRIEF_STOP.wait(interval)


def start_brief_scheduler(get_bridge_fn, interval: float = DEFAULT_INTERVAL_SECONDS) -> None:
    """Start the background ticker. Idempotent."""
    global _BRIEF_THREAD
    if not DEFAULT_ENABLED:
        logger.info("email brief scheduler disabled via env")
        return
    if _BRIEF_THREAD is not None and _BRIEF_THREAD.is_alive():
        return
    _BRIEF_STOP.clear()
    _BRIEF_THREAD = threading.Thread(
        target=_brief_loop, args=(get_bridge_fn, interval),
        name="voice-email-brief", daemon=True,
    )
    _BRIEF_THREAD.start()
    logger.info("email brief scheduler started (interval=%.0fs)", interval)


def stop_brief_scheduler(timeout: float = 3.0) -> None:
    _BRIEF_STOP.set()
    if _BRIEF_THREAD is not None:
        _BRIEF_THREAD.join(timeout=timeout)
