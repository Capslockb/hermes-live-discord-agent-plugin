#!/usr/bin/env python3
"""Wire SORA bridge element tools into plugin/__init__.py.

This is intentionally a tiny idempotent patcher instead of a broad rewrite of
__init__.py. It inserts one import/register block inside register(ctx), right
after the existing voice_live_notes tool registration and before slash command
registration.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "plugin" / "__init__.py"
MARKER = "# SORA bridge elements: preflight/grill/goal synthesis/redaction"
ANCHOR = "    # Slash commands for Discord: register via `ctx.register_command()`."
BLOCK = f'''\n    {MARKER}\n    try:\n        from sora_bridge_elements import register_sora_bridge_tools\n        register_sora_bridge_tools(ctx, _bridge_mod, _active_bridges)\n    except Exception as exc:\n        logger.warning("SORA bridge elements failed to register: %s", exc)\n\n'''


def main() -> None:
    text = INIT.read_text()
    if MARKER in text:
        print(f"already wired: {INIT}")
        return
    if ANCHOR not in text:
        raise SystemExit(f"anchor not found in {INIT}: {ANCHOR!r}")
    text = text.replace(ANCHOR, BLOCK + ANCHOR, 1)
    INIT.write_text(text)
    print(f"wired SORA bridge elements into {INIT}")


if __name__ == "__main__":
    main()
