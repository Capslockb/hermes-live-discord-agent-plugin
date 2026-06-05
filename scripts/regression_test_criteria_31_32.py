#!/usr/bin/env python3
"""
regression_test_criteria_31_32.py — verify video awareness (#31) and
new-user onboarding (#32) wiring.

Three invariant sets:
  1. ONBOARDING_QUESTIONS is exported with 6 questions covering the
     expected fields
  2. mark_onboarding_complete() persists answers + flips
     onboarding_completed=True + mirrors well-known fields to top-level
  3. needs_onboarding() returns True for new profiles, False after
     mark_onboarding_complete() is called
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

PLUGIN_DIR = "/home/caps/.hermes/plugins/discord-voice"
sys.path.insert(0, PLUGIN_DIR)

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    icon = PASS if condition else FAIL
    results.append((icon, name, detail))
    print(f"  {icon} {name}" + (f" — {detail}" if detail and not condition else ""))


# Load user_profiles (no bridge needed for these checks)
spec = importlib.util.spec_from_file_location("user_profiles", os.path.join(PLUGIN_DIR, "user_profiles.py"))
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)

# Set 1: ONBOARDING_QUESTIONS exported
print("\n== Set 1: ONBOARDING_QUESTIONS ==")
check("ONBOARDING_QUESTIONS is a list", isinstance(up.ONBOARDING_QUESTIONS, list))
check("has 6 questions", len(up.ONBOARDING_QUESTIONS) == 6, f"got {len(up.ONBOARDING_QUESTIONS)}")
required_ids = {"name", "timezone", "work", "interests", "style", "pet_peeves"}
got_ids = {q["id"] for q in up.ONBOARDING_QUESTIONS}
check("all required question_ids present", got_ids == required_ids, f"got {got_ids}")
check("every question has 'question' field",
      all(isinstance(q.get("question"), str) and len(q["question"]) > 0 for q in up.ONBOARDING_QUESTIONS))
check("every question has 'field' field",
      all(isinstance(q.get("field"), str) for q in up.ONBOARDING_QUESTIONS))

# Set 2: mark_onboarding_complete persists correctly
print("\n== Set 2: mark_onboarding_complete persistence ==")
with tempfile.TemporaryDirectory() as td:
    up.VOICE_USERS_DIR = Path(td)
    # Create a fresh profile
    profile = up.get_or_create_profile("55555555555555555")
    check("new profile needs_onboarding is True", profile.needs_onboarding())
    check("new profile onboarding_completed is False", profile.onboarding_completed is False)
    # Mark complete with sample answers
    answers = {
        "name": "Alex",
        "timezone": "Europe/Amsterdam",
        "work": "I build open-source agent tools",
        "interests": "hermes, opencode, discord bots, web search",
        "style": "short and direct, no fluff",
        "pet_peeves": "don't say 'certainly' or 'I would be happy to'",
    }
    updated = up.mark_onboarding_complete(profile, answers)
    check("returns UserProfile", isinstance(updated, up.UserProfile))
    check("onboarding_completed is now True", updated.onboarding_completed is True)
    check("onboarding_completed_at is set", updated.onboarding_completed_at is not None)
    check("display_name mirrored from name", updated.display_name == "Alex")
    check("timezone mirrored", updated.timezone == "Europe/Amsterdam")
    check("communication_style mirrored from style",
          updated.communication_style == "short and direct, no fluff")
    check("interests parsed as list", updated.interests == ["hermes", "opencode", "discord bots", "web search"])
    check("onboarding_answers has all 6 keys",
          set(updated.onboarding_answers.keys()) == required_ids)
    # Reload from disk to verify persistence
    reloaded = up.get_or_create_profile("55555555555555555")
    check("reloaded from disk: onboarding_completed is True", reloaded.onboarding_completed is True)
    check("reloaded from disk: display_name persisted", reloaded.display_name == "Alex")
    check("reloaded from disk: interests persisted", reloaded.interests == ["hermes", "opencode", "discord bots", "web search"])
    check("reloaded from disk: needs_onboarding is False", not reloaded.needs_onboarding())

# Set 3: needs_onboarding() works
print("\n== Set 3: needs_onboarding() ==")
with tempfile.TemporaryDirectory() as td:
    up.VOICE_USERS_DIR = Path(td)
    p1 = up.get_or_create_profile("11111111111111111")
    check("fresh profile returns True for needs_onboarding", p1.needs_onboarding())
    up.mark_onboarding_complete(p1, {"name": "X", "timezone": "UTC", "work": "y", "interests": "a,b", "style": "s", "pet_peeves": "n"})
    p1_reload = up.get_or_create_profile("11111111111111111")
    check("post-onboarding returns False for needs_onboarding", not p1_reload.needs_onboarding())

# Set 4: UserProfile dataclass exposes all new fields
print("\n== Set 4: UserProfile fields ==")
required_fields = [
    "discord_id", "honcho_peer_name", "enabled_tools", "disabled_tools",
    "display_name", "voice_name", "system_prompt_overrides",
    "default_workdir", "notes_dir", "opencode_tmux_session",
    "is_owner", "created_at", "last_seen_at",
    "onboarding_completed", "onboarding_answers", "onboarding_completed_at",
    "interests", "timezone", "communication_style",
]
for f in required_fields:
    check(f"UserProfile has field '{f}'", f in up.UserProfile.__dataclass_fields__)

# Set 5: video awareness messaging (#31) — verify the source contains
# the new longer text that explains the /frame command
print("\n== Set 5: #31 video awareness message ==")
init_path = Path(PLUGIN_DIR) / "__init__.py"
text = init_path.read_text()
check("message mentions /frame command",
      "/frame command" in text and "video-frame-feeder.py" in text)
check("message in started screen sharing branch",
      "started screen sharing" in text and "/frame" in text)
check("message in turned on their camera branch",
      "turned on their camera" in text and "share a snapshot" in text)

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
print("ALL CHECKS PASSED — #31 video awareness + #32 onboarding work.")
