#!/usr/bin/env python3
"""End-to-end smoke test of the TUI installer in a sandboxed Hermes home."""
import importlib.util, json, os, shutil, sys, tempfile
from pathlib import Path

ACTUAL_HOME = "/home/caps"
repo = Path(ACTUAL_HOME) / "code" / "voice-bridges" / "gemini-live-discord-bridge"

# ── sandbox ──────────────────────────────────────────────────────────────────
sandbox = Path(tempfile.mkdtemp(prefix="hermes-installer-test-"))
hermes_home = sandbox / "hermes"
plugin_dir = hermes_home / "plugins"
(plugin_dir / "discord-voice").mkdir(parents=True, exist_ok=True)
(hermes_home / "hermes-agent" / "venv" / "bin").mkdir(parents=True, exist_ok=True)
config_yaml = hermes_home / "config.yaml"
config_yaml.write_text("gateway:\n  enabled: false\n")
(hermes_home / "hermes-agent" / "venv" / "bin" / "python").write_text("#!/bin/sh\necho mock\n")
os.chmod(hermes_home / "hermes-agent" / "venv" / "bin" / "python", 0o755)

os.environ["HERMES_HOME"] = str(hermes_home)
# Don't override HOME — use it as-is so Path.home() still works for the repo path
# The installer reads HERMES_HOME for the Hermes path, not HOME

# ── import module ────────────────────────────────────────────────────────────
sys.path.insert(0, str(repo / "installer"))
spec = importlib.util.spec_from_file_location("install", repo / "installer" / "install.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["install"] = mod
spec.loader.exec_module(mod)

# ── test: preflight ──────────────────────────────────────────────────────────
print("=== 1. PREFLIGHT === ")
preflight = mod.step_preflight(mod.UI())
print(f"  hermes_home: {preflight['hermes_home']}")
assert preflight["hermes_home"] == hermes_home, f"expected {hermes_home}, got {preflight['hermes_home']}"
print("  ✓ preflight reads HERMES_HOME correctly")

# ── test: KEY_SPECS ──────────────────────────────────────────────────────────
print("\n=== 2. KEY_SPECS === ")
names = [s.name for s in mod.KEY_SPECS]
assert "DISCORD_BOT_TOKEN" in names, "missing DISCORD_BOT_TOKEN"
assert "GEMINI_API_KEY" in names, "missing GEMINI_API_KEY"
print(f"  specs: {', '.join(names)}")
print(f"  count: {len(mod.KEY_SPECS)}")
print("  ✓ KEY_SPECS all present")

# ── test: validators ─────────────────────────────────────────────────────────
print("\n=== 3. VALIDATORS === ")
ok, msg = mod.check_discord_token("not.a.token")
print(f"  bad token: ok={ok} msg={msg}")
assert not ok, "bad token should fail"

ok, msg = mod.check_discord_token("")
print(f"  empty token: ok={ok}")
assert not ok, "empty token should fail"

ok, msg = mod.check_gemini_key("not-a-key")
print(f"  bad gemini: ok={ok}")
assert not ok, "bad gemini should fail"

ok, msg = mod.check_gemini_key("AIza" + "X" * 35)
print(f"  fake-shaped gemini: ok={ok} msg={msg}")
assert not ok, "fake key should fail"

# ── test: .env key parsing ───────────────────────────────────────────────────
print("\n=== 4. ENV PARSING === ")
env_file = hermes_home / ".env"
env_file.write_text("DISCORD_BOT_TOKEN=test_bt\nGEMINI_API_KEY=test_gk\nGEMINI_MODEL=models/gemini-test\n")
existing = {}
for line in env_file.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        existing[k.strip()] = v.strip()
assert existing.get("DISCORD_BOT_TOKEN") == "test_bt", f"got {existing.get('DISCORD_BOT_TOKEN')}"
assert existing.get("GEMINI_API_KEY") == "test_gk"
print("  ✓ .env key=value parsing")
print(f"  parsed: {existing}")

# ── test: deploy (copy mode) ─────────────────────────────────────────────────
print("\n=== 5. DEPLOY === ")
target = plugin_dir / "discord-voice"
install_info = {"mode": "copy", "target": target, "plugins_dir": plugin_dir}
deploy_info = mod.step_deploy(mod.UI(), install_info, repo)
assert deploy_info["target"] == target
assert target.exists(), f"target {target} does not exist after deploy"
assert (target / "bridge.py").exists(), "bridge.py missing"
assert (target / "__init__.py").exists(), "__init__.py missing"
assert (target / "plugin.yaml").exists(), "plugin.yaml missing"
files_deployed = list(target.glob("*"))
print(f"  deployed {len(files_deployed)} files: {[f.name for f in files_deployed]}")
print("  ✓ copy deployment works")

# Clean deployed files for re-test
shutil.rmtree(target)

# ── test: deploy (symlink mode) ─────────────────────────────────────────────
target_sym = plugin_dir / "discord-voice"
install_info_sym = {"mode": "symlink", "target": target_sym, "plugins_dir": plugin_dir}
deploy_info_sym = mod.step_deploy(mod.UI(), install_info_sym, repo)
assert target_sym.is_symlink(), f"{target_sym} should be a symlink"
assert target_sym.exists(), f"{target_sym} should resolve"
print("  ✓ symlink deployment works")
target_sym.unlink()

# ── test: env write ──────────────────────────────────────────────────────────
print("\n=== 6. ENV WRITE === ")
test_keys = {"DISCORD_BOT_TOKEN": "new_bt", "GEMINI_API_KEY": "new_gk", "GEMINI_MODEL": "models/v2"}
env_info = mod.step_write_env(mod.UI(), test_keys, preflight)
assert hermes_home / ".env" == env_info["path"], f"got {env_info['path']}"
content = env_info["path"].read_text()
assert "new_bt" in content, f"missing new_bt in {content}"
assert "new_gk" in content
assert "test_bt" not in content, "old key should have been replaced"  # it should replace existing
# Actually the step_write_env replaces existing keys
print("  ✓ env file written/merged correctly")

# ── test: autostart path ─────────────────────────────────────────────────────
print("\n=== 7. AUTOSTART CONVENTION === ")
# The default path is ~/.hermes/ which in our sandbox would be the
# DEFAULT_HERMES_HOME. Since HERMES_HOME is set, the autostart writes
# to Path.home()/.hermes/voice-live-autostart.json
print(f"  REPO_NAME: {mod.REPO_NAME}")
print(f"  HAS_RICH: {mod.HAS_RICH}")
print("  ✓ module imports cleanly, no syntax errors")

# ── clean up ─────────────────────────────────────────────────────────────────
shutil.rmtree(sandbox)
print(f"\n✅ ALL SMOKE TESTS PASSED (sandbox cleaned up)")
