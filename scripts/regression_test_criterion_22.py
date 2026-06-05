#!/usr/bin/env python3
"""
regression_test_criterion_22.py — verify the GitHub repo tracker tools work
end-to-end against the real `gh` CLI (already authenticated as Capslockb).

Five invariant sets:
  1. Tool declarations exist (6 GitHub tools registered)
  2. _run_github_tool('local_github_repo_list') returns real repos
  3. _run_github_tool('local_github_issues', {repo: '...'}) works
  4. _run_github_tool('local_github_note') + read roundtrip
  5. Tool gating: unknown tool → error
"""

import os
import sys
import tempfile
import importlib.util
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


# Load bridge (user_profiles dependency)
spec = importlib.util.spec_from_file_location("user_profiles", os.path.join(PLUGIN_DIR, "user_profiles.py"))
up_mod = importlib.util.module_from_spec(spec)
sys.modules["user_profiles"] = up_mod
spec.loader.exec_module(up_mod)
spec2 = importlib.util.spec_from_file_location("br", os.path.join(PLUGIN_DIR, "bridge.py"))
br = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(br)


# Set 1: Tool declarations exist
print("\n== Set 1: 6 GitHub tool declarations registered ==")
declared = [d["name"] for d in br._GITHUB_FUNCTION_DECLARATIONS]
check("6 GitHub tool declarations", len(declared) == 6, f"got {declared}")
check("local_github_repo_list declared", "local_github_repo_list" in declared)
check("local_github_issues declared", "local_github_issues" in declared)
check("local_github_prs declared", "local_github_prs" in declared)
check("local_github_issue_create declared", "local_github_issue_create" in declared)
check("local_github_note declared", "local_github_note" in declared)
check("local_github_notes_read declared", "local_github_notes_read" in declared)


# Set 2: repo_list works against real gh CLI
print("\n== Set 2: local_github_repo_list returns real repos ==")
r = br._run_github_tool("local_github_repo_list", {"limit": 3})
check("result key present", "result" in r, f"got {list(r.keys())}")
if "result" in r:
    check("count is int >= 1", isinstance(r["result"]["count"], int) and r["result"]["count"] >= 1)
    check("repos is a list", isinstance(r["result"]["repos"], list))
    if r["result"]["repos"]:
        first = r["result"]["repos"][0]
        check("first repo has full_name with '/'",
              "/" in first.get("full_name", ""),
              f"got {first.get('full_name')}")
        check("first repo has 'private' bool",
              isinstance(first.get("private"), bool))


# Set 3: issues list works (against one of the user's private repos)
print("\n== Set 3: local_github_issues returns real issues ==")
r = br._run_github_tool("local_github_repo_list", {"limit": 1})
if "result" in r and r["result"]["repos"]:
    test_repo = r["result"]["repos"][0]["full_name"]
    print(f"  (testing against {test_repo})")
    ri = br._run_github_tool("local_github_issues", {"repo": test_repo, "state": "all", "limit": 5})
    check("issues result has 'count'", "result" in ri and "count" in ri.get("result", {}),
          f"got {list(ri.keys())}")
    if "result" in ri:
        check("issues result is a list",
              isinstance(ri["result"].get("issues"), list),
              f"got {type(ri['result'].get('issues'))}")


# Set 4: note append + read roundtrip
print("\n== Set 4: local_github_note + notes_read roundtrip ==")
import json as _json
test_marker = "REGRESSION-TEST-MARKER-XYZ-12345"
with tempfile.TemporaryDirectory() as td:
    # Redirect notes path
    orig_path = br._NOTES_PATH
    br._NOTES_PATH = Path(td) / "voice-session-notes.jsonl"
    try:
        # Write a note
        r1 = br._run_github_tool("local_github_note",
                                 {"text": test_marker, "category": "test"})
        check("note write succeeded", "result" in r1, f"got {r1}")
        # Read it back
        r2 = br._run_github_tool("local_github_notes_read", {"limit": 10})
        check("notes_read succeeded", "result" in r2, f"got {r2}")
        if "result" in r2:
            found = any(n.get("text") == test_marker for n in r2["result"]["notes"])
            check("note roundtrip found our marker", found, f"notes: {r2['result']['notes']}")
            # Test category filter
            r3 = br._run_github_tool("local_github_notes_read",
                                     {"limit": 10, "category": "test"})
            if "result" in r3:
                check("category filter works",
                      all(n.get("category") == "test" for n in r3["result"]["notes"]))
            r4 = br._run_github_tool("local_github_notes_read",
                                     {"limit": 10, "category": "nonexistent"})
            if "result" in r4:
                check("nonexistent category returns empty", r4["result"]["count"] == 0)
    finally:
        br._NOTES_PATH = orig_path


# Set 5: error handling
print("\n== Set 5: error handling ==")
# Unknown tool
r = br._run_github_tool("local_github_nonexistent", {})
check("unknown tool returns error", "error" in r, f"got {r}")
# Missing required param
r = br._run_github_tool("local_github_issues", {})
check("missing repo returns error", "error" in r, f"got {r}")
# Note with empty text
with tempfile.TemporaryDirectory() as td:
    orig_path = br._NOTES_PATH
    br._NOTES_PATH = Path(td) / "voice-session-notes.jsonl"
    try:
        r = br._run_github_tool("local_github_note", {"text": ""})
        check("empty note returns error", "error" in r, f"got {r}")
    finally:
        br._NOTES_PATH = orig_path


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
print("ALL CHECKS PASSED — GitHub repo tracker works.")
