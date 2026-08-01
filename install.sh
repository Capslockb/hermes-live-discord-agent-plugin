#!/usr/bin/env bash
# install.sh — Install the discord-voice plugin for Hermes Agent
#
# Usage:
#   ./install.sh                  # full install (clone + setup + prompts)
#   ./install.sh --from-local     # use the current working dir (for development)
#   ./install.sh --uninstall      # remove symlinks + env entries
#   ./install.sh --no-prompt      # skip env-var prompts (use existing)
#
# Idempotent: re-running on an installed system is safe.

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────
PLUGIN_NAME="discord-voice"
REPO_URL="https://github.com/Capslockb/hermes-live-discord-agent-plugin.git"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="$HERMES_HOME/plugins"
INSTALL_DIR="$PLUGINS_DIR/$PLUGIN_NAME"
SFX_DIR="$HOME/.hermes/voice-users/sfx"
PYTHON_BIN="$HERMES_HOME/hermes-agent/venv/bin/python"
ENV_FILE="$HERMES_HOME/.env"

# ── Argument parsing ──────────────────────────────────────────────────────
FROM_LOCAL=0
UNINSTALL=0
NO_PROMPT=0
for arg in "$@"; do
  case "$arg" in
    --from-local) FROM_LOCAL=1 ;;
    --uninstall)  UNINSTALL=1 ;;
    --no-prompt)  NO_PROMPT=1 ;;
    -h|--help)
      head -16 "$0" | tail -12
      exit 0
      ;;
    *) echo "Unknown arg: $arg"; exit 1 ;;
  esac
done

# ── Uninstall path ────────────────────────────────────────────────────────
if [ "$UNINSTALL" = 1 ]; then
  echo "Uninstalling $PLUGIN_NAME..."
  if [ -L "$INSTALL_DIR" ] || [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "  removed $INSTALL_DIR"
  fi
  if [ -d "$SFX_DIR" ]; then
    echo "  note: $SFX_DIR exists. Remove manually if you want a clean slate:"
    echo "    rm -rf $SFX_DIR"
  fi
  # Remove the autostart file if it points to this plugin
  if [ -f "$HOME/.hermes/voice-live-autostart.json" ]; then
    rm -f "$HOME/.hermes/voice-live-autostart.json"
    echo "  removed voice-live-autostart.json"
  fi
  echo "Done. Restart the Hermes gateway to apply: systemctl --user restart hermes-gateway"
  exit 0
fi

# ── Pre-flight checks ─────────────────────────────────────────────────────
echo "== discord-voice installer =="
echo "  HERMES_HOME:  $HERMES_HOME"
echo "  INSTALL_DIR:  $INSTALL_DIR"
echo "  Python venv:  $PYTHON_BIN"
echo

if [ ! -x "$PYTHON_BIN" ]; then
  echo "ERROR: Hermes Python venv not found at $PYTHON_BIN"
  echo "       Is Hermes installed? Expected layout:"
  echo "         $HERMES_HOME/hermes-agent/venv/bin/python"
  exit 1
fi

# ── Clone or copy the plugin source ───────────────────────────────────────
mkdir -p "$PLUGINS_DIR"

if [ "$FROM_LOCAL" = 1 ]; then
  echo ">> Using current directory as plugin source"
  if [ ! -f "plugin.yaml" ]; then
    echo "ERROR: no plugin.yaml in $(pwd). Are you in the plugin repo?"
    exit 1
  fi
  # If the install dir exists and isn't a symlink to here, offer to replace
  if [ -d "$INSTALL_DIR" ] && [ ! -L "$INSTALL_DIR" ]; then
    echo "  removing existing install at $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
  fi
  if [ -L "$INSTALL_DIR" ]; then rm -f "$INSTALL_DIR"; fi
  ln -s "$(pwd)" "$INSTALL_DIR"
  echo "  linked $(pwd) -> $INSTALL_DIR"
else
  if [ -L "$INSTALL_DIR" ] || [ -d "$INSTALL_DIR" ]; then
    echo ">> Plugin already installed at $INSTALL_DIR (skipping clone)"
  else
    echo ">> Cloning $REPO_URL -> $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
fi

# ── Install Python dependencies ──────────────────────────────────────────
echo
echo ">> Installing Python dependencies into the Hermes venv"
REQ_FILE="$INSTALL_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
  "$PYTHON_BIN" -m pip install -q -r "$REQ_FILE" || {
    echo "ERROR: pip install failed. Check your venv."
    exit 1
  }
else
  echo "  WARNING: no requirements.txt at $REQ_FILE"
fi

# ── Compile-check the plugin ─────────────────────────────────────────────
echo
echo ">> Compile-check (py_compile)"
for f in "$INSTALL_DIR"/*.py; do
  "$PYTHON_BIN" -m py_compile "$f" && echo "  ok $(basename "$f")" || {
    echo "  COMPILE ERROR: $f"
    exit 1
  }
done

# ── Create SFX directory with default slots ──────────────────────────────
echo
echo ">> Setting up SFX directory at $SFX_DIR"
mkdir -p "$SFX_DIR"
SFX_SRC="$INSTALL_DIR/sfx"
if [ -d "$SFX_SRC" ]; then
  for slot in tool_init error notification transition; do
    if [ -f "$SFX_SRC/${slot}.wav" ] && [ ! -f "$SFX_DIR/${slot}.wav" ]; then
      cp "$SFX_SRC/${slot}.wav" "$SFX_DIR/${slot}.wav"
      echo "  installed default ${slot}.wav"
    fi
  done
fi
for slot in tool_init error notification transition; do
  if [ ! -f "$SFX_DIR/${slot}.wav" ]; then
    echo "  NOTE: $SFX_DIR/${slot}.wav missing — slot '${slot}' will be a no-op"
    echo "        See docs/sfx-library.md for how to add your own"
  fi
done

# ── Install Video Feeder ──────────────────────────────────────────────────
echo
echo ">> Installing Video Frame Feeder..."
SCRIPTS_DIR="$HERMES_HOME/scripts"
mkdir -p "$SCRIPTS_DIR"
FEEDER_SRC="$INSTALL_DIR/scripts/video-frame-feeder.py"
FEEDER_DEST="$SCRIPTS_DIR/video-frame-feeder.py"

if [ -f "$FEEDER_SRC" ]; then
  cp "$FEEDER_SRC" "$FEEDER_DEST"
  chmod 0755 "$FEEDER_DEST"
  echo "  installed $FEEDER_DEST"
else
  echo "  WARNING: feeder source not found at $FEEDER_SRC"
fi

# Create control.secret if missing (used for bridge auth)
SECRET_FILE="$HERMES_HOME/control.secret"
if [ ! -f "$SECRET_FILE" ]; then
  echo "  generating $SECRET_FILE..."
  "$PYTHON_BIN" -c "import secrets; print(secrets.token_urlsafe(32))" > "$SECRET_FILE"
  chmod 0600 "$SECRET_FILE"
fi

echo "Video frame feeder installed: $HERMES_HOME/scripts/video-frame-feeder.py"

# ── Run e2e tests (post-fix regression gate) ─────────────────────────────
echo
echo ">> Running e2e regression tests..."
TESTS_DIR="$INSTALL_DIR/tests"
if [ -d "$TESTS_DIR" ]; then
  if "$PYTHON_BIN" -m unittest tests.test_interrupt_latency tests.test_transcript_latency -v 2>&1 | tee /tmp/discord-voice-tests.log; then
    echo
    echo "  ✓ e2e tests passed (interrupt latency < 100ms target)"
  else
    echo
    echo "  ⚠ e2e tests FAILED — see /tmp/discord-voice-tests.log"
    echo "  The plugin is installed but interrupt latency is not verified."
    echo "  Re-run manually: cd $INSTALL_DIR && $PYTHON_BIN -m unittest tests.test_interrupt_latency tests.test_transcript_latency -v"
  fi
else
  echo "  WARNING: tests/ not found at $TESTS_DIR (skipped regression gate)"
fi

# ── Env var prompts ──────────────────────────────────────────────────────

if [ "$NO_PROMPT" = 0 ]; then
  echo
  echo ">> Required environment variables (written to $ENV_FILE)"
  echo "   Press Enter to keep the current value (or skip if unset)."
  echo
  for var in DISCORD_BOT_TOKEN GEMINI_API_KEY; do
    current=$(grep -E "^${var}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)
    if [ -n "$current" ]; then
      echo "  $var: [set, hidden]"
      read -r -p "    replace? [y/N]: " yn
      case "$yn" in y|Y) current=""; ;;
        *) continue ;;
      esac
    fi
    if [ -z "$current" ]; then
      read -r -s -p "    Enter $var: " val; echo
      if [ -n "$val" ]; then
        # Append (or update) the key
        if grep -qE "^${var}=" "$ENV_FILE" 2>/dev/null; then
          sed -i "s|^${var}=.*|${var}=${val}|" "$ENV_FILE"
        else
          echo "${var}=${val}" >> "$ENV_FILE"
        fi
        echo "    saved"
      else
        echo "    SKIPPED (set later: export $var=...)"
      fi
    fi
  done

  # Optional: discord user id (for the bot to know who to listen to)
  if ! grep -qE "^DISCORD_VOICE_LIVE_USER_ID=" "$ENV_FILE" 2>/dev/null; then
    read -r -p "  DISCORD_VOICE_LIVE_USER_ID (your Discord snowflake) [optional, Enter to skip]: " uid
    if [ -n "$uid" ]; then
      echo "DISCORD_VOICE_LIVE_USER_ID=$uid" >> "$ENV_FILE"
      echo "    saved"
    fi
  fi

  chmod 600 "$ENV_FILE" 2>/dev/null || true
fi

# ── Done ──────────────────────────────────────────────────────────────────
echo
echo "== Install complete =="
echo
echo "  Installed at:  $INSTALL_DIR"
echo "  Docs:          $INSTALL_DIR/docs/"
echo "  SFX dir:       $SFX_DIR"
echo "  Env file:      $ENV_FILE"
echo
echo "Next steps:"
echo "  1. Restart the Hermes gateway so it picks up the new plugin:"
echo "       systemctl --user restart hermes-gateway"
echo "  2. From Discord, run:   /voice-live"
echo
