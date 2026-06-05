#!/usr/bin/env python3
"""
regression_test_criteria_17_18_19.py — verify the new webhook dispatcher,
email auto-correction, and email reminder logic.

Three invariant sets:
  1. Webhook dispatcher: per-event-class routing, throttling, embed format
  2. _autocorrect_email_address: handles common STT errors
  3. _should_remind_email: filters spam/automated senders
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

PLUGIN_DIR = "/home/caps/.hermes/plugins/discord-voice"
sys.path.insert(0, PLUGIN_DIR)

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    icon = PASS if condition else FAIL
    results.append((icon, name, detail))
    print(f"  {icon} {name}" + (f" — {detail}" if detail and not condition else ""))


import importlib.util

# Load webhook_dispatcher as a module
spec = importlib.util.spec_from_file_location("wh", os.path.join(PLUGIN_DIR, "webhook_dispatcher.py"))
wh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wh)

# Load bridge (need user_profiles importable too)
spec2 = importlib.util.spec_from_file_location("user_profiles", os.path.join(PLUGIN_DIR, "user_profiles.py"))
up_mod = importlib.util.module_from_spec(spec2)
sys.modules["user_profiles"] = up_mod
spec2.loader.exec_module(up_mod)
spec3 = importlib.util.spec_from_file_location("br", os.path.join(PLUGIN_DIR, "bridge.py"))
br = importlib.util.module_from_spec(spec3)
spec3.loader.exec_module(br)


# Set 1: Webhook dispatcher
print("\n== Set 1: Webhook dispatcher ==")

# Stop the lazy singleton if it spun up
if isinstance(getattr(wh, "_DISPATCHER", None), wh.WebhookDispatcher):
    wh._DISPATCHER.stop()
    wh._DISPATCHER = None

# Mock the HTTP POST
captured = []
def fake_urlopen(req, **kwargs):
    captured.append({
        "url": req.full_url,
        "body": json.loads(req.data.decode("utf-8")),
    })
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.status = 204
    return cm

# Clear env vars
for k in list(os.environ):
    if k.startswith("DISCORD_VOICE_LIVE_WEBHOOK"):
        del os.environ[k]

# Test 1a: No webhooks configured → no URLs to dispatch
os.environ["DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT"] = ""
urls = wh._load_webhook_urls()
check("empty env → empty URL map", all(len(v) == 0 for v in urls.values()))

# Test 1b: One URL per class
os.environ["DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT"] = "https://canary.discord.com/api/webhooks/1/aaa"
os.environ["DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_STATUS"] = "https://canary.discord.com/api/webhooks/2/bbb"
os.environ["DISCORD_VOICE_LIVE_WEBHOOK_EMAIL"] = "https://canary.discord.com/api/webhooks/3/ccc"
urls = wh._load_webhook_urls()
check("3 env vars → 3 URL lists", sum(len(v) for v in urls.values()) == 3)
check("transcript has 1 URL", len(urls["voice.transcript"]) == 1)
check("opencode.status has 1 URL", len(urls["opencode.status"]) == 1)
check("email.sent has 1 URL", len(urls["email.sent"]) == 1)

# Test 1c: Multiple URLs per class (comma-separated)
os.environ["DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT"] = "https://canary.discord.com/api/webhooks/1/aaa,https://canary.discord.com/api/webhooks/2/bbb,https://canary.discord.com/api/webhooks/3/ccc"
urls = wh._load_webhook_urls()
check("comma-separated → multiple URLs", len(urls["voice.transcript"]) == 3)

# Test 1d: Malformed URLs filtered
os.environ["DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT"] = "https://example.com/not-webhook,https://canary.discord.com/api/webhooks/1/aaa"
urls = wh._load_webhook_urls()
check("non-discord URLs filtered", len(urls["voice.transcript"]) == 1)

# Test 1e: Emit + HTTP dispatch (no throttle)
os.environ["DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT"] = "https://canary.discord.com/api/webhooks/test/tok"
with patch.object(urllib.request, "urlopen", fake_urlopen):
    wh._DISPATCHER = wh.WebhookDispatcher()  # re-init with test URL
    wh._DISPATCHER._thread.join(timeout=2)
    wh._DISPATCHER = wh.WebhookDispatcher()
    n = wh.emit_voice_input("hello world")
    check("emit_voice_input returns URL count", n == 1, f"got {n}")
    wh._DISPATCHER._thread.join(timeout=2)
    check("POST was made", len(captured) == 1, f"got {len(captured)}")
    if captured:
        emb = captured[0]["body"]["embeds"][0]
        check("embed has title", "title" in emb and "Voice Transcript" in emb["title"])
        check("embed description has text", emb.get("description") == "hello world")
        check("embed has color", "color" in emb)
        check("embed has timestamp", "timestamp" in emb)
        check("embed has no @everyone pings", captured[0]["body"].get("allowed_mentions", {}).get("parse") == [])

# Test 1f: Throttling (same event within throttle window → 0 dispatched).
# Note: WEBHOOK_THROTTLE_SECONDS is read at module import, so we test
# the dispatcher's _last_sent logic directly rather than going through
# the public emit() path.
captured.clear()
if isinstance(getattr(wh, "_DISPATCHER", None), wh.WebhookDispatcher):
    wh._DISPATCHER.stop()
# Create dispatcher with default 2s throttle (set before import)
wh._DISPATCHER = wh.WebhookDispatcher()
d = wh._DISPATCHER
d._last_sent.clear()
import time as _t
# First call: should be allowed
n1 = wh.emit_voice_output("first")
wh._DISPATCHER._thread.join(timeout=2)
# Manually inject a "recent" timestamp to simulate a call that just happened
d._last_sent[("transcript", wh._load_webhook_urls()["voice.transcript"][0])] = _t.monotonic()
# Second call within throttle window: should be throttled
n2 = wh.emit_voice_output("second")
wh._DISPATCHER._thread.join(timeout=2)
check("first emit dispatched", n1 == 1, f"got {n1}")
check("second emit throttled", n2 == 0, f"got {n2}")

# Test 1g: Embed format — sub_event title
captured.clear()
wh._DISPATCHER.stop()
wh._DISPATCHER = wh.WebhookDispatcher()
with patch.object(urllib.request, "urlopen", fake_urlopen):
    wh.emit_opencode_status("opencode_started", "test-session", "Goal: refactor auth")
    wh._DISPATCHER._thread.join(timeout=2)
    if captured:
        title = captured[0]["body"]["embeds"][0]["title"]
        check("opencode_started title formatted", "Opencode Started" in title)
        check("fields include session name",
              any(f.get("name") == "Session" and f.get("value") == "test-session"
                  for f in captured[0]["body"]["embeds"][0].get("fields", [])))

# Test 1h: add() method adds URL dynamically
os.environ["DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT"] = "https://canary.discord.com/api/webhooks/init/init"
wh._DISPATCHER.stop()
wh._DISPATCHER = wh.WebhookDispatcher()
result = wh._DISPATCHER.add("voice.transcript", "https://canary.discord.com/api/webhooks/added/xyz")
check("add() returns True for new URL", result is True)
result = wh._DISPATCHER.add("voice.transcript", "https://canary.discord.com/api/webhooks/added/xyz")
check("add() returns False for duplicate", result is False)
result = wh._DISPATCHER.add("voice.transcript", "https://not-a-webhook.com/foo")
check("add() rejects non-discord URL", result is False)
result = wh._DISPATCHER.add("nonexistent.class", "https://canary.discord.com/api/webhooks/x/y")
check("add() rejects unknown event class", result is False)

# Cleanup
wh._DISPATCHER.stop()
wh._DISPATCHER = None


# Set 2: Email address auto-correction
print("\n== Set 2: _autocorrect_email_address ==")

# 2a: Plain address → no change
result_addr, notes = br._autocorrect_email_address("alice@example.com")
check("plain address → no change", result_addr == "alice@example.com" and not notes)

# 2b: "at" → "@"
result_addr, notes = br._autocorrect_email_address("alice at example.com")
check("'at' → @", result_addr == "alice@example.com", f"got {result_addr}")

# 2c: "dot" → "."
result_addr, notes = br._autocorrect_email_address("alice at example dot com")
check("'dot' → .", result_addr == "alice@example.com", f"got {result_addr}")

# 2d: Uppercase "AT" / "DOT"
result_addr, notes = br._autocorrect_email_address("ALICE AT EXAMPLE DOT COM")
check("uppercase AT/DOT", result_addr == "alice@example.com", f"got {result_addr}")

# 2e: Domain lowercased
result_addr, notes = br._autocorrect_email_address("alice@EXAMPLE.COM")
check("domain lowercased", result_addr == "alice@example.com", f"got {result_addr}")

# 2e2: Whole address lowercased (STT often returns caps)
result_addr, notes = br._autocorrect_email_address("ALICE@EXAMPLE.COM")
check("whole address lowercased", result_addr == "alice@example.com", f"got {result_addr}")

# 2f: Trailing whitespace stripped
result_addr, notes = br._autocorrect_email_address("  alice@example.com  ")
check("whitespace stripped", result_addr == "alice@example.com", f"got {result_addr}")

# 2g: "underscore" → "_"
result_addr, notes = br._autocorrect_email_address("alice underscore bob at example.com")
check("underscore → _", result_addr == "alice_bob@example.com", f"got {result_addr}")

# 2h: Doubled space (defensive)
result_addr, notes = br._autocorrect_email_address("alice at  example.com")
check("doubled space → single @", result_addr == "alice@example.com", f"got {result_addr}")

# 2i: Returns notes (not empty) when changes were made
result_addr, notes = br._autocorrect_email_address("alice at example dot com")
check("changes have notes", len(notes) > 0, f"notes: {notes}")

# 2j: Unparseable input → return original
result_addr, notes = br._autocorrect_email_address("not an email at all")
check("unparseable → original", result_addr == "not an email at all" and not notes)

# 2k: Empty input
result_addr, notes = br._autocorrect_email_address("")
check("empty input → empty", result_addr == "" and not notes)

# 2l: Multiple @s → reject (bail)
result_addr, notes = br._autocorrect_email_address("a@b@c.com")
check("multiple @ → reject", "@b@" in result_addr and not notes)


# Set 3: _should_remind_email (criterion #19)
print("\n== Set 3: _should_remind_email ==")

# 3a: Real human email → remind
check("real human sender → remind", br._should_remind_email("alice@example.com", "Coffee tomorrow?"))
check("real human with name → remind",
      br._should_remind_email("John Smith <john.smith@acmecorp.com>", "Project status"))

# 3b: noreply → skip
check("noreply sender → skip", not br._should_remind_email("noreply@github.com", "PR merged"))
check("no-reply sender → skip", not br._should_remind_email("no-reply@stripe.com", "Payment failed"))
check("donotreply sender → skip", not br._should_remind_email("donotreply@company.com", "Update"))

# 3c: Newsletter keywords → skip
check("newsletter subject → skip", not br._should_remind_email("alice@example.com", "Monthly Newsletter"))
check("unsubscribe keyword → skip", not br._should_remind_email("alice@example.com", "Click unsubscribe"))

# 3d: CI / dev notifications → skip
check("GitHub PR subject → skip", not br._should_remind_email("alice@example.com", "[repo] PR #123: refactor auth"))
check("build notification → skip", not br._should_remind_email("alice@example.com", "Build #456 passed"))
check("CI sender → skip", not br._should_remind_email("ci@github.com", "Pipeline failed"))

# 3e: Automated keywords → skip
check("automated keyword → skip", not br._should_remind_email("alice@example.com", "Automated report"))
check("auto-generated → skip", not br._should_remind_email("alice@example.com", "Auto-generated summary"))

# 3f: Transactional (but human-ish) → skip
check("receipt subject → skip", not br._should_remind_email("alice@example.com", "Receipt for your purchase"))
check("invoice subject → skip", not br._should_remind_email("alice@example.com", "Invoice #12345"))

# 3g: Empty sender
check("empty sender → skip", not br._should_remind_email("", "Anything"))


# Summary
print("\n" + "=" * 60)
passed = sum(1 for icon, _, _ in results if icon == PASS)
failed = sum(1 for icon, _, _ in results if icon == FAIL)
print(f"  TOTAL: {passed} passed, {failed} failed (of {len(results)} checks)")
print("=" * 60)
if failed:
    print("\nFailed checks:")
    for icon, name, detail in results:
        if icon == FAIL:
            print(f"  {icon} {name} — {detail}")
    sys.exit(1)
print("ALL CHECKS PASSED — webhooks, email autocorrect, and reminders work.")
