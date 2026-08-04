"""Tests for identity fail-close behaviour (Issue #18).

Validates that executable source contains no embedded Discord snowflake,
owner authorization is derived from current explicit configuration, and
persisted historical owner fields cannot retain effective privileges.
"""

import importlib
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).parent.parent
BRIDGE_PY = REPO_ROOT / "bridge.py"
INIT_PY = REPO_ROOT / "__init__.py"
EMAIL_BRIEF_PY = REPO_ROOT / "email_brief.py"
USER_PROFILES_PY = REPO_ROOT / "user_profiles.py"
SNOWFLAKE_LITERAL = re.compile(r"(?<!\d)\d{17,20}(?!\d)")
OWNER_ID = "1" * 18
OTHER_USER_ID = "9" * 18


class TestNoEmbeddedIdentity(unittest.TestCase):
    """Executable source files must not contain literal Discord snowflakes."""

    def _assert_no_snowflake_literal(self, path: Path):
        text = path.read_text(encoding="utf-8")
        self.assertIsNone(
            SNOWFLAKE_LITERAL.search(text),
            f"{path.name} contains an embedded Discord snowflake literal",
        )

    def test_bridge_py_no_hardcoded_id(self):
        self._assert_no_snowflake_literal(BRIDGE_PY)

    def test_init_py_no_hardcoded_id(self):
        self._assert_no_snowflake_literal(INIT_PY)

    def test_email_brief_py_no_hardcoded_id(self):
        self._assert_no_snowflake_literal(EMAIL_BRIEF_PY)

    def test_user_profiles_py_no_hardcoded_id(self):
        self._assert_no_snowflake_literal(USER_PROFILES_PY)


class TestOwnerAuthorization(unittest.TestCase):
    """Profile loading must derive effective ownership from current config."""

    def _load_user_profiles(self):
        mod_name = "user_profiles_test_isolation"
        spec = importlib.util.spec_from_file_location(mod_name, USER_PROFILES_PY)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.modules.pop(mod_name, None)
        return mod

    def _seed_historical_owner(self, mod, directory: str, user_id: str) -> Path:
        data = mod._default_profile_yaml(user_id)
        data["is_owner"] = True
        data["enabled_tools"] = list(data["enabled_tools"]) + list(mod.NEVER_AUTO_ENABLED)
        data["disabled_tools"] = []
        path = Path(directory) / f"{user_id}.yaml"
        mod._atomic_write_yaml(path, data)
        return path

    def _assert_owner_tools_denied(self, mod, profile):
        for tool_name in mod.NEVER_AUTO_ENABLED:
            with self.subTest(tool_name=tool_name):
                self.assertFalse(profile.is_tool_allowed(tool_name))

    def test_unset_owner_id_grants_no_owner(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"VOICE_OWNER_DISCORD_ID": "", "VOICE_USERS_DIR": td},
                clear=False,
            ):
                mod = self._load_user_profiles()
                profile = mod.get_or_create_profile(OTHER_USER_ID)
                self.assertFalse(profile.is_owner)
                self._assert_owner_tools_denied(mod, profile)

    def test_non_matching_owner_id_grants_no_owner(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"VOICE_OWNER_DISCORD_ID": OWNER_ID, "VOICE_USERS_DIR": td},
                clear=False,
            ):
                mod = self._load_user_profiles()
                profile = mod.get_or_create_profile(OTHER_USER_ID)
                self.assertFalse(profile.is_owner)
                self._assert_owner_tools_denied(mod, profile)

    def test_matching_explicit_owner_id_grants_owner(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"VOICE_OWNER_DISCORD_ID": OWNER_ID, "VOICE_USERS_DIR": td},
                clear=False,
            ):
                mod = self._load_user_profiles()
                profile = mod.get_or_create_profile(OWNER_ID)
                self.assertTrue(profile.is_owner)
                for tool_name in mod.NEVER_AUTO_ENABLED:
                    with self.subTest(tool_name=tool_name):
                        self.assertTrue(profile.is_tool_allowed(tool_name))

    def test_malformed_owner_id_grants_no_owner(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {
                    "VOICE_OWNER_DISCORD_ID": "not-a-snowflake",
                    "VOICE_USERS_DIR": td,
                },
                clear=False,
            ):
                mod = self._load_user_profiles()
                profile = mod.get_or_create_profile(OTHER_USER_ID)
                self.assertFalse(profile.is_owner)
                self._assert_owner_tools_denied(mod, profile)

    def test_persisted_owner_is_ignored_when_configuration_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"VOICE_OWNER_DISCORD_ID": "", "VOICE_USERS_DIR": td},
                clear=False,
            ):
                mod = self._load_user_profiles()
                path = self._seed_historical_owner(mod, td, OTHER_USER_ID)
                profile = mod.get_or_create_profile(OTHER_USER_ID)

                self.assertFalse(profile.is_owner)
                self._assert_owner_tools_denied(mod, profile)
                stored = mod._read_yaml(path)
                self.assertTrue(stored["is_owner"])
                self.assertTrue(
                    set(mod.NEVER_AUTO_ENABLED).issubset(stored["enabled_tools"])
                )

    def test_persisted_owner_is_ignored_when_configuration_does_not_match(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"VOICE_OWNER_DISCORD_ID": OWNER_ID, "VOICE_USERS_DIR": td},
                clear=False,
            ):
                mod = self._load_user_profiles()
                self._seed_historical_owner(mod, td, OTHER_USER_ID)
                profile = mod.get_or_create_profile(OTHER_USER_ID)

                self.assertFalse(profile.is_owner)
                self._assert_owner_tools_denied(mod, profile)

    def test_persisted_owner_is_ignored_when_configuration_is_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {
                    "VOICE_OWNER_DISCORD_ID": "malformed-owner",
                    "VOICE_USERS_DIR": td,
                },
                clear=False,
            ):
                mod = self._load_user_profiles()
                self._seed_historical_owner(mod, td, OTHER_USER_ID)
                profile = mod.get_or_create_profile(OTHER_USER_ID)

                self.assertFalse(profile.is_owner)
                self._assert_owner_tools_denied(mod, profile)

    def test_persisted_owner_is_effective_only_for_matching_configuration(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"VOICE_OWNER_DISCORD_ID": OWNER_ID, "VOICE_USERS_DIR": td},
                clear=False,
            ):
                mod = self._load_user_profiles()
                self._seed_historical_owner(mod, td, OWNER_ID)
                profile = mod.get_or_create_profile(OWNER_ID)

                self.assertTrue(profile.is_owner)
                for tool_name in mod.NEVER_AUTO_ENABLED:
                    with self.subTest(tool_name=tool_name):
                        self.assertTrue(profile.is_tool_allowed(tool_name))

    def test_profile_listing_uses_effective_owner_state(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"VOICE_OWNER_DISCORD_ID": "", "VOICE_USERS_DIR": td},
                clear=False,
            ):
                mod = self._load_user_profiles()
                self._seed_historical_owner(mod, td, OTHER_USER_ID)
                rows = mod.list_profiles()

                self.assertEqual(len(rows), 1)
                self.assertFalse(rows[0]["is_owner"])
                self.assertTrue(
                    set(mod.NEVER_AUTO_ENABLED).isdisjoint(rows[0]["enabled_tools"])
                )


if __name__ == "__main__":
    unittest.main()
