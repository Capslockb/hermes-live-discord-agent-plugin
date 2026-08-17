"""Tests for video state watcher camera and screenshare awareness (Issue #10).

Validates that:
- Camera enable and disable transitions in _video_state_watcher are properly awaited.
- Screenshare start and stop transitions in _video_state_watcher remain functional and awaited.
- bridge_info is forwarded to _send_video_awareness on all video and screenshare state transitions.
- Coroutine calls in _video_state_watcher do not emit un-awaited coroutine warnings.
"""

import ast
import asyncio
import os
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
INIT_PY = REPO_ROOT / "__init__.py"


class TestVideoWatcherSourceInvariants(unittest.TestCase):
    """AST validation to ensure all video awareness calls in _video_state_watcher are awaited with bridge_info."""

    def test_all_video_awareness_calls_in_watcher_are_awaited_with_bridge_info(self):
        tree = ast.parse(INIT_PY.read_text(encoding="utf-8"), filename="__init__.py")
        watcher_func = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_video_state_watcher":
                watcher_func = node
                break

        self.assertIsNotNone(watcher_func, "_video_state_watcher not found in __init__.py")

        awareness_calls = []
        for node in ast.walk(watcher_func):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "_send_video_awareness":
                    awareness_calls.append(node)

        self.assertTrue(awareness_calls, "No _send_video_awareness calls found")

        awaited_call_ids = {
            id(node.value)
            for node in ast.walk(watcher_func)
            if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
        }

        for call in awareness_calls:
            self.assertIn(
                id(call),
                awaited_call_ids,
                f"Call at line {call.lineno} is not directly awaited",
            )
            kw_names = [kw.arg for kw in call.keywords]
            self.assertIn("bridge_info", kw_names, f"Call at line {call.lineno} is missing bridge_info argument")


class TestVideoWatcherAsyncBehavior(unittest.IsolatedAsyncioTestCase):
    """Async execution tests verifying camera and screenshare state awareness invocations."""

    async def _wait_for_calls(self, calls_list, expected_count, timeout=0.5):
        """Wait until calls_list reaches expected_count without fixed sleep delays."""
        start_time = asyncio.get_event_loop().time()
        while len(calls_list) < expected_count:
            if asyncio.get_event_loop().time() - start_time > timeout:
                self.fail(f"Timed out waiting for {expected_count} calls (currently {len(calls_list)})")
            await asyncio.sleep(0.005)

    async def test_camera_and_screenshare_state_transitions_and_no_unawaited_warnings(self):
        import importlib
        import sys

        with warnings.catch_warnings(record=True) as recorded_warnings:
            warnings.simplefilter("always")

            with patch.dict(os.environ, {"DISCORD_VOICE_LIVE_VIDEO_STATE_POLL_INTERVAL": "0.01"}):
                spec = importlib.util.spec_from_file_location("discord_voice_live_test", INIT_PY)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["discord_voice_live_test"] = mod
                spec.loader.exec_module(mod)

                mock_member = MagicMock()
                mock_member.id = 12345
                mock_member.display_name = "Alice"
                mock_member.bot = False
                mock_member.voice.self_stream = False
                mock_member.voice.self_video = False

                mock_vc = MagicMock()
                mock_vc.is_connected.return_value = True
                mock_vc.channel.members = [mock_member]

                mock_bridge_mod = MagicMock()
                bridge_info = {
                    "vc": mock_vc,
                    "bridge_mod": mock_bridge_mod,
                    "user_id": "test-user-123",
                }

                guild_id = 99999
                mod._active_bridges[guild_id] = bridge_info

                calls = []

                async def fake_send_awareness(bridge_mod, text, event_type="video_state", bridge_info=None):
                    calls.append({"text": text, "event_type": event_type, "bridge_info": bridge_info})

                mod._send_video_awareness = fake_send_awareness

                task = asyncio.create_task(mod._video_state_watcher(guild_id))

                # Allow initial baseline registration poll
                await asyncio.sleep(0.02)
                self.assertEqual(len(calls), 0)

                # 1. Turn camera ON
                mock_member.voice.self_video = True
                await self._wait_for_calls(calls, 1)
                self.assertEqual(calls[0]["event_type"], "video_state")
                self.assertIn("turned on their camera", calls[0]["text"])
                self.assertIs(calls[0]["bridge_info"], bridge_info)

                # 2. Turn camera OFF
                mock_member.voice.self_video = False
                await self._wait_for_calls(calls, 2)
                self.assertEqual(calls[1]["event_type"], "video_ended")
                self.assertIn("turned off their camera", calls[1]["text"])
                self.assertIs(calls[1]["bridge_info"], bridge_info)

                # 3. Start screenshare (regression test for screenshare preservation)
                mock_member.voice.self_stream = True
                await self._wait_for_calls(calls, 3)
                self.assertEqual(calls[2]["event_type"], "video_state")
                self.assertIn("started screen sharing", calls[2]["text"])
                self.assertIs(calls[2]["bridge_info"], bridge_info)

                # 4. Stop screenshare
                mock_member.voice.self_stream = False
                await self._wait_for_calls(calls, 4)
                self.assertEqual(calls[3]["event_type"], "video_ended")
                self.assertIn("stopped screen sharing", calls[3]["text"])
                self.assertIs(calls[3]["bridge_info"], bridge_info)

                # Stop watcher cleanly
                mod._active_bridges.pop(guild_id, None)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Assert no unawaited coroutine warnings occurred
            unawaited_warnings = [
                w for w in recorded_warnings
                if issubclass(w.category, RuntimeWarning) and "coroutine" in str(w.message).lower()
            ]
            self.assertEqual(
                len(unawaited_warnings),
                0,
                f"Found unawaited coroutine warnings: {[str(w.message) for w in unawaited_warnings]}",
            )
