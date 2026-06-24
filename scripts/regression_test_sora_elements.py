#!/usr/bin/env python3
"""Regression tests for SORA bridge elements (preflight/grill/synth/redact)."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin"))

import sora_bridge_elements as sora


def assert_true(name: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(f"FAIL: {name}")
    print(f"  ✓ {name}")


def test_redact() -> None:
    text = (
        "Authorization: Bearer bearer...mnop "
        "and token=glpat-xxxxxxxxxxxx "
        "and https://discord.com/api/webhooks/123/abcDEF_123 "
        "and jwt.eyJ.ignored"
    )
    out = sora.redact_secrets(text)
    assert_true("redact bearer token", "bearer...mnop" not in out)
    assert_true("redact glpat token", "glpat-xxxxxxxxxxxx" not in out)
    assert_true("redact webhook URL", "https://discord.com/api/webhooks/123/abcDEF_123" not in out)
    assert_true("redact broad jwt-like", "jwt.eyJ.ignored" not in out)
    assert_true("redaction markers present", "[REDACTED" in out)


def test_classify() -> None:
    labels = sora.classify_transcript("User asked about gemini live voice bridge and bidi WSS")
    assert_true("classify gemini_live_bridge", "gemini_live_bridge" in labels)


def test_grill() -> None:
    result = sora.grill_transcript("Build a thing that uses Honcho and Pocket cues")
    questions = result["questions"]
    assert_true("grill returns questions", len(questions) >= 1)
    assert_true("grill flags vague terms", any("vague" in q.lower() or "Build a thing" in q for q in questions))
    assert_true("grill labels", "honcho_memory" in result["labels"])


def test_synth() -> None:
    result = sora.synthesize_goal_subgoals("Ship vapi bridge integration with tailscale and honcho")
    assert_true("synth has goal", result["goal"].startswith("/goal"))
    assert_true("synth has subgoals", len(result["subgoals"]) >= 3)
    assert_true("synth mentions vapi", any("vapi" in sg.lower() for sg in result["subgoals"]))


async def test_preflight() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old_home = os.environ.get("HOME")
        old_peer = os.environ.get("VOICE_LIVE_HONCHO_PEER")
        os.environ["HOME"] = tmp
        os.environ.setdefault("GEMINI_API_KEY", "test-key-" + "x" * 32)
        os.environ["VOICE_LIVE_HONCHO_PEER"] = "private-peer-name"
        try:
            report = await sora.build_preflight_report(active_bridges={123456789: {"vc": None}})
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_peer is None:
                os.environ.pop("VOICE_LIVE_HONCHO_PEER", None)
            else:
                os.environ["VOICE_LIVE_HONCHO_PEER"] = old_peer
    rendered = str(report)
    assert_true("preflight status", report.get("status") in {"ok", "warn", "error"})
    assert_true("preflight component", report.get("component") == "sora-gemini-live-bridge-preflight")
    assert_true("preflight has gemini block", "gemini" in report)
    assert_true("preflight has sidecar block", "sidecar" in report)
    assert_true("preflight redacts bridge keys", "123456789" not in rendered)
    assert_true("preflight redacts honcho peer value", "private-peer-name" not in rendered)


def test_register_tools_no_crash() -> None:
    class FakeCtx:
        def __init__(self) -> None:
            self.tools = []

        def register_tool(self, **kwargs):  # type: ignore[no-untyped-def]
            self.tools.append(kwargs)

    ctx = FakeCtx()
    sora.register_sora_bridge_tools(ctx, active_bridges={})
    assert_true("registered 4 tools", len(ctx.tools) == 4)
    names = {t["name"] for t in ctx.tools}
    assert_true("tool names", names == {"sora_bridge_preflight", "sora_live_grill", "sora_goal_synth", "sora_redact"})


def run() -> int:
    print("SORA bridge elements regression tests")
    test_redact()
    test_classify()
    test_grill()
    test_synth()
    asyncio.run(test_preflight())
    test_register_tools_no_crash()
    print("\nAll SORA bridge element tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
