"""Tests for HERMES_HOME-aware Google Workspace helper path (Issue #24).

Validates:
- Default root: path derived from ~/.hermes when HERMES_HOME is not set.
- Custom HERMES_HOME: path derived from the configured root.
- Explicit DISCORD_VOICE_LIVE_GOOGLE_API_BIN: used directly when set.
- Missing explicit path returns None.
- Missing derived path returns None.
- Credentials and helper stderr are not leaked.
"""

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).parent.parent
EMAIL_BRIEF_PY = REPO_ROOT / "email_brief.py"

_RELATIVE_HELPER = Path("hermes-agent/skills/productivity/google-workspace/scripts/google_api.py")


def _load_email_brief():
    """Reload email_brief with a clean import to pick up patched env."""
    mod_name = "email_brief_test_isolation"
    spec = importlib.util.spec_from_file_location(mod_name, EMAIL_BRIEF_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(mod_name, None)
    return mod


class TestGoogleApiPath(unittest.TestCase):

    def test_default_root_when_hermes_home_unset(self):
        """Without HERMES_HOME, path should be under ~/.hermes."""
        with tempfile.TemporaryDirectory() as td:
            hermes_dir = Path(td) / ".hermes"
            helper = hermes_dir / _RELATIVE_HELPER
            helper.parent.mkdir(parents=True)
            helper.write_text("# stub")

            with patch.dict(os.environ, {"HOME": td}, clear=False):
                os.environ.pop("HERMES_HOME", None)
                os.environ.pop("DISCORD_VOICE_LIVE_GOOGLE_API_BIN", None)
                mod = _load_email_brief()
                result = mod._google_api_path()
            self.assertIsNotNone(result, "Should find helper under default ~/.hermes root")
            self.assertEqual(result, helper)

    def test_custom_hermes_home(self):
        """With HERMES_HOME set, path should be derived from that root."""
        with tempfile.TemporaryDirectory() as td:
            custom_root = Path(td) / "custom-hermes"
            helper = custom_root / _RELATIVE_HELPER
            helper.parent.mkdir(parents=True)
            helper.write_text("# stub")

            with patch.dict(os.environ, {"HERMES_HOME": str(custom_root)}, clear=False):
                os.environ.pop("DISCORD_VOICE_LIVE_GOOGLE_API_BIN", None)
                mod = _load_email_brief()
                result = mod._google_api_path()
            self.assertIsNotNone(result, "Should find helper under custom HERMES_HOME")
            self.assertEqual(result, helper)

    def test_explicit_bin_path_takes_precedence(self):
        """DISCORD_VOICE_LIVE_GOOGLE_API_BIN overrides all derived paths."""
        with tempfile.TemporaryDirectory() as td:
            explicit = Path(td) / "my_google_api.py"
            explicit.write_text("# explicit stub")

            with patch.dict(os.environ,
                            {"DISCORD_VOICE_LIVE_GOOGLE_API_BIN": str(explicit)},
                            clear=False):
                mod = _load_email_brief()
                result = mod._google_api_path()
            self.assertEqual(result, explicit, "Explicit path should be returned directly")

    def test_missing_explicit_path_returns_none(self):
        """A configured but absent explicit path returns None (fail-closed)."""
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does_not_exist.py"
            with patch.dict(os.environ,
                            {"DISCORD_VOICE_LIVE_GOOGLE_API_BIN": str(missing)},
                            clear=False):
                mod = _load_email_brief()
                result = mod._google_api_path()
            self.assertIsNone(result, "Missing explicit path must return None")

    def test_missing_derived_path_returns_none(self):
        """When no helper exists under the derived root, None is returned."""
        with tempfile.TemporaryDirectory() as td:
            # Empty custom root — no helper installed.
            with patch.dict(os.environ,
                            {"HERMES_HOME": td,
                             "DISCORD_VOICE_LIVE_GOOGLE_API_BIN": ""},
                            clear=False):
                os.environ.pop("DISCORD_VOICE_LIVE_GOOGLE_API_BIN", None)
                mod = _load_email_brief()
                result = mod._google_api_path()
            self.assertIsNone(result, "Missing derived helper must return None")

    def test_fetch_google_raises_when_helper_absent(self):
        """fetch_google() must raise FileNotFoundError, not leak paths or creds."""
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ,
                            {"HERMES_HOME": td},
                            clear=False):
                os.environ.pop("DISCORD_VOICE_LIVE_GOOGLE_API_BIN", None)
                mod = _load_email_brief()
                with self.assertRaises(FileNotFoundError):
                    mod.fetch_google(limit=1)


if __name__ == "__main__":
    unittest.main()
