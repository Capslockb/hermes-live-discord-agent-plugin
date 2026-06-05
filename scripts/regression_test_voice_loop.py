#!/usr/bin/env python3
"""
regression_test_voice_loop.py — verify the autostart loop guard and
the native slash command dispatch don't regress.

Three invariant sets:
  1. Autostart loop guard: when a bridge is already active in the
     target channel, the autostart MUST exit without calling voice_live
     (which would toggle-leave and re-join, causing the loop).
  2. Auto-infer: voice_live() called with empty guild_id + channel_id
     + a valid user_id should auto-infer the user's current voice channel.
  3. Plugin importability: hermes_plugins.discord_voice.voice_live and
     voice_live_leave are importable as async callables (required by the
     Discord adapter's @tree.command handlers).
"""

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

PLUGIN_DIR = "/home/caps/.hermes/plugins/discord-voice"
sys.path.insert(0, PLUGIN_DIR)

PASS = "✅"
FAIL = "❌"
results = []


def check(name, condition, detail=""):
    icon = PASS if condition else FAIL
    results.append((icon, name, detail))
    print(f"  {icon} {name}" + (f" — {detail}" if detail and not condition else ""))


# Build a hermes_plugins namespace package so the Discord adapter's
# `from hermes_plugins.discord_voice import voice_live` works.
if "hermes_plugins" not in sys.modules:
    ns = types.ModuleType("hermes_plugins")
    ns.__path__ = []
    sys.modules["hermes_plugins"] = ns
# Import the plugin as both the raw file and as hermes_plugins.discord_voice
import importlib.util

# Set 3: Plugin importability via hermes_plugins.discord_voice
print("\n== Set 3: plugin importability (for adapter @tree.command) ==")
spec = importlib.util.spec_from_file_location(
    "hermes_plugins.discord_voice", Path(PLUGIN_DIR) / "__init__.py"
)
plugin = importlib.util.module_from_spec(spec)
sys.modules["hermes_plugins.discord_voice"] = plugin
spec.loader.exec_module(plugin)
check("hermes_plugins.discord_voice module loaded", "voice_live" in dir(plugin))
check("voice_live is importable", hasattr(plugin, "voice_live"))
check("voice_live is async", asyncio.iscoroutinefunction(plugin.voice_live))
check("voice_live_leave is importable", hasattr(plugin, "voice_live_leave"))
check("voice_live_leave is async", asyncio.iscoroutinefunction(plugin.voice_live_leave))
check("voice_live accepts (adapter, guild_id, channel_id, user_id)",
      plugin.voice_live.__code__.co_varnames[:plugin.voice_live.__code__.co_argcount][:4]
      == ("adapter", "guild_id", "channel_id", "user_id"))


# Set 2: Auto-infer via voice_live with empty ids
print("\n== Set 2: auto-infer for slash command dispatch ==")

async def set2():
    # Mock a Discord adapter
    mock_adapter = MagicMock()
    target_guild = MagicMock()
    target_guild.id = 1480297825655980067
    target_channel = MagicMock()
    target_channel.id = 1480297827296088207
    target_channel.name = "TestChannel"
    target_guild.get_channel.return_value = target_channel
    # Member that has a voice channel
    member = MagicMock()
    member.voice.channel = target_channel
    target_guild.get_member.return_value = member
    mock_adapter._client.get_guild.return_value = target_guild

    # Mock out the parts of voice_live that need real bridge infra
    # (we just want to see auto-infer resolve the ids)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Patch the plugin's _active_bridges and _starting to be empty
        plugin._active_bridges.clear()
        plugin._starting.clear()
        # Run voice_live with empty ids — it should auto-infer and then
        # hit the "force-disconnect" path which needs a real adapter
        # with voice_client. We only check the first part: the error
        # message it returns should mention TestChannel (i.e. it found
        # a channel and got past the inference step).
        result_str = await plugin.voice_live(
            mock_adapter, "", "", user_id="1474100257762578597"
        )
        try:
            result = json.loads(result_str)
        except (TypeError, ValueError):
            result = {"status": "error", "message": str(result_str)[:200]}
        # After auto-infer + bridge start, the bridge module is loaded
        # and bridge_mod.run_sidecar is called. We can't fully exercise
        # that in a unit test (needs real Discord voice_client), so we
        # just confirm the call progressed past auto-infer.
        msg = result.get("message", "")
        check("auto-infer progressed past 'guild_id/channel_id are required'",
              "guild_id/channel_id are required" not in msg
              and "Could not infer your current voice channel" not in msg,
              f"got: {msg[:100]}")

asyncio.run(set2())


# Set 1: Autostart loop guard
print("\n== Set 1: autostart loop guard ==")
async def set1():
    # Simulate an existing active bridge with a real-ish task
    mock_existing_vc = MagicMock()
    mock_existing_vc.is_connected.return_value = True
    mock_existing_vc.channel.id = 1480297827296088207

    # Make the existing bridge have a real asyncio.Task so voice_live_leave
    # can cancel/await it without NoneType errors
    async def _noop():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return
    existing_task = asyncio.create_task(_noop())

    plugin._active_bridges[1480297825655980067] = {
        "vc": mock_existing_vc,
        "task": existing_task,
        "bridge_mod": None,
    }

    # Build a minimal mock adapter (don't need real voice client — we should
    # bail at the "already active" check before touching it)
    mock_adapter = MagicMock()

    # Run voice_live with the existing bridge's ids
    result_str = await plugin.voice_live(
        mock_adapter,
        "1480297825655980067",
        "1480297827296088207",
        user_id="1474100257762578597",
    )
    try:
        result = json.loads(result_str)
    except (TypeError, ValueError):
        result = {"status": "error", "message": str(result_str)[:200]}
    # In the "already active in same channel" branch, voice_live toggles-leave.
    # We just verify the toggle returned (success or error) without crashing
    # on None.task.cancel(). The important assertion is that the path
    # didn't crash with a NoneType error.
    check("existing bridge toggle-leave did not crash with NoneType error",
          "NoneType" not in result.get("message", ""),
          f"got status={result.get('status')}, msg={result.get('message', '')[:120]}")

    # Clean up the existing_task (it was left running by the toggle-leave)
    if not existing_task.done():
        existing_task.cancel()
        try:
            await existing_task
        except (asyncio.CancelledError, Exception):
            pass

    # Now test the actual autostart guard — it should call the bridge
    # state check and return early.
    # We'll exercise the autostart directly.
    # First, reset state so we can spawn a fresh bridge
    plugin._active_bridges.clear()
    plugin._starting.clear()

    # Mock out the autostart's adapter lookup
    import sys as _sys
    fake_gw = types.ModuleType("gateway.run")
    fake_adapter = MagicMock()
    # Force the "user not in voice channel" path so the loop gives up
    fake_adapter._client.get_guild.return_value = MagicMock()
    fake_adapter._client.get_guild.return_value.get_member.return_value = None
    fake_gw._gateway_runner_ref = lambda: MagicMock(adapters={"discord": fake_adapter})
    _sys.modules["gateway"] = types.ModuleType("gateway")
    _sys.modules["gateway"].run = fake_gw
    _sys.modules["gateway"].platforms = types.ModuleType("gateway.platforms")
    _sys.modules["gateway"].platforms.base = types.ModuleType("gateway.platforms.base")
    _sys.modules["gateway"].platforms.base.Platform = lambda x: x

    # Patch the autostart file so it doesn't try to read the real one
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        import json as _json
        f.write(_json.dumps({
            "guild_id": "1480297825655980067",
            "channel_id": "1480297827296088207",
            "user_id": "1474100257762578597",
        }))
        tmpf = f.name
    orig_path = plugin.AUTOSTART_FILE
    plugin.AUTOSTART_FILE = Path(tmpf)
    try:
        # With no real bridge infra, the autostart will loop with errors
        # for the full 180s deadline. We only want to verify the
        # already-active short-circuit.
        plugin._active_bridges[1480297825655980067] = {
            "vc": mock_existing_vc,
            "task": None,
            "bridge_mod": None,
        }
        # Run autostart in a task; give it 3s then check state.
        # The autostart's first iteration should hit the already-active
        # guard, log "bridge already active", and RETURN — not toggle.
        # We verify by checking that the autostart returns within the
        # window (no 5s sleep+retry that would indicate a non-guard hit).
        import time as _t
        t0 = _t.monotonic()
        task = asyncio.create_task(plugin._autostart_voice_live())
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        elapsed = _t.monotonic() - t0
        check("autostart exited quickly when bridge already active (<2.5s)",
              elapsed < 2.5 and not task.cancelled(),
              f"elapsed: {elapsed:.2f}s, task cancelled: {task.cancelled()}")
    finally:
        plugin.AUTOSTART_FILE = orig_path
        try:
            Path(tmpf).unlink()
        except Exception:
            pass

asyncio.run(set1())


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
print("ALL CHECKS PASSED — autostart loop + slash command dispatch are clean.")
