"""
sfx.py — Multi-slot UI sound effects loader + dispatcher (criterion #8).

Slots are env-var-mapped paths, with sensible defaults under
~/.hermes/voice-users/sfx/. Each slot's WAV is cached on first read.

Slots and the bridge events they cover:

  - tool_init     : bridge boot, first tool call (sounds good chime)
  - error         : tool error, fallback triggered
  - notification  : local_notify delivery, email brief, delegate done
  - transition    : session start / end, mode switch

A new `play_sfx(slot)` helper feeds PCM16 24kHz mono into the active
Gemini Live output source. If no source is active (e.g. bridge is idle),
the call is a no-op — sfx is a voice-session-only channel.

All paths are env-overridable. Missing files = silent no-op, not an error.
"""

import logging
import os
import threading
import wave
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("voice-sfx")

# ── Configuration ──────────────────────────────────────────────────────────

SFX_DIR = Path(os.getenv(
    "DISCORD_VOICE_LIVE_SFX_DIR",
    str(Path.home() / ".hermes" / "voice-users" / "sfx"),
)).expanduser()

# Per-slot env var → default path. Convention: DISCORD_VOICE_LIVE_SFX_<SLOT>
# Always uppercase the slot name.
DEFAULT_SFX_PATHS: Dict[str, str] = {
    "tool_init":    "tool_init.wav",
    "error":        "error.wav",
    "notification": "notification.wav",
    "transition":   "transition.wav",
}

# Per-slot volume. PCM16 [-32768, 32767], 1.0 = no scaling.
DEFAULT_SFX_VOLUMES: Dict[str, float] = {
    "tool_init":    0.55,
    "error":        0.45,
    "notification": 0.50,
    "transition":   0.60,
}

# Global enable
SFX_ENABLED = os.getenv("DISCORD_VOICE_LIVE_SFX_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


def _slot_env_var(slot: str) -> str:
    return f"DISCORD_VOICE_LIVE_SFX_{slot.upper()}"


def _slot_volume_env_var(slot: str) -> str:
    return f"DISCORD_VOICE_LIVE_SFX_{slot.upper()}_VOLUME"


def resolve_slot_path(slot: str) -> Optional[Path]:
    """Resolve the WAV path for a slot, or None if not configured / doesn't exist."""
    if not SFX_ENABLED:
        return None
    env_path = os.getenv(_slot_env_var(slot), "").strip()
    if env_path:
        p = Path(env_path).expanduser()
    else:
        default_name = DEFAULT_SFX_PATHS.get(slot)
        if not default_name:
            return None
        p = SFX_DIR / default_name
    if not p.exists():
        return None
    return p


def resolve_slot_volume(slot: str) -> float:
    v = os.getenv(_slot_volume_env_var(slot), "").strip()
    if v:
        try:
            return max(0.0, min(1.5, float(v)))
        except ValueError:
            pass
    return DEFAULT_SFX_VOLUMES.get(slot, 0.5)


# ── PCM cache ──────────────────────────────────────────────────────────────

# Cache: slot → PCM16 24kHz mono bytes (already normalized + volume-scaled)
_PCM_CACHE: Dict[str, bytes] = {}
_PCM_CACHE_LOCK = threading.Lock()
_TARGET_SR = 24000
_TARGET_CH = 1
_TARGET_SW = 2  # 16-bit


def _resample_to_target(frames: bytes, src_sr: int, src_ch: int, src_sw: int) -> bytes:
    """Resample WAV frames (any sr/ch/sample_width) to 24kHz mono PCM16.

    We use a simple numpy linear resampler; sfx are <2s so the small quality
    loss is acceptable and avoids a scipy dependency.
    """
    import numpy as np
    if src_sw == 2:
        raw = np.frombuffer(frames, dtype=np.int16)
    elif src_sw == 1:
        raw = (np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128) * 256
    else:
        # Fallback: skip files we don't understand (24-bit, 32-bit float, etc.)
        return b""
    if src_ch > 1:
        raw = raw.reshape(-1, src_ch).mean(axis=1).astype(np.int16)
    if src_sr != _TARGET_SR:
        # Linear resample
        duration = len(raw) / src_sr
        n_target = int(round(duration * _TARGET_SR))
        if n_target < 1:
            return b""
        x_old = np.linspace(0.0, duration, num=len(raw), endpoint=False)
        x_new = np.linspace(0.0, duration, num=n_target, endpoint=False)
        out = np.interp(x_new, x_old, raw.astype(np.float32)).astype(np.int16)
        raw = out
    return raw.tobytes()


def load_slot_pcm(slot: str) -> Optional[bytes]:
    """Load a slot's WAV as 24kHz mono PCM16, cached. Returns None on miss."""
    with _PCM_CACHE_LOCK:
        if slot in _PCM_CACHE:
            return _PCM_CACHE[slot]
    path = resolve_slot_path(slot)
    if path is None:
        return None
    try:
        with wave.open(str(path), "rb") as wf:
            src_ch = wf.getnchannels()
            src_sr = wf.getframerate()
            src_sw = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())
        pcm = _resample_to_target(frames, src_sr, src_ch, src_sw)
        if not pcm:
            return None
        # Apply per-slot volume
        vol = resolve_slot_volume(slot)
        if vol != 1.0:
            import numpy as np
            raw = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * vol
            pcm = np.clip(raw, -32768, 32767).astype(np.int16).tobytes()
        with _PCM_CACHE_LOCK:
            _PCM_CACHE[slot] = pcm
        logger.info("sfx: loaded slot=%s path=%s dur=%.2fs", slot, path, len(pcm) / (_TARGET_SR * _TARGET_SW))
        return pcm
    except Exception as exc:
        logger.debug("sfx: load failed slot=%s path=%s exc=%s", slot, path, exc)
        return None


def invalidate_cache(slot: Optional[str] = None) -> None:
    """Clear the PCM cache (e.g. after a file swap)."""
    with _PCM_CACHE_LOCK:
        if slot is None:
            _PCM_CACHE.clear()
        else:
            _PCM_CACHE.pop(slot, None)


# ── Active source registry ─────────────────────────────────────────────────
#
# The bridge's LiveAudioSource lives inside a GeminiLiveBridge instance
# owned by a VoiceLiveBridge. Multiple bridges can be active (per-user).
# We track the most-recently-active source by weakref.

_ACTIVE_SOURCES: Dict[str, Any] = {}  # session_id → (weakref to source, ts)
_ACTIVE_LOCK = threading.Lock()


def register_active_source(session_id: str, source: Any) -> None:
    """Mark `source` as the active output for `session_id`. Auto-cleans
    when the source is GC'd."""
    import weakref
    with _ACTIVE_LOCK:
        if source is not None:
            _ACTIVE_SOURCES[session_id] = (weakref.ref(source, lambda ref, sid=session_id: _forget(sid)), source)


def _forget(session_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_SOURCES.pop(session_id, None)


def pick_active_source() -> Optional[Any]:
    """Return a live (non-GC'd) source from the registry, or None."""
    with _ACTIVE_LOCK:
        for sid, (ref, src) in list(_ACTIVE_SOURCES.items()):
            if ref() is None:
                _ACTIVE_SOURCES.pop(sid, None)
                continue
            return src
        return None


# ── Public play helper ─────────────────────────────────────────────────────

def play_sfx(slot: str, source: Optional[Any] = None) -> Dict[str, Any]:
    """Play the sfx for `slot` into the active (or supplied) output source.

    Returns a small dict describing what happened. Never raises.
    """
    if not SFX_ENABLED:
        return {"status": "disabled", "slot": slot}
    pcm = load_slot_pcm(slot)
    if not pcm:
        return {"status": "no_sfx", "slot": slot, "path": str(resolve_slot_path(slot) or "(unset)")}
    src = source or pick_active_source()
    if src is None:
        return {"status": "no_active_source", "slot": slot}
    try:
        # LiveAudioSource.feed() takes 24kHz mono PCM16
        src.feed(pcm)
        return {"status": "played", "slot": slot, "bytes": len(pcm),
                "duration_s": len(pcm) / (_TARGET_SR * _TARGET_SW)}
    except Exception as exc:
        logger.debug("sfx: play failed slot=%s exc=%s", slot, exc)
        return {"status": "feed_failed", "slot": slot, "error": str(exc)}


def list_slots() -> Dict[str, Dict[str, Any]]:
    """Return a status report for every known slot — path, volume, exists?"""
    out: Dict[str, Dict[str, Any]] = {}
    for slot in DEFAULT_SFX_PATHS:
        p = resolve_slot_path(slot)
        out[slot] = {
            "path": str(p) if p else None,
            "exists": bool(p and p.exists()),
            "volume": resolve_slot_volume(slot),
            "cached_bytes": len(_PCM_CACHE.get(slot, b"")),
        }
    return out
