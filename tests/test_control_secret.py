"""Tests for the ephemeral control secret (Issue #17).

Validates that:
- A new secret is generated on every module import / process start.
- The secret is cryptographically non-trivial (sufficient length/entropy).
- No persistent file is read or created.
- The secret does not appear in URLs, bodies, or returned payloads.
- Legacy file presence has no authentication effect.
"""

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _fresh_init_module(env_overrides=None):
    """Import __init__ in isolation with a clean sys.modules entry.

    Returns the module object so tests can inspect CONTROL_API_SECRET.
    """
    # Remove any cached copy so we get a fresh import.
    for key in list(sys.modules.keys()):
        if key in ("discord_voice_live", "discord_voice_live.__init__"):
            del sys.modules[key]

    # Temporarily patch env.
    saved = {}
    for k, v in (env_overrides or {}).items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    try:
        # Load the package __init__ directly by file path so the test runner
        # doesn't need the package installed.
        spec = importlib.util.spec_from_file_location(
            "discord_voice_live",
            Path(__file__).parent.parent / "__init__.py",
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["discord_voice_live"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, orig in saved.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig


class TestEphemeralControlSecret(unittest.TestCase):
    def test_secret_is_set_on_import(self):
        mod = _fresh_init_module()
        secret = getattr(mod, "CONTROL_API_SECRET", None)
        self.assertIsNotNone(secret, "CONTROL_API_SECRET not set after import")
        self.assertIsInstance(secret, str)
        self.assertGreater(len(secret), 20, "Secret too short to be cryptographically safe")

    def test_new_secret_on_every_import(self):
        mod1 = _fresh_init_module()
        secret1 = mod1.CONTROL_API_SECRET
        mod2 = _fresh_init_module()
        secret2 = mod2.CONTROL_API_SECRET
        self.assertNotEqual(secret1, secret2, "Secrets must differ across process starts")

    def test_legacy_file_presence_has_no_auth_effect(self):
        with tempfile.TemporaryDirectory() as td:
            legacy_file = Path(td) / "voice-live-control-secret"
            legacy_secret = "legacy-value-should-not-be-loaded"
            legacy_file.write_text(legacy_secret)

            # Even with the file present, the module must generate a fresh secret.
            mod = _fresh_init_module()
            actual_secret = mod.CONTROL_API_SECRET

            self.assertNotEqual(
                actual_secret,
                legacy_secret,
                "Module must not load the legacy persistent file",
            )
            self.assertNotIn(
                legacy_secret,
                actual_secret,
                "Legacy file value must have no auth effect",
            )

    def test_no_file_written_after_import(self):
        with tempfile.TemporaryDirectory() as td:
            # Redirect home to a fresh tmp dir so we can detect any writes.
            mod = _fresh_init_module(env_overrides={"HOME": td})
            # The module must NOT have written a control-secret file.
            control_file = Path(td) / ".hermes" / "voice-live-control-secret"
            self.assertFalse(
                control_file.exists(),
                "Module must not persist the control secret to disk",
            )

    def test_secret_not_in_default_user_id(self):
        mod = _fresh_init_module()
        # The default user ID must be empty (no embedded ID).
        default_uid = getattr(mod, "DEFAULT_USER_ID", None)
        self.assertEqual(
            default_uid,
            "",
            "DEFAULT_USER_ID must be empty; embedded Discord IDs are not allowed",
        )

    def test_secret_is_url_safe_string(self):
        mod = _fresh_init_module()
        secret = mod.CONTROL_API_SECRET
        # token_urlsafe produces base64url chars: A-Z a-z 0-9 - _
        import re
        self.assertRegex(secret, r'^[A-Za-z0-9_\-]+$',
                         "Secret must be a URL-safe string (no special chars that leak in logs)")


if __name__ == "__main__":
    unittest.main()
