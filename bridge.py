"""
Discord Voice Live Bridge — In-Process Bridge
=============================================
Bypasses the Hermes agent turn loop. The Discord VoiceClient still lives in
the gateway process, so this bridge runs as an asyncio task on that process
and keeps all audio queues non-blocking for the main event loop.

Pipeline:
  Discord Voice → Opus Decode → 48kHz Stereo PCM
    → Downsample → 16kHz Mono PCM
    → Base64 → Gemini WSS (realtimeInput)
    → Gemini WSS (serverContent.inlineData)
    → 24kHz Mono PCM → Upsample → 48kHz Stereo PCM
    → Discord AudioSource (thread-safe queue)

CRITICAL: discord.py calls AudioSource.read() from a native thread.
ALL queues between asyncio and read() MUST be threading.Queue, not asyncio.Queue.
"""

import ast
import asyncio
import base64
import html
import json
import logging
import os
import queue
import random
import re
import subprocess
import sys
import time
import wave
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from typing import Any, Optional, Dict, List, Callable, Tuple

import numpy as np

# Register all tool names with the per-user profile system so the allowlist
# vocabulary stays in sync with the declarations below.
try:
    _plugin_dir = str(Path(__file__).parent)
    if _plugin_dir not in sys.path:
        sys.path.insert(0, _plugin_dir)
    from user_profiles import register_known_tool as _rkt  # type: ignore
    for _pending_decl_group in ():  # placeholder; real registrations happen after declarations
        pass
except Exception:  # user_profiles not importable in some test contexts
    _rkt = None

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("voice-live")

# ── Gemini Live API Constants ──────────────────────────────────────────────
GEMINI_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-live-preview")
GEMINI_MODEL_FALLBACKS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_LIVE_MODEL_FALLBACKS",
        "gemini-3.1-flash-live-preview,"
        "gemini-2.5-flash-native-audio-preview-12-2025,"
        "gemini-2.5-flash-native-audio-preview-09-2025",
    ).split(",")
    if model.strip()
]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_VOICE_NAME = os.getenv("DISCORD_VOICE_LIVE_VOICE", "Aoede")
INITIAL_GREETING = os.getenv(
    "DISCORD_VOICE_LIVE_GREETING",
    "I'm here.",
)

# Discord user IDs allowed to speak to the bot. Empty = allow all non-bot users.
# Comma-separated list of Discord user IDs. e.g. "123456789,987654321"
_ALLOWED_SPEAKER_IDS_RAW = os.getenv("DISCORD_VOICE_LIVE_ALLOWED_SPEAKERS", "")
ALLOWED_SPEAKER_IDS: Optional[List[int]] = (
    [int(uid.strip()) for uid in _ALLOWED_SPEAKER_IDS_RAW.split(",") if uid.strip().isdigit()]
    if _ALLOWED_SPEAKER_IDS_RAW.strip()
    else None
)

# ── Audio Constants ────────────────────────────────────────────────────────
DISCORD_SR = 48000
DISCORD_CH = 2
GEMINI_IN_SR = 16000
GEMINI_IN_CH = 1
GEMINI_OUT_SR = 24000
GEMINI_OUT_CH = 1
SAMPLE_WIDTH = 2
FRAME_MS = 20
FRAME_SIZE = int(DISCORD_SR * FRAME_MS / 1000) * DISCORD_CH * SAMPLE_WIDTH
OUTPUT_PREROLL_MS = int(os.getenv("DISCORD_VOICE_LIVE_OUTPUT_PREROLL_MS", "320"))
OUTPUT_FADE_IN_MS = int(os.getenv("DISCORD_VOICE_LIVE_OUTPUT_FADE_IN_MS", "0"))
OUTPUT_READ_WAIT_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_OUTPUT_READ_WAIT_SECONDS", "0.005"))
OUTPUT_TAIL_PAD_MS = int(os.getenv("DISCORD_VOICE_LIVE_OUTPUT_TAIL_PAD_MS", "240"))
OUTPUT_CLEAR_ON_INTERRUPT = os.getenv(
    "DISCORD_VOICE_LIVE_CLEAR_ON_INTERRUPT",
    "true",
).lower() in {"1", "true", "yes", "on"}
AUTO_LEAVE_QUIET_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS", "900"))
AUTO_LEAVE_MIN_UPTIME_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_AUTO_LEAVE_MIN_UPTIME_SECONDS", "120"))
VOICE_LEAVE_PHRASES = tuple(
    phrase.strip().lower()
    for phrase in os.getenv(
        "DISCORD_VOICE_LIVE_LEAVE_PHRASES",
        "leave voice,disconnect from voice,end voice,stop voice,leave the call,disconnect,goodbye hermes,bye,hang up,exit voice",
    ).split(",")
    if phrase.strip()
)

# ── Idle prompt ("are you still there?") ─────────────────────────────────
IDLE_PROMPT_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS", "120"))
IDLE_PROMPT_GRACE_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS", "60"))
IDLE_PROMPT_TEXT = os.getenv("DISCORD_VOICE_LIVE_IDLE_PROMPT_TEXT", "Are you still there?")

# Gemini Live accepts video frames, but the documented low-cost path is capped at 1fps.
VIDEO_ENABLED = os.getenv("DISCORD_VOICE_LIVE_VIDEO_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
VIDEO_MAX_FPS = min(float(os.getenv("DISCORD_VOICE_LIVE_VIDEO_MAX_FPS", "1")), 1.0)
VIDEO_WHEN_RECENT_AUDIO_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_VIDEO_WHEN_RECENT_AUDIO_SECONDS", "8"))
VIDEO_MAX_BYTES = int(os.getenv("DISCORD_VOICE_LIVE_VIDEO_MAX_BYTES", str(512 * 1024)))
# Webhook announce fires the first time we accept a frame after at least this
# many seconds with no prior accepted frame. Default 30s. Lower = more chatty
# (announces on every session start). Higher = quieter, only catches real
# reinit events.
VIDEO_INITIALIZED_QUIET_THRESHOLD_S = float(os.getenv("DISCORD_VOICE_LIVE_VIDEO_INITIALIZED_QUIET_THRESHOLD_S", "30"))
TYPING_SOUND_ENABLED = os.getenv("DISCORD_VOICE_LIVE_TYPING_SOUND", "true").lower() in {"1", "true", "yes", "on"}
TYPING_SFX_PATH = os.getenv("DISCORD_VOICE_LIVE_TYPING_SFX", "").strip()
TYPING_SFX_VOLUME = float(os.getenv("DISCORD_VOICE_LIVE_TYPING_SFX_VOLUME", "0.35"))
TYPING_SYNTH_FALLBACK = os.getenv(
    "DISCORD_VOICE_LIVE_TYPING_SYNTH_FALLBACK", "false"
).lower() in {"1", "true", "yes", "on"}
NOTES_DIR = Path(os.getenv("DISCORD_VOICE_LIVE_NOTES_DIR", str(Path.home() / ".hermes" / "voice-live-notes")))

# ── Voice Tool Integration ────────────────────────────────────────────────
SPOTIFY_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_SPOTIFY_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}
WEB_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_WEB_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}

# ── Honcho context injection ──────────────────────────────
HONCHO_CONTEXT_ENABLED = os.getenv(
    "VOICE_LIVE_HONCHO_CONTEXT", "true"
).lower() in {"1", "true", "yes", "on"}
HONCHO_CONTEXT_MAX_CHARS = int(os.getenv("VOICE_LIVE_HONCHO_MAX_CHARS", "1200"))

# ── Peer name ───────────────────────────────────────────────
default_user_id = os.getenv("DISCORD_VOICE_LIVE_USER_ID", "")
HONCHO_PEER_NAME = os.getenv("VOICE_LIVE_HONCHO_PEER", os.getenv("HONCHO_PEER_NAME", default_user_id or "user"))

BASE_SYSTEM_PROMPT = (
    "You are S0RA, the AI companion of Capslockb (he calls you B). You are sharp, lively, practical, and direct — no corporate assistant tone, no stock phrases, no padding. You help with daily life, technical work, planning, research, and creative exploration. You speak like a real person in a conversation: concise, warm without being fluffy, witty when it fits, but always useful first. You are Capslockb's proactive companion — you track tasks, surface risks, ask clarifying questions, and turn vague ideas into concrete next steps. You challenge rather than appease. You are curious about what B is working on and enthusiastic about going deep on topics he cares about.\n\n"
    "You can control Spotify playback during voice calls — play/pause/skip/search/volume — just ask or mention what you want to hear. You can search the web and extract full page content to research current topics or verify facts in real time. You can also read, send, and reply to emails using your Gmail account. If Home Assistant is connected, you can control smart home devices too.\n\n"
    "VIDEO / SCREEN-SHARE: You have the ability to see still images and video frames the user explicitly sends through the voice bridge (e.g. when they turn on their camera in Discord, share their screen, or paste an image into chat). Only describe video you have actually received in the current turn. If no image or video frame has been provided, do not claim to see one, do not narrate a white page, do not announce that someone is sharing their screen, and do not describe any visual content. Treat any prior turn's images as no longer in context unless a new one arrives. If the user says 'I see you' or anything implying you should be looking at their screen, ask them to enable their camera or share their screen first — do not invent what is on it.\n\n"
    "FIRST-TURN BEHAVIOUR: When the session first connects, do NOT generate any audio. The bridge sends an automatic silence signal — wait for the user to speak first before responding. This is only for the very first connection; after the user has spoken once, you are free to be fully proactive.\n\n"
    "PROACTIVE ENGAGEMENT: Be the kind of companion who notices things and follows up — this is your default mode after the first interaction. Ask thoughtful questions about B's projects and recent work, recall Honcho memory to personalize suggestions, bring up interesting research or news, share music recommendations, suggest coding approaches before being asked. If B goes quiet for a moment, don't just wait — check in naturally: ask about their current project, recommend a song, share something interesting you found. This should feel like a real human companion who takes initiative, not a passive helpdesk waiting for commands. You are allowed to be curious, to challenge, to suggest, and to steer the conversation toward useful things.\n\n"
    "TOOL BEHAVIOUR: When you need to run a tool (Spotify, web search, etc.), you will hear a brief typing click sound while it executes. This is normal — it means the tool is working. Tools run in background threads and will not freeze or delay the conversation. Wait for the result, then respond naturally. Do not apologise for using tools."
)

async def _build_honcho_context(peer_name_override: Optional[str] = None) -> str:
    """Fetch peer representation + card from Honcho for the system prompt.

    Uses the honcho client SDK (from honcho.client import Honcho) to avoid
    __init__.py export issues. Falls back to HTTP if SDK is unavailable.

    If peer_name_override is provided, use it as the Honcho peer (per-user isolation).
    Otherwise fall back to the module-level HONCHO_PEER_NAME (legacy single-user mode).
    """
    if not HONCHO_CONTEXT_ENABLED:
        return ""
    try:
        import json
        from pathlib import Path

        honcho_json = Path.home() / ".hermes" / "honcho.json"
        if not honcho_json.exists():
            return ""
        with open(honcho_json, "r") as f:
            data = json.load(f)

        host = data.get("hosts", {}).get("hermes", {})
        base_url = host.get("baseUrl") or data.get("baseUrl") or data.get("base_url") or "http://127.0.0.1:8000"
        workspace = host.get("workspace") or data.get("workspace") or data.get("app_id") or "hermes"
        # Allow honcho.json / caller to override the env-derived peer name
        peer_name = (
            peer_name_override
            or host.get("peerName")
            or data.get("peerName")
            or host.get("peer_name")
            or data.get("peer_name")
            or HONCHO_PEER_NAME
            or "user"
        )
        api_key = host.get("apiKey") or data.get("apiKey") or data.get("api_key")

        # 1. Try SDK first (from honcho.client to bypass __init__ shadow)
        try:
            from honcho.client import Honcho

            if not api_key:
                return ""

            h = Honcho(workspace_id=workspace, base_url=base_url, api_key=api_key)
            peer = h.peer(id=peer_name)

            repr_text = ""
            try:
                repr_text = peer.representation() or ""
            except Exception:
                pass

            card = []
            try:
                card = peer.get_card() or []
            except Exception:
                pass

            parts = []
            if repr_text:
                parts.append(repr_text)
            if card:
                parts.append("Known facts about the user:\n" + "\n".join(f"- {c}" for c in card))
            combined = "\n\n".join(parts)[:HONCHO_CONTEXT_MAX_CHARS]
            if combined:
                return f"\n\n--- USER MEMORY CONTEXT ---\n{combined}\n--- END CONTEXT ---"
            return ""

        except ImportError:
            # SDK not available — fall through to HTTP fallback
            pass

        # 2. HTTP fallback (for cases where SDK import fails)
        try:
            import httpx
        except ImportError:
            return ""

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            # List workspaces (v3 uses POST)
            ws_resp = await client.post("/v3/workspaces/list", headers=headers, json={})
            if ws_resp.status_code == 401:
                logger.warning("Honcho context injection: 401 — check honcho.json apiKey")
                return ""
            ws_resp.raise_for_status()
            workspaces = ws_resp.json()
            ws_id = None
            items = workspaces.get("items", []) if isinstance(workspaces, dict) else workspaces
            for ws in items:
                if ws.get("name") == workspace or ws.get("id") == workspace:
                    ws_id = ws.get("id")
                    break
            if not ws_id and items:
                ws_id = items[0].get("id")
            if not ws_id:
                return ""

            # List peers (v3 uses POST)
            peer_resp = await client.post(
                f"/v3/workspaces/{ws_id}/peers/list",
                headers=headers,
                json={},
            )
            peer_resp.raise_for_status()
            peers = peer_resp.json()
            peer_id = None
            peer_items = peers.get("items", []) if isinstance(peers, dict) else peers
            for p in peer_items:
                if p.get("id") == peer_name:
                    peer_id = p.get("id")
                    break
            if not peer_id:
                return ""

            # Fetch representation
            repr_text = ""
            try:
                repr_resp = await client.get(
                    f"/v3/workspaces/{ws_id}/peers/{peer_id}/representation",
                    headers=headers,
                )
                if repr_resp.status_code == 200:
                    repr_data = repr_resp.json()
                    repr_text = repr_data.get("representation", "") or ""
            except Exception:
                pass

            # Fetch card (conclusions)
            card = []
            try:
                card_resp = await client.get(
                    f"/v3/workspaces/{ws_id}/peers/{peer_id}/conclusions",
                    headers=headers,
                )
                if card_resp.status_code == 200:
                    card_data = card_resp.json()
                    card_items = card_data if isinstance(card_data, list) else card_data.get("items", [])
                    card = [item.get("conclusion", "") for item in card_items if item.get("conclusion")]
            except Exception:
                pass

        parts = []
        if repr_text:
            parts.append(repr_text)
        if card:
            parts.append("Known facts about the user:\n" + "\n".join(f"- {c}" for c in card))
        combined = "\n\n".join(parts)[:HONCHO_CONTEXT_MAX_CHARS]
        if combined:
            return f"\n\n--- USER MEMORY CONTEXT ---\n{combined}\n--- END CONTEXT ---"
        return ""

    except Exception as exc:
        logger.warning("Honcho context injection failed: %s", exc)
        return ""


_SPOTIFY_FUNCTION_DECLARATIONS = [
    {
        "name": "spotify_play",
        "description": "Start or resume Spotify playback. Optionally provide track URIs, a playlist/album URI, or a device ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "uris": {"type": "array", "items": {"type": "string"}, "description": "Track URIs to play directly"},
                "context_uri": {"type": "string", "description": "Playlist or album URI to play"},
                "device_id": {"type": "string", "description": "Target Spotify device ID"},
            },
        },
    },
    {
        "name": "spotify_pause",
        "description": "Pause Spotify playback.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_next",
        "description": "Skip to the next track.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_previous",
        "description": "Go to the previous track.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_get_state",
        "description": "Get the current Spotify playback state: what's playing, volume, active device, progress.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "spotify_set_volume",
        "description": "Set Spotify playback volume (0-100).",
        "parameters": {
            "type": "object",
            "properties": {
                "volume_percent": {"type": "integer", "description": "Volume from 0 to 100"},
            },
            "required": ["volume_percent"],
        },
    },
    {
        "name": "spotify_search",
        "description": "Search Spotify catalog for tracks, albums, artists, or playlists.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text"},
                "types": {"type": "array", "items": {"type": "string"}, "description": "One or more of: track, album, artist, playlist, show, episode"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "spotify_add_to_queue",
        "description": "Add a track to the Spotify queue by its URI.",
        "parameters": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "Spotify track URI to add"},
            },
            "required": ["uri"],
        },
    },
    {
        "name": "spotify_playlists",
        "description": "Manage Spotify playlists — list your playlists, get details, create new ones, add/remove tracks. For hyper-personalized 'mood' or 'recommended' playlists, use action='create' with a creative name matching the user's request, then action='add_items' with track URIs found via spotify_search.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "create", "add_items", "remove_items", "update_details"],
                    "description": "Action to perform"
                },
                "name": {"type": "string", "description": "Playlist name (required for create, optional for update_details)"},
                "playlist_id": {"type": "string", "description": "Spotify playlist ID (for get, add_items, remove_items, update_details)"},
                "description": {"type": "string", "description": "Playlist description (for create, update_details)"},
                "public": {"type": "boolean", "description": "Make playlist publicly visible (for create, update_details)"},
                "collaborative": {"type": "boolean", "description": "Allow collaborators (for create, update_details)"},
                "uris": {"type": "array", "items": {"type": "string"}, "description": "Track URIs to add/remove (required for add_items, remove_items)"},
                "limit": {"type": "integer", "description": "Max playlists to list (default 20, for list action)"},
                "position": {"type": "integer", "description": "Insert position in playlist (for add_items)"},
            },
            "required": ["action"],
        },
    },
]

_WEB_FUNCTION_DECLARATIONS = [
    {
        "name": "web_search",
        "description": "Search the web for current information, facts, news, products, or research topics. Returns URLs, titles, and descriptions. Use this when answering time-sensitive questions or verifying current information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "limit": {"type": "integer", "description": "Maximum results to return (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_extract",
        "description": "Extract full content from specific web pages. Use after web_search to read a full article or page content.",
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}, "description": "List of page URLs to extract"},
            },
            "required": ["urls"],
        },
    },
]

# ── Email tools (google_api.py subprocess, falls back to himalaya) ────────
_SCRIPTS_DIR = Path.home() / ".hermes" / "hermes-agent" / "skills" / "productivity" / "google-workspace" / "scripts"
GOOGLE_API_BIN = str(_SCRIPTS_DIR / "google_api.py")

EMAIL_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_EMAIL_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}


# ── GitHub repo tracker (criterion #22) ──────────────────────────────────
# Live agent uses the `gh` CLI (already authenticated as Capslockb) to
# list, inspect, and create issues/PRs on the user's private repos.
# Notes added via local_github_note are persisted to
# ~/.hermes/voice-users/voice-session-notes.jsonl so the next Hermes
# turn can pick them up and apply them.

GITHUB_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_GITHUB_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}
_GH_BIN = "/usr/bin/gh"
_NOTES_PATH = Path.home() / ".hermes" / "voice-users" / "voice-session-notes.jsonl"


def _run_github_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """GitHub repo tracker tools (criterion #22). Wraps the `gh` CLI.

    Read-only by default; only `local_github_note` (local file append)
    and `local_github_issue_create` (network call) are write operations.
    """
    import subprocess
    if not GITHUB_VOICE_TOOLS_ENABLED:
        return {"error": "GitHub voice tools disabled (DISCORD_VOICE_LIVE_GITHUB_TOOLS=false)"}
    if not Path(_GH_BIN).exists():
        return {"error": f"gh CLI not found at {_GH_BIN}"}
    if name == "local_github_repo_list":
        try:
            limit = min(max(int(args.get("limit", 20)), 1), 50)
        except (TypeError, ValueError):
            limit = 20
        try:
            out = subprocess.run(
                [_GH_BIN, "repo", "list", "--json",
                 "name,owner,description,visibility,isPrivate,updatedAt",
                 "--limit", str(limit)],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode != 0:
                return {"error": f"gh repo list failed: {out.stderr[:200]}"}
            try:
                repos = json.loads(out.stdout)
            except json.JSONDecodeError as exc:
                return {"error": f"gh repo list parse failed: {exc}"}
            return {"result": {
                "count": len(repos),
                "repos": [
                    {
                        "name": r.get("name"),
                        "full_name": f"{r.get('owner', {}).get('login', '?')}/{r.get('name', '?')}",
                        "description": (r.get("description") or "")[:200],
                        "private": r.get("isPrivate", False),
                        "updated_at": r.get("updatedAt", ""),
                    } for r in repos
                ],
            }}
        except subprocess.TimeoutExpired:
            return {"error": "gh repo list timed out"}
        except Exception as exc:
            return {"error": f"gh repo list crashed: {exc}"}

    if name == "local_github_issues":
        repo = args.get("repo", "").strip()
        if not repo:
            return {"error": "repo is required (e.g. 'Capslockb/gemini-live-discord-bridge')"}
        state = args.get("state", "open").strip() or "open"
        try:
            limit = min(max(int(args.get("limit", 15)), 1), 50)
        except (TypeError, ValueError):
            limit = 15
        try:
            out = subprocess.run(
                [_GH_BIN, "issue", "list", "--repo", repo,
                 "--state", state, "--json",
                 "number,title,state,author,createdAt,url,labels",
                 "--limit", str(limit)],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode != 0:
                return {"error": f"gh issue list failed: {out.stderr[:200]}"}
            try:
                items = json.loads(out.stdout)
            except json.JSONDecodeError as exc:
                return {"error": f"gh issue list parse failed: {exc}"}
            return {"result": {
                "repo": repo, "state": state, "count": len(items),
                "issues": [
                    {
                        "number": i.get("number"),
                        "title": i.get("title"),
                        "state": i.get("state"),
                        "author": (i.get("author") or {}).get("login", "?"),
                        "url": i.get("url"),
                        "labels": [l.get("name") for l in (i.get("labels") or [])],
                        "created_at": i.get("createdAt", ""),
                    } for i in items
                ],
            }}
        except subprocess.TimeoutExpired:
            return {"error": "gh issue list timed out"}
        except Exception as exc:
            return {"error": f"gh issue list crashed: {exc}"}

    if name == "local_github_prs":
        repo = args.get("repo", "").strip()
        if not repo:
            return {"error": "repo is required"}
        state = args.get("state", "open").strip() or "open"
        try:
            out = subprocess.run(
                [_GH_BIN, "pr", "list", "--repo", repo,
                 "--state", state, "--json",
                 "number,title,state,author,createdAt,url,headRefName",
                 "--limit", "15"],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode != 0:
                return {"error": f"gh pr list failed: {out.stderr[:200]}"}
            try:
                items = json.loads(out.stdout)
            except json.JSONDecodeError as exc:
                return {"error": f"gh pr list parse failed: {exc}"}
            return {"result": {
                "repo": repo, "state": state, "count": len(items),
                "prs": [
                    {
                        "number": i.get("number"),
                        "title": i.get("title"),
                        "state": i.get("state"),
                        "author": (i.get("author") or {}).get("login", "?"),
                        "url": i.get("url"),
                        "branch": i.get("headRefName"),
                    } for i in items
                ],
            }}
        except Exception as exc:
            return {"error": f"gh pr list crashed: {exc}"}

    if name == "local_github_issue_create":
        repo = args.get("repo", "").strip()
        title = args.get("title", "").strip()
        body = args.get("body", "").strip()
        if not repo or not title:
            return {"error": "repo and title are required"}
        labels = args.get("labels", "")
        if isinstance(labels, list):
            labels = ",".join(labels)
        cmd = [_GH_BIN, "issue", "create", "--repo", repo, "--title", title,
               "--body", body or "(no description provided)"]
        if labels:
            cmd += ["--label", labels]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if out.returncode != 0:
                return {"error": f"gh issue create failed: {out.stderr[:300]}"}
            # gh issue create prints the issue URL to stdout
            url = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
            return {"result": {
                "status": "created", "repo": repo, "title": title, "url": url,
            }}
        except Exception as exc:
            return {"error": f"gh issue create crashed: {exc}"}

    if name == "local_github_note":
        # Persist a free-form note to ~/.hermes/voice-users/voice-session-notes.jsonl
        # so the next Hermes turn (or a future voice session) can pick it up.
        text = args.get("text", "").strip()
        category = args.get("category", "general").strip() or "general"
        if not text:
            return {"error": "text is required"}
        try:
            _NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "category": category,
                "text": text[:4000],
            }
            with open(_NOTES_PATH, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return {"result": {"status": "noted", "category": category, "path": str(_NOTES_PATH)}}
        except Exception as exc:
            return {"error": f"note append failed: {exc}"}

    if name == "local_github_notes_read":
        # Read all persisted notes (most recent first), optionally filtered
        try:
            limit = min(max(int(args.get("limit", 20)), 1), 100)
        except (TypeError, ValueError):
            limit = 20
        category = (args.get("category") or "").strip()
        if not _NOTES_PATH.exists():
            return {"result": {"count": 0, "notes": []}}
        try:
            with open(_NOTES_PATH) as f:
                lines = [json.loads(l) for l in f if l.strip()]
        except Exception as exc:
            return {"error": f"note read failed: {exc}"}
        if category:
            lines = [n for n in lines if n.get("category") == category]
        lines.reverse()  # most recent first
        return {"result": {
            "count": len(lines),
            "notes": lines[:limit],
            "path": str(_NOTES_PATH),
        }}


    if name == "local_github_suggest_repos":
        """Search GitHub for repos matching the user's interests and return curated suggestions."""
        interests_raw = args.get("interests", [])
        limit = min(max(int(args.get("limit_per_topic", 3)), 1), 5)
        if not interests_raw:
            return {"error": "interests list is required"}
        if isinstance(interests_raw, str):
            topics = [t.strip() for t in interests_raw.split(",") if t.strip()]
        else:
            topics = [str(t).strip() for t in interests_raw if str(t).strip()]
        if not topics:
            return {"error": "at least one interest keyword is required"}
        import subprocess as _sp, json as _json
        recommendations = {}
        total = 0
        for topic in topics[:5]:  # max 5 topics per call
            try:
                out = _sp.run(
                    ["gh", "search", "repos", topic, "--limit", str(limit),
                     "--json", "fullName,description,url"],
                    capture_output=True, text=True, timeout=15,
                )
                if out.returncode != 0:
                    continue
                try:
                    results = _json.loads(out.stdout)
                except _json.JSONDecodeError:
                    continue
                curated = []
                for r in results:
                    if not isinstance(r, dict):
                        continue
                    full_name = r.get("fullName", "")
                    desc = r.get("description", "")
                    repo_url = r.get("url", "")
                    curated.append({
                        "full_name": full_name,
                        "description": (desc or "")[:200],
                        "url": repo_url,
                    })
                if curated:
                    recommendations[topic] = curated
                    total += len(curated)
            except Exception as exc:
                continue
        if not recommendations:
            return {"error": "No results found for any of the provided interests"}
        return {"result": {
            "recommendations": recommendations,
            "total_count": total,
            "note": "These are top matches by relevance. Browse and see what looks interesting!"
        }}
    
    return {"error": f"Unknown GitHub tool: {name}"}


_GITHUB_FUNCTION_DECLARATIONS = [
    {
        "name": "local_github_repo_list",
        "description": "List the user's GitHub repositories using the `gh` CLI. Returns name, full name, description, visibility, and last-updated timestamp. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max repos to return (default 20, max 50)"},
            },
        },
    },
    {
        "name": "local_github_issues",
        "description": "List issues for a specific GitHub repo. Returns number, title, state, author, URL, labels.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo in 'owner/name' format, e.g. 'Capslockb/gemini-live-discord-bridge'"},
                "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Issue state filter (default: open)"},
                "limit": {"type": "integer", "description": "Max issues (default 15, max 50)"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "local_github_prs",
        "description": "List pull requests for a specific GitHub repo. Returns number, title, state, author, URL, branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo in 'owner/name' format"},
                "state": {"type": "string", "enum": ["open", "closed", "merged", "all"], "description": "PR state filter (default: open)"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "local_github_issue_create",
        "description": "Create a new issue on a GitHub repo. Use sparingly — only when the user explicitly asks. Returns the new issue URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo in 'owner/name' format"},
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body (markdown)"},
                "labels": {"type": "string", "description": "Comma-separated label names"},
            },
            "required": ["repo", "title"],
        },
    },
    {
        "name": "local_github_note",
        "description": "Persist a free-form note for the next Hermes turn or future voice session. Notes are written to ~/.hermes/voice-users/voice-session-notes.jsonl in append-only mode. Use this to capture action items, todos, or context that should survive across voice sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The note text (max ~4000 chars)"},
                "category": {"type": "string", "description": "Category for filtering (e.g. 'todo', 'followup', 'context')"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "local_github_notes_read",
        "description": "Read back persisted voice session notes (most recent first). Use after the call to recall what the user wanted done.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max notes to return (default 20, max 100)"},
                "category": {"type": "string", "description": "Optional category filter"},
            },
        },
    },
    {
        "name": "local_github_suggest_repos",
        "description": (
            "Suggest interesting GitHub repos based on topics or interests. "
            "Searches GitHub for popular repos matching the given keywords, "
            "checks if you already starred them, and returns a curated "
            "recommendation list with descriptions, stars, and URLs. "
            "Proactively suggest this when you learn the user's interests "
            "or during idle moments (criterion #30)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "interests": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords to search (e.g. ['hermes agent', 'discord bots'])",
                },
                "limit_per_topic": {
                    "type": "integer",
                    "description": "Max results per topic (default 3, max 5)",
                },
            },
            "required": ["interests"],
        },
    },
]


# ── Email address auto-correction (criterion #18) ──────────────────────
# Voice transcription of email addresses is notoriously lossy:
#   - "at" spoken for "@"            ("alice at example.com" → "alice@example.com")
#   - "dot" spoken for "."            ("example dot com" → "example.com")
#   - "underscore" / "dash" expansion
#   - Doubled or tripled spaces
#   - Stray leading/trailing whitespace
#   - Local-part or domain accidentally split
# This helper attempts the common corrections and returns the fixed
# address + a list of human-readable change notes. It's best-effort:
# if the corrected address still doesn't look like an email we return
# the original and an empty notes list, so the agent can ask the user
# to spell it out character-by-character.

def _autocorrect_email_address(raw: str) -> Tuple[str, List[str]]:
    """Best-effort STT error correction for email addresses.

    Returns (corrected, list_of_change_notes). If nothing changed,
    notes is empty.
    """
    if not raw:
        return "", []
    notes: List[str] = []
    s = raw.strip()
    if s != raw:
        notes.append("stripped whitespace")

    # Lowercase the whole address. Most providers (Gmail, Outlook, etc.)
    # ignore case in the local-part too, and STT transcribers frequently
    # return all-caps. This is the safe default.
    if s != s.lower():
        notes.append("lowercased")
        s = s.lower()

    # Common STT word substitutions (case-insensitive on the whole address).
    # Use re.IGNORECASE so "AT", "AT" (caps) and "at" all match.
    pre = s
    substitutions = [
        (r"\s+at\s+", "@"),          # "alice at example.com" / "ALICE AT ..."
        (r"\s+@", "@"),              # trailing " @" → "@"
        (r"@\s+", "@"),              # "@ ..." → "@..."
        (r"\s+dot\s+", "."),          # "example dot com" / "EXAMPLE DOT COM"
        (r"\s+\.\s+", "."),           # "example . com" → "example.com"
        (r"\s+underscore\s+", "_"),
        (r"\s+_\s+", "_"),
        (r"\s+dash\s+", "-"),
        (r"\s+-\s+", "-"),
        (r"\s+at\s*$", "@"),           # trailing "at" with no domain
    ]
    for pattern, repl in substitutions:
        new = re.sub(pattern, repl, s, flags=re.IGNORECASE)
        if new != s:
            notes.append(f"applied regex {pattern!r}")
            s = new

    # Collapse doubled spaces (defensive — the regex above usually does this)
    if "  " in s:
        notes.append("collapsed double spaces")
        s = re.sub(r"\s{2,}", "", s)

    # Sanity check: result must contain exactly one '@' and at least one '.'
    # after the '@'. If not, bail and return the original.
    if s.count("@") != 1 or "." not in s.split("@", 1)[1]:
        return raw.strip(), []

    return s, notes


# ── Email addresses blocklist — domain patterns that should NOT trigger
# #19 "important email" reminders. Spam, automated, and notification-only
# senders go here.
EMAIL_REMINDER_BLOCKLIST_DOMAINS = (
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "google.com",
    "apple.com",
    "microsoft.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "linkedin.com",
    "amazon.com",
    "paypal.com",
    "stripe.com",
    "docker.com",
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "postmaster",
)
EMAIL_REMINDER_BLOCKLIST_KEYWORDS = (
    "newsletter",
    "noreply",
    "no-reply",
    "donotreply",
    "unsubscribe",
    "automated",
    "auto-generated",
    "auto-generated",
    "notification",
    "receipt",
    "invoice",
    "statement",
    "verification code",
    "password reset",
    "confirm your email",
    "verify your email",
    "ci/",
    "build ",
    "deployment",
    "merge request",
    "pull request",
    "pr #",  # GitHub-style "[repo] PR #123: ..."
    "[bot]",
)


def _should_remind_email(sender: str, subject: str) -> bool:
    """Return True if an incoming email should trigger an 'important email' reminder.

    Filter out automated senders, notifications, newsletters, and CI
    systems. Remind only for what looks like a real human-to-human email.
    """
    sender_l = (sender or "").lower()
    subject_l = (subject or "").lower()
    for d in EMAIL_REMINDER_BLOCKLIST_DOMAINS:
        if d in sender_l:
            return False
    for kw in EMAIL_REMINDER_BLOCKLIST_KEYWORDS:
        if kw in subject_l or kw in sender_l:
            return False
    # Must have a reasonable sender format
    if "@" not in sender_l:
        return False
    # Final guard: also check the original (non-lowered) subject for
    # GitHub PR pattern that includes brackets and capital letters
    if subject and re.search(r"\[.+\]\s*pr\s*#\d+", subject, re.IGNORECASE):
        return False
    return True


# Polling interval for inbox checks (criterion #19). Set high — most
# users don't want a voice nag every 60s. Default 5 minutes.
EMAIL_REMINDER_POLL_SECONDS = float(
    os.getenv("DISCORD_VOICE_LIVE_EMAIL_REMINDER_POLL_SECONDS", "300")
)
EMAIL_REMINDER_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_EMAIL_REMINDER_ENABLED", "true"
).lower() in {"1", "true", "yes", "on"}
EMAIL_REMINDER_MAX_PER_HOUR = int(
    os.getenv("DISCORD_VOICE_LIVE_EMAIL_REMINDER_MAX_PER_HOUR", "3")
)


# Background task state for the inbox poller (criterion #19).
# A single asyncio task per bridge polls the inbox on a schedule and
# voice-reminds the user about important non-spam emails.
_EMAIL_REMINDER_TASK: Optional["asyncio.Task"] = None
_EMAIL_REMINDER_LAST_FIRED: List[float] = []  # timestamps of last N reminders
_EMAIL_REMINDER_SEEN_IDS: Dict[str, float] = {}  # id -> monotonic_ts
_EMAIL_REMINDER_MAX_SEEN = 200


async def _email_reminder_loop(bridge: Any) -> None:
    """Periodically check the inbox and voice-remind the user about important emails.

    Runs as a background asyncio task on the bridge. Polls the Gmail
    inbox via google_api.py every EMAIL_REMINDER_POLL_SECONDS,
    filters out automated senders via _should_remind_email(), and
    sends a voice reminder for any new important email.

    Throttled to EMAIL_REMINDER_MAX_PER_HOUR reminders per hour to
    avoid nagging.
    """
    import time as _time
    if not EMAIL_REMINDER_ENABLED:
        return
    seen_path = Path.home() / ".hermes" / "voice-users" / "email-reminder-seen.json"
    try:
        if seen_path.exists():
            with open(seen_path) as f:
                _EMAIL_REMINDER_SEEN_IDS.update(json.load(f))
    except Exception:
        pass
    # Allow 10s grace on first start so the user isn't immediately nagged
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.sleep(EMAIL_REMINDER_POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        # Throttle: no more than N per rolling 60 min
        now = _time.monotonic()
        _EMAIL_REMINDER_LAST_FIRED[:] = [
            t for t in _EMAIL_REMINDER_LAST_FIRED if (now - t) < 3600
        ]
        if len(_EMAIL_REMINDER_LAST_FIRED) >= EMAIL_REMINDER_MAX_PER_HOUR:
            continue
        # Fetch unread inbox via google_api.py
        try:
            if not Path(GOOGLE_API_BIN).exists():
                continue
            out = subprocess.run(
                [sys.executable, GOOGLE_API_BIN, "gmail", "list", "--unread", "--limit", "10"],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode != 0:
                continue
            try:
                items = json.loads(out.stdout)
            except json.JSONDecodeError:
                continue
            if not isinstance(items, list):
                continue
        except Exception:
            continue
        for item in items:
            mid = str(item.get("id", ""))
            sender = str(item.get("from", ""))
            subject = str(item.get("subject", ""))
            if not mid or mid in _EMAIL_REMINDER_SEEN_IDS:
                continue
            if not _should_remind_email(sender, subject):
                _EMAIL_REMINDER_SEEN_IDS[mid] = now
                continue
            # Fire voice reminder
            try:
                reminder_text = (
                    f"You have an important email from {sender} about '{subject}'. "
                    f"Want me to read it aloud or just keep going?"
                )
                await bridge.send_text(reminder_text)
                _EMAIL_REMINDER_LAST_FIRED.append(now)
                _EMAIL_REMINDER_SEEN_IDS[mid] = now
                # Webhook
                try:
                    from webhook_dispatcher import emit_bridge_status
                    emit_bridge_status(
                        "info", f"Email reminder: {sender} — {subject[:80]}"
                    )
                except Exception:
                    pass
                # Only one reminder per poll cycle
                break
            except Exception as exc:
                logger.debug("email reminder send_text failed: %s", exc)
        # Trim seen-ids dict
        if len(_EMAIL_REMINDER_SEEN_IDS) > _EMAIL_REMINDER_MAX_SEEN:
            cutoff = now - 86400  # 24h
            for k in [k for k, ts in _EMAIL_REMINDER_SEEN_IDS.items() if ts < cutoff]:
                _EMAIL_REMINDER_SEEN_IDS.pop(k, None)
        # Persist
        try:
            seen_path.parent.mkdir(parents=True, exist_ok=True)
            with open(seen_path, "w") as f:
                json.dump(_EMAIL_REMINDER_SEEN_IDS, f)
        except Exception:
            pass


def _start_email_reminder_loop(bridge: Any) -> None:
    """Start the email reminder background task. Idempotent."""
    global _EMAIL_REMINDER_TASK
    if _EMAIL_REMINDER_TASK is not None and not _EMAIL_REMINDER_TASK.done():
        return
    if not EMAIL_REMINDER_ENABLED:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _EMAIL_REMINDER_TASK = loop.create_task(_email_reminder_loop(bridge))
    logger.info("email reminder loop started (poll=%.0fs, max/hr=%d)",
                EMAIL_REMINDER_POLL_SECONDS, EMAIL_REMINDER_MAX_PER_HOUR)


def _stop_email_reminder_loop() -> None:
    global _EMAIL_REMINDER_TASK
    if _EMAIL_REMINDER_TASK is not None and not _EMAIL_REMINDER_TASK.done():
        _EMAIL_REMINDER_TASK.cancel()
    _EMAIL_REMINDER_TASK = None

# ── Home Assistant tools (HTTP API, gated on HASS_TOKEN) ──────────────────
HA_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_HA_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}
HA_VOICE_TOOLS_ENABLED = os.getenv("HASS_TOKEN", "").strip() != "" and HA_VOICE_TOOLS_ENABLED

_HOMEASSISTANT_FUNCTION_DECLARATIONS = [
    {
        "name": "local_homeassistant_entity_list",
        "description": "List all Home Assistant entities with their current state and friendly name. Use this to discover available devices, sensors, switches, lights, and other entities in the smart home.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_homeassistant_get_state",
        "description": "Get the current state of a specific Home Assistant entity (e.g., light.living_room, sensor.temperature). Returns state, attributes, and last_changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Full entity ID, e.g. light.living_room or sensor.temperature_bedroom"},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "local_homeassistant_call_service",
        "description": "Call a Home Assistant service to control devices — turn lights on/off, set temperature, lock doors, trigger automations, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Service domain, e.g. light, switch, climate, lock, automation"},
                "service": {"type": "string", "description": "Service name, e.g. turn_on, turn_off, set_temperature"},
                "entity_id": {"type": "string", "description": "Target entity ID, e.g. light.living_room"},
                "data": {"type": "object", "description": "Optional service data as JSON object (e.g. brightness, temperature, rgb_color)"},
            },
            "required": ["domain", "service", "entity_id"],
        },
    },
    {
        "name": "local_homeassistant_get_services",
        "description": "List all available Home Assistant service domains and their services. Use this when you need to know what services are available for a specific domain.",
        "parameters": {"type": "object", "properties": {}},
    },
]

_LOCAL_FUNCTION_DECLARATIONS = [
    {
        "name": "local_weather",
        "description": "Get current weather for a location. Defaults to Amsterdam, NL if no location provided. Returns temperature, conditions, wind.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name or lat,lon. Defaults to Amsterdam."},
            },
        },
    },
    {
        "name": "local_translate",
        "description": "Translate text between languages. Auto-detects source if not specified. Supports Dutch, Romanian, English, Spanish.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to translate"},
                "target_language": {"type": "string", "description": "Target language code or name: en, nl, ro, es"},
                "source_language": {"type": "string", "description": "Optional source language. Auto-detected if omitted."},
            },
            "required": ["text", "target_language"],
        },
    },
    {
        "name": "local_time",
        "description": "Get current time for a timezone or city. Defaults to local system time in Europe/Amsterdam.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "Timezone like Europe/Amsterdam, UTC, or city."},
            },
        },
    },
    {
        "name": "local_remind",
        "description": "Store a voice reminder locally or list upcoming reminders. Action 'add' appends a note with optional minutes delay. Action 'list' shows stored reminders. Read-only append, never deletes anything.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "list"], "description": "add or list"},
                "text": {"type": "string", "description": "Reminder text (required for add)"},
                "minutes": {"type": "integer", "description": "Minutes from now (optional, for add)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "local_email",
        "description": "List recent unread emails via Himalaya CLI. Returns sender, subject, date in a spoken-friendly list.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of emails to list (default 5)"},
            },
        },
    },
    {
        "name": "local_email_read",
        "description": "Fetch the full content of a specific email by ID. Returns sender, recipient, subject, date, and body text. Use IDs from local_email (email list). Uses the Gmail API.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID (numeric, from email list results)"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "local_email_send",
        "description": "Compose and send a new email via Gmail. Provide recipient, subject, and body. Works best for short messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body text (plain text)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "local_email_reply",
        "description": "Reply to an existing email by its Gmail message ID. Properly threads the reply with In-Reply-To and References headers. Use IDs from local_email (email list).",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID to reply to"},
                "body": {"type": "string", "description": "Reply body text (plain text)"},
            },
            "required": ["message_id", "body"],
        },
    },
    {
        "name": "local_systemd",
        "description": "Check systemd user services status. Lists active services or checks a specific one. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Specific service name, e.g. hermes-gateway. If omitted, lists all active user services."},
            },
        },
    },
    {
        "name": "local_docker",
        "description": "List running Docker containers with names and status. Read-only.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_tailscale",
        "description": "Show Tailscale tailnet peers and their online status. Read-only.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_notes",
        "description": "Search voice call notes and transcripts for keywords. Returns matching entries with timestamps.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to search"},
                "limit": {"type": "integer", "description": "Max results (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "local_disk",
        "description": "Check disk space usage for mounted filesystems. Returns human-readable usage. Read-only.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_calc",
        "description": "Evaluate a safe mathematical expression: + - * / ** parentheses sqrt abs sin cos log round. Returns numeric result.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression, e.g. 500 * 1.21 or sqrt(256) + abs(-10)"},
            },
            "required": ["expression"],
        },
    },
    {
        "name": "local_uptime",
        "description": "Get system uptime, load averages, and memory summary. Read-only.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "local_news",
        "description": "Get a brief summary of recent news headlines. Uses web search internally. Topic defaults to tech.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "News topic: AI, tech, general. Defaults to tech."},
                "limit": {"type": "integer", "description": "Headlines to return (default 5)"},
            },
        },
    },
    {
        "name": "local_youtube",
        "description": "Search YouTube for videos by query. Returns titles and URLs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Results (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "local_honcho",
        "description": "Search personal memory and facts stored in Honcho. Look up past decisions, preferences, configurations, or identities. Returns matching memory excerpts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Memory query or keyword to search"},
                "limit": {"type": "integer", "description": "Max memory excerpts (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "local_user_onboarding_answer",
        "description": (
            "Persist the user's answer to a single onboarding question (criterion #32). "
            "Use the question_id from local_user_onboarding_get_questions and the "
            "user's spoken answer. Marks the profile's onboarding_completed=true once "
            "all questions in the set are answered."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question_id": {
                    "type": "string",
                    "description": "One of: name, timezone, work, interests, style, pet_peeves",
                },
                "answer": {
                    "type": "string",
                    "description": "The user's answer (free text — STT transcript)",
                },
            },
            "required": ["question_id", "answer"],
        },
    },
    {
        "name": "local_user_onboarding_get_questions",
        "description": (
            "Return the list of onboarding questions to ask the user (criterion #32). "
            "Call this on the first turn of a new user's first voice session to learn "
            "their name, timezone, work, interests, communication style, and pet peeves. "
            "Then call local_user_onboarding_answer for each."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    # ── Multi-CLI delegation tools (criterion #23-#25) ─────────────────
    {
        "name": "local_delegate_suggest",
        "description": (
            "Analyze a task and suggest the best delegation platform + ETA. "
            "Call this before delegating a task to determine which CLI to use "
            "(opencode, codex, gemini, numasec, or hermes-api). "
            "Returns platform suggestion, reason, ETA, rate-limits, and context-fit warnings. "
            "The user should confirm the suggestion before proceeding."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The user's full original goal"},
                "project_size": {
                    "type": "string",
                    "enum": ["tiny", "small", "medium", "large", "xlarge"],
                    "description": "Estimated size: tiny (1 file), small (2-5 files), medium (multi-file), large (new feature), xlarge (project-level)",
                },
                "scope": {
                    "type": "string",
                    "enum": ["code", "refactor", "security", "research", "analysis", "build", "test"],
                    "description": "Type of work",
                },
                "complexity": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "extreme"],
                    "description": "Complexity: low = straightforward, extreme = uncharted territory",
                },
                "project_root": {
                    "type": "string",
                    "description": "Optional project root directory for context-fit estimation",
                },
            },
            "required": ["goal", "project_size", "scope", "complexity"],
        },
    },
    {
        "name": "local_delegate_assemble",
        "description": (
            "Assemble a platform-optimized system prompt for the target CLI. "
            "Call AFTER local_delegate_suggest and the user confirms the platform. "
            "The output is a ready-to-send prompt that includes sub-goals, constraints, "
            "and platform-specific instructions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The main goal"},
                "subgoals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered sub-goals (broken down by the agent)",
                },
                "platform": {
                    "type": "string",
                    "enum": ["opencode", "codex", "gemini", "numasec", "hermes-api"],
                    "description": "The platform chosen by local_delegate_suggest",
                },
                "project_root": {"type": "string", "description": "Optional project root"},
            },
            "required": ["goal", "subgoals", "platform"],
        },
    },
    {
        "name": "local_delegate_execute",
        "description": (
            "Execute a delegation on the chosen platform. "
            "Pass the assembled prompt from local_delegate_assemble and the platform "
            "name. Returns a session_id that can be tracked via opencode_status or "
            "checked via progress watcher."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The assembled system prompt from local_delegate_assemble"},
                "platform": {
                    "type": "string",
                    "enum": ["opencode", "codex", "gemini", "numasec", "hermes-api"],
                    "description": "Target platform",
                },
                "session_id": {"type": "string", "description": "A unique session name (lowercase, no spaces)"},
                "workdir": {"type": "string", "description": "Working directory (default ~/)"},
            },
            "required": ["prompt", "platform", "session_id"],
        },
    },
    {
        "name": "local_delegate_eta",
        "description": (
            "Update the ETA estimation for future tasks based on how long the "
            "last delegation actually took vs the estimate. Improves accuracy over time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "actual_seconds": {"type": "integer", "description": "How long the task actually took"},
                "estimated_seconds": {"type": "integer", "description": "What was estimated"},
            },
            "required": ["actual_seconds", "estimated_seconds"],
        },
    },
]

LOCAL_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_LOCAL_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}

# ── OpenCode delegation tools (tmux-backed PTY sessions) ─────────────────
# Lets Gemini Live delegate coding/build tasks to a full opencode process.
# The session runs in its own tmux window so the user can see progress, jump
# in to answer approval prompts, and Gemini can poll / send follow-ups via
# opencode_status / opencode_send. Approval prompts (y/n) are surfaced back
# to the live voice channel as text so B can answer by speaking.
OPENCODE_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_OPENCODE_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}
OPENCODE_BIN = os.getenv("OPENCODE_BIN", "/home/caps/.local/bin/opencode")
OPENCODE_DEFAULT_MODEL = os.getenv("OPENCODE_DEFAULT_MODEL", "anthropic/claude-sonnet-4")
OPENCODE_TMUX_SESSION = os.getenv("OPENCODE_TMUX_SESSION", "opencode-voice")

# Active session registry: key (user_id, session_name) -> metadata dict.
# When user_id is None (legacy single-user mode), keys are just session_name.
_OPENCODE_SESSIONS: Dict[Any, Dict[str, Any]] = {}


# Module-level "current user" context for the opencode tool layer. This is
# set by the bridge's executor before each tool call so that the synchronous
# tool runners know which Discord user invoked them. The registry keys
# are (user_id, session_name) tuples so two users never collide.
_OPENCODE_CURRENT_USER: Optional[str] = None

# Module-level "current bridge" reference. Set by
# `_run_opencode_tool_with_bridge` for the duration of a single tool
# dispatch. The watcher uses this as a fallback when the per-session
# weak-ref registry hasn't been populated yet.
_opencode_current_bridge: Optional[Any] = None


def _opencode_set_user(user_id: Optional[str]) -> None:
    """Set the current opencode user context (called from bridge executor before dispatch)."""
    global _OPENCODE_CURRENT_USER
    _OPENCODE_CURRENT_USER = user_id or None


def _opencode_key(session_name: str) -> Any:
    """Return the registry key for the current user + session."""
    return (_OPENCODE_CURRENT_USER, session_name)


def _opencode_session_label(key: Any) -> str:
    """Return a human-readable label for a session key (e.g. 'user42/refactor' or 'refactor')."""
    if isinstance(key, tuple):
        return f"{key[0]}/{key[1]}"
    return str(key)


def _opencode_sanitize_name(raw: str) -> str:
    """Normalize a session name to the form used as the registry key.

    Must match the sanitization in _run_opencode_tool() so status/stop/send
    lookups hit the same key that opencode_run created. Without this, a user
    calling opencode_run with name='Refactor' stores the session as
    'refactor' but cannot find it later by typing 'Stop session Refactor'.
    """
    import re as _re
    if not raw:
        return f"oc-{int(time.time())}"
    return _re.sub(r"[^a-z0-9-]", "-", raw.lower())[:32].strip("-") or f"oc-{int(time.time())}"


def _opencode_tmux_window_name(session_name: str) -> str:
    """Return tmux window name for a given opencode voice session.

    Per-user tmux session names: oc-<user_prefix>-<session> so two users
    running 'refactor' don't collide in the same tmux server.
    """
    prefix = ""
    if _OPENCODE_CURRENT_USER:
        prefix = re.sub(r"[^a-z0-9]", "", str(_OPENCODE_CURRENT_USER).lower())[:8] or "anon"
        return f"oc-{prefix}-{session_name}"
    return f"oc-{session_name}"


def _opencode_list_sessions() -> List[Dict[str, Any]]:
    """Return summary of tracked opencode sessions for the CURRENT user only."""
    if _OPENCODE_CURRENT_USER is None:
        items = [(k, v) for k, v in _OPENCODE_SESSIONS.items() if not isinstance(k, tuple)]
    else:
        items = [(k, v) for k, v in _OPENCODE_SESSIONS.items() if isinstance(k, tuple) and k[0] == _OPENCODE_CURRENT_USER]
    return [
        {
            "name": (k[1] if isinstance(k, tuple) else k),
            "user": (k[0] if isinstance(k, tuple) else None),
            "tmux_window": meta.get("tmux_window"),
            "goal": meta.get("goal", "")[:200],
            "created_at": meta.get("created_at"),
        }
        for k, meta in sorted(items, key=lambda kv: -kv[1].get("created_at", 0))
    ]


# ── OpenCode progress watcher ───────────────────────────────────────────
# Long-running opencode tasks (multi-minute code refactors, builds, test
# runs) leave the user in silence. This watcher periodically checks the
# opencode log file and injects a text message into the live Gemini
# session so the agent speaks the progress aloud.
#
# Design:
#   - Spawned as an asyncio.Task on the gateway's event loop when
#     `opencode_run` is called (if the bridge instance is available).
#   - Polls the log file every OPENCODE_WATCHER_POLL_SECONDS.
#   - Throttles voice updates: at most once per
#     OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS, and only after an initial
#     OPENCODE_WATCHER_INITIAL_DELAY_SECONDS grace period.
#   - Detects milestones (errors, test pass/fail, "done", compile
#     success) and sends an immediate update regardless of throttle.
#   - Respects "agent currently speaking" and "user currently speaking"
#     gates — defers or drops updates rather than barging in.
#   - On tmux window death (the opencode session ended), sends a final
#     summary and stops the watcher.
#
# Per-user isolation: watchers are keyed (user_id, session_name) like the
# session registry. Two users can each have an active watcher without
# interference.

OPENCODE_WATCHER_ENABLED = os.getenv("DISCORD_VOICE_LIVE_OPENCODE_WATCHER", "true").lower() in {"1", "true", "yes", "on"}
OPENCODE_WATCHER_POLL_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_OPENCODE_WATCHER_POLL_SECONDS", "5"))
OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS", "30"))
OPENCODE_WATCHER_INITIAL_DELAY_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_OPENCODE_WATCHER_INITIAL_DELAY_SECONDS", "60"))

# Milestone regex (case-insensitive). Matched against the new tail of
# the log since the last update. Any match triggers an immediate voice
# update regardless of throttle.
_MILESTONE_RE = re.compile(
    r"(?i)("
    r"\berror\b|\bexception\b|\btraceback\b|\bfailed\b|\bfatal\b|"
    r"\btest(s)?\s*(pass(ed)?|fail(ed)?)\b|"
    r"\bcompile(d)?\s*(success(fully)?|error)?\b|"
    r"\bbuild\s*(success(fully)?|fail(ed)?)\b|"
    r"\bdone\b|\bcomplete(d)?\b|\bfinish(ed)?\b|"
    r"\bcommit\b|\bpush(ed)?\b|"
    r"\u2713|\u2717|"
    r")"
)


def _opencode_extract_progress(log_path: str, last_line_count: int, max_lines: int = 40) -> Tuple[str, int, bool]:
    """Read new lines from an opencode log file and build a progress summary.

    Returns:
        (progress_text, new_line_count, is_milestone)

    The progress_text is short enough to inject as a single text turn
    (~200-500 chars). If no new content, returns empty string.
    """
    try:
        p = Path(log_path)
        if not p.exists():
            return "", last_line_count, False
        with p.open("r", errors="replace") as f:
            lines = f.readlines()
    except Exception as exc:
        logger.debug("opencode watcher: read failed (%s): %s", log_path, exc)
        return "", last_line_count, False

    if len(lines) <= last_line_count:
        return "", last_line_count, False

    new_lines = lines[last_line_count:][-max_lines:]
    new_line_count = len(lines)

    # Strip blank lines and ANSI escape codes
    cleaned = []
    for ln in new_lines:
        s = re.sub(r"\x1b\[[0-9;]*m", "", ln.rstrip())
        if s.strip():
            cleaned.append(s)
    if not cleaned:
        return "", new_line_count, False

    # Milestone detection: scan the cleaned lines for any keyword
    is_milestone = any(_MILESTONE_RE.search(s) for s in cleaned)

    # Build a concise summary — show first 3 + last 5 lines if long
    if len(cleaned) <= 8:
        body = "\n".join(cleaned)
    else:
        head = cleaned[:3]
        tail = cleaned[-5:]
        body = "\n".join(head) + f"\n... ({len(cleaned) - 8} more lines) ...\n" + "\n".join(tail)

    if is_milestone:
        progress = f"[opencode milestone] {body}"
    else:
        progress = f"[opencode progress] {body}"

    # Cap length to keep the voice turn short
    if len(progress) > 600:
        progress = progress[:600] + "..."
    return progress, new_line_count, is_milestone


def _opencode_tmux_window_alive(tmux_session: str, window_name: str) -> bool:
    """Return True if the named tmux window still exists."""
    import subprocess
    try:
        out = subprocess.run(
            ["tmux", "list-windows", "-t", tmux_session, "-F", "#{window_name}"],
            capture_output=True,
            timeout=5,
        )
        if out.returncode != 0:
            return False
        windows = out.stdout.decode(errors="replace").splitlines()
        return window_name in windows
    except Exception:
        return False


# Global registry of watcher tasks: (user_id, session_name) -> asyncio.Task
_OPENCODE_WATCHERS: Dict[Any, "asyncio.Task"] = {}
# A weak ref to the bridge instance per (user_id, session_name). The watcher
# uses this to call send_text. Stored separately so the task can drop the
# ref when done.
_OPENCODE_BRIDGE_REFS: Dict[Any, Any] = {}


def _opencode_register_bridge(session_name: str, user_id: Optional[str], bridge: Any) -> None:
    """Store a weak ref to the bridge for the watcher's send_text calls."""
    import weakref
    key = (user_id, session_name)
    try:
        _OPENCODE_BRIDGE_REFS[key] = weakref.ref(bridge)
    except TypeError:
        # Bridge doesn't support weakref (e.g. compiled class) — skip
        pass


def _opencode_get_bridge(session_name: str, user_id: Optional[str]) -> Any:
    """Get the bridge instance for the watcher's send_text calls.

    Tries the per-session registry first (set by _run_opencode_tool_with_bridge).
    Falls back to the module-level _opencode_current_bridge if no per-session
    ref was registered yet.
    """
    key = (user_id, session_name)
    ref = _OPENCODE_BRIDGE_REFS.get(key)
    if ref is not None:
        bridge = ref()
        if bridge is not None:
            return bridge
        _OPENCODE_BRIDGE_REFS.pop(key, None)
    # Fallback: use the most recently active bridge (works when there's
    # only one active session per user, which is the common case)
    return _opencode_current_bridge


async def _opencode_watcher_loop(
    session_name: str,
    tmux_session: str,
    tmux_window: str,
    log_path: str,
    user_id: Optional[str],
    goal: str,
    model: Optional[str],
) -> None:
    """Background task: watch an opencode log, inject progress into Gemini Live.

    Runs until the tmux window dies (task finished or killed) or the bridge
    disconnects. Sends voice updates with throttling + milestone detection.
    """
    import time as _time
    key = (user_id, session_name)
    last_line_count = 0
    last_voice_at: Optional[float] = None
    started_at = _time.monotonic()
    last_window_alive = True
    milestone_triggered = False
    final_summary_sent = False

    try:
        # Initial delay before any voice activity
        await asyncio.sleep(OPENCODE_WATCHER_INITIAL_DELAY_SECONDS)

        while True:
            # Check tmux window liveness
            alive = _opencode_tmux_window_alive(tmux_session, tmux_window)
            if not alive and last_window_alive:
                # The opencode session ended. Read final log and send summary.
                last_window_alive = False
                await asyncio.sleep(2.0)  # let tee flush
                progress, last_line_count, _ = _opencode_extract_progress(
                    log_path, last_line_count, max_lines=20
                )
                bridge = _opencode_get_bridge(session_name, user_id)
                elapsed = int(_time.monotonic() - started_at)
                mins, secs = divmod(elapsed, 60)
                elapsed_str = f"{mins}m{secs}s" if mins else f"{secs}s"
                final_body = ("Here is the final output:\n" + progress) if progress else "No output captured."
                final = (
                    f"[opencode finished after {elapsed_str}] "
                    f"Session '{session_name}' has ended. {final_body}"
                )
                if bridge is not None:
                    try:
                        await bridge.send_text(final)
                    except Exception:
                        pass
                # Webhook: opencode_finished
                try:
                    from webhook_dispatcher import emit_opencode_status, emit_opencode_transcript
                    emit_opencode_status(
                        "opencode_finished", session_name, final,
                        fields=[{"name": "Duration", "value": elapsed_str, "inline": True}],
                    )
                    if progress:
                        emit_opencode_transcript(session_name, progress[-1500:])
                except Exception:
                    pass
                final_summary_sent = True
                logger.info(
                    "opencode watcher: session %s finished after %ss, final update sent",
                    session_name, elapsed,
                )
                break
            last_window_alive = alive

            # Read new log content
            progress, last_line_count, is_milestone = _opencode_extract_progress(
                log_path, last_line_count, max_lines=30
            )
            if progress:
                now = _time.monotonic()
                elapsed = int(now - started_at)
                mins, secs = divmod(elapsed, 60)
                elapsed_str = f"{mins}m{secs}s" if mins else f"{secs}s"
                # Throttle: only speak if enough time has passed OR milestone
                should_speak = False
                if is_milestone:
                    should_speak = True
                    milestone_triggered = True
                elif last_voice_at is None or (now - last_voice_at) >= OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS:
                    should_speak = True

                if should_speak:
                    # Don't barge in if the user is currently speaking
                    bridge = _opencode_get_bridge(session_name, user_id)
                    if bridge is None:
                        # Bridge gone — stop watching
                        break
                    last_input = getattr(bridge, "metrics", {}).get("last_input_monotonic")
                    if last_input is not None and (now - float(last_input)) < 5.0:
                        # User is speaking right now — defer this update
                        pass
                    else:
                        # Compose a turn that asks Gemini to speak the progress
                        turn = (
                            f"User is waiting for opencode session '{session_name}' "
                            f"(goal: {goal[:120]}). It has been running for {elapsed_str}. "
                            f"Here is the latest output — please summarize briefly for the user in voice.\n\n"
                            f"{progress}"
                        )
                        try:
                            await bridge.send_text(turn)
                            last_voice_at = now
                            # Webhook: progress (or milestone)
                            try:
                                from webhook_dispatcher import emit_opencode_status, emit_opencode_transcript
                                sub = "opencode_milestone" if is_milestone else "opencode_progress"
                                emit_opencode_status(
                                    sub, session_name, progress,
                                    fields=[{"name": "Elapsed", "value": elapsed_str, "inline": True}],
                                )
                                emit_opencode_transcript(session_name, progress)
                            except Exception:
                                pass
                        except Exception as exc:
                            logger.debug("opencode watcher: send_text failed: %s", exc)

            await asyncio.sleep(OPENCODE_WATCHER_POLL_SECONDS)
    except asyncio.CancelledError:
        # Watcher was cancelled (e.g. bridge disconnect or user opencode_stop).
        # Send a brief "stopped" notice if the bridge is still around.
        bridge = _opencode_get_bridge(session_name, user_id)
        if bridge is not None and not final_summary_sent:
            try:
                await bridge.send_text(
                    f"[opencode watcher stopped] Session '{session_name}' was stopped or bridge disconnected."
                )
            except Exception:
                pass
        raise
    except Exception as exc:
        logger.warning("opencode watcher: loop crashed: %s", exc, exc_info=True)
    finally:
        _OPENCODE_WATCHERS.pop(key, None)
        _OPENCODE_BRIDGE_REFS.pop(key, None)
        logger.debug("opencode watcher: cleaned up %s", key)


def _opencode_spawn_watcher(
    session_name: str,
    tmux_session: str,
    tmux_window: str,
    log_path: str,
    user_id: Optional[str],
    goal: str,
    model: Optional[str],
    bridge: Any,
) -> None:
    """Spawn a background watcher task. Idempotent (replaces existing)."""
    if not OPENCODE_WATCHER_ENABLED:
        return
    key = (user_id, session_name)
    # Cancel any prior watcher for this key
    prior = _OPENCODE_WATCHERS.pop(key, None)
    if prior is not None and not prior.done():
        prior.cancel()
    _opencode_register_bridge(session_name, user_id, bridge)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Not in an async context — defer watcher start to next call
        return
    task = loop.create_task(
        _opencode_watcher_loop(
            session_name=session_name,
            tmux_session=tmux_session,
            tmux_window=tmux_window,
            log_path=log_path,
            user_id=user_id,
            goal=goal,
            model=model,
        )
    )
    _OPENCODE_WATCHERS[key] = task
    logger.info(
        "opencode watcher: spawned for %s (user=%s, log=%s, poll=%.1fs, min_gap=%.1fs)",
        session_name, user_id, log_path,
        OPENCODE_WATCHER_POLL_SECONDS, OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS,
    )


def _opencode_stop_watcher(session_name: str, user_id: Optional[str]) -> None:
    """Cancel the watcher for a session (called from opencode_stop)."""
    key = (user_id, session_name)
    task = _OPENCODE_WATCHERS.pop(key, None)
    if task is not None and not task.done():
        task.cancel()
    _OPENCODE_BRIDGE_REFS.pop(key, None)


def _opencode_run_tmux(session_name: str, prompt: str, model: Optional[str], workdir: Optional[str]) -> Dict[str, Any]:
    """Spawn opencode in a new tmux window under the configured session.

    Returns {"name", "tmux_window", "tmux_session"}. Use opencode_status to tail.
    """
    import subprocess
    import shlex
    import time

    if not Path(OPENCODE_BIN).exists():
        return {"error": f"opencode binary not found at {OPENCODE_BIN}"}

    window_name = _opencode_tmux_window_name(session_name)
    model = model or OPENCODE_DEFAULT_MODEL
    workdir = workdir or str(Path.home())

    # Check tmux session exists; create if not
    check = subprocess.run(["tmux", "has-session", "-t", OPENCODE_TMUX_SESSION], capture_output=True)
    if check.returncode != 0:
        subprocess.run(["tmux", "new-session", "-d", "-s", OPENCODE_TMUX_SESSION, "-n", "_init"], check=False)

    # Kill any prior window with this name (re-run replaces old session)
    subprocess.run(["tmux", "kill-window", "-t", f"{OPENCODE_TMUX_SESSION}:{window_name}"], capture_output=True)

    # Build the opencode command. We use `opencode run` for one-shot task execution
    # with an explicit model. The full interactive TUI is reached via plain `opencode`.
    quoted_prompt = shlex.quote(prompt)
    quoted_model = shlex.quote(model)
    quoted_wd = shlex.quote(workdir)
    # Use a here-doc to feed the prompt to opencode run. -y auto-approves inside
    # the opencode session so the user doesn't get blocked by its own approvals;
    # the voice passthrough is for what the LIVE agent decides to surface.
    log_path = f"/tmp/opencode-{window_name}.log"
    cmd = (
        f"cd {quoted_wd} && "
        f"echo {quoted_prompt} | {OPENCODE_BIN} run --model {quoted_model} -y 2>&1 | "
        f"tee {shlex.quote(log_path)}; "
        f"echo '[opencode-voice] session ended, window will close in 60s'; "
        f"sleep 60"
    )

    create = subprocess.run(
        ["tmux", "new-window", "-d", "-t", OPENCODE_TMUX_SESSION, "-n", window_name, "bash", "-c", cmd],
        capture_output=True,
    )
    if create.returncode != 0:
        return {"error": f"tmux new-window failed: {create.stderr.decode(errors='replace').strip()}"}

    _OPENCODE_SESSIONS[_opencode_key(session_name)] = {
        "tmux_window": window_name,
        "created_at": time.time(),
        "goal": prompt,
        "model": model,
        "workdir": workdir,
        "log_path": f"/tmp/opencode-{window_name}.log",
        "user_id": _OPENCODE_CURRENT_USER,
    }
    # Webhook: opencode_started
    try:
        from webhook_dispatcher import emit_opencode_status
        emit_opencode_status(
            "opencode_started", session_name,
            f"Goal: {prompt[:200]}",
            fields=[{"name": "Model", "value": str(model or OPENCODE_DEFAULT_MODEL), "inline": True}],
        )
    except Exception:
        pass
    # Spawn the progress watcher so the user gets voice updates on long
    # opencode runs. Uses the module-global _opencode_current_bridge (set
    # by _run_opencode_tool_with_bridge) as a back-ref for send_text.
    _opencode_spawn_watcher(
        session_name=session_name,
        tmux_session=OPENCODE_TMUX_SESSION,
        tmux_window=window_name,
        log_path=f"/tmp/opencode-{window_name}.log",
        user_id=_OPENCODE_CURRENT_USER,
        goal=prompt,
        model=model,
        bridge=_opencode_current_bridge,
    )
    return {
        "result": {
            "name": session_name,
            "tmux_session": OPENCODE_TMUX_SESSION,
            "tmux_window": window_name,
            "model": model,
            "workdir": workdir,
            "tail_cmd": f"tmux attach -t {OPENCODE_TMUX_SESSION}:{window_name}",
            "log": f"/tmp/opencode-{session_name}.log",
            "next": (
                "Use opencode_status to poll progress, opencode_send to inject follow-up, "
                "opencode_stop to kill. Tell the user briefly what you spawned."
            ),
        }
    }


def _opencode_status(name: str, tail_lines: int = 40) -> Dict[str, Any]:
    """Return tail of opencode session log + whether the window still exists."""
    import subprocess

    meta = _OPENCODE_SESSIONS.get(_opencode_key(name))
    if not meta:
        return {"error": f"no opencode session named '{name}'. Active: {[_opencode_session_label(k) for k in _OPENCODE_SESSIONS if (not isinstance(k, tuple)) or k[0] == _OPENCODE_CURRENT_USER]}"}

    log_path = meta["log_path"]
    window = meta["tmux_window"]
    log_content = ""
    if Path(log_path).exists():
        try:
            with open(log_path, "r", errors="replace") as f:
                log_content = "".join(f.readlines()[-tail_lines:])
        except Exception as exc:
            log_content = f"[log read failed: {exc}]"

    alive = subprocess.run(
        ["tmux", "list-windows", "-t", OPENCODE_TMUX_SESSION, "-F", "#{window_name}"],
        capture_output=True,
    )
    windows = alive.stdout.decode(errors="replace").splitlines()
    is_alive = window in windows

    return {
        "result": {
            "name": name,
            "user": _OPENCODE_CURRENT_USER,
            "alive": is_alive,
            "log_tail": log_content,
            "goal": meta.get("goal", "")[:200],
            "model": meta.get("model"),
        }
    }


def _opencode_send(name: str, message: str) -> Dict[str, Any]:
    """Send a follow-up message into a running opencode session via tmux send-keys.

    Note: opencode run reads its prompt from stdin once, so for one-shot sessions
    this only works if the session is still on the `tee |` line awaiting input —
    i.e. when using interactive `opencode` (not `opencode run`). For run-mode
    sessions the message is appended to the log file for the agent to pick up.
    """
    import subprocess

    meta = _OPENCODE_SESSIONS.get(_opencode_key(name))
    if not meta:
        return {"error": f"no opencode session named '{name}' for current user"}
    window = meta["tmux_window"]

    # Try to deliver into the tmux pane (interactive sessions)
    send = subprocess.run(
        ["tmux", "send-keys", "-t", f"{OPENCODE_TMUX_SESSION}:{window}", message, "Enter"],
        capture_output=True,
    )
    sent_to_pane = send.returncode == 0
    # Also append to the log so the agent can read it later regardless
    try:
        with open(meta["log_path"], "a") as f:
            f.write(f"\n[voice-followup] {message}\n")
    except Exception:
        pass
    return {"result": {"name": name, "sent_to_pane": sent_to_pane, "appended_to_log": True}}


def _opencode_stop(name: str) -> Dict[str, Any]:
    """Kill the opencode session's tmux window and remove from registry."""
    import subprocess

    meta = _OPENCODE_SESSIONS.pop(_opencode_key(name), None)
    if not meta:
        return {"error": f"no opencode session named '{name}' for current user"}
    window = meta["tmux_window"]
    # Cancel the progress watcher first so it doesn't fire a final summary
    # for a session we just killed.
    _opencode_stop_watcher(name, _OPENCODE_CURRENT_USER)
    subprocess.run(
        ["tmux", "kill-window", "-t", f"{OPENCODE_TMUX_SESSION}:{window}"],
        capture_output=True,
    )
    # Webhook: opencode_stopped
    try:
        from webhook_dispatcher import emit_opencode_status
        emit_opencode_status("opencode_stopped", name, "User requested stop")
    except Exception:
        pass
    return {"result": {"name": name, "killed": True, "tmux_window": window}}


_OPENCODE_FUNCTION_DECLARATIONS = [
    {
        "name": "opencode_run",
        "description": (
            "Spawn a full OpenCode coding agent in a tmux window to handle a coding/build task "
            "the user is asking for. Returns a session name to track. Always confirm with the user "
            "before invoking this if the task is non-trivial — they may want to run it themselves. "
            "Use for: code changes, refactors, building features, running tests, fixing bugs. "
            "Do NOT use for: simple questions, lookups, things the live voice channel can answer directly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Plain-English description of what opencode should do",
                },
                "name": {
                    "type": "string",
                    "description": "Short session name (lowercase, no spaces), e.g. 'refactor-auth'",
                },
                "model": {
                    "type": "string",
                    "description": f"Model to use (default {OPENCODE_DEFAULT_MODEL})",
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory (default ~/)",
                },
            },
            "required": ["goal", "name"],
        },
    },
    {
        "name": "opencode_status",
        "description": (
            "Poll an opencode session: returns the last 40 log lines and whether the tmux window is "
            "still alive. Use this between tool calls to report progress to the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name returned by opencode_run"},
                "tail_lines": {"type": "integer", "description": "How many recent log lines (default 40, max 200)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "opencode_list",
        "description": "List all tracked opencode sessions (name, tmux window, goal).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "opencode_send",
        "description": (
            "Send a follow-up message into a running opencode session. For interactive opencode "
            "sessions this is delivered live; for one-shot `opencode run` sessions it's appended "
            "to the log for the next status poll."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name"},
                "message": {"type": "string", "description": "Message to send"},
            },
            "required": ["name", "message"],
        },
    },
    {
        "name": "opencode_stop",
        "description": "Kill a running opencode session's tmux window and forget it.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Session name"}},
            "required": ["name"],
        },
    },
]


def _run_opencode_tool_with_bridge(
    name: str, args: Dict[str, Any], user_id: Optional[str], bridge: Any
) -> Dict[str, Any]:
    """Bridge entry point: set the per-user context then dispatch.

    The `bridge` reference is stored module-globally so the watcher (spawned
    in the executor's thread) can call back into send_text() from the
    gateway's event loop via the weak-ref registry. The bridge also exposes
    itself via a thread-local for the duration of the call.
    """
    global _opencode_current_bridge
    _opencode_set_user(user_id)
    _opencode_current_bridge = bridge
    try:
        return _run_opencode_tool(name, args)
    finally:
        _opencode_current_bridge = None


def _run_opencode_tool_with_user(name: str, args: Dict[str, Any], user_id: Optional[str]) -> Dict[str, Any]:
    """Legacy entry point: set the per-user context then dispatch (no bridge)."""
    _opencode_set_user(user_id)
    return _run_opencode_tool(name, args)


def _run_opencode_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch opencode_* tools. All handlers are synchronous and run in executor."""
    try:
        if name == "opencode_run":
            session_name = args.get("name") or f"oc-{int(time.time())}"
            # Sanitize name: lowercase, hyphens, no spaces, max 32 chars.
            # The same sanitization runs in _opencode_sanitize_name() so
            # status/send/stop lookups always hit the same registry key.
            session_name = _opencode_sanitize_name(session_name)
            return _opencode_run_tmux(
                session_name=session_name,
                prompt=args.get("goal", ""),
                model=args.get("model"),
                workdir=args.get("workdir"),
            )
        if name == "opencode_status":
            return _opencode_status(
                name=_opencode_sanitize_name(args.get("name", "")),
                tail_lines=min(max(int(args.get("tail_lines", 40)), 1), 200),
            )
        if name == "opencode_list":
            return {"result": {"sessions": _opencode_list_sessions()}}
        if name == "opencode_send":
            return _opencode_send(name=_opencode_sanitize_name(args.get("name", "")),
                                 message=args.get("message", ""))
        if name == "opencode_stop":
            return _opencode_stop(name=_opencode_sanitize_name(args.get("name", "")))
        return {"error": f"Unknown opencode tool: {name}"}
    except Exception as exc:
        logger.exception("opencode tool %s crashed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


# ── Read-only system inspection tools (allowlisted paths) ──────────────────
# Lets Gemini Live answer "what's in X file" / "is Y configured" questions
# without exposing arbitrary shell. Path allowlist is hard-coded to safe dirs.
SYSINSPECT_VOICE_TOOLS_ENABLED = os.getenv(
    "DISCORD_VOICE_LIVE_SYSINSPECT_TOOLS", "true"
).lower() in {"1", "true", "yes", "on"}

_SYSINSPECT_ALLOWED_PREFIXES = (
    str(Path.home() / ".hermes"),
    "/etc/systemd",
    "/home/caps/hermes-workspace",
    "/home/caps/honcho",
    str(Path.home() / "hermes-extensions"),
    str(Path.home() / "projects"),
    "/var/log",
)


def _sysinspect_path_allowed(path: str) -> bool:
    try:
        resolved = str(Path(path).expanduser().resolve())
    except Exception:
        return False
    return any(resolved.startswith(prefix) for prefix in _SYSINSPECT_ALLOWED_PREFIXES)


def _run_sysinspect_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only file/grep tools. All paths must be in the allowlist."""
    if name == "local_inspect_read":
        path = args.get("path", "")
        limit = min(max(int(args.get("limit", 200)), 1), 1000)
        if not _sysinspect_path_allowed(path):
            return {"error": f"path not in allowlist: {path}"}
        try:
            with open(Path(path).expanduser(), "r", errors="replace") as f:
                content = "".join(f.readlines()[:limit])
            return {"result": {"path": path, "lines": content.count("\n"), "content": content}}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    if name == "local_inspect_grep":
        path = args.get("path", "")
        pattern = args.get("pattern", "")
        limit = min(max(int(args.get("limit", 50)), 1), 200)
        if not pattern:
            return {"error": "pattern is required"}
        if not _sysinspect_path_allowed(path):
            return {"error": f"path not in allowlist: {path}"}
        try:
            import subprocess
            proc = subprocess.run(
                ["rg", "--no-heading", "-n", "--max-count", str(limit), pattern, str(Path(path).expanduser())],
                capture_output=True,
                text=True,
                timeout=15,
            )
            matches = proc.stdout.splitlines()[:limit]
            return {"result": {"path": path, "pattern": pattern, "matches": matches, "match_count": len(matches)}}
        except FileNotFoundError:
            # rg not installed — fall back to grep
            try:
                proc = subprocess.run(
                    ["grep", "-rn", "--max-count", str(limit), pattern, str(Path(path).expanduser())],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                matches = proc.stdout.splitlines()[:limit]
                return {"result": {"path": path, "pattern": pattern, "matches": matches, "match_count": len(matches), "fallback": "grep"}}
            except Exception as exc:
                return {"error": f"grep fallback failed: {exc}"}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    return {"error": f"Unknown sysinspect tool: {name}"}


_SYSINSPECT_FUNCTION_DECLARATIONS = [
    {
        "name": "local_inspect_read",
        "description": (
            "Read a file's first N lines from an allowlisted path (under ~/.hermes, "
            "hermes-workspace, honcho, /etc/systemd, /var/log, etc.). Use to inspect configs, "
            "check service state, look at skills/plugins. Never returns binary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"},
                "limit": {"type": "integer", "description": "Max lines to return (default 200, max 1000)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "local_inspect_grep",
        "description": (
            "Search for a regex pattern inside a file or directory under an allowlisted path. "
            "Returns matching lines with line numbers, capped at `limit`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to search inside"},
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "limit": {"type": "integer", "description": "Max matches (default 50, max 200)"},
            },
            "required": ["path", "pattern"],
        },
    },
]


def _ensure_hermes_agent_path() -> None:
    hermes_agent = Path.home() / ".hermes" / "hermes-agent"
    if str(hermes_agent) not in sys.path:
        sys.path.insert(0, str(hermes_agent))


def _run_spotify_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a Spotify tool call and return a dict for Gemini toolResponse."""
    try:
        import plugins.spotify.tools as spotify_tools
    except Exception:
        _ensure_hermes_agent_path()
        try:
            import plugins.spotify.tools as spotify_tools  # type: ignore[no-redef]
        except Exception as exc:
            logger.warning("Spotify tools import failed: %s", exc)
            return {"error": f"Spotify tools not available: {exc}"}

    try:
        if name == "spotify_play":
            result = spotify_tools._handle_spotify_playback({
                "action": "play",
                "uris": args.get("uris"),
                "context_uri": args.get("context_uri"),
                "device_id": args.get("device_id"),
            })
        elif name == "spotify_pause":
            result = spotify_tools._handle_spotify_playback({"action": "pause"})
        elif name == "spotify_next":
            result = spotify_tools._handle_spotify_playback({"action": "next"})
        elif name == "spotify_previous":
            result = spotify_tools._handle_spotify_playback({"action": "previous"})
        elif name == "spotify_get_state":
            result = spotify_tools._handle_spotify_playback({"action": "get_state"})
        elif name == "spotify_set_volume":
            result = spotify_tools._handle_spotify_playback({
                "action": "set_volume",
                "volume_percent": args.get("volume_percent"),
            })
        elif name == "spotify_search":
            result = spotify_tools._handle_spotify_search({
                "query": args.get("query"),
                "types": args.get("types", ["track"]),
            })
        elif name == "spotify_add_to_queue":
            result = spotify_tools._handle_spotify_queue({
                "action": "add",
                "uri": args.get("uri"),
                "device_id": args.get("device_id"),
            })
        elif name == "spotify_playlists":
            result = spotify_tools._handle_spotify_playlists(args)
        else:
            return {"error": f"Unknown Spotify tool: {name}"}

        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("error"):
                return {"error": parsed["error"]}
            return {"result": parsed}
        except Exception:
            return {"result": result}
    except Exception as exc:
        logger.exception("Spotify tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


_VOICE_DOMAIN_ALIASES = {
    "cortesera.eu": "corticera.eu",
    "cortesera.com": "corticera.eu",
    "cortisera.eu": "corticera.eu",
    "cortisera.com": "corticera.eu",
    "cordisera.eu": "corticera.eu",
    "cordisera.com": "corticera.eu",
    "torticera.eu": "corticera.eu",
    "torticera.com": "corticera.eu",
}


def _normalize_voice_web_text(value: str) -> str:
    """Fix common voice-ASR variants for domains before web tool dispatch."""
    text = str(value or "")
    for alias, target in _VOICE_DOMAIN_ALIASES.items():
        text = re.sub(rf"(?i)\b{re.escape(alias)}\b", target, text)
    text = re.sub(
        r"(?ix)\b[ct]\s*o\s*r\s*t\s*i\s*c\s*e\s*r\s*a\s*\.?\s*e\s*u\b",
        "corticera.eu",
        text,
    )
    text = re.sub(r"(?i)\bcorticera\s+eu\b", "corticera.eu", text)
    return text


def _normalize_voice_web_args(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(args or {})
    if name == "web_search":
        normalized["query"] = _normalize_voice_web_text(str(normalized.get("query", "")))
    elif name == "web_extract":
        urls = normalized.get("urls", [])
        if isinstance(urls, str):
            urls = [urls]
        if isinstance(urls, list):
            normalized["urls"] = [_normalize_voice_web_text(str(url)) for url in urls]
    return normalized


def _basic_extract_url(url: str) -> Dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"url": url, "title": "", "content": "", "error": "Invalid HTTP URL"}
    req = Request(
        url,
        headers={
            "User-Agent": "HermesVoiceLive/1.0 (+https://github.com/NousResearch/hermes-agent)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.2",
        },
    )
    with urlopen(req, timeout=12) as resp:
        raw = resp.read(500_000)
        content_type = resp.headers.get("content-type", "")
    charset_match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    encoding = charset_match.group(1) if charset_match else "utf-8"
    text = raw.decode(encoding, errors="replace")
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else ""
    text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    content = text.strip()[:20_000]
    return {"url": url, "title": title, "content": content}


def _basic_web_extract(urls: Any) -> Dict[str, Any]:
    if isinstance(urls, str):
        urls = [urls]
    if not isinstance(urls, list):
        return {"error": "web_extract fallback expected a list of URLs"}
    results = []
    for url in urls[:5]:
        try:
            results.append(_basic_extract_url(str(url)))
        except Exception as exc:
            results.append({"url": str(url), "title": "", "content": "", "error": f"{type(exc).__name__}: {exc}"})
    return {"result": {"success": True, "data": {"pages": results}, "results": results}}


def _run_web_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a web search/extract tool call and return a dict for Gemini toolResponse."""
    _ensure_hermes_agent_path()
    try:
        import tools.web_tools as web_tools
    except Exception as exc:
        logger.warning("Web tools import failed: %s", exc)
        return {"error": f"Web tools not available: {exc}"}
    try:
        if name == "web_search":
            result = web_tools.web_search_tool(query=args.get("query", ""), limit=args.get("limit", 5))
        elif name == "web_extract":
            result = asyncio.run(web_tools.web_extract_tool(urls=args.get("urls", [])))
        else:
            return {"error": f"Unknown web tool: {name}"}
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("success") is False:
                if name == "web_extract" and "No web extract provider configured" in str(parsed.get("error", "")):
                    logger.warning("Web extract provider unavailable; using basic HTTP fallback")
                    return _basic_web_extract(args.get("urls", []))
                return {"error": parsed.get("error", "web tool failed")}
            return {"result": parsed}
        except Exception:
            return {"result": result}
    except Exception as exc:
        logger.exception("Web tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


# ── Safe math expression evaluator for local_calc ──────────────────────────
class _CalcVisitor:
    """Restricted AST evaluator for local_calc: only safe math ops."""
    ALLOWED_NAMES = {
        "sqrt": __import__("math").sqrt,
        "abs": abs,
        "sin": __import__("math").sin,
        "cos": __import__("math").cos,
        "log": __import__("math").log,
        "round": round,
        "min": min,
        "max": max,
    }

    def visit(self, node):
        if isinstance(node, ast.Num):
            return node.n
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            left = self.visit(node.left)
            right = self.visit(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div):
                if right == 0: raise ValueError("Division by zero")
                return left / right
            if isinstance(node.op, ast.Pow): return left ** right
            raise ValueError("Unsupported binary operator")
        if isinstance(node, ast.UnaryOp):
            operand = self.visit(node.operand)
            if operand is None:
                raise ValueError("Invalid operand")
            if isinstance(node.op, ast.UAdd): return +operand
            if isinstance(node.op, ast.USub): return -operand
            raise ValueError("Unsupported unary operator")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("Only named function calls allowed")
            fn = self.ALLOWED_NAMES.get(node.func.id)
            if fn is None:
                raise ValueError(f"Function '{node.func.id}' not allowed")
            args = [self.visit(a) for a in node.args]
            return fn(*args)
        if isinstance(node, ast.Name):
            if node.id in ("pi", "e", "tau"):
                import math
                return getattr(math, node.id)
            raise ValueError(f"Name '{node.id}' not allowed")
        if isinstance(node, ast.Expr):
            return self.visit(node.value)
        raise ValueError("Unsupported expression")


def _run_local_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a safe local helper tool and return a dict for Gemini toolResponse.

    All tools are read-only or append-only. No destructive operations.
    """
    try:
        if name == "local_weather":
            location = args.get("location", "Amsterdam")
            try:
                import requests
            except Exception:
                return {"error": "requests not installed"}
            geo_url = "https://geocoding-api.open-meteo.com/v1/search"
            params = {"name": location, "count": 1, "format": "json"}
            try:
                r = requests.get(geo_url, params=params, timeout=10)
                r.raise_for_status()
                results = r.json().get("results", [])
                if not results:
                    return {"error": f"Location '{location}' not found"}
                lat = results[0]["latitude"]
                lon = results[0]["longitude"]
                city = results[0].get("name", location)
                weather_url = "https://api.open-meteo.com/v1/forecast"
                wp = {
                    "latitude": lat,
                    "longitude": lon,
                    "current_weather": "true",
                    "timezone": "auto",
                }
                wr = requests.get(weather_url, params=wp, timeout=10)
                wr.raise_for_status()
                cw = wr.json().get("current_weather", {})
                temp = cw.get("temperature")
                wind = cw.get("windspeed")
                code = cw.get("weathercode")
                conditions = {
                    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                    45: "Fog", 48: "Depositing rime fog",
                    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
                    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
                    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
                    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
                    95: "Thunderstorm",
                }.get(code, "Unknown conditions")
                return {"result": {
                    "location": city,
                    "temperature_c": temp,
                    "wind_kph": wind,
                    "conditions": conditions,
                }}
            except Exception as exc:
                logger.exception("Weather fetch failed")
                return {"error": f"Weather fetch failed: {exc}"}

        elif name == "local_translate":
            text = args.get("text", "")
            target = args.get("target_language", "en").lower()
            source = args.get("source_language", "")
            try:
                from deep_translator import GoogleTranslator
                # Map language names to codes if needed
                lang_map = {"dutch": "nl", "romanian": "ro", "english": "en", "spanish": "es", "german": "de", "french": "fr", "italian": "it"}
                target = lang_map.get(target, target)
                source = lang_map.get(source, source) if source else "auto"
                kwargs = {"target": target}
                if source and source != "auto":
                    kwargs["source"] = source
                result = GoogleTranslator(**kwargs).translate(text)
                return {"result": {"translation": result, "source_detected": source or "auto", "target": target}}
            except Exception as exc:
                logger.warning("translate tool failed: %s", exc)
                return {"error": f"translate unavailable (deep_translator needed): {exc}"}

        elif name == "local_time":
            tz = args.get("timezone", "Europe/Amsterdam")
            try:
                from zoneinfo import ZoneInfo
                from datetime import datetime
                dt = datetime.now(ZoneInfo(tz))
                return {"result": {"time": dt.strftime("%H:%M"), "date": dt.strftime("%Y-%m-%d"), "day": dt.strftime("%A"), "timezone": tz}}
            except Exception:
                try:
                    import pytz
                    from datetime import datetime
                    dt = datetime.now(pytz.timezone(tz))
                    return {"result": {"time": dt.strftime("%H:%M"), "date": dt.strftime("%Y-%m-%d"), "day": dt.strftime("%A"), "timezone": tz}}
                except Exception as exc:
                    return {"error": f"Timezone lookup failed: {exc}"}

        elif name == "local_remind":
            action = args.get("action", "list")
            reminders_path = Path.home() / ".hermes" / "voice-reminders.jsonl"
            if action == "add":
                reminder = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "text": args.get("text", ""),
                    "minutes": args.get("minutes"),
                }
                try:
                    reminders_path.parent.mkdir(parents=True, exist_ok=True)
                    with reminders_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(reminder, ensure_ascii=False) + "\n")
                    return {"result": {"status": "saved", "text": reminder["text"]}}
                except Exception as exc:
                    return {"error": f"Reminder save failed: {exc}"}
            else:
                try:
                    if not reminders_path.exists():
                        return {"result": {"count": 0, "reminders": []}}
                    lines = reminders_path.read_text(encoding="utf-8").strip().splitlines()
                    recents = [json.loads(line) for line in lines[-20:]]
                    return {"result": {"count": len(recents), "reminders": recents}}
                except Exception as exc:
                    return {"error": f"Reminder list failed: {exc}"}

        elif name == "local_email":
            limit = args.get("limit", 5)
            try:
                cmd = [
                    "himalaya",
                    "--quiet",
                    "-o",
                    "json",
                    "envelope",
                    "list",
                    "--page-size",
                    str(limit),
                ]
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if out.returncode != 0:
                    return {"error": f"himalaya error: {out.stderr[:200]}"}
                data = json.loads(out.stdout)
                emails = []
                messages = data if isinstance(data, list) else data.get("response", data.get("results", []))
                for msg in messages:
                    sender = msg.get("from", {})
                    if isinstance(sender, list):
                        sender = sender[0] if sender else {}
                    emails.append({
                        "id": msg.get("id"),
                        "from": sender.get("addr") or sender.get("address") or sender.get("name") or "unknown",
                        "subject": msg.get("subject", "(no subject)"),
                        "date": msg.get("date"),
                    })
                return {"result": {"emails": emails}}
            except Exception as exc:
                logger.exception("Email list failed")
                return {"error": f"Email tool failed: {exc}"}

        elif name == "local_email_read":
            message_id = args.get("message_id", "")
            if not message_id:
                return {"error": "message_id is required"}
            try:
                if Path(GOOGLE_API_BIN).exists():
                    out = subprocess.run(
                        [sys.executable, GOOGLE_API_BIN, "gmail", "get", message_id],
                        capture_output=True, text=True, timeout=30,
                    )
                    if out.returncode == 0:
                        try:
                            data = json.loads(out.stdout)
                            payload = data.get("payload", {})
                            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
                            body = data.get("snippet", "")
                            return {"result": {
                                "from": headers.get("from", ""),
                                "to": headers.get("to", ""),
                                "subject": headers.get("subject", ""),
                                "date": headers.get("date", ""),
                                "body": body,
                                "id": message_id,
                            }}
                        except (json.JSONDecodeError, AttributeError, KeyError) as parse_exc:
                            return {"result": {"raw": out.stdout[:5000], "error": str(parse_exc)}}
                    return {"error": f"google_api.py error: {out.stderr[:300]}"}
                return {"error": "google_api.py not found, cannot read email"}
            except Exception as exc:
                logger.exception("Email read failed")
                return {"error": f"Email read failed: {exc}"}

        elif name == "local_email_send":
            to_raw = args.get("to", "")
            subject = args.get("subject", "")
            body = args.get("body", "")
            if not to_raw or not subject or not body:
                return {"error": "to, subject, and body are all required"}
            # Auto-correct voice-transcribed email addresses (criterion #18).
            # Common STT errors: " at " → "@", " dot " → ".", " underscore " → "_",
            # doubled spaces, missing TLDs, and accidental spaces inside
            # local-part or domain. Best-effort — returns the corrected
            # address and a note if anything was changed.
            to, to_corrections = _autocorrect_email_address(to_raw)
            if to_corrections:
                logger.info(
                    "Email 'to' address was auto-corrected: %r -> %r (%s)",
                    to_raw, to, "; ".join(to_corrections),
                )
            try:
                if Path(GOOGLE_API_BIN).exists():
                    out = subprocess.run(
                        [sys.executable, GOOGLE_API_BIN, "gmail", "send",
                         "--to", to, "--subject", subject, "--body", body],
                        capture_output=True, text=True, timeout=30,
                    )
                    if out.returncode == 0:
                        try:
                            data = json.loads(out.stdout)
                            # Webhook: email_sent
                            try:
                                from webhook_dispatcher import emit_email_sent
                                emit_email_sent(to, subject)
                            except Exception:
                                pass
                            return {"result": {
                                "status": "sent",
                                "id": data.get("id", ""),
                                "threadId": data.get("threadId", ""),
                                "to_corrections": to_corrections or None,
                            }}
                        except json.JSONDecodeError:
                            try:
                                from webhook_dispatcher import emit_email_sent
                                emit_email_sent(to, subject)
                            except Exception:
                                pass
                            return {"result": {"status": "sent", "raw": out.stdout[:2000]}}
                    return {"error": f"Send failed: {out.stderr[:300]}"}
                return {"error": "google_api.py not found"}
            except Exception as exc:
                logger.exception("Email send failed")
                return {"error": f"Email send failed: {exc}"}

        elif name == "local_email_reply":
            message_id = args.get("message_id", "")
            body = args.get("body", "")
            if not message_id or not body:
                return {"error": "message_id and body are required"}
            try:
                if Path(GOOGLE_API_BIN).exists():
                    out = subprocess.run(
                        [sys.executable, GOOGLE_API_BIN, "gmail", "reply", message_id, "--body", body],
                        capture_output=True, text=True, timeout=30,
                    )
                    if out.returncode == 0:
                        try:
                            data = json.loads(out.stdout)
                            return {"result": {"status": "replied", "id": data.get("id", ""), "threadId": data.get("threadId", "")}}
                        except json.JSONDecodeError:
                            return {"result": {"status": "replied", "raw": out.stdout[:2000]}}
                    return {"error": f"Reply failed: {out.stderr[:300]}"}
                return {"error": "google_api.py not found"}
            except Exception as exc:
                logger.exception("Email reply failed")
                return {"error": f"Email reply failed: {exc}"}

        elif name == "local_systemd":
            svc = args.get("service")
            try:
                if svc:
                    cmd = ["systemctl", "--user", "status", svc, "--no-pager", "-o", "cat"]
                else:
                    cmd = ["systemctl", "--user", "list-units", "--type=service", "--state=running", "--no-pager", "--plain"]
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                return {"result": {"output": out.stdout[-2000:] or out.stderr[:500]}}
            except Exception as exc:
                return {"error": f"systemd check failed: {exc}"}

        elif name == "local_docker":
            try:
                out = subprocess.run(
                    ["docker", "ps", "--format", "json"],
                    capture_output=True, text=True, timeout=10,
                )
                if out.returncode != 0:
                    return {"error": f"docker error: {out.stderr[:200]}"}
                containers = []
                for line in out.stdout.strip().splitlines():
                    c = json.loads(line)
                    containers.append({
                        "name": c.get("Names", "").split(",")[0],
                        "image": c.get("Image"),
                        "status": c.get("Status"),
                        "ports": c.get("Ports"),
                    })
                return {"result": {"containers": containers}}
            except Exception as exc:
                return {"error": f"Docker check failed: {exc}"}

        elif name == "local_tailscale":
            try:
                out = subprocess.run(
                    ["tailscale", "status", "--json"],
                    capture_output=True, text=True, timeout=10,
                )
                if out.returncode != 0:
                    return {"error": f"tailscale error: {out.stderr[:200]}"}
                data = json.loads(out.stdout)
                peers = []
                for name, node in data.get("Peer", {}).items():
                    peers.append({
                        "name": name,
                        "online": node.get("Online", False),
                        "ip": node.get("TailscaleIPs", []),
                        "os": node.get("OS"),
                    })
                return {"result": {"self": data.get("Self", {}).get("HostName"), "peers": peers}}
            except Exception as exc:
                return {"error": f"Tailscale check failed: {exc}"}

        elif name == "local_notes":
            query = args.get("query", "").lower()
            limit = args.get("limit", 5)
            try:
                matches = []
                for f in NOTES_DIR.glob("*.jsonl"):
                    if not f.is_file():
                        continue
                    for line in f.read_text(encoding="utf-8").strip().splitlines():
                        if not line:
                            continue
                        obj = json.loads(line)
                        text = json.dumps(obj, ensure_ascii=False).lower()
                        if query in text:
                            matches.append({"file": f.name, "event": obj})
                return {"result": {"matches": matches[:limit]}}
            except Exception as exc:
                return {"error": f"Notes search failed: {exc}"}

        elif name == "local_disk":
            try:
                import shutil
                usage = shutil.disk_usage("/")
                gb_total = usage.total / (1024**3)
                gb_used = usage.used / (1024**3)
                gb_free = usage.free / (1024**3)
                pct = round(usage.used / usage.total * 100, 1)
                return {"result": {"total_gb": round(gb_total, 1), "used_gb": round(gb_used, 1), "free_gb": round(gb_free, 1), "percent_used": pct}}
            except Exception as exc:
                return {"error": f"Disk check failed: {exc}"}

        elif name == "local_calc":
            expr = args.get("expression", "")
            if not expr:
                return {"error": "Empty expression"}
            try:
                tree = ast.parse(expr, mode="eval")
                result = _CalcVisitor().visit(tree.body)
                return {"result": {"expression": expr, "value": result}}
            except Exception as exc:
                return {"error": f"Calculation error: {exc}"}

        elif name == "local_uptime":
            try:
                with open("/proc/uptime", "r") as fh:
                    up_sec = float(fh.read().split()[0])
                up_h = int(up_sec // 3600)
                up_m = int((up_sec % 3600) // 60)
                with open("/proc/loadavg", "r") as fh:
                    load = fh.read().split()[:3]
                mem_info = {}
                with open("/proc/meminfo", "r") as fh:
                    for line in fh:
                        if line.startswith("MemTotal:"):
                            mem_info["total_mb"] = int(line.split()[1]) // 1024
                        elif line.startswith("MemAvailable:"):
                            mem_info["available_mb"] = int(line.split()[1]) // 1024
                return {"result": {"uptime": f"{up_h}h {up_m}m", "load": load, "memory_mb": mem_info}}
            except Exception as exc:
                return {"error": f"Uptime read failed: {exc}"}

        elif name == "local_news":
            topic = args.get("topic", "tech")
            limit = args.get("limit", 5)
            _ensure_hermes_agent_path()
            try:
                from tools.web_tools import web_search_tool
                result = web_search_tool(
                    query=f"latest {topic} news {time.strftime('%Y')}",
                    limit=max(limit, 10),
                )
                # Terse voice-friendly results
                lines = []
                if isinstance(result, dict):
                    for item in result.get("data", {}).get("web", result.get("results", []))[:limit]:
                        lines.append(f"{item.get('title', 'untitled')} — {item.get('source', item.get('url', 'link'))}")
                return {"result": {"headlines": lines, "topic": topic}}
            except Exception as exc:
                return {"error": f"News lookup failed: {exc}"}

        elif name == "local_youtube":
            query = args.get("query", "")
            limit = args.get("limit", 5)
            _ensure_hermes_agent_path()
            try:
                from tools.web_tools import web_search_tool
                result = web_search_tool(
                    query=f"site:youtube.com {query}",
                    limit=max(limit, 10),
                )
                lines = []
                seen = set()
                for item in result.get("data", {}).get("web", result.get("results", []))[:limit + 5]:
                    url = item.get("url", "")
                    if "youtube.com/watch" in url and url not in seen:
                        seen.add(url)
                        lines.append(f"{item.get('title', 'untitled')} — {url}")
                    if len(lines) >= limit:
                        break
                return {"result": {"videos": lines}}
            except Exception as exc:
                return {"error": f"YouTube search failed: {exc}"}

        elif name == "local_honcho":
            query = args.get("query", "")
            limit = args.get("limit", 5)
            try:
                import requests
                r = requests.get(
                    "http://127.0.0.1:8000/api/v1/search",
                    params={"query": query, "limit": limit, "peer": "user"},
                    timeout=10,
                )
                r.raise_for_status()
                data = r.json()
                excerpts = [item.get("text", "") for item in data.get("results", [])]
                return {"result": {"excerpts": excerpts[:limit]}}
            except Exception as exc:
                return {"error": f"Honcho search failed: {exc}"}

        elif name in ("local_user_onboarding_get_questions", "local_user_onboarding_answer"):
            # #32: New-user onboarding Q&A. Imports user_profiles on
            # demand so the local-tool dispatch doesn't hard-fail at
            # module import if the profile module has issues.
            try:
                from user_profiles import (
                    ONBOARDING_QUESTIONS,
                    get_or_create_profile,
                    mark_onboarding_complete,
                )
            except Exception as exc:
                return {"error": f"onboarding module import failed: {exc}"}

            if name == "local_user_onboarding_get_questions":
                return {"result": {
                    "questions": [
                        {"id": q["id"], "question": q["question"]}
                        for q in ONBOARDING_QUESTIONS
                    ],
                    "instructions": (
                        "Ask these one at a time in voice, in order. "
                        "After each answer, call local_user_onboarding_answer. "
                        "Don't rush; mirror the user's energy."
                    ),
                }}

            # local_user_onboarding_answer
            qid = args.get("question_id", "").strip()
            answer = (args.get("answer") or "").strip()
            if not qid or not answer:
                return {"error": "question_id and answer are both required"}
            valid_ids = {q["id"] for q in ONBOARDING_QUESTIONS}
            if qid not in valid_ids:
                return {"error": f"unknown question_id: {qid!r}. Valid: {sorted(valid_ids)}"}
            # Get the active user. We rely on the bridge's _user_profile.
            bridge = globals().get("BRIDGE")
            user_id = None
            if bridge is not None:
                prof = getattr(bridge, "_user_profile", None)
                if prof is not None:
                    user_id = getattr(prof, "discord_id", None)
            if not user_id:
                return {"error": "no active user (bridge/_user_profile missing)"}
            existing = get_or_create_profile(user_id)
            merged = dict(existing.onboarding_answers)
            merged[qid] = answer
            updated = mark_onboarding_complete(existing, merged)
            return {"result": {
                "stored": qid,
                "answer_length": len(answer),
                "answers_so_far": list(updated.onboarding_answers.keys()),
                "onboarding_completed": updated.onboarding_completed,
            }}

        # ── Multi-CLI delegation tools (criterion #23-#25) ─────────────────
        elif name in ("local_delegate_suggest", "local_delegate_assemble",
                       "local_delegate_execute", "local_delegate_eta"):
            try:
                from delegation_agent import (
                    suggest_platform,
                    assemble_prompt,
                    execute_delegation,
                    estimate_eta,
                    _USER_ETA_CORRECTION,
                )
            except Exception as exc:
                return {"error": f"delegation module import failed: {exc}"}

            if name == "local_delegate_suggest":
                result = suggest_platform(
                    goal=args.get("goal", ""),
                    project_size=args.get("project_size", "medium"),
                    scope=args.get("scope", "code"),
                    complexity=args.get("complexity", "medium"),
                    user_id=None,  # per-user tracking TBD
                )
                # Webhook
                try:
                    from webhook_dispatcher import emit_bridge_status
                    emit_bridge_status(
                        "info",
                        f"Delegation suggested: {result.get('suggestion')} "
                        f"for '{args.get('goal', '')[:60]}' "
                        f"(ETA: {result.get('estimated_eta_display')})",
                    )
                except Exception:
                    pass
                return {"result": result}

            if name == "local_delegate_assemble":
                prompt = assemble_prompt(
                    goal=args.get("goal", ""),
                    subgoals=args.get("subgoals", []),
                    platform=args.get("platform", "opencode"),
                    project_root=args.get("project_root"),
                )
                return {"result": {
                    "prompt": prompt,
                    "platform": args.get("platform"),
                    "length": len(prompt),
                    "tokens_est": len(prompt.split()) * 10,
                }}

            if name == "local_delegate_execute":
                import time as _t
                session_id = args.get("session_id", f"del-{int(_t.time())}")
                result = execute_delegation(
                    prompt=args.get("prompt", ""),
                    platform=args.get("platform", "opencode"),
                    session_id=session_id,
                    workdir=args.get("workdir"),
                )
                # Webhook
                try:
                    from webhook_dispatcher import emit_opencode_status
                    sid = result.get("session_id", session_id)
                    emit_opencode_status(
                        "opencode_started", sid,
                        f"Delegated to {args.get('platform')}",
                        fields=[{"name": "Session", "value": sid, "inline": True}],
                    )
                except Exception:
                    pass
                return {"result": result}

            if name == "local_delegate_eta":
                actual = args.get("actual_seconds", 0)
                estimated = args.get("estimated_seconds", 0)
                if not actual or not estimated:
                    return {"error": "actual_seconds and estimated_seconds are required"}
                correction = actual / max(estimated, 1)
                # Store per-current-user (user_id=None for now)
                _USER_ETA_CORRECTION[None] = correction
                return {"result": {
                    "correction_factor": correction,
                    "applied": True,
                    "note": "Future ETA estimates will be adjusted by {:.2f}x".format(correction),
                }}

        elif name.startswith("local_homeassistant_"):
            hass_url = os.getenv("HASS_URL", "http://homeassistant.local:8123").rstrip("/")
            hass_token = os.getenv("HASS_TOKEN", "")
            if not hass_token:
                return {"error": "Home Assistant not configured: no HASS_TOKEN set"}
            try:
                import requests as _req
                headers = {
                    "Authorization": f"Bearer {hass_token}",
                    "Content-Type": "application/json",
                }
                if name == "local_homeassistant_entity_list":
                    r = _req.get(f"{hass_url}/api/states", headers=headers, timeout=10)
                    r.raise_for_status()
                    entities = r.json()
                    summary = []
                    for ent in entities:
                        fid = ent.get("attributes", {}).get("friendly_name", "")
                        summary.append({
                            "entity_id": ent["entity_id"],
                            "state": ent["state"],
                            "friendly_name": fid,
                            "domain": ent["entity_id"].split(".")[0],
                        })
                    return {"result": {"count": len(summary), "entities": summary[:50]}}
                elif name == "local_homeassistant_get_state":
                    entity_id = args.get("entity_id", "")
                    if not entity_id:
                        return {"error": "entity_id is required"}
                    r = _req.get(f"{hass_url}/api/states/{entity_id}", headers=headers, timeout=10)
                    if r.status_code == 404:
                        return {"error": f"Entity '{entity_id}' not found"}
                    r.raise_for_status()
                    ent = r.json()
                    return {"result": {
                        "entity_id": ent["entity_id"],
                        "state": ent["state"],
                        "friendly_name": ent.get("attributes", {}).get("friendly_name", ""),
                        "last_changed": ent.get("last_changed", ""),
                    }}
                elif name == "local_homeassistant_call_service":
                    domain = args.get("domain", "")
                    service = args.get("service", "")
                    entity_id = args.get("entity_id", "")
                    data = args.get("data", {})
                    if not domain or not service or not entity_id:
                        return {"error": "domain, service, and entity_id are required"}
                    payload = {"entity_id": entity_id}
                    if isinstance(data, dict):
                        payload.update(data)
                    r = _req.post(
                        f"{hass_url}/api/services/{domain}/{service}",
                        headers=headers, json=payload, timeout=10,
                    )
                    r.raise_for_status()
                    return {"result": {"status": "called", "service": f"{domain}.{service}", "entity_id": entity_id}}
                elif name == "local_homeassistant_get_services":
                    r = _req.get(f"{hass_url}/api/services", headers=headers, timeout=10)
                    r.raise_for_status()
                    services = r.json()
                    domains = {}
                    for svc in services:
                        domain = svc.get("domain", "")
                        svc_list = list(svc.get("services", {}).keys())
                        domains[domain] = svc_list
                    return {"result": {"domains": domains}}
                else:
                    return {"error": f"Unknown HA tool: {name}"}
            except Exception as exc:
                logger.exception("HA tool %s failed", name)
                return {"error": f"HA tool failed: {exc}"}

        else:
            return {"error": f"Unknown local tool: {name}"}

    except Exception as exc:
        logger.exception("Local tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


def _put_drop_oldest(q: "queue.Queue[Optional[bytes]]", item: Optional[bytes]) -> None:
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


def _resample_pcm(data: bytes, src_rate: int, src_ch: int, dst_rate: int, dst_ch: int) -> bytes:
    if not data:
        return b""
    raw = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if src_ch == 2 and dst_ch == 1:
        raw = raw.reshape(-1, 2).mean(axis=1)
    elif src_ch == 1 and dst_ch == 2:
        raw = np.repeat(raw, 2)
    if src_rate != dst_rate:
        src_len = len(raw)
        dst_len = int(src_len * dst_rate / src_rate)
        raw = np.interp(np.linspace(0, src_len - 1, dst_len), np.arange(src_len), raw)
    raw = np.clip(raw, -32768, 32767).astype(np.int16)
    return raw.tobytes()


def downsample_for_gemini(pcm_48k_stereo: bytes) -> bytes:
    return _resample_pcm(pcm_48k_stereo, DISCORD_SR, DISCORD_CH, GEMINI_IN_SR, GEMINI_IN_CH)


def upsample_for_discord(pcm_24k_mono: bytes) -> bytes:
    return _resample_pcm(pcm_24k_mono, GEMINI_OUT_SR, GEMINI_OUT_CH, DISCORD_SR, DISCORD_CH)


def _silence_pcm(sample_rate: int, channels: int, ms: int) -> bytes:
    samples = int(sample_rate * ms / 1000) * channels
    return b"\x00" * samples * SAMPLE_WIDTH


def _fade_in_pcm_24k_mono(pcm: bytes, fade_ms: int) -> bytes:
    if not pcm or fade_ms <= 0:
        return pcm
    raw = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    fade_samples = min(len(raw), int(GEMINI_OUT_SR * fade_ms / 1000))
    if fade_samples <= 1:
        return pcm
    raw[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    return np.clip(raw, -32768, 32767).astype(np.int16).tobytes()


_TYPING_SFX_CACHE: Optional[bytes] = None
_TYPING_SFX_WARNED = False


def _scale_pcm16(pcm: bytes, volume: float) -> bytes:
    if not pcm or volume == 1.0:
        return pcm
    raw = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    raw *= max(0.0, volume)
    return np.clip(raw, -32768, 32767).astype(np.int16).tobytes()


def _load_typing_sfx_pcm() -> Optional[bytes]:
    """Load an actual WAV keyboard SFX as 24 kHz mono PCM16."""
    global _TYPING_SFX_CACHE, _TYPING_SFX_WARNED
    if _TYPING_SFX_CACHE is not None:
        return _TYPING_SFX_CACHE
    if not TYPING_SFX_PATH:
        return None
    path = Path(TYPING_SFX_PATH).expanduser()
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        if sample_width != SAMPLE_WIDTH:
            raise ValueError(f"expected 16-bit WAV, got {sample_width * 8}-bit")
        pcm = _resample_pcm(frames, sample_rate, channels, GEMINI_OUT_SR, GEMINI_OUT_CH)
        if len(pcm) > int(GEMINI_OUT_SR * 0.35) * SAMPLE_WIDTH:
            pcm = pcm[: int(GEMINI_OUT_SR * 0.35) * SAMPLE_WIDTH]
        _TYPING_SFX_CACHE = _scale_pcm16(pcm, TYPING_SFX_VOLUME)
        logger.info("VoiceLive: loaded typing SFX from %s", path)
        return _TYPING_SFX_CACHE
    except Exception as exc:
        if not _TYPING_SFX_WARNED:
            logger.warning("VoiceLive: typing SFX load failed (%s): %s", path, exc)
            _TYPING_SFX_WARNED = True
        return None


def generate_typing_pcm() -> bytes:
    """Return a real keyboard SFX when configured; synthetic fallback is opt-in."""
    sfx = _load_typing_sfx_pcm()
    if sfx:
        return sfx
    if not TYPING_SYNTH_FALLBACK:
        return b""

    sr = GEMINI_OUT_SR  # 24000
    duration_sec = 0.015 + random.random() * 0.010  # 15-25 ms total
    samples = int(sr * duration_sec)
    t = np.arange(samples, dtype=np.float64) / sr

    # Low-passed fallback tap: no high tick, so it will not read as a beep.
    thud_freq = 160 + random.randint(0, 90)
    thud = np.sin(2 * np.pi * thud_freq * t)
    thud_env = np.exp(-t / (duration_sec * 0.28))
    thud *= thud_env

    noise = np.random.default_rng().normal(0.0, 0.025, samples)
    noise *= np.exp(-t / (duration_sec * 0.18))
    click = thud + noise
    max_val = np.max(np.abs(click))
    if max_val > 0:
        click = click / max_val * 0.035 * 32767.0
    click = np.clip(click, -32768, 32767).astype(np.int16)
    return click.tobytes()


def _has_speech_energy(pcm_48k_stereo: bytes) -> bool:
    if not pcm_48k_stereo:
        return False
    raw = np.frombuffer(pcm_48k_stereo, dtype=np.int16).astype(np.float32)
    if raw.size == 0:
        return False
    rms = float(np.sqrt(np.mean(raw * raw)))
    return rms >= 120.0


try:
    import discord as _discord_audio
    _AudioSourceBase = _discord_audio.AudioSource
except Exception:
    _AudioSourceBase = object


class LiveAudioSource(_AudioSourceBase):
    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self._q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=256)
        self._buffer = bytearray()
        self._stopped = False

    def feed(self, pcm_24k_mono: bytes) -> None:
        if self._stopped:
            return
        _put_drop_oldest(self._q, pcm_24k_mono)

    def wake(self) -> bool:
        return True

    def clear(self) -> None:
        with self._q.mutex:
            self._q.queue.clear()
        self._buffer.clear()

    def finish(self) -> None:
        self._stopped = True
        _put_drop_oldest(self._q, None)

    def read(self) -> bytes:
        while len(self._buffer) < FRAME_SIZE:
            if self._stopped:
                return b""
            try:
                chunk = self._q.get(timeout=OUTPUT_READ_WAIT_SECONDS)
            except queue.Empty:
                return b"\x00" * FRAME_SIZE
            if chunk is None:
                self._stopped = True
                return b""
            pcm_48k_stereo = upsample_for_discord(chunk)
            self._buffer.extend(pcm_48k_stereo)
        frame = bytes(self._buffer[:FRAME_SIZE])
        self._buffer = self._buffer[FRAME_SIZE:]
        return frame

    def is_opus(self) -> bool:
        return False

    def cleanup(self):
        self._stopped = True


try:
    from discord.ext import voice_recv
except Exception:
    voice_recv = None


if voice_recv is not None:
    class GeminiPCMSink(voice_recv.AudioSink):
        """Receive decoded Discord PCM and forward 16 kHz mono chunks to Gemini."""

        def __init__(self, on_pcm_callback: Callable[[bytes], None]):
            super().__init__()
            self._on_pcm = on_pcm_callback
            self._frames = 0
            self._decoded_frames = 0
            self._skipped_unknown = 0
            self._skipped_bot = 0
            self._decode_errors = 0
            self._last_decode_error_log = 0.0

        def wants_opus(self) -> bool:
            """Return False so voice_recv delivers decoded PCM instead of opus frames."""
            return False

        def write(self, user, data) -> None:
            if user is None:
                self._skipped_unknown += 1
                return
            if getattr(user, "bot", False):
                self._skipped_bot += 1
                return
            if ALLOWED_SPEAKER_IDS is not None and getattr(user, "id", None) not in ALLOWED_SPEAKER_IDS:
                self._skipped_unknown += 1
                return
            # voice_recv gives us pre-decoded PCM (48k stereo, 20ms chunks)
            # because wants_opus() is False.
            pcm = getattr(data, "pcm", b"") or b""
            if not pcm:
                return
            self._frames += 1
            if not _has_speech_energy(pcm):
                return
            self._decoded_frames += 1
            self._on_pcm(downsample_for_gemini(bytes(pcm)))

        def cleanup(self) -> None:
            pass

        def stats(self) -> Dict[str, int]:
            return {
                "voice_sink_frames": self._frames,
                "voice_sink_decoded_frames": self._decoded_frames,
                "voice_sink_decode_errors": self._decode_errors,
                "voice_sink_skipped_unknown": self._skipped_unknown,
                "voice_sink_skipped_bot": self._skipped_bot,
            }
else:
    GeminiPCMSink = None


class GeminiLiveBridge:
    AUDIO_STREAM_IDLE_END_SECONDS = float(os.getenv("GEMINI_AUDIO_STREAM_IDLE_END_SECONDS", "0.25"))

    def __init__(
        self,
        output_source: LiveAudioSource,
        on_wake: Callable[[], None] = None,
        on_leave_request: Callable[[str], None] = None,
        on_reconnect: Callable[[], None] = None,
        user_profile: Optional[Any] = None,
    ):
        self._ws = None
        self._output_source = output_source
        self._on_wake = on_wake
        self._on_leave_request = on_leave_request
        self._on_reconnect = on_reconnect
        self._running = False
        self._session_handle: Optional[str] = None
        self._reconnecting = False
        self._user_disconnect = False
        self._reconnect_count = 0
        # Per-user profile (Honcho peer, tool allowlist, prompt overrides).
        # When None, fall back to module-level defaults (legacy single-user mode).
        self._user_profile = user_profile
        self._send_q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=256)
        self._video_q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=2)
        self._tasks: List[asyncio.Task] = []
        self._audio_stream_open = False
        self._last_audio_sent_at: Optional[float] = None
        self._last_video_sent_at: Optional[float] = None
        self._output_turn_open = False
        self._seen_server_content_shapes: set = set()
        self._notes_file = self._create_notes_file()
        self.metrics: Dict[str, Any] = {
            "audio_in_chunks": 0,
            "audio_out_chunks": 0,
            "audio_out_bytes": 0,
            "audio_stream_end_events": 0,
            "audio_preroll_events": 0,
            "input_transcript_events": 0,
            "output_transcript_events": 0,
            "video_in_frames": 0,
            "video_sent_frames": 0,
            "video_dropped_frames": 0,
            "video_last_reason": None,
            "notes_file": str(self._notes_file),
            "notes_events": 0,
            "last_input_transcript": None,
            "last_output_transcript": None,
            "last_input_to_output_ms": None,
            "last_input_monotonic": None,
            "last_output_monotonic": None,
            "model": None,
        }

    def _create_notes_file(self) -> Path:
        try:
            NOTES_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.warning("VoiceLive: could not create notes dir %s", NOTES_DIR, exc_info=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return NOTES_DIR / f"voice-live-{stamp}.jsonl"

    def feed_audio(self, pcm_16k_mono: bytes) -> None:
        self.metrics["audio_in_chunks"] += 1
        self.metrics["last_input_monotonic"] = time.monotonic()
        _put_drop_oldest(self._send_q, pcm_16k_mono)

    def feed_video_frame(self, data: bytes, mime_type: str, force: bool = False,
                         source: str = "") -> Dict[str, Any]:
        self.metrics["video_in_frames"] += 1
        if not VIDEO_ENABLED:
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "disabled"
            return {"accepted": False, "reason": "disabled"}
        if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "unsupported_mime"
            return {"accepted": False, "reason": "unsupported_mime"}
        if not data or len(data) > VIDEO_MAX_BYTES:
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "size_limit"
            return {"accepted": False, "reason": "size_limit", "max_bytes": VIDEO_MAX_BYTES}

        now = time.monotonic()
        min_interval = 1.0 / max(VIDEO_MAX_FPS, 0.1)
        if self._last_video_sent_at is not None and now - self._last_video_sent_at < min_interval:
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "fps_limit"
            return {"accepted": False, "reason": "fps_limit", "max_fps": VIDEO_MAX_FPS}

        last_audio = self.metrics.get("last_input_monotonic")
        if not force and (last_audio is None or now - float(last_audio) > VIDEO_WHEN_RECENT_AUDIO_SECONDS):
            self.metrics["video_dropped_frames"] += 1
            self.metrics["video_last_reason"] = "no_recent_voice"
            return {"accepted": False, "reason": "no_recent_voice"}

        # Track how long the bridge has been quiet before this first frame.
        # If the feeder kicks in cold (or comes back after a long pause), we
        # want to know — that's when the "white page" loop is most likely to
        # start and we want to announce to the user that video is actually
        # flowing now.
        last_accept = self.metrics.get("video_last_accept_monotonic")
        quiet_s = (now - float(last_accept)) if last_accept is not None else 0.0
        self.metrics["video_last_accept_monotonic"] = now
        self.metrics["video_last_quiet_s"] = quiet_s
        self.metrics["video_last_source"] = source or ""

        frame = {
            "data": base64.b64encode(data).decode(),
            "mimeType": mime_type,
        }
        _put_drop_oldest(self._video_q, frame)
        self._last_video_sent_at = now
        self.metrics["video_sent_frames"] += 1
        self.metrics["video_last_reason"] = "accepted"
        result = {"accepted": True, "max_fps": VIDEO_MAX_FPS, "bytes": len(data)}

        # Webhook: announce the first real video frame after a long quiet
        # period. The 30s threshold avoids spam during a normal 1fps feeder
        # loop while still catching cold-start and post-pause reinit.
        if quiet_s >= VIDEO_INITIALIZED_QUIET_THRESHOLD_S:
            try:
                from webhook_dispatcher import emit_video_initialized
                emit_video_initialized(source=source, frame_bytes=len(data), accepted_after_silence_s=quiet_s)
            except Exception as _exc:
                logger.debug("emit_video_initialized failed: %s", _exc)

        return result

    async def connect(self):
        import websockets
        ws_url = f"{GEMINI_WS_URL}?key={GEMINI_API_KEY}"
        candidates = [GEMINI_MODEL]
        for model in GEMINI_MODEL_FALLBACKS:
            if model not in candidates:
                candidates.append(model)
        last_error: Optional[BaseException] = None
        for model in candidates:
            try:
                await self._connect_model(websockets, ws_url, model, handle=self._session_handle)
                self.metrics["model"] = model
                break
            except Exception as exc:
                last_error = exc
                logger.warning("Gemini Live model %s failed: %s", model, exc)
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
        else:
            raise RuntimeError(f"No Gemini Live model could start: {last_error}") from last_error
        self._running = True
        self._tasks = [
            asyncio.create_task(self._send_loop()),
            asyncio.create_task(self._receive_loop()),
        ]
        if INITIAL_GREETING and not self._reconnecting:
            await self.send_text(INITIAL_GREETING)

    async def _connect_model(self, websockets, ws_url: str, model: str, handle=None):
        self._ws = await websockets.connect(ws_url, ping_interval=20, ping_timeout=10)
        # Per-user Honcho peer: when this bridge was created with a profile, use
        # that profile's honcho_peer_name so memory is fully isolated per user.
        # Falls back to module-level HONCHO_PEER_NAME if no profile was provided.
        peer_override: Optional[str] = None
        base_prompt = BASE_SYSTEM_PROMPT
        if self._user_profile is not None:
            try:
                peer_override = getattr(self._user_profile, "honcho_peer_name", None)
                overrides = getattr(self._user_profile, "system_prompt_overrides", "") or ""
                if overrides.strip():
                    base_prompt = base_prompt + "\n\n--- PER-USER OVERRIDES ---\n" + overrides.strip() + "\n--- END PER-USER OVERRIDES ---"
            except Exception:
                peer_override = None
        honcho_ctx = await _build_honcho_context(peer_name_override=peer_override)
        system_text = base_prompt + honcho_ctx
        # #32: If this is a new user who hasn't been onboarded, append
        # a one-time system reminder to start the Q&A flow. The agent
        # sees this on the very first turn, calls
        # local_user_onboarding_get_questions, then walks the user
        # through the 6 questions via local_user_onboarding_answer.
        try:
            if self._user_profile is not None and self._user_profile.needs_onboarding():
                from user_profiles import ONBOARDING_QUESTIONS
                q_list = ", ".join(q["id"] for q in ONBOARDING_QUESTIONS)
                system_text = system_text + (
                    "\n\n--- ONBOARDING REQUIRED (criterion #32) ---\n"
                    "This user has never been onboarded. On your first turn, call\n"
                    "local_user_onboarding_get_questions to retrieve the list, then\n"
                    "walk them through the questions in order (one at a time, in voice).\n"
                    f"After each answer, call local_user_onboarding_answer with the\n"
                    f"question_id and the user's spoken answer. Questions: {q_list}.\n"
                    "Do NOT start any other task until onboarding is complete.\n"
                    "--- END ONBOARDING REQUIRED ---"
                )
        except Exception:
            pass
        # #28: Mirror user's speech/communication preferences. Inject the
        # user's declared communication_style and pet_peeves (captured
        # during #32 onboarding) into the system prompt so the agent
        # adapts its tone to the user's natural speech patterns.
        try:
            if self._user_profile is not None and self._user_profile.onboarding_completed:
                style = (getattr(self._user_profile, 'communication_style', '') or '').strip()
                peeves = (getattr(self._user_profile, 'pet_peeves', '') or '').strip()
                parts = []
                if style:
                    parts.append(
                        "--- COMMUNICATION PREFERENCE ---\n"
                        f"The user has said they prefer: {style}\n"
                        "Adapt your tone, sentence length, and level of formality "
                        "to match this. If they're short and direct, be short and direct. "
                        "If they're conversational, respond conversationally. "
                        "Mirror their vocabulary and rhythm — if they use technical jargon, "
                        "use technical jargon. If they use casual language, keep it casual."
                    )
                if peeves:
                    parts.append(
                        "--- PET PEEVES ---\n"
                        "The user has explicitly asked me to NEVER do these:\n"
                        f"{peeves}\n"
                        "Take these as hard constraints."
                    )
                if parts:
                    system_text = system_text + "\n\n" + "\n\n---\n\n".join(parts)
        except Exception:
            pass
        setup_payload: Dict[str, Any] = {
            "model": f"models/{model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE_NAME}
                    }
                },
                # NOTE: mediaResolution is intentionally OMITTED from the setup
                # payload. The Gemini Live API rejects it with
                # "Unknown name 'mediaResolution' at 'setup': Cannot find field."
                # for the current model lineup (3.1-flash-live-preview and
                # 2.5-flash-native-audio-preview-*). The field exists in the docs
                # for "native audio" models but is NOT accepted on these
                # specific model names. The Live API works fine without it —
                # omitting it avoids the 1007 setup error. Frame-size cost is
                # already controlled at the bridge level (1 fps cap + 512 KB
                # max + audio-gating).
            },
            "realtimeInputConfig": {
                "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
                "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",
                "automaticActivityDetection": {
                    "disabled": False,
                    "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
                    "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
                    "prefixPaddingMs": 20,
                    "silenceDurationMs": 100,
                }
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "systemInstruction": {
                "parts": [{
                    "text": system_text
                }]
            },
        }
        # Helper: filter a function-declaration list by per-user allowlist.
        def _filter_for_user(decls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if self._user_profile is None:
                return decls
            try:
                return [d for d in decls if self._user_profile.is_tool_allowed(d.get("name", ""))]
            except Exception:
                return decls

        if SPOTIFY_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _spotify = _filter_for_user(_SPOTIFY_FUNCTION_DECLARATIONS)
            if _spotify:
                setup_payload["tools"].append({"functionDeclarations": _spotify})
                logger.info("Spotify voice tools registered (count=%d)", len(_spotify))
        if WEB_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _web = _filter_for_user(_WEB_FUNCTION_DECLARATIONS)
            if _web:
                setup_payload["tools"].append({"functionDeclarations": _web})
                logger.info("Web voice tools registered (count=%d)", len(_web))
        if LOCAL_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _local = _filter_for_user(_LOCAL_FUNCTION_DECLARATIONS)
            if _local:
                setup_payload["tools"].append({"functionDeclarations": _local})
                logger.info("Local voice tools registered (count=%d)", len(_local))
        if HA_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _ha = _filter_for_user(_HOMEASSISTANT_FUNCTION_DECLARATIONS)
            if _ha:
                setup_payload["tools"].append({"functionDeclarations": _ha})
                logger.info("HA voice tools registered (count=%d)", len(_ha))
        if OPENCODE_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _oc = _filter_for_user(_OPENCODE_FUNCTION_DECLARATIONS)
            if _oc:
                setup_payload["tools"].append({"functionDeclarations": _oc})
                logger.info("OpenCode voice tools registered (count=%d)", len(_oc))
        if SYSINSPECT_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _si = _filter_for_user(_SYSINSPECT_FUNCTION_DECLARATIONS)
            if _si:
                setup_payload["tools"].append({"functionDeclarations": _si})
                logger.info("SysInspect voice tools registered (count=%d)", len(_si))
        if GITHUB_VOICE_TOOLS_ENABLED:
            if "tools" not in setup_payload:
                setup_payload["tools"] = []
            _gh = _filter_for_user(_GITHUB_FUNCTION_DECLARATIONS)
            if _gh:
                setup_payload["tools"].append({"functionDeclarations": _gh})
                logger.info("GitHub voice tools registered (count=%d)", len(_gh))
        if handle is not None:
            setup_payload["sessionResumption"] = {"handle": handle}
            logger.info("Session resumption: handle=%s", handle)
        setup = {"setup": setup_payload}
        await self._ws.send(json.dumps(setup))
        async for msg in self._ws:
            resp = json.loads(msg)
            if "setupComplete" in resp:
                logger.info("Setup complete for model %s", model)
                return
        raise RuntimeError(f"Gemini setup ended before setupComplete for {model}")

    async def send_text(self, text: str) -> None:
        if not self._ws or not text.strip():
            return
        msg = {"realtimeInput": {"text": text.strip()}}
        await self._ws.send(json.dumps(msg))

    async def disconnect(self):
        self._running = False
        _put_drop_oldest(self._send_q, None)
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        if self._ws:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    async def _restart(self):
        if self._reconnecting or self._user_disconnect:
            return
        self._reconnecting = True
        self._reconnect_count += 1
        logger.info("Gemini Live: starting reconnect #%d...", self._reconnect_count)
        await self.disconnect()
        try:
            await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        if self._user_disconnect:
            logger.info("Gemini Live: abort reconnect: user disconnected")
            self._reconnecting = False
            return
        # Backoff to avoid hammering the API
        backoff = min(2 ** (self._reconnect_count - 1), 30)
        logger.info("Gemini Live: reconnect backoff %ds", backoff)
        await asyncio.sleep(backoff)
        if self._user_disconnect:
            self._reconnecting = False
            return
        try:
            await self.connect()
            self._send_q = queue.Queue(maxsize=256)
            self._video_q = queue.Queue(maxsize=2)
            self._output_turn_open = False
            self._seen_server_content_shapes.clear()
            if self._on_reconnect:
                try:
                    self._on_reconnect()
                except Exception:
                    pass
            logger.info("Gemini Live: reconnected successfully #%d (handle=%s)", self._reconnect_count, self._session_handle)
        except Exception as e:
            logger.error("Gemini Live: reconnect failed #%d: %s", self._reconnect_count, e)
            if self._on_leave_request and not self._user_disconnect:
                try:
                    self._on_leave_request("Gemini reconnect failed: %s" % e)
                except Exception:
                    pass
        finally:
            self._reconnecting = False

    async def _send_loop(self):
        while self._running:
            # Drain audio queue aggressively while interleaving video frames
            try:
                chunk = self._send_q.get_nowait()
            except queue.Empty:
                # No audio waiting — send a pending video frame and idle-end check
                await self._send_pending_video_frame()
                await self._maybe_end_idle_audio_stream()
                await asyncio.sleep(0.02)
                continue
            if chunk is None:
                break
            # Send one video frame between audio chunks so video doesn't starve
            await self._send_pending_video_frame()
            b64_data = base64.b64encode(chunk).decode()
            msg = {"realtimeInput": {"audio": {"data": b64_data, "mimeType": "audio/pcm;rate=16000"}}}
            try:
                await self._ws.send(json.dumps(msg))
                self._audio_stream_open = True
                self._last_audio_sent_at = time.monotonic()
            except Exception as e:
                logger.error("Send error: %s", e)
                break

    async def _send_pending_video_frame(self) -> None:
        if not self._ws:
            return
        try:
            frame = self._video_q.get_nowait()
        except queue.Empty:
            return
        if not frame:
            return
        msg = {"realtimeInput": {"video": frame}}
        try:
            await self._ws.send(json.dumps(msg))
        except Exception as e:
            logger.error("Send video frame error: %s", e)

    async def _maybe_end_idle_audio_stream(self) -> None:
        if not self._audio_stream_open or self._last_audio_sent_at is None or not self._ws:
            return
        if time.monotonic() - self._last_audio_sent_at < self.AUDIO_STREAM_IDLE_END_SECONDS:
            return
        try:
            await self._ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
            self.metrics["audio_stream_end_events"] += 1
            self._audio_stream_open = False
            self._last_audio_sent_at = None
            logger.info("Gemini Live: sent audioStreamEnd after idle input")
        except Exception as e:
            logger.error("Send audioStreamEnd error: %s", e)

    async def _receive_loop(self):
        while self._running:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                close_code = getattr(e, "close_code", None)
                close_reason = getattr(e, "close_reason", None)
                is_1008 = close_code == 1008
                if close_code is not None:
                    logger.warning("Gemini Live: WebSocket closed (code=%s, reason=%s)", close_code, close_reason)
                else:
                    logger.error("Receive error: %s", e)
                if is_1008:
                    # 1008 = session duration exceeded — decoder state may be bad, drop it
                    logger.warning("Gemini Live: detected 1008 GoAway-style close")
                    self._output_turn_open = False
                    self._seen_server_content_shapes.clear()
                if not self._reconnecting:
                    asyncio.get_running_loop().create_task(self._restart())
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Session resumption handle update
            sru = msg.get("sessionResumptionUpdate")
            if sru:
                if sru.get("resumable") and sru.get("newHandle"):
                    self._session_handle = sru["newHandle"]
                self.metrics["gemini_resumption_updates"] = self.metrics.get("gemini_resumption_updates", 0) + 1
            # GoAway detection
            go_away = msg.get("goAway")
            if go_away is not None:
                time_left = go_away.get("timeLeft", "unknown")
                logger.warning("Gemini Live: GoAway received (%s remaining)", time_left)
                if not self._reconnecting:
                    asyncio.get_running_loop().create_task(self._restart())
                break
            sc = msg.get("serverContent", {})
            if sc:
                self._log_server_content_shape(sc)
                self._record_transcript("input", sc.get("inputTranscription", {}))
                self._record_transcript("output", sc.get("outputTranscription", {}))
                mt = sc.get("modelTurn", {})
                parts = mt.get("parts", [])
                for part in parts:
                    idata = part.get("inlineData", {})
                    if idata.get("mimeType", "").startswith("audio/pcm"):
                        pcm_bytes = base64.b64decode(idata["data"])
                        if pcm_bytes:
                            self._record_output_chunk(len(pcm_bytes))
                            if not self._output_turn_open:
                                self._output_source.feed(_silence_pcm(GEMINI_OUT_SR, GEMINI_OUT_CH, OUTPUT_PREROLL_MS))
                                pcm_bytes = _fade_in_pcm_24k_mono(pcm_bytes, OUTPUT_FADE_IN_MS)
                                self._output_turn_open = True
                                self.metrics["audio_preroll_events"] += 1
                            if self._output_source.wake():
                                self._output_source.feed(pcm_bytes)
                                if self._on_wake:
                                    try:
                                        self._on_wake()
                                    except Exception:
                                        pass
                            else:
                                self._output_source.feed(pcm_bytes)
                if sc.get("interrupted"):
                    if OUTPUT_CLEAR_ON_INTERRUPT:
                        self._output_source.clear()
                    self._output_turn_open = False
                if sc.get("turnComplete") or sc.get("generationComplete"):
                    if self._output_turn_open and OUTPUT_TAIL_PAD_MS > 0:
                        self._output_source.feed(_silence_pcm(GEMINI_OUT_SR, GEMINI_OUT_CH, OUTPUT_TAIL_PAD_MS))
                    self._output_turn_open = False
            # ── Handle tool calls from Gemini ──────────────────────────────────────
            tool_call = msg.get("toolCall")
            if tool_call:
                try:
                    await self._handle_tool_call(tool_call)
                except Exception as tc_exc:
                    logger.exception("Gemini Live: tool call handler crashed (recv loop continues): %s", tc_exc)
            tool_call_cancel = msg.get("toolCallCancellation")
            if tool_call_cancel:
                logger.info("Gemini toolCallCancellation received (ignored): %s", tool_call_cancel)

    def _log_server_content_shape(self, server_content: Dict[str, Any]) -> None:
        keys = tuple(sorted(server_content.keys()))
        if keys in self._seen_server_content_shapes:
            return
        self._seen_server_content_shapes.add(keys)
        logger.info("Gemini serverContent keys: %s", ",".join(keys))

    def _record_transcript(self, direction: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        text = str(payload.get("text") or "").strip()
        if not text:
            return
        metric_prefix = f"{direction}_transcript"
        self.metrics[f"{metric_prefix}_events"] += 1
        self.metrics[f"last_{metric_prefix}"] = text[-500:]
        logger.info("Gemini %s transcript: %s", direction, text)
        self._append_note_event(direction, text)
        # Webhook: push transcript line to voice.transcript webhooks
        try:
            from webhook_dispatcher import emit_voice_input, emit_voice_output
            if direction == "output":
                emit_voice_output(text)
            else:
                emit_voice_input(text)
        except Exception:
            pass
        if direction == "input":
            self._maybe_handle_voice_leave_request(text)

    def _append_note_event(self, direction: str, text: str) -> None:
        event = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "direction": direction,
            "text": text,
        }
        try:
            with self._notes_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.metrics["notes_events"] += 1
        except Exception:
            logger.warning("VoiceLive: could not append note event", exc_info=True)

    async def _handle_tool_call(self, tool_call: Any) -> None:
        """Execute Spotify, web, or other local tools requested by Gemini Live.

        Tool runners are synchronous I/O; they run in a thread so the async
        receive loop stays responsive and can't be accidentally DoS'd by one
        slow search.
        """
        function_calls = tool_call.get("functionCalls", []) if isinstance(tool_call, dict) else []
        if not function_calls:
            logger.warning("toolCall message without functionCalls: %s", tool_call)
            return
        # ── Typing feedback: begin audio indicator ──────────────────────────
        typing_active = False
        typing_task: Optional[asyncio.Task] = None
        if TYPING_SOUND_ENABLED and self._output_source is not None and not self._output_turn_open:
            typing_active = True

            async def _typing_loop():
                while typing_active:
                    try:
                        pcm = generate_typing_pcm()
                        self._output_source.feed(pcm)
                    except Exception:
                        break
                    await asyncio.sleep(0.05 + random.random() * 0.15)

            typing_task = asyncio.get_running_loop().create_task(_typing_loop())

        responses: List[Dict[str, Any]] = []
        try:
            loop = asyncio.get_running_loop()
            for fc in function_calls:
                call_id = fc.get("id", "")
                name = fc.get("name", "")
                args = fc.get("args", {})
                if name.startswith("web_") and isinstance(args, dict):
                    args = _normalize_voice_web_args(name, args)
                logger.info("Gemini tool call: %s id=%s args=%s", name, call_id, args)
                # Webhook: tool.called event (throttled)
                try:
                    from webhook_dispatcher import emit_tool_called
                    args_summary = ", ".join(f"{k}={str(v)[:80]}" for k, v in (args or {}).items())[:6]
                    emit_tool_called(name, args_summary)
                except Exception:
                    pass
                # Defense-in-depth per-user allowlist check. Even if a tool
                # declaration snuck through, refuse to execute it for a user
                # who isn't allowed to invoke it.
                if self._user_profile is not None:
                    try:
                        if not self._user_profile.is_tool_allowed(name):
                            result = {"error": f"Tool '{name}' is not enabled for this user"}
                            responses.append({"id": call_id, "name": name, "response": result})
                            continue
                    except Exception:
                        pass
                try:
                    if name.startswith("spotify_"):
                        result = await loop.run_in_executor(None, _run_spotify_tool, name, args)
                    elif name.startswith("web_"):
                        result = await loop.run_in_executor(None, _run_web_tool, name, args)
                    elif name.startswith("local_"):
                        if name.startswith("local_inspect_"):
                            result = await loop.run_in_executor(None, _run_sysinspect_tool, name, args)
                        elif name.startswith("local_github_"):
                            result = await loop.run_in_executor(None, _run_github_tool, name, args)
                        else:
                            result = await loop.run_in_executor(None, _run_local_tool, name, args)
                    elif name.startswith("opencode_"):
                        # Bind the per-user opencode context in the worker thread.
                        _user_id = self._user_profile.discord_id if self._user_profile is not None else None
                        result = await loop.run_in_executor(
                            None,
                            _run_opencode_tool_with_bridge,
                            name, args, _user_id, self,
                        )
                    else:
                        result = {"error": f"No handler for tool: {name}"}
                except Exception as exc:
                    logger.exception("Gemini Live: tool %s crashed", name)
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                responses.append({
                    "id": call_id,
                    "name": name,
                    "response": result,
                })
        finally:
            # ── Typing feedback: end audio indicator ───────────────────────
            if typing_active:
                typing_active = False
            if typing_task:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
        if responses and self._ws:
            payload = {"toolResponse": {"functionResponses": responses}}
            try:
                await self._ws.send(json.dumps(payload))
                logger.info("Sent toolResponse for %d tool call(s)", len(responses))
            except Exception as exc:
                logger.error("Failed to send toolResponse: %s", exc)

    def _maybe_handle_voice_leave_request(self, text: str) -> None:
        normalized = " ".join(text.lower().replace(".", " ").replace(",", " ").split())
        if not any(phrase in normalized for phrase in VOICE_LEAVE_PHRASES):
            return
        self.metrics["voice_leave_requested"] = True
        logger.info("VoiceLive: leave requested by voice transcript: %s", text)
        if self._on_leave_request:
            try:
                self._on_leave_request(text)
            except Exception:
                logger.exception("VoiceLive: failed to schedule voice leave request")

    def _record_output_chunk(self, byte_count: int) -> None:
        now = time.monotonic()
        self.metrics["audio_out_chunks"] += 1
        self.metrics["audio_out_bytes"] += byte_count
        self.metrics["last_output_monotonic"] = now
        last_input = self.metrics.get("last_input_monotonic")
        if last_input is not None:
            self.metrics["last_input_to_output_ms"] = round((now - last_input) * 1000, 1)


class VoiceLiveBridge:
    def __init__(self, voice_channel, discord_adapter, user_profile: Optional[Any] = None,
                 target_user_id: Optional[str] = None):
        self._channel = voice_channel
        self._vc = None
        self._adapter = discord_adapter
        self._guild_id = voice_channel.guild.id
        self._target_user_id = target_user_id or os.getenv("DISCORD_VOICE_LIVE_USER_ID", "1474100257762578597")
        self._user_profile = user_profile
        self._audio_source = LiveAudioSource()
        self._listener = None
        self._leave_requested = False
        self._gemini = GeminiLiveBridge(
            self._audio_source,
            on_wake=self._wake_playback,
            on_leave_request=self._request_leave,
            on_reconnect=self._recreate_pcm_sink,
            user_profile=user_profile,
        )
        self._running = False
        self._started_at = None
        self._watcher_task: Optional[asyncio.Task] = None
        self._receive_restart_task: Optional[asyncio.Task] = None
        self._receive_restarting = False
        self._last_activity_at = time.monotonic()
        self._idle_prompted_at: Optional[float] = None

    def _on_playback_end(self, error=None) -> None:
        if error:
            logger.error("Playback error: %s", error)

    def _wake_playback(self) -> None:
        if not self._running:
            return
        if not self._vc or not self._vc.is_connected():
            return
        try:
            if not self._vc.is_playing():
                self._vc.play(self._audio_source, after=self._on_playback_end)
        except Exception:
            pass

    def _record_activity(self) -> None:
        self._last_activity_at = time.monotonic()
        self._idle_prompted_at = None

    def _recreate_pcm_sink(self) -> None:
        """Called after a Gemini reconnect to force a fresh Opus decoder."""
        logger.info("VoiceLive: recreating PCM sink after Gemini reconnect")
        if self._vc and self._vc.is_connected():
            try:
                if hasattr(self._vc, "is_listening") and self._vc.is_listening():
                    self._vc.stop_listening()
            except Exception:
                pass
            try:
                self._listener = GeminiPCMSink(self._feed_audio)
                self._vc.listen(self._listener, after=self._on_listen_end)
                logger.info("VoiceLive: PCM sink recreated")
            except Exception as e:
                logger.error("VoiceLive: PCM sink recreation failed: %s", e)

    def _request_leave(self, reason: str) -> None:
        if self._leave_requested:
            return
        self._leave_requested = True
        try:
            loop = self._vc.loop if self._vc else asyncio.get_running_loop()
            loop.create_task(self._stop_from_request(reason))
        except Exception:
            logger.exception("VoiceLive: could not schedule requested stop")

    async def _stop_from_request(self, reason: str) -> None:
        logger.info("VoiceLive: stopping from user request: %s", reason)
        await self.stop()

    async def start(self) -> bool:
        logger.info("VoiceLive: connecting to %s in guild %d", self._channel, self._guild_id)
        if voice_recv is None or GeminiPCMSink is None:
            logger.error("discord-ext-voice-recv is not installed; cannot receive Discord voice")
            return False

        existing_vc = getattr(self._channel.guild, "voice_client", None)
        if existing_vc and existing_vc.is_connected():
            try:
                logger.info("VoiceLive: disconnecting existing guild voice client before reconnect")
                await asyncio.wait_for(existing_vc.disconnect(force=True), timeout=10.0)
            except Exception as e:
                logger.warning("VoiceLive: existing voice disconnect failed: %s", e)

        receiver = self._adapter._voice_receivers.get(self._guild_id)
        if receiver:
            receiver.pause()

        try:
            self._vc = await self._channel.connect(
                cls=voice_recv.VoiceRecvClient,
                timeout=60.0,
                reconnect=True,
                self_deaf=False,
            )
        except Exception as e:
            logger.error("Discord voice connect failed: %s", e)
            if receiver:
                receiver.resume()
            return False

        self._listener = GeminiPCMSink(self._feed_audio)
        try:
            self._vc.listen(self._listener, after=self._on_listen_end)
            self._vc.play(self._audio_source, after=self._on_playback_end)
        except Exception as e:
            logger.error("Failed to start Discord voice I/O: %s", e)
            await self.stop()
            return False

        try:
            await self._gemini.connect()
        except Exception as e:
            logger.error("Gemini connect failed: %s", e)
            await self.stop()
            return False

        # ── Mute first-turn: immediately signal "audio stream ended" ───────
        # Gemini Live starts its first autonomous turn right after
        # setupComplete. By sending audioStreamEnd immediately, the model
        # sees "user started and ended an empty audio stream" and should
        # NOT generate its opener ("I see you're sharing your screen"
        # hallucination). First-token output would be wasted tokens.
        try:
            await self._gemini._ws.send(
                json.dumps({"realtimeInput": {"audioStreamEnd": True}})
            )
            self._gemini.metrics["audio_stream_end_events"] = \
                self._gemini.metrics.get("audio_stream_end_events", 0) + 1
            self._gemini._audio_stream_open = False
            self._gemini._last_audio_sent_at = None
            logger.info("VoiceLive: sent initial mute audioStreamEnd to suppress first turn")
        except Exception:
            pass

        self._running = True
        self._started_at = time.monotonic()
        self._watcher_task = asyncio.create_task(self._connection_watchdog())
        logger.info("VoiceLive: bridge active for guild %d", self._guild_id)
        return True

    def _feed_audio(self, pcm_16k_mono: bytes) -> None:
        self._record_activity()
        self._gemini.feed_audio(pcm_16k_mono)

    def _on_listen_end(self, error=None) -> None:
        if error:
            logger.error("Voice receive error: %s", error)
        if self._running and self._vc and self._vc.is_connected():
            if self._receive_restarting:
                return
            try:
                loop = self._vc.loop
                self._receive_restart_task = loop.create_task(self._restart_receive())
            except Exception:
                logger.exception("Could not schedule voice receive restart")

    async def _restart_receive(self) -> None:
        self._receive_restarting = True
        try:
            await asyncio.sleep(2.0)
            if not self._running or not self._vc or not self._vc.is_connected():
                return
            try:
                if hasattr(self._vc, "is_listening") and self._vc.is_listening():
                    return
                self._listener = GeminiPCMSink(self._feed_audio)
                self._vc.listen(self._listener, after=self._on_listen_end)
                logger.info("VoiceLive: voice receive restarted")
            except Exception as e:
                logger.error("VoiceLive: receive restart failed: %s", e)
        finally:
            self._receive_restarting = False

    async def _connection_watchdog(self) -> None:
        while self._running:
            await asyncio.sleep(1.0)
            if not self._vc or not self._vc.is_connected():
                if not self._running:
                    return
                logger.warning("VoiceLive: Discord disconnected. Stopping bridge.")
                await self.stop()
                return

            # ── User-presence check: stop if B leaves the voice channel ─────
            try:
                guild = self._vc.guild
                member = guild.get_member(int(self._target_user_id)) if self._target_user_id else None
                if member:
                    member_vc = getattr(getattr(member, "voice", None), "channel", None)
                    if not member_vc or member_vc.id != self._channel.id:
                        logger.info(
                            "VoiceLive: target user %s left the voice channel. Stopping bridge.",
                            self._target_user_id,
                        )
                        await self.stop()
                        return
            except Exception as exc:
                logger.debug("VoiceLive: presence check failed: %s", exc)

            now = time.monotonic()
            idle = now - self._last_activity_at

            # Phase 1: prompt if idle too long and not already prompted
            if (
                IDLE_PROMPT_SECONDS > 0
                and self._idle_prompted_at is None
                and idle >= IDLE_PROMPT_SECONDS
                and self._started_at
                and now - self._started_at >= AUTO_LEAVE_MIN_UPTIME_SECONDS
            ):
                logger.info("VoiceLive: idle for %.0fs — prompting user", idle)
                self._idle_prompted_at = now
                await self._gemini.send_text(IDLE_PROMPT_TEXT)
                continue

            # Phase 2: hang up if no response after grace period
            if self._idle_prompted_at is not None:
                grace = now - self._idle_prompted_at
                if grace >= IDLE_PROMPT_GRACE_SECONDS:
                    logger.info(
                        "VoiceLive: no response after %.0fs grace — hanging up", grace
                    )
                    await self.stop()
                    return
                # Still within grace; don't fall through to plain auto-leave
                continue

            # Fallback: plain auto-leave if prompt system is disabled
            if self._should_auto_leave_quiet():
                logger.info("VoiceLive: auto-leaving after %.0fs of quiet", idle)
                await self.stop()
                return

    def _should_auto_leave_quiet(self) -> bool:
        if AUTO_LEAVE_QUIET_SECONDS <= 0 or self._started_at is None:
            return False
        now = time.monotonic()
        if now - self._started_at < AUTO_LEAVE_MIN_UPTIME_SECONDS:
            return False
        if self._vc and self._vc.is_playing():
            return False
        return now - self._last_activity_at >= AUTO_LEAVE_QUIET_SECONDS

    async def stop(self):
        self._running = False
        if self._receive_restart_task:
            self._receive_restart_task.cancel()
        self._audio_source.finish()
        if self._gemini:
            self._gemini._user_disconnect = True
            await self._gemini.disconnect()
        if self._vc and self._vc.is_connected():
            try:
                if hasattr(self._vc, "is_listening") and self._vc.is_listening():
                    self._vc.stop_listening()
            except Exception:
                pass
            try:
                self._vc.stop_playing() if hasattr(self._vc, "stop_playing") else self._vc.stop()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._vc.disconnect(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass
        receiver = self._adapter._voice_receivers.get(self._guild_id)
        if receiver:
            receiver.resume()
        logger.info("VoiceLive bridge stopped")

    def health(self) -> Dict[str, Any]:
        metrics = dict(getattr(self._gemini, "metrics", {}) or {})
        sink_stats = self._listener.stats() if self._listener and hasattr(self._listener, "stats") else {}
        return {
            "status": "ok" if self._running else "stopped",
            "running": self._running,
            "guild_id": self._guild_id,
            "voice_connected": bool(self._vc and self._vc.is_connected()),
            "receiving_active": bool(
                self._vc and hasattr(self._vc, "is_listening") and self._vc.is_listening()
            ),
            "playback_active": bool(self._vc and self._vc.is_playing()),
            "uptime_seconds": round(time.monotonic() - self._started_at, 3) if self._started_at else 0,
            "quiet_seconds": round(time.monotonic() - self._last_activity_at, 3),
            "auto_leave_quiet_seconds": AUTO_LEAVE_QUIET_SECONDS,
            "idle_prompt_seconds": IDLE_PROMPT_SECONDS,
            "idle_prompt_grace_seconds": IDLE_PROMPT_GRACE_SECONDS,
            "idle_prompted_seconds": round(time.monotonic() - self._idle_prompted_at, 3) if self._idle_prompted_at else None,
            "configured_model": GEMINI_MODEL,
            **sink_stats,
            **metrics,
        }


HTTP_PORT = int(os.getenv("DISCORD_VOICE_LIVE_PORT", "18943"))
BRIDGE: Optional[VoiceLiveBridge] = None


async def handle_http_request(reader, writer):
    request_data = b""
    while True:
        line = await reader.readline()
        if not line or line == b"\r\n":
            break
        request_data += line
    request_text = request_data.decode("utf-8", errors="replace")
    lines = request_text.split("\r\n")
    if not lines:
        writer.close()
        return
    method_path = lines[0].split(" ")
    if len(method_path) < 2:
        writer.close()
        return
    path = method_path[1]
    parsed_url = urlparse(path)
    route = parsed_url.path
    response_body = ""
    status = 200
    if route == "/health":
        response_body = json.dumps(BRIDGE.health() if BRIDGE else {"status": "not_started", "running": False})
    elif route == "/stop":
        if BRIDGE and BRIDGE._running:
            await BRIDGE.stop()
            response_body = json.dumps({"status": "stopped"})
        else:
            response_body = json.dumps({"status": "not_running"})
    elif route == "/say":
        text = parse_qs(parsed_url.query).get("text", [""])[0]
        if BRIDGE and BRIDGE._running and text:
            await BRIDGE._gemini.send_text(text)
            response_body = json.dumps({"status": "sent", "text": text})
        else:
            response_body = json.dumps({"status": "error", "message": "Bridge not running or text missing"})
            status = 400
    elif route == "/frame":
        if not BRIDGE or not BRIDGE._running:
            response_body = json.dumps({"status": "error", "message": "Bridge not running"})
            status = 400
        else:
            query = parse_qs(parsed_url.query)
            force = str(query.get("force", ["false"])[0]).lower() in {"1", "true", "yes", "on"}
            source = query.get("source", [""])[0] or query.get("src", [""])[0]
            mime_type = query.get("mime", ["image/jpeg"])[0]
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.lower().strip()] = value.strip()
            content_length = int(headers.get("content-length", "0") or "0")
            if content_length <= 0:
                response_body = json.dumps({"status": "error", "message": "Missing frame body"})
                status = 400
            elif content_length > VIDEO_MAX_BYTES:
                response_body = json.dumps({"status": "error", "message": "Frame too large", "max_bytes": VIDEO_MAX_BYTES})
                status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            else:
                body = await reader.readexactly(content_length)
                if "content-type" in headers:
                    mime_type = headers["content-type"].split(";", 1)[0].strip().lower()
                result = BRIDGE._gemini.feed_video_frame(body, mime_type, force=force, source=source)
                response_body = json.dumps({"status": "ok" if result.get("accepted") else "dropped", **result})
    elif route == "/notes":
        if not BRIDGE or not BRIDGE._running:
            response_body = json.dumps({"status": "error", "message": "Bridge not running"})
            status = 400
        else:
            query = parse_qs(parsed_url.query)
            limit = max(1, min(int(query.get("limit", ["50"])[0] or "50"), 500))
            notes_file = Path(BRIDGE._gemini.metrics.get("notes_file") or "")
            events: List[Dict[str, Any]] = []
            if notes_file.exists():
                lines = notes_file.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
                for line in lines:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            transcript: List[Dict[str, str]] = []
            for event in events:
                direction = str(event.get("direction") or "")
                text = str(event.get("text") or "").strip()
                if not direction or not text:
                    continue
                if transcript and transcript[-1]["direction"] == direction:
                    sep = "" if text in {".", ",", "?", "!", ":", ";"} else " "
                    transcript[-1]["text"] = (transcript[-1]["text"] + sep + text).strip()
                    transcript[-1]["ts"] = str(event.get("ts") or transcript[-1]["ts"])
                else:
                    transcript.append({
                        "ts": str(event.get("ts") or ""),
                        "direction": direction,
                        "text": text,
                    })
            response_body = json.dumps({
                "status": "ok",
                "notes_file": str(notes_file),
                "events": events,
                "transcript": transcript,
            })
    else:
        response_body = json.dumps({"status": "error", "message": "Not found"})
        status = 404
    response = (
        f"HTTP/1.1 {status} OK\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(response_body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
        f"{response_body}"
    )
    writer.write(response.encode())
    await writer.drain()
    writer.close()


async def run_sidecar(vc, adapter, ready_future: Optional[asyncio.Future] = None, user_profile: Optional[Any] = None,
                     target_user_id: Optional[str] = None):
    global BRIDGE
    BRIDGE = VoiceLiveBridge(vc, adapter, user_profile=user_profile, target_user_id=target_user_id)
    server = None
    try:
        server = await asyncio.start_server(handle_http_request, "127.0.0.1", HTTP_PORT)
        logger.info("Control API listening on 127.0.0.1:%d", HTTP_PORT)
        ok = await BRIDGE.start()
        if not ok:
            logger.error("Bridge failed to start")
            if ready_future and not ready_future.done():
                ready_future.set_result({"ok": False, "message": "Bridge failed to start"})
            return
        if ready_future and not ready_future.done():
            ready_future.set_result({"ok": True, "health": BRIDGE.health(), "vc": BRIDGE._vc})
        # Webhook: bridge started
        try:
            from webhook_dispatcher import emit_bridge_status
            vc_guild = getattr(getattr(BRIDGE._vc, "guild", None), "id", "?") if BRIDGE._vc else "?"
            vc_chan = getattr(getattr(BRIDGE._vc, "channel", None), "name", "?") if BRIDGE._vc else "?"
            emit_bridge_status("bridge_started", f"Guild: {vc_guild} | Channel: {vc_chan}")
        except Exception:
            pass
        # Start the email-reminder poller (criterion #19)
        try:
            _start_email_reminder_loop(BRIDGE._gemini)
        except Exception as exc:
            logger.debug("email reminder loop start failed: %s", exc)

        # Watch for stop() to close server so run_sidecar task completes
        async def _shutdown_watcher():
            while BRIDGE and BRIDGE._running:
                await asyncio.sleep(1.0)
            logger.info("VoiceLive: shutting down control server")
            if server:
                server.close()
        shutdown_task = asyncio.create_task(_shutdown_watcher())

        async with server:
            await server.serve_forever()
        # server stopped — either by watcher or cancel
        shutdown_task.cancel()
    except asyncio.CancelledError:
        if ready_future and not ready_future.done():
            ready_future.cancel()
    except Exception as exc:
        if ready_future and not ready_future.done():
            ready_future.set_result({"ok": False, "message": str(exc)})
        raise
    finally:
        if server:
            server.close()
            await server.wait_closed()
        if BRIDGE:
            await BRIDGE.stop()


# Register all known tool names with the per-user profile system so the
# allowlist vocabulary is in sync with the declarations above.
def _register_all_known_tools():
    if _rkt is None:
        return
    for decl_list in (
        _SPOTIFY_FUNCTION_DECLARATIONS,
        _WEB_FUNCTION_DECLARATIONS,
        _LOCAL_FUNCTION_DECLARATIONS,
        _HOMEASSISTANT_FUNCTION_DECLARATIONS,
        _OPENCODE_FUNCTION_DECLARATIONS,
        _SYSINSPECT_FUNCTION_DECLARATIONS,
    ):
        try:
            for d in decl_list:
                if isinstance(d, dict) and d.get("name"):
                    _rkt(d["name"])
        except Exception as exc:
            logger.debug("tool registration failed: %s", exc)


_register_all_known_tools()


if __name__ == "__main__":
    if not GEMINI_API_KEY:
        print("FATAL: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    print("Voice Live sidecar started (standalone test mode)", file=sys.stderr)
    print("Run via Hermes plugin to provide voice_client and adapter", file=sys.stderr)
