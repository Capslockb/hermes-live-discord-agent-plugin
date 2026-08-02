"""Tests for scripts/video-frame-feeder.py (Issue #9 fix verification).

Covers:
- CLI argument parsing (--help exits 0, no -h/-H conflict, --once present)
- _resolve_api_secret() resolution order (env var, file, default path, empty)
- post_frame() injects X-API-Secret header when secret is non-empty
- post_frame() omits X-API-Secret when no secret is configured
- _control_post_frame() in __init__ injects X-API-Secret header
"""

import asyncio
import importlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

# Resolve absolute path to the feeder script.
REPO_ROOT = Path(__file__).parent.parent
FEEDER_PY = REPO_ROOT / "scripts" / "video-frame-feeder.py"
_ENV_VAR = "DISCORD_VOICE_LIVE_API_SECRET"
_ENV_FILE_VAR = "DISCORD_VOICE_LIVE_SECRET_FILE"


def _load_feeder_module():
    """Load the feeder script as a module (without executing main)."""
    spec = importlib.util.spec_from_file_location("video_frame_feeder", FEEDER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_init_module(env_overrides=None):
    """Import discord_voice_live __init__ in isolation.

    Clears and restores sys.modules and env vars around each import so tests
    are independent.
    """
    for key in list(sys.modules.keys()):
        if key in ("discord_voice_live", "discord_voice_live.__init__"):
            del sys.modules[key]

    saved = {}
    for k, v in (env_overrides or {}).items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    try:
        spec = importlib.util.spec_from_file_location(
            "discord_voice_live",
            REPO_ROOT / "__init__.py",
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


class TestFeederArgParsing(unittest.TestCase):
    """CLI argument parsing must not crash and --help must work."""

    def test_help_exits_zero(self):
        """--help must exit 0 (argparse -h alias conflict caused SystemExit(2))."""
        result = subprocess.run(
            [sys.executable, str(FEEDER_PY), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"--help returned non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_help_mentions_once_flag(self):
        """--once flag must be documented in help output."""
        result = subprocess.run(
            [sys.executable, str(FEEDER_PY), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertIn(
            "--once", result.stdout,
            "--once must appear in --help output",
        )

    def test_help_no_conflict_error_in_stderr(self):
        """stderr must be empty (no argparse conflict traceback)."""
        result = subprocess.run(
            [sys.executable, str(FEEDER_PY), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.stderr, "",
            f"Unexpected stderr from --help: {result.stderr}",
        )

    def test_no_minus_h_alias_for_height(self):
        """--height must NOT accept -h (that alias was the root bug of Issue #9)."""
        # Passing -h 480 must NOT be treated as --height 480; argparse must
        # interpret -h as the built-in help flag (exit 0) or a recognised arg.
        result = subprocess.run(
            [sys.executable, str(FEEDER_PY), "--help"],
            capture_output=True,
            text=True,
        )
        # If -h was still aliased to --height, the option listing would show
        # "-h HEIGHT" somewhere in the usage/options section. It must not.
        self.assertNotIn(
            "-h HEIGHT",
            result.stdout,
            "-h must NOT be listed as an alias for --height in help output",
        )


class TestFeederSecretResolution(unittest.TestCase):
    """_resolve_api_secret() must follow documented precedence order."""

    def setUp(self):
        self._mod = _load_feeder_module()
        # Save and clear both secret env vars to isolate tests.
        self._saved_env = os.environ.get(_ENV_VAR)
        self._saved_file_env = os.environ.get(_ENV_FILE_VAR)
        os.environ.pop(_ENV_VAR, None)
        os.environ.pop(_ENV_FILE_VAR, None)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop(_ENV_VAR, None)
        else:
            os.environ[_ENV_VAR] = self._saved_env
        if self._saved_file_env is None:
            os.environ.pop(_ENV_FILE_VAR, None)
        else:
            os.environ[_ENV_FILE_VAR] = self._saved_file_env

    def test_env_var_takes_precedence(self):
        """Env var must win over file-based secrets."""
        # Deliberately a fixture placeholder — not a real credential.
        os.environ[_ENV_VAR] = "env-var-wins-test-fixture"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".secret", delete=False) as f:
            f.write("file-secret-fixture")
            secret_file = f.name
        try:
            os.environ[_ENV_FILE_VAR] = secret_file
            result = self._mod._resolve_api_secret()
            self.assertEqual(result, "env-var-wins-test-fixture")
        finally:
            os.unlink(secret_file)
            os.environ.pop(_ENV_VAR, None)
            os.environ.pop(_ENV_FILE_VAR, None)

    def test_file_fallback_when_env_not_set(self):
        """File at DISCORD_VOICE_LIVE_SECRET_FILE must be read when env var absent."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".secret", delete=False) as f:
            # Deliberately a fixture placeholder — not a real credential.
            f.write("file-only-secret-fixture\n")
            secret_file = f.name
        try:
            os.environ[_ENV_FILE_VAR] = secret_file
            result = self._mod._resolve_api_secret()
            self.assertEqual(result, "file-only-secret-fixture")
        finally:
            os.unlink(secret_file)
            os.environ.pop(_ENV_FILE_VAR, None)

    def test_empty_string_when_nothing_configured(self):
        """Returns empty string when no env var and no default file exists."""
        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td)
            # Point default file path to a non-existent location.
            os.environ[_ENV_FILE_VAR] = str(fake_home / ".hermes" / "voice-live-control-secret")
            result = self._mod._resolve_api_secret()
            self.assertEqual(result, "")
            os.environ.pop(_ENV_FILE_VAR, None)

    def test_explicit_missing_file_returns_empty(self):
        """Non-existent path in DISCORD_VOICE_LIVE_SECRET_FILE returns empty (no exception)."""
        os.environ[_ENV_FILE_VAR] = "/tmp/does-not-exist-feeder-secret-test"
        result = self._mod._resolve_api_secret()
        self.assertEqual(result, "")
        os.environ.pop(_ENV_FILE_VAR, None)

    def test_env_var_stripped_of_whitespace(self):
        """Leading/trailing whitespace on the env var value must be stripped."""
        os.environ[_ENV_VAR] = "  whitespace-fixture  "
        result = self._mod._resolve_api_secret()
        self.assertEqual(result, "whitespace-fixture")
        os.environ.pop(_ENV_VAR, None)


class TestFeederPostFrameAuth(unittest.TestCase):
    """post_frame() must include X-API-Secret when a secret is set, and omit it when not."""

    def setUp(self):
        self._mod = _load_feeder_module()

    def test_header_included_when_secret_set(self):
        """X-API-Secret header must be sent when api_secret is non-empty."""
        captured_kwargs = {}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"accepted": True}

        def fake_post(url, data=None, headers=None, timeout=None):
            captured_kwargs["headers"] = dict(headers or {})
            return FakeResponse()

        fake_requests = types.ModuleType("requests")
        fake_requests.post = fake_post
        fake_requests.RequestException = Exception

        with patch.dict(sys.modules, {"requests": fake_requests}):
            # Deliberately a fixture placeholder — not a real credential.
            result = self._mod.post_frame(
                "http://127.0.0.1:9999/frame",
                b"\xff\xd8\xff\xe0",
                api_secret="test-header-fixture",
            )

        self.assertIn("X-API-Secret", captured_kwargs["headers"])
        self.assertEqual(captured_kwargs["headers"]["X-API-Secret"], "test-header-fixture")
        self.assertTrue(result.get("accepted"))

    def test_header_absent_when_no_secret(self):
        """X-API-Secret must NOT appear in headers when api_secret is empty."""
        captured_kwargs = {}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"accepted": True}

        def fake_post(url, data=None, headers=None, timeout=None):
            captured_kwargs["headers"] = dict(headers or {})
            return FakeResponse()

        fake_requests = types.ModuleType("requests")
        fake_requests.post = fake_post
        fake_requests.RequestException = Exception

        with patch.dict(sys.modules, {"requests": fake_requests}):
            self._mod.post_frame(
                "http://127.0.0.1:9999/frame",
                b"\xff\xd8\xff\xe0",
                api_secret="",
            )

        self.assertNotIn(
            "X-API-Secret", captured_kwargs.get("headers", {}),
            "X-API-Secret must not be sent when api_secret is empty",
        )

    def test_secret_not_in_url(self):
        """The API secret must NEVER appear in the request URL."""
        captured_url = {}

        class FakeResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"accepted": True}

        def fake_post(url, data=None, headers=None, timeout=None):
            captured_url["url"] = url
            return FakeResponse()

        fake_requests = types.ModuleType("requests")
        fake_requests.post = fake_post
        fake_requests.RequestException = Exception

        secret = "url-leakage-test-fixture"
        with patch.dict(sys.modules, {"requests": fake_requests}):
            self._mod.post_frame(
                "http://127.0.0.1:9999/frame",
                b"\xff\xd8\xff\xe0",
                api_secret=secret,
            )

        self.assertNotIn(
            secret, captured_url.get("url", ""),
            "Secret must never appear in the request URL",
        )


class TestInProcessFrameAuth(unittest.TestCase):
    """_control_post_frame() in __init__.py must inject X-API-Secret."""

    def test_control_post_frame_sends_secret_header(self):
        """The raw HTTP request built by _control_post_frame must contain X-API-Secret."""
        mod = _fresh_init_module(env_overrides={_ENV_VAR: None})
        # Deliberately a fixture placeholder — not a real credential.
        expected_secret = "in-process-test-fixture"
        mod.CONTROL_API_SECRET = expected_secret

        written_data = []

        class FakeWriter:
            def write(self, data):
                written_data.append(data)
            async def drain(self): pass
            def close(self): pass
            async def wait_closed(self): pass

        class FakeReader:
            async def read(self):
                return b"HTTP/1.1 200 OK\r\n\r\n{\"accepted\":true}"

        fake_reader = FakeReader()
        fake_writer = FakeWriter()

        async def fake_open_connection(host, port):
            return fake_reader, fake_writer

        with patch("asyncio.open_connection", side_effect=fake_open_connection):
            asyncio.run(mod._control_post_frame(b"\xff\xd8\xff\xe0", "image/jpeg"))

        # Join all written bytes and inspect the raw HTTP request headers.
        raw = b"".join(written_data).decode("utf-8", errors="replace")
        self.assertIn(
            f"X-API-Secret: {expected_secret}",
            raw,
            f"X-API-Secret header missing from raw request.\nGot:\n{raw}",
        )

    def test_control_post_frame_secret_not_in_body(self):
        """Secret must appear only in HTTP headers, never in the POST body."""
        mod = _fresh_init_module(env_overrides={_ENV_VAR: None})
        # Deliberately a fixture placeholder — not a real credential.
        expected_secret = "body-test-fixture"
        mod.CONTROL_API_SECRET = expected_secret

        written_data = []

        class FakeWriter:
            def write(self, data):
                written_data.append(data)
            async def drain(self): pass
            def close(self): pass
            async def wait_closed(self): pass

        class FakeReader:
            async def read(self):
                return b"HTTP/1.1 200 OK\r\n\r\n{\"accepted\":true}"

        frame_bytes = b"\xff\xd8\xff\xe0FAKE_JPEG_BODY"

        async def fake_open_connection(host, port):
            return FakeReader(), FakeWriter()

        with patch("asyncio.open_connection", side_effect=fake_open_connection):
            asyncio.run(mod._control_post_frame(frame_bytes, "image/jpeg"))

        # The written_data list: index 0 is headers+body concatenated.
        raw = b"".join(written_data)
        header_part, _, body_part = raw.partition(b"\r\n\r\n")
        header_text = header_part.decode("utf-8", errors="replace")
        body_text = body_part.decode("utf-8", errors="replace")

        self.assertIn(
            f"X-API-Secret: {expected_secret}", header_text,
            "X-API-Secret must appear in headers",
        )
        self.assertNotIn(
            expected_secret, body_text,
            "Secret must NOT appear in the request body",
        )


if __name__ == "__main__":
    unittest.main()
