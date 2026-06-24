"""
Discord Voice Live Plugin — Hermes Plugin Registration
"""

import asyncio
import json
import logging
import os
import secrets
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

# Shared secret for the HTTP control API. Generated once at module import
# (so it survives across requests in the same process) and exported on this
# module so bridge.py can pick it up via sys.modules lookups. Hardcoding a
# default would be a vuln — secrets.token_urlsafe(32) gives ~256 bits.
_CONTROL_SECRET_FILE = Path(os.getenv(
    "DISCORD_VOICE_LIVE_SECRET_FILE",
    str(Path.home() / ".hermes" / "voice-live-control-secret"),
))
try:
    if _CONTROL_SECRET_FILE.exists():
        CONTROL_API_SECRET = _CONTROL_SECRET_FILE.read_text().strip() or secrets.token_urlsafe(32)
    else:
        CONTROL_API_SECRET = secrets.token_urlsafe(32)
        try:
            _CONTROL_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CONTROL_SECRET_FILE.write_text(CONTROL_API_SECRET)
            _CONTROL_SECRET_FILE.chmod(0o600)
        except OSError:
            # If we can't persist it, the in-memory secret still works for
            # this process lifetime. Just log so we know it's not sticky.
            logger.warning("VoiceLive: could not persist control secret to %s", _CONTROL_SECRET_FILE)
except Exception as _exc:  # pragma: no cover — defensive
    logger.warning("VoiceLive: control secret init failed, generating ephemeral: %s", _exc)
    CONTROL_API_SECRET = secrets.token_urlsafe(32)


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key, "")
    if not val:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}


KEEP_AUTOSTART_FILE = _env_bool("DISCORD_VOICE_LIVE_KEEP_AUTOSTART_FILE", True)
VIDEO_STATE_DETECTION_ENABLED = _env_bool("DISCORD_VOICE_LIVE_VIDEO_STATE_DETECTION", True)
VIDEO_STATE_POLL_INTERVAL_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_VIDEO_STATE_POLL_INTERVAL", "5"))
AUTOSTART_FILE = Path(os.getenv(
    "DISCORD_VOICE_LIVE_AUTOSTART_FILE",
    str(Path.home() / ".hermes" / "voice-live-autostart.json"),
))

_active_bridges: Dict[int, Dict[str, Any]] = {}
_STARTING: Dict[int, float] = {}
_STARTING_TTL = 180.0


def _is_starting(gid: int) -> bool:
    started = _STARTING.get(gid)
    if started is None:
        return False
    if time.monotonic() - started > _STARTING_TTL:
        _STARTING.pop(gid, None)
        return False
    return True


def _set_starting(gid: int) -> None:
    _STARTING[gid] = time.monotonic()


def _clear_starting(gid: int) -> None:
    _STARTING.pop(gid, None)


# Patch 7: defensive adapter lookup. Returns (adapter, error_str). Replaces
# the inline `gateway.run._gateway_runner_ref` traversal which leaks a
# bare AttributeError when the gateway is mid-restart.
def _get_discord_adapter():
    try:
        import gateway.run as gateway_run
    except Exception as exc:
        return None, f"gateway.run not importable: {exc}"
    runner = None
    ref = getattr(gateway_run, "_gateway_runner_ref", None)
    if callable(ref):
        try:
            runner = ref()
        except Exception as exc:
            return None, f"runner ref raised: {exc}"
    if runner is None:
        runner = getattr(getattr(gateway_run, "GatewayRunner", object), "_instance", None)
    if runner is None:
        return None, "Gateway runner not available"
    try:
        from gateway.platforms.base import Platform
        adapter = runner.adapters.get(Platform("discord"))
    except Exception as exc:
        return None, f"adapter lookup failed: {exc}"
    if adapter is None:
        return None, "Discord adapter not registered"
    return adapter, None


# Patch 8: load bridge.py exactly once at module import. Re-execing the
# module on every /voice-live invocation interacted badly with module-level
# globals (BRIDGE, _OPENCODE_SESSIONS, _EMAIL_REMINDER_TASK).
import importlib.util as _importlib_util
_bridge_mod = None
try:
    _BRIDGE_SPEC = _importlib_util.spec_from_file_location(
        "discord_voice_live_bridge", str(PLUGIN_DIR / "bridge.py")
    )
    _bridge_mod = _importlib_util.module_from_spec(_BRIDGE_SPEC)
    _BRIDGE_SPEC.loader.exec_module(_bridge_mod)
except Exception as exc:
    logger.error("Failed to load bridge.py at module import: %s: %s", type(exc).__name__, exc)
    logger.error("The discord-voice plugin will have limited functionality. Check requirements (numpy, etc.)")


async def _safe_disconnect_vc(vc, timeout: float = 5.0) -> bool:
    if not vc or not vc.is_connected():
        return True
    try:
        await asyncio.wait_for(vc.disconnect(), timeout=timeout)
        await asyncio.sleep(0.5)
        return True
    except asyncio.TimeoutError:
        return False
    except Exception as exc:
        logger.warning("VoiceLive: safe disconnect failed: %s", exc)
        return False


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
    # Sibling-release callout: Vapi.ai bridge is now available as a parallel
    # transport for Discord voice.  The Gemini Live bridge (this plugin) is the
    # default; the Vapi bridge is exposed by the `discord-vapi` plugin and
    # registers the `voice_vapi` tool.  See PLUGIN_SIBLINGS below.
    PLUGIN_SIBLINGS = {
        "voice_vapi": {
            "status": "NEW, RELEASED",
            "transport": "Vapi.ai",
            "tagline": "Managed conversational AI — same Discord voice UX, different LLM/voice stack.",
            "use_when": "You want Vapi's hosted assistant model (durable call IDs, dashboard-managed prompts, multi-provider voice/TTS) instead of streaming directly to Gemini Live.",
        },
    }

    ctx.register_tool(
        name="voice_live",
        toolset="hermes",
        schema={
            "name": "voice_live",
            "description": (
                "Start a live Discord voice bridge in a voice channel via Gemini Multimodal Live. "
                "If a sibling Vapi bridge is also installed, this tool is the Gemini transport; "
                "use the `voice_vapi` tool to start the Vapi transport instead."
            ),
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
            "description": "Send a manual image frame to the active Gemini Live voice bridge. Use when a user uploads an image for the agent to see. Accepts HTTP image_url, local file_path, or raw base64 data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "guild_id": {"type": "string", "description": "Discord guild ID"},
                    "image_url": {"type": "string", "description": "HTTP(S) URL of the image to fetch and send"},
                    "file_path": {"type": "string", "description": "Local filesystem path to an image file to send (jpg/png/webp)"},
                    "base64_data": {"type": "string", "description": "Pre-base64-encoded image data (raw base64, no data: prefix)"},
                    "mime_type": {"type": "string", "description": "MIME type when passing base64_data (default image/jpeg)"},
                    "source": {"type": "string", "description": "Source label for the video_initialized webhook (default 'agent')"},
                    "force": {"type": "boolean", "description": "Bypass audio-gating (default true for manual pushes)"},
                },
                "additionalProperties": False,
            },
        },
        handler=_voice_live_frame_handler,
        check_fn=lambda: True,
        is_async=True,
    )

    ctx.register_tool(
        name="voice_live_video_status",
        toolset="hermes",
        schema={
            "name": "voice_live_video_status",
            "description": "Return the current video feed state of the active Gemini Live voice bridge. Includes frame counts, last-accept timestamp, last drop reason, and source label. Use to verify whether a video push was accepted or to diagnose why frames are not flowing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "guild_id": {"type": "string", "description": "Discord guild ID (informational, the bridge is process-global)"},
                },
                "additionalProperties": False,
            },
        },
        handler=_voice_live_video_status_handler,
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


    # SORA bridge elements: preflight/grill/goal synthesis/redaction
    try:
        from sora_bridge_elements import register_sora_bridge_tools
        register_sora_bridge_tools(ctx, _bridge_mod, _active_bridges)
    except Exception as exc:
        logger.warning("SORA bridge elements failed to register: %s: %s", type(exc).__name__, exc)

    # Slash commands for Discord: register via `ctx.register_command()`.
    # The Discord adapter (`plugins/platforms/discord/adapter.py:3189-3216`)
    # iterates `_iter_plugin_command_entries()` and mirrors every plugin
    # command into Discord's native slash picker. Earlier code skipped this
    # call (assumption: `register_command` was Hermes-CLI only), which broke
    # `/voice-live` and `/voice-live-leave` with `CommandNotFound`. The
    # adapter has a dedicated plugin-command mirror path; the wrappers below
    # are what it surfaces to Discord.
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
        result = json.loads(await voice_live(adapter, guild_id_str, channel_id_str))
        status = result.get("status", "error")
        msg = result.get("message", "")
        if status == "success":
            return f"✅ voice-live: {msg}"
        if status == "pending":
            return f"⏳ voice-live: {msg}"
        return f"❌ voice-live: {msg or status}"

    async def _slash_voice_live_leave(raw_args: str) -> str:
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
            return "No active voice session found."
        guild_id_str = inferred[0]
        result = json.loads(await voice_live_leave(guild_id_str))
        status = result.get("status", "error")
        msg = result.get("message", "")
        if status == "success":
            return f"✅ voice-live-leave: {msg}"
        return f"❌ voice-live-leave: {msg or status}"

    ctx.register_command(
        name="voice-live",
        handler=_slash_voice_live,
        description="Start Gemini Live voice bridge in your current voice channel",
        args_hint="",
    )
    ctx.register_command(
        name="voice-live-leave",
        handler=_slash_voice_live_leave,
        description="Stop Gemini Live voice bridge",
        args_hint="",
    )



async def _voice_live_handler(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    params = _coerce_tool_args(args, kwargs)
    # Patch 7: use the defensive adapter lookup helper so we get a structured
    # error rather than a bare AttributeError if the gateway shape changes.
    try:
        import gateway.run as gateway_run  # noqa: F401  (used for the runner)
    except Exception as exc:
        return json.dumps({"status": "error", "message": f"gateway.run not importable: {exc}"})
    adapter, err = _get_discord_adapter()
    if err:
        return json.dumps({"status": "error", "message": err})
    runner = getattr(gateway_run, "_gateway_runner_ref", None)
    runner = runner() if callable(runner) else None
    if runner is None:
        return json.dumps({"status": "error", "message": "Gateway runner ref unavailable"})

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
    body = await _control_get("/health")
    # Sibling-release hint: the Vapi bridge (also a Discord voice transport)
    # is NEW, RELEASED.  Append a transport_choice note so callers know an
    # alternative exists without making this handler the source of truth.
    try:
        data = json.loads(body) if isinstance(body, str) else body
        if isinstance(data, dict):
            data.setdefault("sibling_transports", []).append({
                "name": "voice_vapi",
                "status": "NEW, RELEASED",
                "transport": "Vapi.ai",
                "tool": "voice_vapi",
            })
            return json.dumps(data)
    except (json.JSONDecodeError, TypeError):
        pass
    return body


async def _voice_live_notes_handler(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    params = _coerce_tool_args(args, kwargs)
    try:
        limit = int(params.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 500))
    return await _control_get(f"/notes?limit={limit}")


async def _voice_live_video_status_handler(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    """Return the current video state of the active bridge.

    The data lives in `BRIDGE._gemini.metrics` (and a few session-lifetime
    counters). We pull it via the bridge's own `/health` HTTP control
    endpoint, which already merges `metrics` into the response. To make
    the result tool-friendly we synthesize a dedicated `video` block.
    """
    try:
        import discord_voice_bridge as _dvb
    except Exception:
        # Fall back to the loaded module via the plugin's bridge_mod
        try:
            import bridge as _dvb  # type: ignore
        except Exception as e:
            return json.dumps({"status": "error", "message": f"bridge module not importable: {e}"})

    BRIDGE = getattr(_dvb, "BRIDGE", None)
    if BRIDGE is None:
        return json.dumps({
            "status": "not_started",
            "video": {"enabled": True, "running": False, "in_frames": 0, "sent_frames": 0, "dropped_frames": 0, "last_reason": "no_bridge"},
        })

    metrics = getattr(BRIDGE._gemini, "metrics", {}) if getattr(BRIDGE, "_gemini", None) else {}
    now = time.monotonic()
    last_accept_mono = metrics.get("video_last_accept_monotonic")
    last_accept_age_s = (now - float(last_accept_mono)) if last_accept_mono else None

    return json.dumps({
        "status": "ok",
        "video": {
            "running": bool(getattr(BRIDGE, "_running", False)),
            "voice_connected": bool(BRIDGE._vc and BRIDGE._vc.is_connected()) if getattr(BRIDGE, "_vc", None) else False,
            "in_frames": metrics.get("video_in_frames", 0),
            "sent_frames": metrics.get("video_sent_frames", 0),
            "dropped_frames": metrics.get("video_dropped_frames", 0),
            "last_reason": metrics.get("video_last_reason"),
            "last_source": metrics.get("video_last_source", ""),
            "last_accept_age_s": last_accept_age_s,
            "last_quiet_s": metrics.get("video_last_quiet_s"),
            "max_fps": 1.0,
            "max_bytes": 512 * 1024,
        },
    })


async def _voice_live_frame_handler(args: Optional[Dict[str, Any]] = None, **kwargs) -> str:
    params = _coerce_tool_args(args, kwargs)
    image_url = params.get("image_url")
    file_path = params.get("file_path")
    base64_data = params.get("base64_data")
    mime_type = (params.get("mime_type") or "image/jpeg").lower()
    source = params.get("source") or "agent"
    force = bool(params.get("force", True))

    # Exactly one of image_url / file_path / base64_data is required.
    provided = [k for k in (image_url, file_path, base64_data) if k]
    if len(provided) == 0:
        return json.dumps({"status": "error", "message": "one of image_url, file_path, or base64_data is required"})
    if len(provided) > 1:
        return json.dumps({"status": "error", "message": "pass exactly one of image_url, file_path, or base64_data"})

    try:
        if image_url:
            import urllib.request
            req = urllib.request.Request(image_url, headers={"User-Agent": "Hermes/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            detected_mime = resp.headers.get("Content-Type", "image/jpeg").split(";", 1)[0].strip().lower()
        elif file_path:
            # Local file: read bytes, sniff mime from magic bytes
            with open(file_path, "rb") as f:
                data = f.read()
            head = data[:12]
            if head.startswith(b"\xff\xd8\xff"):
                detected_mime = "image/jpeg"
            elif head.startswith(b"\x89PNG\r\n\x1a\n"):
                detected_mime = "image/png"
            elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                detected_mime = "image/webp"
            else:
                detected_mime = mime_type
        else:  # base64_data
            import base64
            try:
                data = base64.b64decode(base64_data, validate=True)
            except Exception as e:
                return json.dumps({"status": "error", "message": f"base64 decode failed: {e}"})
            detected_mime = mime_type
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Failed to load image: {e}"})

    # Forward to the bridge via its HTTP control API.
    # Use a query string to pass source and force flags.
    import urllib.parse as _u
    qs = _u.urlencode({"force": "true" if force else "false", "source": source})
    return await _control_post_frame(data, detected_mime, force=force, query=qs)




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


async def _control_post_frame(data: bytes, mime: str, force: bool = False, query: str = "") -> str:
    # Build the query string ONCE. Caller-supplied `query` takes precedence
    # (it can include source/force/etc.); if none given, fall back to a
    # plain force flag.
    path = "/frame"
    if query:
        path = f"{path}?{query}"
    elif force:
        path = f"{path}?force=true"
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
    """Spawn the bridge for the autostart request, then exit.

    Loop safety contract:
      - Returns immediately if the bridge is ALREADY active in the target
        guild/channel (avoids the leave-join loop where successive autostart
        iterations kept toggling the same voice connection on and off).
      - On error, retries up to a 180s deadline, then gives up.
      - Never returns "Bridge is being started" / "pending" — those are
        transient state markers from the inner voice_live() function; the
        autostart thread should sleep and re-check the actual connection.
      - Token-burn guard: if the configured user is not in any voice
        channel, the autostart returns silently (no Discord connect, no
        Gemini WebSocket, zero API tokens). The thread re-checks every
        5s in case the user joins a channel. The autostart file/env
        triggers remain set so this works as soon as the user appears.
    """
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
            adapter, err = _get_discord_adapter()
            if err:
                last_error = err
                await asyncio.sleep(2.0)
                continue
            # ── Presence gate: no user in any voice channel → exit silently ──
            # Prevents token burn when DISCORD_VOICE_LIVE_AUTOSTART=true is set
            # but the target user is not in Discord voice. The thread polls
            # every 5s in case the user joins.
            try:
                _u = str(params.get("user_id") or DEFAULT_USER_ID)
                _u_int = int(_u) if _u and str(_u).isdigit() else None
                _user_in_vc = False
                if _u_int is not None:
                    for _g in getattr(getattr(adapter, "_client", None), "guilds", []) or []:
                        _m = _g.get_member(_u_int)
                        if _m and getattr(getattr(_m, "voice", None), "channel", None):
                            _user_in_vc = True
                            break
                if not _user_in_vc:
                    last_error = "user not in voice"
                    await asyncio.sleep(5.0)
                    continue
            except Exception:
                # Never let the presence check itself wedge the autostart.
                pass
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

            # ── Already-active guard: exit cleanly instead of toggling ───────
            # If a bridge is already running in this guild+channel, the
            # autostart has nothing to do. Returning prevents the loop where
            # the inner voice_live() sees "channel matches" and triggers
            # voice_live_leave(), which the next loop iteration then
            # reverses by re-joining.
            try:
                gid_int = int(guild_id)
                cid_int = int(channel_id)
            except (TypeError, ValueError):
                gid_int = cid_int = None
            if gid_int is not None and gid_int in _active_bridges:
                existing = _active_bridges[gid_int]
                existing_vc = existing.get("vc")
                if existing_vc and existing_vc.is_connected() and existing_vc.channel \
                        and existing_vc.channel.id == cid_int:
                    logger.info(
                        "voice-live autostart: bridge already active in guild=%d channel=%d, exiting",
                        gid_int, cid_int,
                    )
                    if not KEEP_AUTOSTART_FILE:
                        try:
                            AUTOSTART_FILE.unlink(missing_ok=True)
                        except Exception:
                            pass
                    return

            result_str = await voice_live(adapter, str(guild_id), str(channel_id), user_id=str(user_id))
            try:
                result = json.loads(result_str)
            except (TypeError, ValueError):
                result = {"status": "error", "message": str(result_str)[:200]}
            status = result.get("status", "error")
            if status == "success":
                if not KEEP_AUTOSTART_FILE:
                    try:
                        AUTOSTART_FILE.unlink(missing_ok=True)
                    except Exception:
                        pass
                return
            if status == "pending":
                # The bridge is being started by someone else — wait, then
                # re-check the connection state on the next loop iteration.
                await asyncio.sleep(2.0)
                continue
            last_error = result.get("message", str(result))
            logger.warning("voice-live autostart: %s — retrying in 5s", last_error)
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
    """Module-level entry point for the voice bridge.

    Called from:
      - Slash command `/voice-live` in the Discord adapter (native tree.command)
      - Agent tool calls (LLM-invoked via `_voice_live_handler`)
      - Autostart thread

    When invoked with empty guild_id + channel_id, auto-infers the user's
    current voice channel. This is the path the Discord slash command takes.
    """
    guild_id_int = None
    if guild_id:
        try:
            guild_id_int = int(guild_id)
        except (TypeError, ValueError):
            guild_id_int = None

    if _is_starting(guild_id_int):
        return json.dumps({"status": "pending", "message": "Bridge is being started"})

    if guild_id_int is not None and guild_id_int in _active_bridges:
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
        _clear_starting(guild_id_int)
        # fall through to start fresh

    if not hasattr(adapter, "_client") or not adapter._client:
        return json.dumps({"status": "error", "message": "Discord client not connected"})

    # ── Auto-infer: invoked by the slash command with empty ids ───────────
    if guild_id_int is None or not guild_id:
        inferred = _infer_user_voice_channel(adapter, str(user_id or DEFAULT_USER_ID))
        if inferred:
            guild_id_int = int(inferred[0])
            channel_id = str(inferred[1])
        else:
            return json.dumps({
                "status": "error",
                "message": "Could not infer your current voice channel. Join a voice channel first.",
            })

    guild = adapter._client.get_guild(guild_id_int) if hasattr(adapter, "_client") else None
    if not guild:
        return json.dumps({"status": "error", "message": f"Guild {guild_id} not found"})

    # Resolve effective user id BEFORE any branch that reads it.
    # An earlier version assigned this further down, which made Python treat
    # the name as local for the whole function and crashed on the read below
    # with UnboundLocalError (autostart thread spammed this every 5s).
    effective_user_id = user_id or DEFAULT_USER_ID

    # ── Presence gate: only start if B is actually in this voice channel ───
    target_member = guild.get_member(int(effective_user_id)) if effective_user_id else None
    if target_member:
        member_vc = getattr(getattr(target_member, "voice", None), "channel", None)
        if not member_vc or member_vc.id != int(channel_id):
            return json.dumps({
                "status": "error",
                "message": "You are not in this voice channel. Join it first before starting the bridge.",
            })
    else:
        # If we can't look up the member (edge case), log and continue — better
        # to let the bridge start and let the watchdog catch it.
        logger.warning("VoiceLive: could not verify user presence in guild %s", guild_id)

    # Force-disconnect any existing voice client in this guild (prevents Vapi↔Gemini conflicts)
    existing_vc = getattr(guild, "voice_client", None)
    await _safe_disconnect_vc(existing_vc)

    channel = guild.get_channel(int(channel_id))
    if not channel:
        return json.dumps({"status": "error", "message": f"Channel {channel_id} not found"})

    _set_starting(guild_id_int)
    try:
        bridge_mod = _bridge_mod  # Patch 8: module already loaded at import
        if bridge_mod is None:
            _clear_starting(guild_id_int)
            return json.dumps({"status": "error", "message": "Bridge module failed to load at startup. Check requirements (numpy, etc.) and restart gateway."})

        # Resolve per-user profile (auto-creates a new profile on first contact)
        user_profile = None
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
        bridge_task = asyncio.create_task(bridge_mod.run_sidecar(channel, adapter, ready_future, user_profile,
                                                                      target_user_id=effective_user_id))
        bridge_task.add_done_callback(
            lambda _task, _gid=guild_id_int: _active_bridges.pop(_gid, None)
        )
        _active_bridges[guild_id_int] = {
            "vc": None,
            "adapter": adapter,
            "task": bridge_task,
            "bridge_mod": _bridge_mod,
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
        _clear_starting(guild_id_int)


async def voice_live_leave(guild_id: str) -> str:
    guild_id_int = int(guild_id)
    bridge = _active_bridges.pop(guild_id_int, None)
    if not bridge:
        return json.dumps({"status": "error", "message": "No active voice bridge"})
    try:
        task = bridge.get("task")
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        vc = bridge.get("vc")
        if vc and vc.is_connected():
            try:
                await asyncio.wait_for(vc.disconnect(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
        _clear_starting(guild_id_int)
        return json.dumps({"status": "success", "message": "Voice live bridge stopped."})
    except Exception as e:
        _clear_starting(guild_id_int)
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
                # User started screen sharing. Discord bots do NOT receive
                # the video stream — we can't see it automatically. The
                # video-frame-feeder.py is an OPTIONAL external script the
                # user has to start themselves, and on a headless host
                # there's no display to capture from anyway. So this nudge
                # is ONLY an awareness ping. Do NOT promise automatic
                # screenshot to chat, (b) start video-frame-feeder.py on a
                # machine with a real display and point it at the bridge. You should acknowledge the screen share and
                # ask whether they want to share a specific frame.
                await _send_video_awareness(
                    bridge_mod,
                    f"[SYSTEM EVENT] {member.display_name} started screen sharing. "
                    f"Note: I cannot see Discord video streams automatically — "
                    f"Discord bots do not receive the video feed. The user needs to "
                    f"either (a) paste a screenshot in chat, or (b) start "
                    f"video-frame-feeder.py on a machine with a real display pointing "
                    f"at the bridge. You should acknowledge the screen share and "
                    f"ask whether they want to share a specific frame.",
                    event_type="video_state",
                    bridge_info=bridge_info,
                )
            elif not current["stream"] and previous["stream"]:
                await _send_video_awareness(bridge_mod, f"[SYSTEM EVENT] {member.display_name} stopped screen sharing. Video feed ended.", event_type="video_ended", bridge_info=bridge_info)

            if current["video"] and not previous["video"]:
                # User enabled camera. Same constraint as screen share —
                # Discord bots do NOT receive the video stream. This is
                # an awareness ping, not a video feed.
                _send_video_awareness(
                    bridge_mod,
                    f"[SYSTEM EVENT] {member.display_name} turned on their camera. "
                    f"Note: I cannot see Discord camera streams automatically. "
                    f"For me to actually see them, the user needs to either "
                    f"(a) paste a screenshot in chat, or (b) start the "
                    f"video-frame-feeder.py on a machine with a real camera/display "
                    f"and point it at the bridge.",
                    event_type="video_state",
                )
            elif not current["video"] and previous["video"]:
                _send_video_awareness(bridge_mod, f"[SYSTEM EVENT] {member.display_name} turned off their camera. Video feed ended.", event_type="video_ended")

            last_states[mid] = current


async def _send_video_awareness(bridge_mod, text: str, event_type: str = "video_state", bridge_info: Optional[Dict[str, Any]] = None) -> None:
    """Send a text nudge to Gemini Live via the active bridge.
    
    event_type: "video_state" (camera/screen on/off), "video_frame" (feeder active), "video_ended" (stopped)
    """
    try:
        bridge = getattr(bridge_mod, "BRIDGE", None)
        if bridge and hasattr(bridge, "_gemini") and hasattr(bridge._gemini, "send_text"):
            asyncio.create_task(bridge._gemini.send_text(text))
    except Exception:
        logger.debug("Failed to send video awareness text", exc_info=True)

    if not bridge_info:
        return

    # 1. Honcho peer message (every transition)
    try:
        from honcho.client import Honcho
        import json
        from pathlib import Path
        honcho_json = Path.home() / ".hermes" / "honcho.json"
        if honcho_json.exists():
            cfg = json.loads(honcho_json.read_text())
            workspace = cfg.get("workspace") or "default"
            profile = bridge_info.get("user_profile")
            peer_name = profile.honcho_peer_name if profile and hasattr(profile, "honcho_peer_name") else None
            if not peer_name:
                peer_name = f"discord-{bridge_info.get('user_id', 'unknown')}"
            h = Honcho(workspace=workspace, api_key=cfg.get("apiKey"))
            p = h.peer(id=peer_name)
            p.message(text)
    except Exception:
        logger.debug("Honcho write failed in _video_state_watcher", exc_info=True)

    # 2. Discord DM notification (START transitions only)
    if event_type == "video_state":
        try:
            from notification import deliver as _matrix_deliver
            dm_text = (
                "You just turned on screen share. Heads-up: I can't actually see Discord video streams — "
                "to get a frame to me, paste a screenshot in this chat or run the feeder on a host with a real display."
                if "screen sharing" in text.lower() else
                "Camera on. Same note as screen share — Discord doesn't send the bot the video, "
                "so paste a screenshot or run the feeder if you want me to see what you're seeing."
            )
            user_id = bridge_info.get("user_id")
            adapter = bridge_info.get("adapter")
            
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: _matrix_deliver(
                    text=dm_text,
                    mode="dm",
                    source="video_state_watcher",
                    user_id=user_id,
                    adapter=adapter,
                ),
            )
        except Exception:
            logger.debug("notification.deliver failed in _video_state_watcher", exc_info=True)
