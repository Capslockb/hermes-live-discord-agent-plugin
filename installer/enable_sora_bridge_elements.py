#!/usr/bin/env python3
"""Idempotent helper to confirm SORA bridge elements are wired.

Since v0.4.0 the elements are registered automatically from plugin/__init__.py.
This script only validates the import path and prints the next steps.
"""

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent.parent / "plugin"
INIT = PLUGIN_DIR / "__init__.py"
SORA = PLUGIN_DIR / "sora_bridge_elements.py"


def main() -> int:
    ok = True
    if not SORA.exists():
        print(f"ERROR: {SORA} not found. Cannot enable SORA bridge elements.")
        ok = False
    else:
        print(f"OK: {SORA} exists")

    if not INIT.exists():
        print(f"ERROR: {INIT} not found.")
        ok = False
    else:
        content = INIT.read_text(errors="replace")
        if "from sora_bridge_elements import register_sora_bridge_tools" in content:
            print(f"OK: {INIT} already imports register_sora_bridge_tools")
        else:
            print(f"WARN: {INIT} does not import register_sora_bridge_tools")
            ok = False
        if "register_sora_bridge_tools(ctx" in content:
            print(f"OK: register_sora_bridge_tools is called inside register(ctx)")
        else:
            print(f"WARN: register_sora_bridge_tools is not called inside register(ctx)")
            ok = False

    print()
    if ok:
        print("SORA bridge elements are wired. Restart the Hermes gateway to load them:")
        print("  systemctl --user restart hermes-gateway")
        print("Then ask Hermes: sora_bridge_preflight")
        return 0
    print("SORA bridge elements wiring looks incomplete. Apply the patch from docs/SORA_MIGRATION.md or open an issue.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
