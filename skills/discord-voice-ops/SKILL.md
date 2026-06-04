---
name: discord-voice-ops
description: Drive the Discord Voice Live bridge, process call transcripts, and extract actionable post-call intelligence
title: Discord Voice Live Operations
trigger:
  - voice-live
  - voice_live
  - discord voice bridge
  - call notes
  - voice transcript
  - /voice-live
prerequisites:
  - Hermes gateway running with discord-voice plugin installed
  - discord-ext-voice-recv installed in gateway venv
  - GEMINI_API_KEY set in ~/.hermes/.env
  - DISCORD_BOT_TOKEN set in ~/.hermes/.env
---

# Discord Voice Live Operations

Skill for driving the `/voice-live` Discord↔Gemini bidirectional voice bridge, analyzing call transcripts, and extracting actionable post-call intelligence.

## Architecture Recap

The bridge runs **in-process** on the gateway. It does not use the agent turn loop.

Pipeline:
```
Discord UDP → NaCl decrypt → Opus decode → 48kHz stereo
  → numpy downsample → 16kHz mono → base64
  → Gemini WSS (realtimeInput)
  → Gemini WSS (serverContent.inlineData)
  → 24kHz mono → numpy upsample → 48kHz stereo
  → LiveAudioSource (thread-safe queue) → Discord VoiceClient.play()
```

Control API listens on `127.0.0.1:18943` (env: `DISCORD_VOICE_LIVE_PORT`).
Notes are written to `~/.hermes/voice-live-notes/voice-live-YYYYMMDD-HHMMSS.jsonl`.

## Available Tools

| Tool | Args | What it does |
|---|---|---|
| `voice_live` | `guild_id`, `channel_id` (opt), `user_id` (opt) | Joins voice channel and starts bridge |
| `voice_live_leave` | `guild_id` | Stops bridge and disconnects |
| `voice_live_status` | none | Returns full health JSON |
| `voice_live_notes` | `limit` (1–500) | Returns recent note events + compiled transcript |

## CLI Health Check

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

Key fields to watch:
- `voice_connected` — is Discord VC alive?
- `playback_active` — is audio playing?
- `receiving_active` — is microphone input being captured?
- `model` — which Gemini model is active?
- `quiet_seconds` — seconds since last speech
- `idle_prompt_seconds` — configured idle threshold before prompting
- `idle_prompt_grace_seconds` — grace period after prompt before hangup
- `idle_prompted_seconds` — null if not prompted, else seconds since prompt was spoken
- `auto_leave_quiet_seconds` — plain auto-leave threshold (fallback if prompt disabled)
- `uptime_seconds`

## Known Quirks

1. **Discord CDN handshake rejection** — The voice WebSocket endpoint (`c-ams08.discord.media`) rejects the first ~5 handshakes with code 4006. A single `channel.connect()` takes ~27s of internal retries. **Do not restart the gateway repeatedly** — each restart resets the retry clock.
2. **Playback restart semantics** — `_on_playback_end` only logs errors; it does NOT restart. `_wake_playback` restarts playback when new Gemini audio arrives after silence. If playback stops during natural silence, the green ring turns off; new audio triggers a restart automatically.
3. **Module import path** — The plugin must be importable as `plugins.discord_voice` (Python-safe name). The actual directory is `discord-voice` but the import is underscore-normalized.

## Post-Call Processing Workflow

After a voice session ends, the notes file contains raw word-level events plus a compiled transcript. Use the helper script below to extract a clean summary.

### Step 1: Locate the latest notes file

```bash
ls -lt ~/.hermes/voice-live-notes/ | head -5
```

### Step 2: Run the post-call analyzer

```bash
python3 ~/.hermes/skills/discord-voice-ops/scripts/voice_notes_analyzer.py \
  --file ~/.hermes/voice-live-notes/voice-live-20260527-105715.jsonl \
  --summary --tasks --decisions
```

Output sections:
- **Summary** — bullet-point conversation overview
- **Tasks** — action items with inferred owners if mentioned
- **Decisions** — commitments or choices made
- **Questions** — unresolved questions the assistant asked
- **Follow-ups** — suggested next steps

### Step 3: Save to knowledge base

```bash
# After reviewing the analyzer output, save the finding
hermes knowledge save --topic "Voice Call 2026-05-27" --content "$(cat summary.md)"
```

## Driving the Bridge Proactively

### Join a specific channel

```bash
# In Hermes chat:
/voice-live guild_id=1480297825655980067 channel_id=1480297827296088207
```

Or let it infer from your current voice channel:

```bash
/voice-live user_id=1474100257762578597
```

### Send a text message through the voice bridge

The bridge exposes a `/say` endpoint:

```bash
curl -s "http://127.0.0.1:18943/say?text=Hello+from+the+bridge" | python3 -m json.tool
```

This is useful for:
- Announcing yourself after joining
- Broadcasting reminders or alerts into the voice channel
- Triggering the assistant to speak without voice input

### Monitor during a long call

```bash
watch -n 10 'curl -s http://127.0.0.1:18943/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"connected={d[chr(39)+chr(39)]voice_connected{chr(39)+chr(39)]} playing={d[chr(39)+chr(39)]playback_active{chr(39)+chr(39)]} model={d.get(chr(39)+chr(39)]model{chr(39)+chr(39)]} quiet={d[chr(39)+chr(39)]quiet_seconds{chr(39)+chr(39)]}s\")"'
```

### Auto-leave configuration

Set in `~/.hermes/.env`:

```env
# Leave after 15 minutes of silence (default 900)
DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS=900

# Minimum uptime before auto-leave kicks in (default 120)
DISCORD_VOICE_LIVE_AUTO_LEAVE_MIN_UPTIME_SECONDS=120

# Phrases that trigger immediate leave (comma-separated)
DISCORD_VOICE_LIVE_LEAVE_PHRASES="leave voice,disconnect from voice,end voice,stop voice,leave the call,disconnect,goodbye hermes"
```

Restart gateway after changing `.env`.

## Idle Prompt ("Are you still there?")

The bridge implements a **two-phase idle detection** that asks before hanging up:

1. **Prompt phase** — After `IDLE_PROMPT_SECONDS` of silence, the assistant speaks: "Are you still there?"
2. **Grace phase** — Waits `IDLE_PROMPT_GRACE_SECONDS` for the user to respond.
3. **Hangup** — If still no response, the bridge disconnects. If the user speaks, the prompt is cancelled and the timer resets.

Configure in `~/.hermes/.env`:

```env
# Seconds of silence before prompting (default 300 = 5 min)
DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS=300

# Grace period after prompt before hanging up (default 60 = 1 min)
DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS=60

# Custom prompt text (default: "Are you still there?")
DISCORD_VOICE_LIVE_IDLE_PROMPT_TEXT="Are you still there?"
```

Set `IDLE_PROMPT_SECONDS=0` to disable the prompt and fall back to plain auto-leave (the old behavior).

**How it works:** The bridge sends the prompt text via `send_text()` into the Gemini Live WebSocket. Gemini processes it as a user message and generates a spoken response. Any subsequent user speech resets the idle timer and cancels the grace-period countdown. The `health` JSON exposes `idle_prompted_seconds` (null when not prompted).

## Post-Call Analyzer Script

The analyzer is at `~/.hermes/skills/discord-voice-ops/scripts/voice_notes_analyzer.py`.

Features:
- Reads `.jsonl` note files
- Merges word-level events into clean turn-based transcript
- Extracts tasks, decisions, questions via keyword heuristics
- Can export to Markdown or JSON
- Supports filtering by time window

Example output:

```markdown
# Voice Call Summary — 2026-05-27 10:57 UTC

## Tasks
- [ ] Extract tasks from voice call notes (inferred: assistant)

## Decisions
- Assistant will track what was spoken and capture requests/decisions

## Questions
- "Anything specific you were curious about?"
- "What would make this conversation feel more useful?"

## Follow-ups
- Review call notes for action items
- Verify voice bridge health after session
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Gateway not available" on `/voice-live` | Plugin not loaded | Check `systemctl --user status hermes-gateway`, restart if needed |
| "Discord adapter not found" | Discord platform not enabled | Verify `discord:` section in `config.yaml` |
| No audio output (green ring off) | Playback stopped during silence | Speak again — `_wake_playback` should restart automatically |
| Repeated "undecodable Opus frame" | Decoder state corruption | Normal under packet loss; >100 errors in 5s = connection issue |
| Bridge starts but no Gemini response | API key invalid or model unavailable | Check `GEMINI_API_KEY`, verify model exists in fallbacks |
| Notes file missing | `NOTES_DIR` not writable | Ensure `~/.hermes/voice-live-notes/` exists and is writable |

## Compilation Check

After editing any bridge/plugin file:

```bash
~/.hermes/hermes-agent/venv/bin/python -m py_compile \
  ~/.hermes/plugins/discord-voice/bridge.py \
  ~/.hermes/plugins/discord-voice/__init__.py
```

## Related Files

| File | Purpose |
|---|---|
| `~/.hermes/plugins/discord-voice/bridge.py` | Core bridge (1053 lines) |
| `~/.hermes/plugins/discord-voice/__init__.py` | Plugin registration |
| `~/.hermes/plugins/discord-voice/plugin.yaml` | Plugin metadata |
| `~/.hermes/voice-live-notes/*.jsonl` | Call transcripts |
| `~/.hermes/voice-live-autostart.json` | Autostart trigger file |
