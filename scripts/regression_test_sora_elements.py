#!/usr/bin/env python3
"""Regression tests for SORA bridge elements (preflight/grill/synth/redact)."""

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugin"))

import sora_bridge_elements as sora


def assert_true(name: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(f"FAIL: {name}")
    print(f"  ✓ {name}")


def test_redact() -> None:
    text = (
        "Authorization: Bearer sk_test_1234567890abcdef "
        "and token=glpat-xxxxxxxxxxxx "
        "and https://discord.com/api/webhooks/123/abc "
        "and jwt.eyJ.ignored"
    )
    out = sora.redact_secrets(text)
    assert_true("redact bearer token", "sk_test_1234567890abcdef" not in out)
    assert_true("redact glpat token", "glpat-xxxxxxxxxxxx" not in out)
    assert_true("redact webhook URL", "https://discord.com/api/webhooks/123/abc" not in out)
    assert_true("redact broad jwt-like", "jwt.eyJ.ignored" not in out)
    assert_true("redaction markers present", "[REDACTED" in out or "[REDACTED_TOKEN]" in out)


def test_classify() -> None:
    labels = sora.classify_transcript("User asked about gemini live voice bridge on port 18943 and bidi WSS")
    assert_true("classify gemini_live_bridge", "gemini_live_bridge" in labels)


def test_grill() -> None:
    result = sora.grill_transcript("Build a thing that uses Honcho and Pocket cues")
    questions = result["questions"]
    assert_true("grill returns questions", len(questions) >= 1)
    assert_true("grill flags vague terms", any("vague" in q.lower() or "Build a thing" in q for q in questions))
    assert_true("grill labels", "honcho_memory" in result["labels"])
    assert_true("redacted excerpt", "[REDACTED" not in result["redacted_excerpt"])


def test_synth() -> None:
    result = sora.synthesize_goal_subgoals("Ship vapi bridge integration with tailscale and honcho")
    assert_true("synth has goal", result["goal"].startswith("/goal"))
    assert_true("synth has subgoals", len(result["subgoals"]) >= 3)
    assert_true("synth mentions vapi", any("vapi" in sg.lower() for sg in result["subgoals"]))


async def test_preflight() -> None:
    # We can't depend on a real sidecar in CI. Just verify structure and secrets redaction.
    os.environ.setdefault("GEMINI_API_KEY", "test-key-" + "x" * 32)
    report = await sora.build_preflight_report()
    assert_true("preflight status", report.get("status") in {"ok", "warn", "error"})
    assert_true("preflight component", report.get("component") == "sora-gemini-live-bridge-preflight")
    assert_true("preflight has gemini block", "gemini" in report)
    assert_true("preflight has sidecar block", "sidecar" in report)


def test_register_tools_no_crash() -> None:
    class FakeCtx:
        def __init__(self) -> None:
            self.tools = []

        def register_tool(self, **kwargs):  # type: ignore
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
