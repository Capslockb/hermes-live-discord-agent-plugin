# Gemini Live Discord Bridge

> Full-duplex Discord voice channel ↔ Google Gemini Multimodal Live API bridge, packaged as a [Hermes Agent](https://hermes-agent.nousresearch.com) plugin.

Bring Gemini's multimodal real-time reasoning (audio + images + tool calls) into any Discord voice channel. Speak to it, it speaks back — with function calling, idle hangup, post-call transcripts, and a 3-minute oneshot installer.

```
  Discord user ──► Discord UDP ──► [NaCl] ──► Opus ──► resample ──► Gemini WSS
  Discord user ◄── Discord UDP ◄── [NaCl] ◄── Opus ◄── upsample ◄── Gemini WSS
                                    │
                                    └──► JSONL transcript at ~/.hermes/voice-live-notes/
```

---

## ⚡ Quick install (3 min)

```bash
git clone https://github.com/Capslockb/gemini-live-discord-bridge.git
cd gemini-live-discord-bridge
python3 installer/install.py
```

The installer walks you through every step interactively:

| Step | What happens |
|------|--------------|
| 1    | System preflight — detects Python, Hermes home, venv, git, gh |
| 2    | API key collection — Discord bot token + Gemini key (with **live network validation**) |
| 3    | Install mode — copy into `~/.hermes/plugins/`, symlink, or local |
| 4    | Plugin deployment — copies files, fixes perms, runs `pip install` |
| 5    | `.env` merge — writes keys to `~/.hermes/.env` (chmod 600) |
| 6    | Optional autostart — drops `voice-live-autostart.json` so the bridge joins on gateway boot |

**Scripted / CI mode:** add `--yes` (or `-y`) to auto-answer all prompts with defaults:
```bash
python3 installer/install.py --yes
```

No external CLI deps. Only requires `rich` (already in the Hermes venv). Pure stdlib otherwise.

---

## 🧰 What you need

| Item | Where | Cost |
|------|-------|------|
| Discord bot token | [discord.com/developers](https://discord.com/developers/applications) → Bot → Reset Token | free |
| Gemini API key | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | free tier is plenty |
| Python 3.10+ | system | free |
| Hermes Agent (optional, but recommended) | `pip install hermes-agent` | free / self-hosted |

Discord bot must have these intents enabled: **Server Members**, **Message Content**, and **Voice States** under the Bot tab. Invite URL needs `bot` scope + `connect`, `speak`, `use_voice_activity` permissions.

---

## 🎯 Usage

### Slash command (in Discord)

```
/voice-live
```

The bot joins **your current voice channel** and starts the bridge. Speak naturally.

### Hermes chat

```
voice_live guild_id=1234567890 channel_id=0987654321
```

### Health check

```bash
curl -s http://127.0.0.1:18943/health | python3 -m json.tool
```

Key fields: `voice_connected`, `playback_active`, `model`, `quiet_seconds`, `idle_prompted_seconds`.

### Send text into the bridge

```bash
curl -s "http://127.0.0.1:18943/say?text=Hello+from+the+bridge"
```

### Leave the channel

```
/voice-live-leave
# or
voice_live_leave guild_id=1234567890
```

---

## 🏗️ Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full breakdown.

```
┌─────────────────┐                    ┌──────────────────────┐
│  Discord Voice  │  ◄──Opus 48kHz──►  │   LiveAudioSource    │
│  (UDP + NaCl)   │                    │   (thread-safe queue)│
└────────┬────────┘                    └──────────┬───────────┘
         │                                        │
         ▼                                        ▼
┌─────────────────┐                    ┌──────────────────────┐
│  VoiceListener  │  ──16kHz mono──►   │  GeminiLiveBridge    │
│  (rx thread)    │                    │  (WSS + tool calls)  │
└─────────────────┘                    └──────────┬───────────┘
                                                    │
                                                    ▼
                                         ┌──────────────────────┐
                                         │   Gemini Live API    │
                                         │  (multimodal, real-  │
                                         │   time + functions)  │
                                         └──────────────────────┘
```

The bridge is **in-process** — it lives inside the Hermes gateway's asyncio loop. No external services to run.

---

## 🔧 Configuration

All config goes in `~/.hermes/.env`:

```env
# Required
DISCORD_BOT_TOKEN=...
GEMINI_API_KEY=...

# Optional — model selection
GEMINI_MODEL=models/gemini-2.5-flash-native-audio-preview-09-2025
GEMINI_LIVE_MODEL_FALLBACKS=models/gemini-2.0-flash-live-001,models/gemini-2.5-flash-live-preview

# Optional — networking
DISCORD_VOICE_LIVE_PORT=18943

# Optional — auto-leave (hangup after N seconds of silence)
DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS=900
DISCORD_VOICE_LIVE_AUTO_LEAVE_MIN_UPTIME_SECONDS=120
DISCORD_VOICE_LIVE_LEAVE_PHRASES=leave voice,disconnect from voice,end voice,stop voice,goodbye hermes

# Optional — idle prompt ("are you still there?")
DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS=300
DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS=60
DISCORD_VOICE_LIVE_IDLE_PROMPT_TEXT="Are you still there?"
```

`IDLE_PROMPT_SECONDS=0` disables the prompt and falls back to plain auto-leave.

---

## 📞 Idle hangup — how it works

The bridge implements a **two-phase idle detection**:

1. **Prompt phase** — after `IDLE_PROMPT_SECONDS` of silence, the assistant asks "Are you still there?" by injecting the text into the Gemini Live session.
2. **Grace phase** — waits `IDLE_PROMPT_GRACE_SECONDS` for the user to respond.
3. **Hangup** — if no response, the bridge leaves. If the user speaks, both timers reset.

This is much nicer than a silent timeout — the user gets a chance to react.

---

## 💸 Cost optimization

Live audio is expensive. By default this bridge:

- Uses `mediaResolution: LOW` on the Gemini Live session (~100 tokens/frame vs ~258 default).
- Sets `turnCoverage: TURN_INCLUDES_ONLY_ACTIVITY` — video frames are only billed when speech is detected.
- Gates frame uploads to **1 fps** + audio-gating at the bridge level.
- Caps frame payload at 512 KB.

Result: roughly **$0.03–$0.06/hour** of real conversation on the Flex tier for full-duplex audio. Idle frames are not billed.

---

## 📝 Post-call transcripts

Every call writes a JSONL transcript to `~/.hermes/voice-live-notes/voice-live-YYYYMMDD-HHMMSS.jsonl` containing:

- Word-level events with timestamps
- Model + user turn boundaries
- Tool call invocations
- Idle prompt / hangup events
- Final compiled transcript at the bottom

Use the analyzer at [`scripts/post_call_summary.py`](scripts/post_call_summary.py):

```bash
python3 scripts/post_call_summary.py --file ~/.hermes/voice-live-notes/voice-live-20260604-190000.jsonl
```

---

## 🐛 Known bugs / quirks

> Full list in [`docs/KNOWN_BUGS.md`](docs/KNOWN_BUGS.md). Summary:

1. **Discord CDN handshake rejection** — `c-ams08.discord.media` rejects the first ~5 handshakes with code 4006. A single `channel.connect()` takes ~27 s of internal retries. **Do not restart the gateway repeatedly** — each restart resets the retry clock.
2. **Playback restart semantics** — `_on_playback_end` only logs errors; it does NOT restart. `_wake_playback` restarts playback when new Gemini audio arrives after silence. If playback stops during natural silence, the green ring turns off; new audio triggers a restart automatically.
3. **Module import path** — the plugin dir is `discord-voice` but Python imports it as `discord_voice` (dash → underscore normalization).
4. **Stale rejoin fix** — if a guild entry remains in `_active_bridges` but `vc.is_connected()` returns False, a new `/voice-live` hangs with "Bridge still starting". The plugin detects this and starts fresh instead of returning pending.

---

## 🛠️ Development

```bash
git clone https://github.com/Capslockb/gemini-live-discord-bridge.git
cd gemini-live-discord-bridge
pip install -r plugin/requirements.txt

# Symlink install (good for development)
python3 installer/install.py   # pick "symlink" mode

# Compile check after editing
python3 -m py_compile plugin/bridge.py plugin/__init__.py
```

### Project layout

```
gemini-live-discord-bridge/
├── README.md
├── LICENSE
├── plugin/                    # the actual Hermes plugin
│   ├── __init__.py            #   tool registration + slash commands
│   ├── bridge.py              #   core audio pipeline
│   ├── plugin.yaml            #   Hermes plugin metadata
│   └── requirements.txt
├── installer/
│   └── install.py             # oneshot TUI installer
├── scripts/
│   └── post_call_summary.py   # extract tasks/decisions from .jsonl
├── docs/
│   ├── ARCHITECTURE.md        # deep dive on the audio pipeline
│   ├── CONFIGURATION.md       # every env var explained
│   ├── KNOWN_BUGS.md          # full bug list with repro steps
│   └── diagrams/
│       ├── dataflow.txt       # ASCII dataflow diagram
│       ├── audio-pipeline.txt
│       └── idle-hangup.txt
└── .github/
    └── workflows/
        └── lint.yml
```

---

## 📜 License

MIT.

---

## 🙏 Credits

- Discord voice protocol — [`discord.py`](https://github.com/Rapptz/discord.py) + [`discord-ext-voice-recv`](https://github.com/imayhaveborkedit/discord-ext-voice-recv) for the receive side.
- Opus decoding — bundled with `discord.py`.
- Gemini Live — [Google AI Studio](https://aistudio.google.com).
- Hermes Agent — [Nous Research](https://nousresearch.com).
- Built and battle-tested at [capslock.nl](https://capslock.nl).
