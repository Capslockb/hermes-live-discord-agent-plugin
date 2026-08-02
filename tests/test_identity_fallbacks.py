"""Tests for identity fail-close behaviour (Issue #18).

Validates that:
- No hardcoded Discord ID is present in the embedded source.
- Owner authorization requires an explicitly configured VOICE_OWNER_DISCORD_ID.
- An unset or empty VOICE_OWNER_DISCORD_ID grants no owner capabilities.
- A non-matching ID grants no owner capabilities.
- A matching, valid ID grants owner capabilities.
- email_brief background loop does not use a hardcoded fallback user ID.
"""

import importlib
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).parent.parent
BRIDGE_PY = REPO_ROOT / "bridge.py"
INIT_PY = REPO_ROOT / "__init__.py"
EMAIL_BRIEF_PY = REPO_ROOT / "email_brief.py"
USER_PROFILES_PY = REPO_ROOT / "user_profiles.py"

# The hardcoded Discord snowflake that was in use prior to this fix.
_BANNED_ID = "1474100257762578597"


class TestNoEmbeddedIdentity(unittest.TestCase):
    """Source files must not contain the formerly embedded Discord snowflake."""

    def _assert_no_banned_id(self, path: Path):
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(
            _BANNED_ID,
            text,
            f"{path.name} still contains the embedded Discord ID {_BANNED_ID!r}",
        )

    def test_bridge_py_no_hardcoded_id(self):
        self._assert_no_banned_id(BRIDGE_PY)

    def test_init_py_no_hardcoded_id(self):
        self._assert_no_banned_id(INIT_PY)

    def test_email_brief_py_no_hardcoded_id(self):
        self._assert_no_banned_id(EMAIL_BRIEF_PY)

    def test_user_profiles_py_no_hardcoded_id(self):
        self._assert_no_banned_id(USER_PROFILES_PY)


class TestOwnerAuthorization(unittest.TestCase):
    """get_or_create_profile must be fail-closed when VOICE_OWNER_DISCORD_ID is absent."""

    def _load_user_profiles(self):
        """Reload user_profiles module with a clean import to avoid cached state."""
        mod_name = "user_profiles_test_isolation"
        spec = importlib.util.spec_from_file_location(mod_name, USER_PROFILES_PY)
        mod = importlib.util.module_from_spec(spec)
        # Provide a minimal sys.modules entry so relative imports inside succeed.
        sys.modules[mod_name] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.modules.pop(mod_name, None)
        return mod

    def test_unset_owner_id_grants_no_owner(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"VOICE_OWNER_DISCORD_ID": "", "VOICE_USERS_DIR": td}, clear=False):
                mod = self._load_user_profiles()
                profile = mod.get_or_create_profile("999888777666555444")
                self.assertFalse(
                    getattr(profile, "is_owner", False),
                    "Unset VOICE_OWNER_DISCORD_ID must not grant owner",
                )

    def test_non_matching_owner_id_grants_no_owner(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ,
                            {"VOICE_OWNER_DISCORD_ID": "111222333444555666",
                             "VOICE_USERS_DIR": td}, clear=False):
                mod = self._load_user_profiles()
                # Different user — must not be owner.
                profile = mod.get_or_create_profile("999888777666555444")
                self.assertFalse(
                    getattr(profile, "is_owner", False),
                    "Non-matching VOICE_OWNER_DISCORD_ID must not grant owner",
                )

    def test_matching_explicit_owner_id_grants_owner(self):
        import tempfile
        owner_id = "111222333444555666"
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ,
                            {"VOICE_OWNER_DISCORD_ID": owner_id,
                             "VOICE_USERS_DIR": td}, clear=False):
                mod = self._load_user_profiles()
                profile = mod.get_or_create_profile(owner_id)
                self.assertTrue(
                    getattr(profile, "is_owner", False),
                    "Matching explicit VOICE_OWNER_DISCORD_ID must grant owner",
                )

    def test_malformed_owner_id_grants_no_owner(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ,
                            {"VOICE_OWNER_DISCORD_ID": "not-a-snowflake",
                             "VOICE_USERS_DIR": td}, clear=False):
                mod = self._load_user_profiles()
                # A real-looking numeric ID must not accidentally match a junk owner.
                profile = mod.get_or_create_profile("999888777666555444")
                self.assertFalse(
                    getattr(profile, "is_owner", False),
                    "Malformed VOICE_OWNER_DISCORD_ID must not grant owner",
                )


if __name__ == "__main__":
    unittest.main()
