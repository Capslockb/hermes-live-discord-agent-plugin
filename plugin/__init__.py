"""
Discord Voice Live Plugin — Hermes Plugin Registration
"""

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("discord-voice-plugin")

PLUGIN_DIR = Path(__file__).parent
CONTROL_PORT = int(os.getenv("DISCORD_VOICE_LIVE_PORT", "18943"))
DEFAULT_USER_ID = os.getenv("DISCORD_VOICE_LIVE_USER_ID", "1474100257762578597")
DEFAULT_GUILD_ID = os.getenv("DISCORD_VOICE_LIVE_GUILD_ID", "")
DEFAULT_CHANNEL_ID = os.getenv("DISCORD_VOICE_LIVE_CHANNEL_ID", "")
KEEP_AUTOSTART_FILE = os.getenv(
    "DISCORD_VOICE_LIVE_KEEP_AUTOSTART_FILE",
    "true",
).lower() in {"1", "true", "yes", "on"}
VIDEO_STATE_DETECTION_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_VIDEO_STATE_DETECTION", "true"
).lower() in {"1", "true", "yes", "on"}
VIDEO_STATE_POLL_INTERVAL_SECONDS = float(
    os.getenv("DISCORD_VOICE_LIVE_VIDEO_STATE_POLL_INTERVAL_SECONDS", "3")
)
AUTOSTART_FILE = Path(os.getenv(
    "DISCORD_VOICE_LIVE_AUTOSTART_FILE",
    str(Path.home() / ".hermes" / "voice-live-autostart.json"),
))

_active_bridges: Dict[int, Dict[str, Any]] = {}
_starting: Dict[int, bool] = {}


async def _disconnect_any_existing_vc(adapter, guild_id_int: int) -> None:
    """Force-disconnect any active voice client in the guild, regardless of plugin."""
    guild = adapter._client.get_guild(guild_id_int) if hasattr(adapter, "_client") else None
    if not guild:
        return
    existing_vc = getattr(guild, "voice_client", None)
    if existing_vc and existing_vc.is_connected():
        try:
            logger.info("VoiceLive: force-disconnecting existing guild voice client before starting")
            await asyncio.wait_for(existing_vc.disconnect(force=True), timeout=10.0)
        except Exception as e:
            logger.warning("VoiceLive: existing voice disconnect failed: %s", e)
        # Give Discord a moment to propagate the disconnect
        await asyncio.sleep(0.5)


def _coerce_tool_args(args: Optional[Dict[str, Any]], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(args, dict):
        merged.update(args)
        for key in ("arguments", "args", "input"):
            nested = args.get(key)
            if isinstance(nested, dict):
                merged.update(nested)
    merged.update(kwargs)
    return merged


def register(ctx):
    ctx.register_tool(
        name="voice_live",
        toolset="hermes",
        schema={
            "name": "voice_live",
            "description": "Start a live Discord voice bridge in a voice channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "guild_id": {"type": "string", "description": "Discord guild ID"},
                    "channel_id": {"type": "string", "description": "Voice channel ID to join"},
                    "user_id": {"type": "string", "description": "Discord user ID whose current voice channel should be used when channel_id is omitted"},
                },
                "additionalProperties": False,
            },
        },
        handler=_voice_live_handler,
        check_fn=lambda: True,
        is_async=True,
    )

    if AUTOSTART_FILE.exists() or os.getenv("DISCORD_VOICE_LIVE_AUTOSTART", "").lower() in {"1", "true", "yes"}:
        _schedule_autostart_thread()

    ctx.register_tool(
        name="voice_live_leave",
        toolset="hermes",
        schema={
            "name": "voice_live_leave",
            "description": "Stop the live Discord voice bridge for a guild.",
            "parameters": {
                "type": "object",
                "properties": {"guild_id": {"type": "string", "description": "Discord guild ID"}},
                "required": ["guild_id"],
                "additionalProperties": False,
            },
        },
        handler=_voice_live_leave_handler,
        check_fn=lambda: True,
        is_async=True,
    )

    ctx.register_tool(
        name="voice_live_status",
        toolset="hermes",
        schema={
            "name": "voice_live_status",
            "description": "Check the live Discord voice bridge health, transcripts, notes path, and quiet timer.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        handler=_voice_live_status_handler,
        check_fn=lambda: True,
        is_async=True,
    )

    ctx.register_tool(
        name="voice_live_frame",
        toolset="hermes",
        schema={
            "name": "voice_live_frame",
            "description": "Send a manual image frame to the active Gemini Live voice bridge. Use when a user uploads an image for the agent to see.",
            "parameters": {
                "type": "object",
                "properties": {
                    "guild_id": {"type": "string", "description": "Discord guild ID"},
                    "image_url": {"type": "string", "description": "URL of the image to send"},
                },
                "required": ["image_url"],
                "additionalProperties": False,
            },
        },
        handler=_voice_live_frame_handler,
        check_fn=lambda: True,
        is_async=True,
    )


    ctx.register_tool(
        name="voice_live_notes",
        toolset="hermes",
        schema={
            "name": "voice_live_notes",
            "description": "Read recent call-note transcript events captured by the live Discord voice bridge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum recent note events to return (default 50, max 500)",
                    }
                },
                "additionalProperties": False,
            },
        },
        handler=_voice_live_notes_handler,
        check_fn=lambda: True,
        is_async=True,
    )

    # Slash command wrapper: /voice-live in Discord starts the Gemini Live bridge.
    # The plugin already exposes voice_live as a tool for the LLM; this adds
    # the /voice-live slash command so the gateway router recognizes it.
    async def _slash_voice_live(raw_args: str) -> str:
        import gateway.run as _gw
        from gateway.platforms.base import Platform
        runner = None
        ref = getattr(_gw, "_gateway_runner_ref", None)
        if callable(ref):
            runner = ref()
        if runner is None:
            runner = getattr(getattr(_gw, "GatewayRunner", object), "_instance", None)
        if not runner:
            return "Gateway not available."
        adapter = runner.adapters.get(Platform("discord"))
        if not adapter:
            return "Discord adapter not found."
        user_id = DEFAULT_USER_ID
        inferred = _infer_user_voice_channel(adapter, str(user_id))
        if not inferred:
            return "Could not infer your current voice channel. Join a voice channel first."
        guild_id_str, channel_id_str = inferred
        result = json.loads(await voice_live(adapter, guild_id_str, channel_id_str, user_id=str(user_id)))
        status = result.get("status", "error")
        msg = result.get("message", "")
        if status == "success":
            return f"✅ voice-live: {msg}"
        if status == "pending":
            return f"⏳ voice-live: {msg}"
        return f"❌ voice-live: {msg or status}"

    ctx.register_command(
        name="voice-live",
        handler=_slash_voice_live,
        description="Start Gemini Live voice bridge in your current voice channel",
        args_hint="",
    )

    async def _slash_voice_live_leave(raw_args: str) -> str:
        import gateway.run as _gw
        runner = None
        ref = getattr(_gw, "_gateway_runner_ref", None)
        if callable(ref):
            runner = ref()
        if runner is None:
            runner = getattr(getattr(_gw, "GatewayRunner", object), "_instance", None)
        if not runner:
            return "Gateway not available."
        from gateway.platforms.base import Platform
        adapter = runner.adapters.get(Platform("discord"))
        if not adapter:
            return "Discord adapter not found."
        user_id = DEFAULT_USER_ID
        inferred = _infer_user_voice_channel(adapter, str(user_id))
        if not inferred:
            return "No active voice session found."
        guild_id_str = inferred[0]
        result = json.loads(await voice_live_leave(guild_id_str))
        status = result.get("status", "error")
        msg = result.get("message", "")
        if status == "success":
            return f"✅ voice-live-leave: {msg}"
        return f"❌ voice-live-leave: {msg or status}"

    ctx.register_command(
        name="voice-live-leave",
        handler=_slash_voice_live_leave,
        description="Stop Gemini Live voice bridge",
        args_hint="",
    )



async def _voice_live_handler(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    params = _coerce_tool_args(args, kwargs)
    import gateway.run as gateway_run
    from gateway.platforms.base import Platform
    runner = None
    ref = getattr(gateway_run, "_gateway_runner_ref", None)
    if callable(ref):
        runner = ref()
    if runner is None:
        runner = getattr(getattr(gateway_run, "GatewayRunner", object), "_instance", None)
    if not runner:
        return json.dumps({"status": "error", "message": "Gateway not available"})
    adapter = runner.adapters.get(Platform("discord"))
    if not adapter:
        return json.dumps({"status": "error", "message": "Discord adapter not found"})

    guild_id = params.get("guild_id")
    channel_id = params.get("channel_id")
    user_id = str(params.get("user_id") or DEFAULT_USER_ID)
    if not guild_id or not channel_id:
        inferred = _infer_user_voice_channel(adapter, user_id)
        if inferred:
            guild_id, channel_id = inferred
    if not guild_id or not channel_id:
        return json.dumps({"status": "error", "message": "guild_id/channel_id are required"})
    return await _run_on_gateway_loop(runner, voice_live(adapter, str(guild_id), str(channel_id)))


async def _voice_live_leave_handler(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    params = _coerce_tool_args(args, kwargs)
    guild_id = params.get("guild_id")
    if not guild_id:
        return json.dumps({"status": "error", "message": "guild_id is required"})
    import gateway.run as gateway_run
    runner = None
    ref = getattr(gateway_run, "_gateway_runner_ref", None)
    if callable(ref):
        runner = ref()
    if runner is not None:
        return await _run_on_gateway_loop(runner, voice_live_leave(str(guild_id)))
    return await voice_live_leave(str(guild_id))


async def _voice_live_status_handler(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    return await _control_get("/health")


async def _voice_live_notes_handler(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    params = _coerce_tool_args(args, kwargs)
    try:
        limit = int(params.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 500))
    return await _control_get(f"/notes?limit={limit}")


async def _voice_live_frame_handler(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    params = _coerce_tool_args(args, kwargs)
    image_url = params.get("image_url")
    if not image_url:
        return json.dumps({"status": "error", "message": "image_url is required"})
    try:
        import urllib.request
        req = urllib.request.Request(image_url, headers={"User-Agent": "Hermes/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        mime = resp.headers.get("Content-Type", "image/jpeg").split(";", 1)[0].strip().lower()
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to fetch image: {e}"})
    return await _control_post_frame(data, mime, force=True)




async def _control_get(path: str) -> str:
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", CONTROL_PORT)
        request = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        writer.write(request.encode("utf-8"))
        await writer.drain()
        data = await asyncio.wait_for(reader.read(), timeout=3.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        body = data.split(b"\r\n\r\n", 1)[-1]
        return body.decode("utf-8", errors="replace") or json.dumps({"status": "error", "message": "empty response"})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Voice bridge control API unavailable: {e}"})


async def _control_post_frame(data: bytes, mime: str, force: bool = False) -> str:
    path = "/frame"
    if force:
        path += "?force=true"
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", CONTROL_PORT)
        headers = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            f"Content-Type: {mime}\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"Connection: close\r\n\r\n"
        )
        writer.write(headers.encode("utf-8") + data)
        await writer.drain()
        resp = await asyncio.wait_for(reader.read(), timeout=5.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        body = resp.split(b"\r\n\r\n", 1)[-1]
        return body.decode("utf-8", errors="replace") or json.dumps({"status": "error", "message": "empty response"})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Voice bridge control API unavailable: {e}"})


async def _run_on_gateway_loop(runner, coro):
    loop = getattr(runner, "_gateway_loop", None)
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if loop is None or loop is current_loop or not loop.is_running():
        return await coro
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return await asyncio.wrap_future(future)


def _infer_user_voice_channel(adapter, user_id: str) -> Optional[tuple]:
    client = getattr(adapter, "_client", None)
    if not client:
        return None
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    for guild in getattr(client, "guilds", []) or []:
        member = guild.get_member(uid)
        if member and member.voice and member.voice.channel:
            return str(guild.id), str(member.voice.channel.id)
    return None


async def _autostart_voice_live() -> None:
    deadline = time.monotonic() + 180.0
    last_error = ""
    while time.monotonic() < deadline:
        try:
            params = {}
            if AUTOSTART_FILE.exists():
                try:
                    params = json.loads(AUTOSTART_FILE.read_text())
                except Exception:
                    pass
            import gateway.run as gateway_run
            from gateway.platforms.base import Platform
            runner = None
            ref = getattr(gateway_run, "_gateway_runner_ref", None)
            if callable(ref):
                runner = ref()
            adapter = runner.adapters.get(Platform("discord")) if runner else None
            if not adapter:
                last_error = "Discord adapter not ready"
                await asyncio.sleep(2.0)
                continue
            guild_id = params.get("guild_id") or DEFAULT_GUILD_ID
            channel_id = params.get("channel_id") or DEFAULT_CHANNEL_ID
            user_id = str(params.get("user_id") or DEFAULT_USER_ID)
            if not guild_id or not channel_id:
                inferred = _infer_user_voice_channel(adapter, user_id)
                if inferred:
                    guild_id = str(inferred[0])
                    channel_id = str(inferred[1])
            if not guild_id or not channel_id:
                last_error = "Target voice channel not found"
                await asyncio.sleep(10.0)
                continue

            result = json.loads(await voice_live(adapter, str(guild_id), str(channel_id), user_id=str(user_id)))
            if result.get("status") == "success":
                if not KEEP_AUTOSTART_FILE:
                    try:
                        AUTOSTART_FILE.unlink(missing_ok=True)
                    except Exception:
                        pass
                return
            if result.get("status") == "pending":
                await asyncio.sleep(5.0)
                continue
            last_error = result.get("message", str(result))
        except Exception as exc:
            last_error = str(exc)
            logger.warning("voice-live autostart failed: %s", exc)
        await asyncio.sleep(5.0)
    logger.error("voice-live autostart gave up: %s", last_error)


_autostart_thread_started = False


def _schedule_autostart_thread() -> None:
    global _autostart_thread_started
    if _autostart_thread_started:
        return
    _autostart_thread_started = True

    def _thread_main():
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            try:
                import gateway.run as gateway_run
                ref = getattr(gateway_run, "_gateway_runner_ref", None)
                runner = ref() if callable(ref) else None
                loop = getattr(runner, "_gateway_loop", None) if runner else None
                if loop and loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(_autostart_voice_live(), loop)
                    future.result(timeout=185.0)
                    return
            except Exception:
                pass
            time.sleep(1.0)

    thread = threading.Thread(target=_thread_main, name="voice-live-autostart", daemon=True)
    thread.start()


async def voice_live(adapter, guild_id: str, channel_id: str, user_id: Optional[str] = None) -> str:
    guild_id_int = int(guild_id)

    if _starting.get(guild_id_int):
        return json.dumps({"status": "pending", "message": "Bridge is being started"})

    if guild_id_int in _active_bridges:
        bridge_info = _active_bridges[guild_id_int]
        current_vc = bridge_info.get("vc")
        if current_vc and current_vc.is_connected() and current_vc.channel:
            if str(current_vc.channel.id) == channel_id:
                # Second trigger = leave (toggle behavior)
                return await voice_live_leave(guild_id)
            guild = adapter._client.get_guild(guild_id_int) if hasattr(adapter, "_client") else None
            target = guild.get_channel(int(channel_id)) if guild else None
            if target:
                try:
                    await current_vc.move_to(target)
                    return json.dumps({"status": "success", "message": f"Moved to {target.name}"})
                except Exception as e:
                    return json.dumps({"status": "success", "message": f"Active but couldn't move: {e}"})
        # Stale bridge (auto-left or disconnected) — cancel and clean up
        task = bridge_info.get("task")
        if task and not task.done():
            task.cancel()
        _active_bridges.pop(guild_id_int, None)
        _starting.pop(guild_id_int, None)
        # fall through to start fresh

    if not hasattr(adapter, "_client") or not adapter._client:
        return json.dumps({"status": "error", "message": "Discord client not connected"})

    guild = adapter._client.get_guild(guild_id_int)
    if not guild:
        return json.dumps({"status": "error", "message": f"Guild {guild_id} not found"})

    # Force-disconnect any existing voice client in this guild (prevents Vapi↔Gemini conflicts)
    await _disconnect_any_existing_vc(adapter, guild_id_int)

    channel = guild.get_channel(int(channel_id))
    if not channel:
        return json.dumps({"status": "error", "message": f"Channel {channel_id} not found"})

    _starting[guild_id_int] = True
    try:
        import importlib.util
        bridge_path = PLUGIN_DIR / "bridge.py"
        spec = importlib.util.spec_from_file_location("discord_voice_live_bridge", bridge_path)
        bridge_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge_mod)

        # Resolve per-user profile (auto-creates a new profile on first contact)
        user_profile = None
        effective_user_id = user_id or DEFAULT_USER_ID
        try:
            from user_profiles import get_or_create_profile  # type: ignore
            user_profile = get_or_create_profile(effective_user_id)
            logger.info("VoiceLive: loaded profile for user %s (owner=%s, tools=%d)",
                        user_profile.discord_id, user_profile.is_owner, len(user_profile.enabled_tools))
        except Exception as exc:
            logger.warning("VoiceLive: could not load user profile, falling back to single-user mode: %s", exc)
            user_profile = None

        loop = asyncio.get_running_loop()
        ready_future = loop.create_future()
        bridge_task = asyncio.create_task(bridge_mod.run_sidecar(channel, adapter, ready_future, user_profile))
        bridge_task.add_done_callback(
            lambda _task, _gid=guild_id_int: _active_bridges.pop(_gid, None)
        )
        _active_bridges[guild_id_int] = {
            "vc": None,
            "adapter": adapter,
            "task": bridge_task,
            "bridge_mod": bridge_mod,
            "user_profile": user_profile,
            "user_id": effective_user_id,
        }

        try:
            ready = await asyncio.wait_for(ready_future, timeout=120.0)
        except asyncio.TimeoutError:
            bridge_task.cancel()
            return json.dumps({"status": "error", "message": "Timed out waiting for bridge"})

        if not ready.get("ok"):
            bridge_task.cancel()
            return json.dumps({"status": "error", "message": ready.get("message", "Bridge failed")})

        _active_bridges[guild_id_int]["vc"] = ready.get("vc")

        if VIDEO_STATE_DETECTION_ENABLED:
            watcher_task = asyncio.create_task(
                _video_state_watcher(guild_id_int)
            )
            _active_bridges[guild_id_int]["video_watcher"] = watcher_task
            watcher_task.add_done_callback(
                lambda _task, _gid=guild_id_int: _active_bridges.get(_gid, {}).pop("video_watcher", None)
            )

        return json.dumps({
            "status": "success",
            "message": f"Voice live bridge started in {channel.name}",
            "health": ready.get("health", {}),
        })
    except Exception as e:
        logger.error("Failed to start bridge: %s", e, exc_info=True)
        return json.dumps({"status": "error", "message": f"Failed: {e}"})
    finally:
        _starting.pop(guild_id_int, None)


async def voice_live_leave(guild_id: str) -> str:
    guild_id_int = int(guild_id)
    bridge = _active_bridges.pop(guild_id_int, None)
    if not bridge:
        return json.dumps({"status": "error", "message": "No active voice bridge"})
    try:
        bridge["task"].cancel()
        try:
            await asyncio.wait_for(bridge["task"], timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        vc = bridge["vc"]
        if vc and vc.is_connected():
            try:
                await asyncio.wait_for(vc.disconnect(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
        _starting.pop(guild_id_int, None)
        return json.dumps({"status": "success", "message": "Voice live bridge stopped."})
    except Exception as e:
        _starting.pop(guild_id_int, None)
        return json.dumps({"status": "error", "message": f"Error: {e}"})


# ---------------------------------------------------------------------------
# Video state watcher — detects screenshare / camera activity via voice states
# ---------------------------------------------------------------------------

async def _video_state_watcher(guild_id: int) -> None:
    """
    Poll the voice channel for members who enable/disable self_stream / self_video.
    When a state change is detected, send a contextual text message to Gemini Live
    so the AI knows video activity is happening (even though it can't see the feed).
    """
    bridge_info = _active_bridges.get(guild_id)
    if not bridge_info:
        return

    # Track last-known states: {member_id: {"stream": bool, "video": bool}}
    last_states: Dict[int, Dict[str, bool]] = {}

    while True:
        await asyncio.sleep(VIDEO_STATE_POLL_INTERVAL_SECONDS)

        bridge_info = _active_bridges.get(guild_id)
        if not bridge_info:
            break

        vc = bridge_info.get("vc")
        if not vc or not vc.is_connected():
            continue

        channel = vc.channel
        if not channel:
            continue

        bridge_mod = bridge_info.get("bridge_mod")
        if not bridge_mod:
            continue

        for member in channel.members:
            if member.bot:
                continue
            mid = member.id
            current = {
                "stream": getattr(member.voice, "self_stream", False) or False,
                "video": getattr(member.voice, "self_video", False) or False,
            }
            previous = last_states.get(mid)
            if previous is None:
                last_states[mid] = current
                continue

            if current["stream"] and not previous["stream"]:
                _send_video_awareness(bridge_mod, f"{member.display_name} started screen sharing. I can't see the shared screen, but I know it's active.")
            elif not current["stream"] and previous["stream"]:
                _send_video_awareness(bridge_mod, f"{member.display_name} stopped screen sharing.")

            if current["video"] and not previous["video"]:
                _send_video_awareness(bridge_mod, f"{member.display_name} turned on their camera. I can't see the video feed, but I know it's on.")
            elif not current["video"] and previous["video"]:
                _send_video_awareness(bridge_mod, f"{member.display_name} turned off their camera.")

            last_states[mid] = current


def _send_video_awareness(bridge_mod, text: str) -> None:
    """Send a text nudge to Gemini Live via the active bridge."""
    try:
        bridge = getattr(bridge_mod, "BRIDGE", None)
        if bridge and hasattr(bridge, "_gemini") and hasattr(bridge._gemini, "send_text"):
            asyncio.create_task(bridge._gemini.send_text(text))
    except Exception:
        logger.debug("Failed to send video awareness text", exc_info=True)
