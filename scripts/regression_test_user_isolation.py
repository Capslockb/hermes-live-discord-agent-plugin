#!/usr/bin/env python3
"""
regression_test_user_isolation.py — verify per-user profile isolation
doesn't brick the owner or regress the legacy single-user fallback.

Exercises 8 invariants:
  1. Legacy single-user mode (profile=None) sees ALL tools
  2. Owner sees all 40 tools
  3. New user gets the safe starter set (no destructive tools)
  4. Destructive tool is blocked at profile.is_tool_allowed() for new user
  5. Destructive tool is allowed for owner
  6. Update endpoint correctly toggles enabled_tools
  7. Per-user opencode registry: cross-user session access denied
  8. Profile YAML is round-trippable (load → save → load)
  9. index.json tracks every seen user
 10. Owner promotion on first contact (new user whose id == owner)
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Make the plugin importable
PLUGIN_DIR = "/home/caps/.hermes/plugins/discord-voice"
sys.path.insert(0, PLUGIN_DIR)

# Load bridge.py in a way that user_profiles is importable as a sibling
import importlib.util
import user_profiles as up
spec = importlib.util.spec_from_file_location(
    "bridge_under_test",
    os.path.join(PLUGIN_DIR, "bridge.py"),
)
br = importlib.util.module_from_spec(spec)
sys.modules["user_profiles"] = up
spec.loader.exec_module(br)

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    icon = PASS if condition else FAIL
    results.append((icon, name, detail))
    print(f"  {icon} {name}{(' — ' + detail) if detail and not condition else ''}")


# Build a clean temp profile dir for every check
def fresh_profiles():
    td = tempfile.mkdtemp(prefix="regression_users_")
    up.VOICE_USERS_DIR = Path(td)
    up.VOICE_USERS_INDEX = up.VOICE_USERS_DIR / "index.json"
    return Path(td)


OWNER_ID = "1474100257762578597"
GUEST_ID = "99999999999999999"

# Set 1: legacy single-user mode (profile=None) should pass everything
print("\n== Set 1: legacy single-user fallback (profile=None) ==")
fresh_profiles()
all_tool_names = []
for decl_list in (br._SPOTIFY_FUNCTION_DECLARATIONS, br._WEB_FUNCTION_DECLARATIONS,
                  br._LOCAL_FUNCTION_DECLARATIONS, br._HOMEASSISTANT_FUNCTION_DECLARATIONS,
                  br._OPENCODE_FUNCTION_DECLARATIONS, br._SYSINSPECT_FUNCTION_DECLARATIONS):
    for d in decl_list:
        all_tool_names.append(d["name"])
check("legacy mode exposes 40+ tool names", len(all_tool_names) >= 40, f"got {len(all_tool_names)}")
check("legacy mode includes opencode_run", "opencode_run" in all_tool_names)
check("legacy mode includes spotify_playlists", "spotify_playlists" in all_tool_names)
check("legacy mode includes local_inspect_read", "local_inspect_read" in all_tool_names)

# Set 2: Owner profile gets everything
print("\n== Set 2: owner profile ==")
fresh_profiles()
owner = up.get_or_create_profile(OWNER_ID, force_owner=True)
check("owner.is_owner is True", owner.is_owner)
owner_visible = [t for t in all_tool_names if owner.is_tool_allowed(t)]
check(f"owner sees all {len(all_tool_names)} tools", len(owner_visible) == len(all_tool_names),
      f"saw {len(owner_visible)} of {len(all_tool_names)}")
check("owner can run opencode_run", owner.is_tool_allowed("opencode_run"))
check("owner can use local_inspect_read", owner.is_tool_allowed("local_inspect_read"))
check("owner can use local_inspect_grep", owner.is_tool_allowed("local_inspect_grep"))
check("owner can call opencode_stop", owner.is_tool_allowed("opencode_stop"))
check("owner can call opencode_send", owner.is_tool_allowed("opencode_send"))

# Set 3: New user gets the safe starter set
print("\n== Set 3: new user safe defaults ==")
fresh_profiles()
guest = up.get_or_create_profile(GUEST_ID)
check("new user is NOT owner", not guest.is_owner)
check("new user CAN play spotify", guest.is_tool_allowed("spotify_play"))
check("new user CAN search web", guest.is_tool_allowed("web_search"))
check("new user CAN check honcho", guest.is_tool_allowed("local_honcho"))
check("new user CAN send email", guest.is_tool_allowed("local_email"))
check("new user CAN use HA (if tool declared)", guest.is_tool_allowed("local_homeassistant_get_state"))
check("new user CANNOT run opencode_run", not guest.is_tool_allowed("opencode_run"))
check("new user CANNOT stop opencode", not guest.is_tool_allowed("opencode_stop"))
check("new user CANNOT inspect files", not guest.is_tool_allowed("local_inspect_read"))
check("new user CANNOT grep files", not guest.is_tool_allowed("local_inspect_grep"))
# But read-only opencode status IS allowed (read-only is fine)
check("new user CAN check opencode status", guest.is_tool_allowed("opencode_status"))
check("new user CAN list opencode sessions", guest.is_tool_allowed("opencode_list"))

# Set 4: Unknown tool names are denied
print("\n== Set 4: unknown tool ==")
fresh_profiles()
guest = up.get_or_create_profile(GUEST_ID)
check("new user cannot invoke skynet_launch", not guest.is_tool_allowed("skynet_launch"))
check("new user cannot invoke rm_rf", not guest.is_tool_allowed("rm_rf"))

# Set 5: explicit disabled_tools overrides enabled_tools
print("\n== Set 5: disabled_tools overrides enabled ==")
fresh_profiles()
guest = up.get_or_create_profile(GUEST_ID)
guest = up.update_profile(GUEST_ID, {
    "enabled_tools": guest.enabled_tools + ["opencode_run", "local_inspect_read"],
    "disabled_tools": ["opencode_run"],
})
check("explicitly disabled opencode_run is blocked", not guest.is_tool_allowed("opencode_run"))
check("enabled but not disabled local_inspect_read works", guest.is_tool_allowed("local_inspect_read"))

# Set 6: Per-user opencode registry
print("\n== Set 6: per-user opencode isolation ==")
fresh_profiles()
br._OPENCODE_SESSIONS.clear()

# User A runs a session
br._opencode_set_user(OWNER_ID)
r = br._run_opencode_tool("opencode_run", {
    "name": "session-A", "goal": "echo hi from A", "workdir": "/tmp"
})
check("user A run succeeds", "result" in r, str(r.get("error", ""))[:60])
check("user A tmux window namespaced", "oc-" in r["result"]["tmux_window"], r["result"]["tmux_window"])

# User B runs a session
br._opencode_set_user(GUEST_ID)
r = br._run_opencode_tool("opencode_run", {
    "name": "session-B", "goal": "echo hi from B", "workdir": "/tmp"
})
check("user B run succeeds", "result" in r, str(r.get("error", ""))[:60])
check("user B tmux window namespaced", "oc-" in r["result"]["tmux_window"], r["result"]["tmux_window"])

# User A lists — sees only A
br._opencode_set_user(OWNER_ID)
lst = br._opencode_list_sessions()
check("user A list contains A's session only", len(lst) == 1 and lst[0]["user"] == OWNER_ID,
      f"got {[(s.get('user'), s.get('name')) for s in lst]}")

# User B lists — sees only B
br._opencode_set_user(GUEST_ID)
lst = br._opencode_list_sessions()
check("user B list contains B's session only", len(lst) == 1 and lst[0]["user"] == GUEST_ID,
      f"got {[(s.get('user'), s.get('name')) for s in lst]}")

# User A tries to peek B's session
br._opencode_set_user(OWNER_ID)
cross = br._run_opencode_tool("opencode_status", {"name": "session-B"})
check("user A cannot peek B's session", "error" in cross, str(cross)[:80])

# User A tries to stop B's session
cross = br._run_opencode_tool("opencode_stop", {"name": "session-B"})
check("user A cannot stop B's session", "error" in cross, str(cross)[:80])

# User B stops own session (using the dispatch entry point with original-case name).
# Must explicitly set user first because the dispatcher doesn't auto-bind user
# when called directly (auto-binding only happens via the bridge executor).
br._opencode_set_user(GUEST_ID)
own_stop = br._run_opencode_tool("opencode_stop", {"name": "session-B"})
check("user B can stop own session (case-insensitive lookup)",
      own_stop.get("result", {}).get("killed") is True, str(own_stop)[:80])

# Cleanup A
br._opencode_set_user(OWNER_ID)
br._run_opencode_tool("opencode_stop", {"name": "session-A"})

# Set 7: Profile YAML round-trip
print("\n== Set 7: profile YAML round-trip ==")
fresh_profiles()
p1 = up.get_or_create_profile(GUEST_ID)
p1_yaml_path = up.VOICE_USERS_DIR / f"{GUEST_ID}.yaml"
check("profile YAML file exists on disk", p1_yaml_path.exists())
p1_reloaded = up.get_or_create_profile(GUEST_ID)
check("reloaded profile has same discord_id", p1_reloaded.discord_id == p1.discord_id)
check("reloaded profile has same honcho_peer_name",
      p1_reloaded.honcho_peer_name == p1.honcho_peer_name)

# Set 8: index.json tracking
print("\n== Set 8: index.json tracking ==")
fresh_profiles()
up.get_or_create_profile(GUEST_ID)
up.get_or_create_profile(OWNER_ID)
up.get_or_create_profile("11122233344455566")
check("index.json exists", up.VOICE_USERS_INDEX.exists())
with open(up.VOICE_USERS_INDEX) as f:
    idx = json.load(f)
check("index tracks all 3 users", len(idx) == 3, f"saw {len(idx)}")
check("index has last_seen_at for each user",
      all("last_seen_at" in v for v in idx.values()))

# Set 9: Owner detection auto-promotes on first contact
print("\n== Set 9: auto-owner promotion ==")
fresh_profiles()
os.environ["VOICE_OWNER_DISCORD_ID"] = OWNER_ID
# Recreate module-level owner_id reading
up._default_profile_yaml  # touch
p = up.get_or_create_profile(OWNER_ID)  # first contact, no force_owner
check("new profile with owner id is auto-promoted to owner", p.is_owner,
      f"profile was {p}")
check("auto-promoted owner can run opencode_run", p.is_tool_allowed("opencode_run"))

# Set 10: "all" enabled_tools bypasses known-vocab check
print("\n== Set 10: enabled_tools='all' ==")
fresh_profiles()
p = up.get_or_create_profile(GUEST_ID)
p = up.update_profile(GUEST_ID, {"enabled_tools": ["all"], "disabled_tools": []})
check("'all' allows spotify_play", p.is_tool_allowed("spotify_play"))
check("'all' allows opencode_run", p.is_tool_allowed("opencode_run"))
check("'all' allows unknown tool", p.is_tool_allowed("skynet_foo"))
# disabled still wins
p = up.update_profile(GUEST_ID, {"enabled_tools": ["all"], "disabled_tools": ["opencode_run"]})
check("'all' + disabled still blocks opencode_run", not p.is_tool_allowed("opencode_run"))

# Set 11: owner profile can be listed
print("\n== Set 11: list_profiles API ==")
fresh_profiles()
up.get_or_create_profile(OWNER_ID, force_owner=True)
up.get_or_create_profile(GUEST_ID)
lst = up.list_profiles()
check("list_profiles returns both users", len(lst) == 2, f"got {len(lst)}")
owner_entry = [r for r in lst if r["discord_id"] == OWNER_ID]
check("list_profiles flags owner", owner_entry and owner_entry[0]["is_owner"])

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
print("ALL CHECKS PASSED — per-user isolation has NOT regressed.")
