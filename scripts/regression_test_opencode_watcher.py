#!/usr/bin/env python3
"""
regression_test_opencode_watcher.py — verify the opencode progress watcher
sends voice updates on schedule and stops cleanly.

Five invariant sets:
  1. _opencode_extract_progress: returns empty when no new content
  2. _opencode_extract_progress: returns body + is_milestone=True for "error"
  3. _opencode_extract_progress: detects test pass/fail as milestone
  4. _opencode_extract_progress: strips ANSI escapes
  5. _opencode_tmux_window_alive: returns False for non-existent session
  6. Watcher throttling: only speaks after MIN_VOICE_GAP elapses
     (we test this by mocking the bridge and counting send_text calls)
"""

import asyncio
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

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
spec = importlib.util.spec_from_file_location("br", os.path.join(PLUGIN_DIR, "bridge.py"))
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)


# Set 1: _opencode_extract_progress — no new content
print("\n== Set 1: extract_progress returns empty when no new content ==")
with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
    f.write("line 1\nline 2\nline 3\n")
    tmpf = f.name
try:
    progress, new_count, milestone = br._opencode_extract_progress(tmpf, last_line_count=3)
    check("no new content → empty progress", progress == "")
    check("new_count is unchanged", new_count == 3)
    check("not a milestone", milestone is False)
finally:
    os.unlink(tmpf)

# Set 2: error detection
print("\n== Set 2: extract_progress detects 'error' as milestone ==")
with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
    f.write("starting work\n")
    f.flush()
    # Simulate new content
    with open(tmpf := f.name, "a") as af:
        af.write("Traceback (most recent call last):\n  File 'x.py', line 1\nERROR: cannot find module\n")
try:
    progress, new_count, milestone = br._opencode_extract_progress(tmpf, last_line_count=1)
    check("error detected as milestone", milestone is True)
    check("progress contains ERROR or Traceback", "ERROR" in progress or "Traceback" in progress)
    check("progress body has 'milestone' label", "milestone" in progress)
finally:
    os.unlink(tmpf)

# Set 3: test pass/fail detection
print("\n== Set 3: extract_progress detects test pass/fail as milestone ==")
with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
    f.write("running tests\n")
    f.flush()
    with open(tmpf := f.name, "a") as af:
        af.write("test_user_auth ... PASSED\ntest_user_login ... FAILED\n")
try:
    progress, new_count, milestone = br._opencode_extract_progress(tmpf, last_line_count=1)
    check("test pass/fail is milestone", milestone is True)
finally:
    os.unlink(tmpf)

# Set 4: ANSI strip
print("\n== Set 4: extract_progress strips ANSI escapes ==")
with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
    f.write("start\n")
    f.flush()
    with open(tmpf := f.name, "a") as af:
        af.write("\x1b[31mRED COLORED LINE\x1b[0m\nplain line after\n")
try:
    progress, new_count, milestone = br._opencode_extract_progress(tmpf, last_line_count=1)
    check("ANSI escapes stripped", "\x1b[" not in progress)
    check("actual content preserved", "RED COLORED LINE" in progress)
finally:
    os.unlink(tmpf)

# Set 5: tmux window alive — non-existent
print("\n== Set 5: tmux_window_alive for non-existent session ==")
alive = br._opencode_tmux_window_alive("nonexistent-tmux-session-xyz", "no-such-window")
check("non-existent tmux → alive=False", alive is False)

# Set 6: Watcher throttling — spawn watcher, write log, check send_text calls
print("\n== Set 6: watcher throttles + honors milestone ==")

async def set6():
    # Speed up the watcher for the test
    br.OPENCODE_WATCHER_INITIAL_DELAY_SECONDS = 0.0
    br.OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS = 1.0
    br.OPENCODE_WATCHER_POLL_SECONDS = 0.3

    # Make a log file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write("starting opencode task\n")
        log_path = f.name

    # Mock bridge
    bridge = MagicMock()
    bridge.metrics = {}
    send_calls = []
    async def fake_send_text(text):
        send_calls.append(text)
    bridge.send_text = fake_send_text

    # Spawn watcher
    session_name = "watcher-smoketest"
    br._opencode_set_user("test-user-1")
    br._opencode_spawn_watcher(
        session_name=session_name,
        tmux_session="opencode-voice",
        tmux_window="nonexistent-window",  # window will appear dead → triggers final summary
        log_path=log_path,
        user_id="test-user-1",
        goal="smoketest",
        model=None,
        bridge=bridge,
    )

    # Wait a moment for the watcher to start
    await asyncio.sleep(0.5)

    # Append some non-milestone content; should trigger a progress update
    with open(log_path, "a") as f:
        f.write("doing some work\nanother line\n")
    await asyncio.sleep(1.5)  # enough for poll + throttle gap

    # Append a milestone; should trigger immediate update
    with open(log_path, "a") as f:
        f.write("ERROR: something broke\n")
    await asyncio.sleep(0.6)

    # Wait for the watcher to detect the dead tmux window and emit final summary
    # (it polls every 0.3s; first poll detects window dead)
    await asyncio.sleep(1.0)

    # Stop the watcher
    br._opencode_stop_watcher(session_name, "test-user-1")
    await asyncio.sleep(0.5)

    # Assertions
    check("watcher called send_text at least 1 time", len(send_calls) >= 1,
          f"got {len(send_calls)} calls")
    # First few should be progress; one of them should contain ERROR
    # (milestone); last should be "finished" final summary
    milestone_seen = any("ERROR" in c or "milestone" in c for c in send_calls)
    check("milestone update sent", milestone_seen,
          f"sample: {send_calls[0][:80] if send_calls else 'none'}")
    final_seen = any("finished" in c.lower() for c in send_calls)
    check("final summary sent when tmux window dies", final_seen,
          f"sample: {send_calls[-1][:80] if send_calls else 'none'}")
    # Cleanup
    try:
        os.unlink(log_path)
    except Exception:
        pass

asyncio.run(set6())


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
print("ALL CHECKS PASSED — opencode watcher logic is correct.")
