# Skills (operator docs)

This directory contains the Hermes Agent skills used to drive this bridge.

| Skill | Purpose |
|-------|---------|
| [`discord-voice-ops/`](discord-voice-ops/SKILL.md) | Drive the bridge in chat, monitor health, analyze transcripts. |
| [`voice-bridge-protocols/`](voice-bridge-protocols/SKILL.md) | Shared bridge patterns — audio pipeline, lifecycle, cost control. |
| [`gemini-live-spotify-tools/`](gemini-live-spotify-tools/SKILL.md) | Pattern for extending Gemini Live with in-process tools. |
| [`gemini-live-audio/`](gemini-live-audio/SKILL.md) | Gemini Live API configuration, system prompts, turn coverage. |

## Install a skill into your Hermes home

```bash
# Pick the profile you want
HERMES_HOME=~/.hermes

# Symlink (recommended for development — edits in this repo show up live)
ln -sf "$(pwd)/skills/discord-voice-ops" "$HERMES_HOME/skills/discord-voice-ops"
ln -sf "$(pwd)/skills/voice-bridge-protocols" "$HERMES_HOME/skills/devops/voice-bridge-protocols"
ln -sf "$(pwd)/skills/gemini-live-spotify-tools" "$HERMES_HOME/skills/devops/gemini-live-spotify-tools"
ln -sf "$(pwd)/skills/gemini-live-audio" "$HERMES_HOME/skills/mlops/gemini-live-audio"
```

If you don't want to symlink, copy them instead:

```bash
cp -r skills/discord-voice-ops "$HERMES_HOME/skills/"
cp -r skills/voice-bridge-protocols "$HERMES_HOME/skills/devops/"
cp -r skills/gemini-live-spotify-tools "$HERMES_HOME/skills/devops/"
cp -r skills/gemini-live-audio "$HERMES_HOME/skills/mlops/"
```

Then restart the gateway:

```bash
systemctl --user restart hermes-gateway
```

## Sources

These skills are the same ones running on the original capslock.nl install. This repo is the canonical source of truth going forward — if you change a skill here, commit and push, then `git pull` and the symlinks will pick up the new version automatically.
