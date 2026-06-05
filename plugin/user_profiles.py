"""
user_profiles.py — Per-Discord-user profile system for the voice bridge.

Every Discord user who joins a Gemini Live voice session gets their own
isolated profile with:
  - Honcho peer name (so memory is per-user, not global)
  - Allowed tool prefixes (so a guest user can't trigger your opencode sessions)
  - Per-user notes + transcripts directory
  - Per-user default workdir for opencode runs
  - Optional per-user system prompt overrides

Profiles live in ~/.hermes/voice-users/<discord_id>.yaml and are
auto-created on first contact with safe defaults.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("voice-user-profiles")

VOICE_USERS_DIR = Path(
    os.getenv("VOICE_USERS_DIR", str(Path.home() / ".hermes" / "voice-users"))
)
VOICE_USERS_INDEX = VOICE_USERS_DIR / "index.json"

# Default tools enabled for a new user. "all" = no restriction. Order matters
# for the prose in the system prompt override that mentions them.
DEFAULT_ENABLED_TOOL_PREFIXES = (
    "spotify",
    "web_",
    "local_",
    "local_email",
    "local_honcho",
    "local_homeassistant",
    "opencode_",
)

# Tools that are NEVER enabled for a fresh profile. The user must explicitly
# add them after the profile is created (e.g. via a Discord admin command).
# This is the safety floor for "new user" = "no destructive capability".
NEVER_AUTO_ENABLED = {
    "opencode_run",
    "opencode_send",
    "opencode_stop",
    "local_inspect_read",
    "local_inspect_grep",
}

# All known tool name prefixes/names. Used to validate enabled_tools entries.
KNOWN_TOOL_NAMES: List[str] = []  # populated by register_known_tool()


def register_known_tool(name: str) -> None:
    """Called by bridge.py at import time to populate the allowlist vocabulary."""
    if name and name not in KNOWN_TOOL_NAMES:
        KNOWN_TOOL_NAMES.append(name)


def _safe_discord_id(raw: str) -> str:
    """Discord IDs are 17-20 digit snowflakes — sanitize input to that format only."""
    if not raw:
        return "anonymous"
    cleaned = re.sub(r"[^0-9]", "", str(raw))
    return cleaned or "anonymous"


def _default_profile_yaml(discord_id: str) -> Dict[str, Any]:
    """Return the safe-default profile dict for a brand-new Discord user."""
    now = int(time.time())
    return {
        "discord_id": discord_id,
        "display_name": None,            # filled in later via Discord API if available
        "honcho_peer_name": f"discord-{discord_id}",   # isolated per-user
        "voice_name": None,              # inherits global default
        "enabled_tools": list(DEFAULT_ENABLED_TOOL_PREFIXES),  # safe starter set
        "disabled_tools": list(NEVER_AUTO_ENABLED),             # never-on floor
        "system_prompt_overrides": "",   # appended to BASE_SYSTEM_PROMPT
        "default_workdir": str(Path.home()),
        "notes_dir": str(VOICE_USERS_DIR / discord_id / "notes"),
        "opencode_tmux_session": f"opencode-voice-{discord_id}",
        "created_at": now,
        "last_seen_at": now,
        "is_owner": False,                # owner-only tools gated on this
    }


def _atomic_write_yaml(path: Path, data: Dict[str, Any]) -> None:
    """Write YAML atomically (tmp + rename) to avoid corrupted files on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    tmp.replace(path)


def _read_yaml(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Failed to read profile %s: %s", path, exc)
        return None


@dataclass
class UserProfile:
    """Immutable snapshot of a Discord user's voice-bridge configuration."""
    discord_id: str
    honcho_peer_name: str
    enabled_tools: List[str] = field(default_factory=list)
    disabled_tools: List[str] = field(default_factory=list)
    display_name: Optional[str] = None
    voice_name: Optional[str] = None
    system_prompt_overrides: str = ""
    default_workdir: str = ""
    notes_dir: str = ""
    opencode_tmux_session: str = ""
    is_owner: bool = False
    created_at: int = 0
    last_seen_at: int = 0

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check whether this user is allowed to invoke a given tool.

        A tool is allowed if:
          - It passes vocabulary check (known or "all" enabled)
          - AND not in self.disabled_tools
          - AND (self.enabled_tools contains a matching prefix / exact name / "all")
        """
        if KNOWN_TOOL_NAMES and tool_name not in KNOWN_TOOL_NAMES and "all" not in self.enabled_tools:
            return False
        if tool_name in self.disabled_tools:
            return False
        if not self.enabled_tools or "all" in self.enabled_tools:
            return True
        for entry in self.enabled_tools:
            if entry == "all":
                return True
            if entry == tool_name:
                return True
            # Prefix match: "spotify" matches "spotify_play", "local_" matches "local_weather"
            stripped = entry.rstrip("_")
            if tool_name == stripped or tool_name.startswith(stripped + "_"):
                return True
        return False

    def full_system_prompt(self, base_prompt: str) -> str:
        """Return base + per-user overrides."""
        if not self.system_prompt_overrides.strip():
            return base_prompt
        return (
            base_prompt
            + "\n\n"
            + "--- PER-USER OVERRIDES ---\n"
            + self.system_prompt_overrides.strip()
            + "\n--- END PER-USER OVERRIDES ---"
        )


def _update_index(discord_id: str) -> None:
    """Add or update entry in the index file. Best-effort, never throws."""
    try:
        index = {}
        VOICE_USERS_DIR.mkdir(parents=True, exist_ok=True)
        if VOICE_USERS_INDEX.exists():
            try:
                with open(VOICE_USERS_INDEX, "r") as f:
                    index = json.load(f) or {}
            except Exception:
                index = {}
        index[discord_id] = {"last_seen_at": int(time.time())}
        tmp = VOICE_USERS_INDEX.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(index, f, indent=2, sort_keys=True)
        tmp.replace(VOICE_USERS_INDEX)
    except Exception as exc:
        logger.debug("Index update failed: %s", exc)


def get_or_create_profile(discord_id: str, *, force_owner: bool = False) -> UserProfile:
    """Load a profile from disk, or create a new one with safe defaults.

    The OWNER discord ID is read from VOICE_OWNER_DISCORD_ID (default: B's snowflake).
    Owners get the destructive tools enabled by default.
    """
    discord_id = _safe_discord_id(discord_id)
    owner_id = _safe_discord_id(os.getenv("VOICE_OWNER_DISCORD_ID", "1474100257762578597"))
    path = VOICE_USERS_DIR / f"{discord_id}.yaml"
    data = _read_yaml(path)
    is_new = False
    if not data:
        data = _default_profile_yaml(discord_id)
        is_new = True
    # Owner gets the full toolset
    if force_owner or discord_id == owner_id:
        data["is_owner"] = True
        # Owner gets the full toolset: ensure all never-auto-enabled tools
        # are removed from disabled_tools AND added to enabled_tools.
        for t in NEVER_AUTO_ENABLED:
            data["disabled_tools"] = [x for x in data.get("disabled_tools", []) if x != t]
        existing_enabled = list(data.get("enabled_tools", []))
        for t in NEVER_AUTO_ENABLED:
            if t not in existing_enabled:
                existing_enabled.append(t)
        data["enabled_tools"] = existing_enabled
    if is_new:
        try:
            _atomic_write_yaml(path, data)
        except Exception as exc:
            logger.warning("Could not persist new profile %s: %s", path, exc)
    else:
        # Update last_seen_at without rewriting the whole file
        data["last_seen_at"] = int(time.time())
        try:
            _atomic_write_yaml(path, data)
        except Exception:
            pass
    _update_index(discord_id)
    return UserProfile(
        discord_id=str(data.get("discord_id", discord_id)),
        honcho_peer_name=str(data.get("honcho_peer_name", f"discord-{discord_id}")),
        enabled_tools=list(data.get("enabled_tools", [])),
        disabled_tools=list(data.get("disabled_tools", [])),
        display_name=data.get("display_name"),
        voice_name=data.get("voice_name"),
        system_prompt_overrides=str(data.get("system_prompt_overrides", "")),
        default_workdir=str(data.get("default_workdir", str(Path.home()))),
        notes_dir=str(data.get("notes_dir", str(VOICE_USERS_DIR / discord_id / "notes"))),
        opencode_tmux_session=str(data.get("opencode_tmux_session", f"opencode-voice-{discord_id}")),
        is_owner=bool(data.get("is_owner", False)),
        created_at=int(data.get("created_at", 0)),
        last_seen_at=int(data.get("last_seen_at", 0)),
    )


def update_profile(discord_id: str, updates: Dict[str, Any]) -> UserProfile:
    """Merge updates into the profile and persist. Returns the new profile."""
    discord_id = _safe_discord_id(discord_id)
    path = VOICE_USERS_DIR / f"{discord_id}.yaml"
    data = _read_yaml(path) or _default_profile_yaml(discord_id)
    data.update(updates)
    data["last_seen_at"] = int(time.time())
    _atomic_write_yaml(path, data)
    return get_or_create_profile(discord_id)


def list_profiles() -> List[Dict[str, Any]]:
    """Enumerate all known profiles (most recently seen first)."""
    out: List[Dict[str, Any]] = []
    if not VOICE_USERS_DIR.exists():
        return out
    for p in VOICE_USERS_DIR.glob("*.yaml"):
        try:
            data = _read_yaml(p) or {}
            out.append({
                "discord_id": data.get("discord_id", p.stem),
                "display_name": data.get("display_name"),
                "is_owner": bool(data.get("is_owner", False)),
                "honcho_peer_name": data.get("honcho_peer_name"),
                "last_seen_at": data.get("last_seen_at", 0),
                "created_at": data.get("created_at", 0),
                "enabled_tools": data.get("enabled_tools", []),
            })
        except Exception:
            continue
    out.sort(key=lambda r: -int(r.get("last_seen_at", 0)))
    return out


def delete_profile(discord_id: str) -> bool:
    """Remove a profile from disk. Returns True if something was removed."""
    discord_id = _safe_discord_id(discord_id)
    path = VOICE_USERS_DIR / f"{discord_id}.yaml"
    if path.exists():
        try:
            path.unlink()
            return True
        except Exception as exc:
            logger.warning("Could not delete profile %s: %s", path, exc)
    return False
