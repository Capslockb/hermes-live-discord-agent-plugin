#!/usr/bin/env python3
"""
Gemini Live Discord Bridge — Oneshot TUI Installer
====================================================

Walks the user through:
  1. API key collection (Discord bot token, Gemini API key, optional)
  2. Connection validation (real network checks)
  3. Install location (Hermes plugin dir or local path)
  4. Plugin deployment (copy, symlink, or write)
  5. .env updates + permissions
  6. Final summary + next steps

Uses only stdlib + rich (color/tables). No external CLI deps.
Run with:  python3 install.py
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from getpass import getpass
from pathlib import Path
from typing import Any, Callable, Optional

# --- rich is the only external dep (already in Hermes venv + most distros) ---
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
    from rich.text import Text
    from rich.tree import Tree
    HAS_RICH = True
except ImportError:  # pragma: no cover
    HAS_RICH = False

# ============================================================================
# Configuration
# ============================================================================

REPO_NAME = "gemini-live-discord-bridge"
PLUGIN_NAME = "discord-voice"          # plugin dir name Hermes expects
DEFAULT_HERMES_HOME = Path.home() / ".hermes"
DEFAULT_PLUGIN_DEST = DEFAULT_HERMES_HOME / "plugins" / PLUGIN_NAME
DEFAULT_ENV_FILE = DEFAULT_HERMES_HOME / ".env"

# All keys we know how to ask for. `required` controls the flow.
# `validate` returns (ok: bool, message: str). `secret` masks input.
@dataclass
class KeySpec:
    name: str
    prompt: str
    secret: bool = True
    required: bool = True
    default: Optional[str] = None
    validate: Optional[Callable[[str], tuple[bool, str]]] = None
    help_text: str = ""
    group: str = "core"

KEY_SPECS: list[KeySpec] = [
    KeySpec(
        name="DISCORD_BOT_TOKEN",
        prompt="Discord bot token",
        secret=True,
        required=True,
        validate=lambda v: (
            (v.count(".") == 2 and len(v) > 50, f"Got {len(v)} chars, expected ~3 dot-separated parts")
            if v else (False, "empty")
        ),
        help_text="From https://discord.com/developers/applications → Bot → Reset Token",
        group="core",
    ),
    KeySpec(
        name="GEMINI_API_KEY",
        prompt="Google Gemini API key",
        secret=True,
        required=True,
        validate=lambda v: (
            (v.startswith("AIza") and len(v) >= 35, f"Looks like a Gemini key ({len(v)} chars)")
            if v else (False, "empty")
        ),
        help_text="From https://aistudio.google.com/apikey",
        group="core",
    ),
    KeySpec(
        name="GEMINI_MODEL",
        prompt="Gemini Live model",
        secret=False,
        required=False,
        default="models/gemini-2.5-flash-native-audio-preview-09-2025",
        validate=lambda v: (True, f"model = {v}"),
        help_text="Default: models/gemini-2.5-flash-native-audio-preview-09-2025",
        group="model",
    ),
    KeySpec(
        name="GEMINI_LIVE_MODEL_FALLBACKS",
        prompt="Comma-separated fallback models",
        secret=False,
        required=False,
        default="models/gemini-2.0-flash-live-001,models/gemini-2.5-flash-live-preview",
        validate=lambda v: (True, f"fallbacks = {v or '(none)'}"),
        help_text="Tried in order if primary model is unavailable",
        group="model",
    ),
    KeySpec(
        name="DISCORD_VOICE_LIVE_PORT",
        prompt="Local HTTP control port",
        secret=False,
        required=False,
        default="18943",
        validate=lambda v: (
            (v.isdigit() and 1024 <= int(v) <= 65535, f"port = {v}")
            if v else (False, "empty")
        ),
        help_text="Default 18943. Health endpoint: /health on this port",
        group="advanced",
    ),
    KeySpec(
        name="DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS",
        prompt="Auto-leave after N seconds of silence (0 = disable)",
        secret=False,
        required=False,
        default="900",
        validate=lambda v: (
            (v.isdigit(), f"threshold = {v}s")
            if v else (False, "empty")
        ),
        help_text="Default 900 (15 min). 0 disables auto-leave",
        group="advanced",
    ),
    KeySpec(
        name="DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS",
        prompt="Idle prompt threshold (N seconds before asking 'are you still there?')",
        secret=False,
        required=False,
        default="300",
        validate=lambda v: (
            (v.isdigit(), f"prompt after = {v}s")
            if v else (False, "empty")
        ),
        help_text="Default 300 (5 min). 0 = disable prompt, use plain auto-leave",
        group="advanced",
    ),
]


# ============================================================================
# Console wrapper — works with or without rich
# ============================================================================

class UI:
    """Thin console wrapper. Uses rich if available, else plain text."""

    def __init__(self) -> None:
        self.has_rich = HAS_RICH
        if self.has_rich:
            self.console = Console()
        self.answers: dict[str, str] = {}

    # -- output --
    def banner(self) -> None:
        text = f"╔═ {REPO_NAME} installer ═╗"
        if self.has_rich:
            self.console.print(Panel.fit(
                f"[bold cyan]{REPO_NAME}[/bold cyan]\n"
                "[dim]Discord ↔ Gemini Multimodal Live API bridge for Hermes Agent[/dim]",
                border_style="cyan",
            ))
        else:
            print(text)
            print("=" * len(text))

    def info(self, msg: str) -> None:
        if self.has_rich:
            self.console.print(f"[blue]ℹ[/blue] {msg}")
        else:
            print(f"[INFO] {msg}")

    def ok(self, msg: str) -> None:
        if self.has_rich:
            self.console.print(f"[green]✓[/green] {msg}")
        else:
            print(f"[OK] {msg}")

    def warn(self, msg: str) -> None:
        if self.has_rich:
            self.console.print(f"[yellow]⚠[/yellow] {msg}")
        else:
            print(f"[WARN] {msg}")

    def err(self, msg: str) -> None:
        if self.has_rich:
            self.console.print(f"[red]✗[/red] {msg}")
        else:
            print(f"[ERR] {msg}")

    def step(self, n: int, total: int, msg: str) -> None:
        if self.has_rich:
            self.console.print(f"\n[bold magenta]Step {n}/{total}[/bold magenta] {msg}")
        else:
            print(f"\n--- Step {n}/{total}: {msg} ---")

    def table(self, title: str, rows: list[tuple[str, str]]) -> None:
        if self.has_rich:
            t = Table(title=title, show_header=True, header_style="bold magenta")
            t.add_column("Key", style="cyan")
            t.add_column("Value", style="white")
            for k, v in rows:
                t.add_row(k, v)
            self.console.print(t)
        else:
            print(f"\n=== {title} ===")
            for k, v in rows:
                print(f"  {k}: {v}")

    # -- input --
    def prompt(self, question: str, default: str = "", secret: bool = False) -> str:
        if secret:
            if self.has_rich:
                return Prompt.ask(f"[cyan]{question}[/cyan]", password=True, default=default)
            return getpass(f"{question}: ").strip() or default
        if self.has_rich:
            return Prompt.ask(f"[cyan]{question}[/cyan]", default=default).strip()
        raw = input(f"{question} [{default}]: ").strip()
        return raw or default

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.has_rich:
            return Confirm.ask(f"[cyan]{question}[/cyan]", default=default)
        raw = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        if not raw:
            return default
        return raw in {"y", "yes", "1", "true"}

    def menu(self, question: str, options: list[tuple[str, str]]) -> str:
        """Pick one of `options`, where each is (key, label). Returns key."""
        if self.has_rich:
            t = Table(show_header=False, box=None)
            t.add_column("k", style="cyan", no_wrap=True)
            t.add_column("label")
            for k, label in options:
                t.add_row(f"[{k}]", label)
            self.console.print(t)
        else:
            for k, label in options:
                print(f"  [{k}] {label}")
        while True:
            keys = [k for k, _ in options]
            choice = self.prompt(question).lower()
            if choice in keys:
                return choice
            self.warn(f"pick one of: {', '.join(keys)}")

    def spinner(self, message: str) -> "_Spinner":
        return _Spinner(self, message)


class _Spinner:
    def __init__(self, ui: "UI", message: str) -> None:
        self.ui = ui
        self.message = message
        self._ctx: Any = None

    def __enter__(self) -> "_Spinner":
        if self.ui.has_rich:
            self._ctx = self.ui.console.status(self.message, spinner="dots")
            self._ctx.__enter__()
        else:
            print(f"... {self.message}")
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._ctx is not None:
            self._ctx.__exit__(*exc)


# ============================================================================
# Validators (network)
# ============================================================================

def check_discord_token(token: str) -> tuple[bool, str]:
    """Verify a Discord bot token by hitting /users/@me."""
    if not token or token.count(".") != 2:
        return False, "token must be 3 dot-separated parts"
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {token}"},
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                bot_name = data.get("username", "?")
                bot_id = data.get("id", "?")
                return True, f"Discord bot @me OK — {bot_name} ({bot_id})"
            return False, f"Discord API returned HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"Discord API HTTP {e.code}: {e.reason}"
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, f"network: {e}"


def check_gemini_key(key: str) -> tuple[bool, str]:
    """Verify a Gemini API key by listing models."""
    if not key or not key.startswith("AIza"):
        return False, "key should start with 'AIza'"
    try:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={urllib.parse.quote(key)}",
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                models = data.get("models", [])
                live = [m["name"] for m in models if "live" in m.get("name", "").lower()][:3]
                return True, f"Gemini API OK — {len(models)} models, live candidates: {live or '(none with live in name)'}"
            return False, f"Gemini API returned HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        return False, f"Gemini API HTTP {e.code}: {body}"
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, f"network: {e}"


# ============================================================================
# Step functions
# ============================================================================

def step_preflight(ui: UI) -> dict[str, Any]:
    """System preflight: detect Hermes home, venv, Python, git, curl."""
    ui.step(1, 6, "System preflight")

    info: dict[str, Any] = {}

    # Python
    py = sys.executable
    info["python"] = py
    info["python_version"] = platform.python_version()
    ui.ok(f"Python {info['python_version']} at {py}")

    # Hermes home
    hermes_home = Path(os.environ.get("HERMES_HOME", str(DEFAULT_HERMES_HOME)))
    info["hermes_home"] = hermes_home
    if not (hermes_home / "config.yaml").exists():
        ui.warn(f"Hermes config not found at {hermes_home / 'config.yaml'}")
    else:
        ui.ok(f"Hermes home: {hermes_home}")

    # venv
    venv_python = hermes_home / "hermes-agent" / "venv" / "bin" / "python"
    info["venv_python"] = venv_python
    if venv_python.exists():
        ui.ok(f"Hermes venv python: {venv_python}")
    else:
        ui.warn(f"Hermes venv not found at {venv_python}")
        ui.info("plugin install will not auto-verify; you may need to run pip install manually")

    # git
    info["git"] = shutil.which("git")
    if info["git"]:
        ui.ok(f"git: {info['git']}")
    else:
        ui.warn("git not found in PATH")

    # gh CLI
    info["gh"] = shutil.which("gh")
    if info["gh"]:
        ui.ok(f"gh CLI: {info['gh']}")
    else:
        ui.info("gh CLI not found (optional — only used for source updates)")

    # sudo check
    info["is_root"] = os.geteuid() == 0
    if info["is_root"]:
        ui.warn("running as root — fine for venv installs, careful with system packages")

    return info


def step_collect_keys(ui: UI, preflight: dict[str, Any]) -> dict[str, str]:
    """Walk the user through KEY_SPECS. Run network validators on the two required ones."""
    ui.step(2, 6, "Collect API keys & settings")

    # Load any existing values to pre-fill
    env_file = preflight["hermes_home"] / ".env"
    existing: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip().strip('"').strip("'")

    # Group the keys
    groups: dict[str, list[KeySpec]] = {}
    for spec in KEY_SPECS:
        groups.setdefault(spec.group, []).append(spec)

    group_order = ["core", "model", "advanced"]
    group_titles = {
        "core": "Required API keys",
        "model": "Model selection",
        "advanced": "Advanced tuning",
    }

    for group_key in group_order:
        specs = groups.get(group_key, [])
        if not specs:
            continue
        ui.info(f"\n{group_titles[group_key]}:")
        for spec in specs:
            prefill = existing.get(spec.name, spec.default or "")
            if spec.secret and prefill:
                # mask pre-existing secrets
                shown = prefill[:6] + "…" + prefill[-4:] if len(prefill) > 12 else "***"
                ui.info(f"  found {spec.name} in .env: {shown}")
                if not ui.confirm(f"  keep existing {spec.name}?", default=True):
                    prefill = spec.default or ""
                else:
                    ui.answers[spec.name] = prefill
                    continue
            if spec.name not in ui.answers:
                value = ui.prompt(spec.prompt, default=prefill, secret=spec.secret)
                if not value and spec.required:
                    ui.err(f"{spec.name} is required")
                    sys.exit(1)
                if value and spec.validate:
                    ok, msg = spec.validate(value)
                    if ok:
                        ui.ok(f"{spec.name}: {msg}")
                    else:
                        ui.warn(f"{spec.name}: {msg}")
                ui.answers[spec.name] = value or (spec.default or "")

    # Network validation on the two required keys
    ui.info("\nValidating network credentials...")
    with ui.spinner("checking Discord bot token..."):
        ok, msg = check_discord_token(ui.answers.get("DISCORD_BOT_TOKEN", ""))
    if ok:
        ui.ok(msg)
    else:
        ui.warn(f"Discord token check failed: {msg}")
        if not ui.confirm("Continue anyway? (token may be valid but network may be flaky)", default=True):
            sys.exit(1)

    with ui.spinner("checking Gemini API key..."):
        ok, msg = check_gemini_key(ui.answers.get("GEMINI_API_KEY", ""))
    if ok:
        ui.ok(msg)
    else:
        ui.warn(f"Gemini key check failed: {msg}")
        if not ui.confirm("Continue anyway?", default=True):
            sys.exit(1)

    return ui.answers


def step_choose_install(ui: UI, preflight: dict[str, Any]) -> dict[str, Any]:
    """Choose install mode: copy vs symlink, and target path."""
    ui.step(3, 6, "Choose install mode")

    hermes_home: Path = preflight["hermes_home"]
    plugins_dir = hermes_home / "plugins"

    if not plugins_dir.exists():
        ui.warn(f"Hermes plugins dir not found: {plugins_dir}")
        ui.info("You can still install locally; you can move/symlink later.")
        if not ui.confirm("Create the plugins directory?", default=True):
            sys.exit(0)
        plugins_dir.mkdir(parents=True, exist_ok=True)

    ui.info(f"Hermes plugins dir: {plugins_dir}")

    mode = ui.menu(
        "Install mode:",
        [
            ("copy", f"Copy plugin files into {plugins_dir / PLUGIN_NAME} (recommended for production)"),
            ("symlink", f"Symlink {plugins_dir / PLUGIN_NAME} → this repo (good for development)"),
            ("local", "Install to a custom local path (no Hermes integration)"),
        ],
    )

    if mode == "copy":
        target = plugins_dir / PLUGIN_NAME
    elif mode == "symlink":
        target = plugins_dir / PLUGIN_NAME
    else:
        custom = ui.prompt("Custom install path", default=str(Path.cwd() / PLUGIN_NAME))
        target = Path(custom).expanduser().resolve()

    if target.exists() or target.is_symlink():
        ui.warn(f"target already exists: {target}")
        action = ui.menu(
            "What do you want to do?",
            [
                ("overwrite", f"Overwrite {target} (existing files will be lost)"),
                ("backup", f"Rename existing to {target}.bak-{{timestamp}} and install fresh"),
                ("abort", "Abort install"),
            ],
        )
        if action == "abort":
            ui.info("aborted by user")
            sys.exit(0)
        if action == "backup":
            ts = time.strftime("%Y%m%d-%H%M%S")
            backup = target.with_name(f"{target.name}.bak-{ts}")
            target.rename(backup)
            ui.ok(f"backed up to {backup}")

    return {"mode": mode, "target": target, "plugins_dir": plugins_dir}


def step_deploy(ui: UI, install: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Copy/symlink plugin/ into install['target']. Optionally install pip deps."""
    ui.step(4, 6, "Deploy plugin files")

    target: Path = install["target"]
    mode: str = install["mode"]
    source = repo_root / "plugin"

    if not source.exists():
        ui.err(f"plugin source not found: {source}")
        sys.exit(1)

    if mode == "symlink":
        target.symlink_to(source.resolve())
        ui.ok(f"symlinked {target} → {source.resolve()}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        # set perms
        for p in target.rglob("*"):
            if p.is_file():
                p.chmod(0o644)
            elif p.is_dir():
                p.chmod(0o755)
        (target / "bridge.py").chmod(0o644)
        ui.ok(f"copied {source} → {target}")

    # Install pip deps into Hermes venv
    venv_python = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
    if venv_python.exists():
        ui.info("Installing pip requirements into Hermes venv...")
        try:
            subprocess.run(
                [str(venv_python), "-m", "pip", "install", "-q", "-r", str(target / "requirements.txt")],
                check=True,
                timeout=180,
            )
            ui.ok("pip requirements installed")
        except subprocess.CalledProcessError as e:
            ui.warn(f"pip install failed (exit {e.returncode})")
            ui.info(f"you can retry manually: {venv_python} -m pip install -r {target / 'requirements.txt'}")
        except subprocess.TimeoutExpired:
            ui.warn("pip install timed out")
    else:
        ui.info(f"venv not found, skipping auto pip install. Run manually: pip install -r {target / 'requirements.txt'}")

    return {"target": target}


def step_write_env(ui: UI, keys: dict[str, str], preflight: dict[str, Any]) -> dict[str, Any]:
    """Update ~/.hermes/.env with the collected keys (or write a new file)."""
    ui.step(5, 6, "Write env file")

    env_file: Path = preflight["hermes_home"] / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)

    if env_file.exists():
        ui.info(f"Existing env file: {env_file}")
        # Read and update
        lines = env_file.read_text().splitlines()
        new_lines: list[str] = []
        seen: set[str] = set()
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in keys:
                    new_lines.append(f"{k}={keys[k]}")
                    seen.add(k)
                    continue
            new_lines.append(line)
        for k, v in keys.items():
            if k not in seen:
                new_lines.append(f"{k}={v}")
        env_file.write_text("\n".join(new_lines) + "\n")
        env_file.chmod(0o600)
        ui.ok(f"updated {env_file} with {len(keys)} keys (perms 600)")
    else:
        if not ui.confirm(f"No env file at {env_file}. Create one?", default=True):
            ui.info("skipped env write; remember to add the keys manually")
            return {"path": None}
        with env_file.open("w") as f:
            f.write(f"# {REPO_NAME} installer — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            for k, v in keys.items():
                f.write(f"{k}={v}\n")
        env_file.chmod(0o600)
        ui.ok(f"wrote {env_file} (perms 600)")

    return {"path": env_file}


def step_autostart(ui: UI, deploy_info: dict[str, Any]) -> dict[str, Any]:
    """Optionally create the voice-live-autostart.json file so the bridge auto-joins on gateway boot."""
    ui.step(6, 6, "Optional: autostart on gateway boot")

    ui.info("The Hermes gateway can auto-start this bridge when it boots.")
    ui.info("That requires a voice-live-autostart.json in ~/.hermes/.")

    if not ui.confirm("Set up autostart?", default=False):
        return {"enabled": False}

    guild_id = ui.prompt("Discord guild (server) ID")
    channel_id = ui.prompt("Discord voice channel ID to auto-join")
    user_id = ui.prompt("Your Discord user ID (used as a hint)", default="")
    if not guild_id or not channel_id:
        ui.warn("guild_id and channel_id are required for autostart; skipped")
        return {"enabled": False}

    payload: dict[str, str] = {"guild_id": guild_id, "channel_id": channel_id}
    if user_id:
        payload["user_id"] = user_id
    autostart_path = Path.home() / ".hermes" / "voice-live-autostart.json"
    autostart_path.write_text(json.dumps(payload, indent=2))
    autostart_path.chmod(0o600)
    ui.ok(f"wrote {autostart_path}")

    return {"enabled": True, "path": autostart_path, "payload": payload}


def step_summary(ui: UI, preflight: dict[str, Any], install: dict[str, Any],
                 deploy_info: dict[str, Any], env_info: dict[str, Any],
                 autostart: dict[str, Any], keys: dict[str, str]) -> None:
    """Print the final summary panel + next-steps."""
    rows = [
        ("Plugin target", str(install["target"])),
        ("Install mode", install["mode"]),
        ("Env file", str(env_info.get("path") or "(skipped)")),
        ("Hermes venv", str(preflight.get("venv_python", "(not found)"))),
        ("Autostart", "yes" if autostart.get("enabled") else "no"),
        ("Keys written", f"{len(keys)} (DISCORD_BOT_TOKEN, GEMINI_API_KEY, ...)"),
    ]
    ui.table("Install summary", rows)

    next_steps = textwrap.dedent(f"""
    1. Restart the Hermes gateway so it picks up the new plugin:
         systemctl --user restart hermes-gateway
         journalctl --user -u hermes-gateway -f
    2. Verify the plugin is loaded:
         curl -s http://127.0.0.1:{keys.get('DISCORD_VOICE_LIVE_PORT', '18943')}/health
    3. Start the bridge (in a Discord voice channel):
         /voice-live
       Or, in chat with Hermes:
         voice_live guild_id=<id> channel_id=<id>
    4. Watch live audio levels / health:
         watch -n 5 'curl -s http://127.0.0.1:{keys.get('DISCORD_VOICE_LIVE_PORT', '18943')}/health'
    5. Pull future updates (if symlinked, just `git pull` in this repo):
         cd {Path(__file__).resolve().parent.parent}
         git pull
    """).strip()
    ui.info("\nNext steps:")
    print(next_steps)


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    ui = UI()
    ui.banner()

    if not Path("plugin").exists():
        ui.err("install.py must be run from the repo root (no 'plugin/' dir found).")
        return 2

    repo_root = Path(__file__).resolve().parent.parent
    try:
        preflight = step_preflight(ui)
        keys = step_collect_keys(ui, preflight)
        install = step_choose_install(ui, preflight)
        deploy_info = step_deploy(ui, install, repo_root)
        env_info = step_write_env(ui, keys, preflight)
        autostart = step_autostart(ui, deploy_info)
        step_summary(ui, preflight, install, deploy_info, env_info, autostart, keys)
    except KeyboardInterrupt:
        ui.warn("\naborted by user")
        return 130
    except Exception as e:  # noqa: BLE001
        ui.err(f"fatal: {e}")
        if os.environ.get("DEBUG"):
            raise
        return 1

    ui.ok("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
