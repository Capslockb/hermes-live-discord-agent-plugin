#!/usr/bin/env python3
"""
Voice Live Driver
=================
Lightweight CLI for driving the Discord Voice Live bridge without
touching the Hermes agent turn loop.

Commands:
    health          Show bridge health JSON
    join            Write autostart file and signal gateway (requires restart)
    leave           Stop the active bridge via control API
    say TEXT        Send text into the voice channel via bridge
    notes [N]       Read last N events from current notes file
    latest          Show latest notes file path and summary
    monitor         Poll health every 10s until Ctrl+C

Usage:
    python3 voice_live_driver.py health
    python3 voice_live_driver.py say "Hello from the bridge"
    python3 voice_live_driver.py notes 20
    python3 voice_live_driver.py latest --markdown
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import urllib.request
import urllib.error

CONTROL_PORT = int(os.getenv("DISCORD_VOICE_LIVE_PORT", "18943"))
BASE_URL = f"http://127.0.0.1:{CONTROL_PORT}"
NOTES_DIR = Path(os.getenv("DISCORD_VOICE_LIVE_NOTES_DIR", str(Path.home() / ".hermes" / "voice-live-notes")))
AUTOSTART_FILE = Path(os.getenv("DISCORD_VOICE_LIVE_AUTOSTART_FILE", str(Path.home() / ".hermes" / "voice-live-autostart.json")))

DEFAULT_GUILD_ID = os.getenv("DISCORD_VOICE_LIVE_GUILD_ID", "1480297825655980067")
DEFAULT_CHANNEL_ID = os.getenv("DISCORD_VOICE_LIVE_CHANNEL_ID", "")
DEFAULT_USER_ID = os.getenv("DISCORD_VOICE_LIVE_USER_ID", "1474100257762578597")


def _api(path: str, timeout: float = 3.0) -> Dict[str, Any]:
    url = f"{BASE_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"status": "error", "message": body, "code": e.code}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def cmd_health() -> int:
    data = _api("/health")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def cmd_leave() -> int:
    data = _api("/stop")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def cmd_say(text: str) -> int:
    if not text.strip():
        print("ERROR: empty text", file=sys.stderr)
        return 1
    import urllib.parse
    data = _api(f"/say?text={urllib.parse.quote(text.strip())}")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _latest_notes_file() -> Optional[Path]:
    if not NOTES_DIR.exists():
        return None
    files = sorted(NOTES_DIR.glob("voice-live-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def cmd_notes(limit: int = 50) -> int:
    latest = _latest_notes_file()
    if not latest:
        print("ERROR: no notes files found", file=sys.stderr)
        return 1
    data = _api(f"/notes?limit={max(1, min(limit, 500))}")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def cmd_latest(markdown: bool = False) -> int:
    latest = _latest_notes_file()
    if not latest:
        print("ERROR: no notes files found", file=sys.stderr)
        return 1
    print(f"Latest notes file: {latest}")
    print(f"Size: {latest.stat().st_size} bytes")
    print()

    # Count events
    events: List[Dict[str, Any]] = []
    with latest.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    print(f"Events: {len(events)}")
    directions = {"input": 0, "output": 0}
    for ev in events:
        d = ev.get("direction")
        if d in directions:
            directions[d] += 1
    print(f"  User turns: {directions['input']}")
    print(f"  Assistant turns: {directions['output']}")

    if markdown:
        # Try to run the analyzer if available
        analyzer = Path(__file__).parent / "voice_notes_analyzer.py"
        if analyzer.exists():
            os.system(f'python3 "{analyzer}" --file "{latest}" --markdown')
        else:
            print("(analyzer script not found)")
    return 0


def cmd_join(guild_id: str, channel_id: str, user_id: str) -> int:
    """Write autostart file so the gateway joins on next restart."""
    payload = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id) if channel_id else None,
        "user_id": str(user_id),
    }
    # Remove None values
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUTOSTART_FILE.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        print(f"Autostart file written: {AUTOSTART_FILE}")
        print(f"  Contents: {json.dumps(payload)}")
        print("Restart the gateway to join voice:")
        print("  systemctl --user restart hermes-gateway")
    except Exception as e:
        print(f"ERROR writing autostart file: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_monitor() -> int:
    print(f"Monitoring {BASE_URL}/health every 10s (Ctrl+C to stop)...")
    try:
        while True:
            data = _api("/health")
            ts = time.strftime("%H:%M:%S")
            status = data.get("status", "unknown")
            vc = data.get("voice_connected", False)
            pb = data.get("playback_active", False)
            rc = data.get("receiving_active", False)
            model = data.get("model") or "none"
            quiet = data.get("quiet_seconds", 0)
            up = data.get("uptime_seconds", 0)
            print(f"[{ts}] status={status} vc={vc} play={pb} recv={rc} model={model} up={up:.0f}s quiet={quiet:.0f}s")
            time.sleep(10)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive the Discord Voice Live bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Show bridge health")
    sub.add_parser("leave", help="Stop the bridge")
    sub.add_parser("monitor", help="Poll health every 10s")

    p_say = sub.add_parser("say", help="Send text through the bridge")
    p_say.add_argument("text", nargs="+", help="Text to speak")

    p_notes = sub.add_parser("notes", help="Read recent notes")
    p_notes.add_argument("limit", nargs="?", type=int, default=50, help="Max events (1-500)")

    p_latest = sub.add_parser("latest", help="Show latest notes file info")
    p_latest.add_argument("--markdown", action="store_true", help="Run markdown analyzer")

    p_join = sub.add_parser("join", help="Write autostart file for next gateway restart")
    p_join.add_argument("--guild", default=DEFAULT_GUILD_ID, help="Discord guild ID")
    p_join.add_argument("--channel", default=DEFAULT_CHANNEL_ID, help="Voice channel ID")
    p_join.add_argument("--user", default=DEFAULT_USER_ID, help="Discord user ID")

    args = parser.parse_args()

    if args.command == "health":
        return cmd_health()
    if args.command == "leave":
        return cmd_leave()
    if args.command == "say":
        return cmd_say(" ".join(args.text))
    if args.command == "notes":
        return cmd_notes(args.limit)
    if args.command == "latest":
        return cmd_latest(args.markdown)
    if args.command == "join":
        return cmd_join(args.guild, args.channel, args.user)
    if args.command == "monitor":
        return cmd_monitor()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
