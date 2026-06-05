# Gemini Live Discord Bridge

> Full-duplex Discord voice channel ↔ Google Gemini Multimodal Live API bridge, packaged as a [Hermes Agent](https://hermes-agent.nousresearch.com) plugin.

Bring Gemini's multimodal real-time reasoning (audio + images + tool calls) into any Discord voice channel. Speak to it, it speaks back — with Spotify control, email, Home Assistant, OpenCode delegation, per-user profiles, post-call transcripts, idle hangup, and a 3-minute oneshot installer.

```
  Discord user ──► Discord UDP ──► [NaCl] ──► Opus ──► resample ──► Gemini WSS
  Discord user ◄── Discord UDP ◄── [NaCl] ◄── Opus ◄── upsample ◄── Gemini WSS
                                    │
                                    └──► JSONL transcript at ~/.hermes/voice-live-notes/
```

---

## ✨ Features

| Capability | What it does |
|---|---|
| Full-duplex voice | 16 kHz mono ↔ 24 kHz mono with Opus decode on the receive side, NaCl encryption, 1 fps video |
| **40 Gemini function tools** | Spotify playback & playlists · web search & extract · email · Home Assistant · OpenCode delegation · system file inspection · Honcho memory recall · weather · translate · YouTube · reminders |
| **Per-user profiles** | Every Discord user gets isolated Honcho memory, tool allowlist, system prompt overrides, and opencode namespace — auto-created on first contact, owner-gated for destructive tools |
| **OpenCode delegation** | Gemini Live can spawn a full OpenCode coding session in a tmux window, poll progress, send follow-ups, kill it — voice-passthrough ready |
| **System file inspection** | `local_inspect_read` + `local_inspect_grep` with a hard-coded allowlist (`~/.hermes`, `/etc/systemd`, `hermes-workspace`, `honcho`, `~/projects`, `/var/log`) |
| **Honcho memory** | Peer representation + card injected at connect time; `local_honcho` tool lets Gemini search past decisions and preferences mid-conversation |
| **Spotify playlists** | `spotify_playlists` with `create` / `list` / `get` / `add_items` / `remove_items` / `update_details` — for hyper-personalized mood / work playlists |
| **Email** | Read, send, and reply via Gmail (`local_email`) — wired to the google-workspace skill |
| **Home Assistant** | `local_homeassistant_*` tools (entity list, get state, call service, get services) — gated on `HASS_TOKEN` |
| **Video sight** | `/frame` HTTP endpoint + `voice_live_frame` tool + a cross-platform feeder script (`video-frame-feeder.py`) for screen capture → Gemini |
| **Real keyboard SFX** | `voice-live-typing.wav` plays a single keyboard click whenever Gemini is running a tool, so the voice channel feels alive instead of silent |
| **Idle hangup** | Two-phase (prompt + grace) auto-leave after configurable quiet timeout, with voice-phrase override (`"bye"`, `"hang up"`, etc.) |
| **Cost-optimized** | `mediaResolution: LOW`, `turnCoverage: TURN_INCLUDES_ONLY_ACTIVITY`, 1 fps + audio-gated frames, 512 KB cap → **~$0.03–0.06/hour** of real conversation on the Flex tier |
| **Post-call transcripts** | JSONL with word-level events, model + user turn boundaries, tool calls, idle prompt / hangup events, compiled transcript at the bottom |
| **Oneshot TUI installer** | Live network validation of API keys, 3-minute setup, supports symlink mode for dev |
| **HTTP control API** | `127.0.0.1:18943` — `/health`, `/say`, `/leave`, `/frame`, `/notes` |
| **Autostart** | `voice-live-autostart.json` makes the bridge auto-join on gateway boot (deleted on success) |

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
| Home Assistant token *(optional)* | HA profile → Long-Lived Access Tokens | free |
| Gmail OAuth *(optional, for email)* | `hermes auth google-workspace` (handled by the google-workspace skill) | free |
| Python 3.10+ | system | free |
| Hermes Agent *(optional, but recommended)* | `pip install hermes-agent` | free / self-hosted |

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

Key fields: `voice_connected`, `playback_active`, `model`, `quiet_seconds`, `idle_prompted_seconds`, `video_in_frames`.

### Send text into the bridge

```bash
curl -s "http://127.0.0.1:18943/say?text=Hello+from+the+bridge"
```

### Push a video frame

```bash
curl -s -X POST -H "Content-Type: image/jpeg" --data-binary @photo.jpg http://127.0.0.1:18943/frame
```

Or run the cross-platform screen feeder:
```bash
python3 scripts/video-frame-feeder.py --endpoint http://127.0.0.1:18943/frame
```

### Leave the channel

```
/voice-live-leave
# or
voice_live_leave guild_id=1234567890
```

---

## 🧰 Function tools reference

The bridge exposes 40 Gemini function declarations, grouped by namespace. Tool declarations are filtered by the **active user's per-user profile** — destructive tools are owner-only by default.

### `spotify_*` (7 tools)
`spotify_play`, `spotify_pause`, `spotify_next`, `spotify_previous`, `spotify_get_state`, `spotify_set_volume`, `spotify_search`, `spotify_add_to_queue`, `spotify_playlists` *(create / list / get / add_items / remove_items / update_details)*. Wires the bundled `spotify` Hermes plugin.

### `web_*` (2 tools)
`web_search`, `web_extract`. Multi-provider fallback: SearXNG → Brave → Serper → Tavily → Exa.

### `local_*` (10 tools)
`local_weather`, `local_translate`, `local_time`, `local_remind`, `local_email` *(Gmail)*, `local_honcho` *(search past facts)*, `local_youtube`, plus the `local_inspect_*` and `local_homeassistant_*` families below.

### `local_homeassistant_*` (4 tools, owner-only if `HASS_TOKEN` is set)
`local_homeassistant_entity_list`, `local_homeassistant_get_state`, `local_homeassistant_call_service`, `local_homeassistant_get_services`. Gated on `HASS_TOKEN` env var.

### `local_inspect_*` (2 tools, **owner-only by default**)
`local_inspect_read`, `local_inspect_grep`. Read-only file/grep behind a hard allowlist (`~/.hermes`, `/etc/systemd`, `hermes-workspace`, `honcho`, `~/projects`, `/var/log`). New users get these tools **disabled**; the owner always has them.

### `opencode_*` (5 tools, **owner-only by default**)
`opencode_run` *(spawns a coding agent in a tmux window)*, `opencode_status` *(tails the log + checks window liveness)*, `opencode_list` *(shows tracked sessions for the active user)*, `opencode_send` *(injects follow-up message)*, `opencode_stop` *(kills the window + forgets the session)*. Per-user tmux session names: `oc-<user_hash>-<session>` so two users never collide.

### Toggle any tool category on/off
| Env var | Default | Effect |
|---|---|---|
| `DISCORD_VOICE_LIVE_SPOTIFY_TOOLS` | `true` | Spotify tools (requires `spotify` plugin enabled) |
| `DISCORD_VOICE_LIVE_WEB_TOOLS` | `true` | Web search / extract |
| `DISCORD_VOICE_LIVE_LOCAL_TOOLS` | `true` | Weather, translate, email, honcho search, etc. |
| `DISCORD_VOICE_LIVE_EMAIL_TOOLS` | `true` | `local_email` |
| `DISCORD_VOICE_LIVE_HA_TOOLS` | `true` | Home Assistant (also requires `HASS_TOKEN`) |
| `DISCORD_VOICE_LIVE_OPENCODE_TOOLS` | `true` | OpenCode delegation |
| `DISCORD_VOICE_LIVE_SYSINSPECT_TOOLS` | `true` | System file inspection |

---

## 👥 Per-user profiles

Every Discord user who joins the bridge gets their own profile at `~/.hermes/voice-users/<discord_id>.yaml`, auto-created on first contact. Profiles give you:

- **Isolated Honcho memory** — each user has their own `honcho_peer_name` (`discord-<id>`) so Gemini's memory is per-user, not global.
- **Per-user tool allowlist** — new users get a safe starter set (Spotify, web, email, local helpers, honcho search, HA). Destructive tools (`opencode_run`, `local_inspect_*`, etc.) are **owner-only** by default.
- **Per-user system prompt overrides** — append a custom block to the base prompt for a specific user.
- **Per-user opencode namespace** — tmux windows, log files, and session registry are all keyed by `(user_id, session_name)`. Two users running "refactor" don't collide; users cannot see each other's sessions.
- **Per-user notes / transcripts directory** — isolated, persisted.

### How "owner" is decided

The owner is the user whose Discord snowflake matches `VOICE_OWNER_DISCORD_ID` (default: B's snowflake `1474100257762578597`). Set this env var to a different ID to transfer ownership. Owners get the full toolset on first contact; every other user gets the safe starter set.

### Editing a profile

Profiles are plain YAML, atomic write. Hand-edit or use the Python API:
```python
import sys; sys.path.insert(0, "/home/caps/.hermes/plugins/discord-voice")
from user_profiles import get_or_create_profile, update_profile, list_profiles

# List all known users
list_profiles()
# → [{'discord_id': '1474...', 'is_owner': True, ...}, ...]

# Promote a user to owner
update_profile("12345678901234567", {"enabled_tools": ["all"]})

# Force-reload after edit
p = get_or_create_profile("12345678901234567")
print(p.is_tool_allowed("opencode_run"))  # → True
```

### Defense-in-depth

The dispatch path in `_handle_tool_call` re-checks the per-user allowlist **before** executing any tool, so a tool declaration that snuck through the filter is still refused at runtime. Cross-user opencode access returns `no opencode session named 'X' for current user`.

---

## 🔊 Real keyboard typing SFX

When Gemini is busy running a tool, the bridge plays a short keyboard click so the voice channel feels alive instead of silent. The SFX is `assets/voice-live-typing.wav` — a 60 ms single-keystroke taken from Mixkit's *Hard laptop keyboard typing* (CC0 / no attribution required), trimmed, band-limited (200 Hz – 4 kHz), and EBU-normalized to -16 LUFS.

To customize:
```env
# Path to your own WAV (must be 16-bit; any sample rate / channels)
DISCORD_VOICE_LIVE_TYPING_SFX=/path/to/your/sfx.wav
DISCORD_VOICE_LIVE_TYPING_SFX_VOLUME=0.45   # 0.0 – 1.0

# Disable entirely
DISCORD_VOICE_LIVE_TYPING_SOUND=false

# Use synthetic thud fallback (built-in, not as convincing)
DISCORD_VOICE_LIVE_TYPING_SYNTH_FALLBACK=true
```

The SFX fires while a tool is in-flight, then stops. Multiple tools in a single Gemini turn replay the click repeatedly, so you hear a staccato "thinking" pattern that ends when Gemini starts speaking.

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

The bridge is **in-process** — it lives inside the Hermes gateway's asyncio loop. No external services to run. Each Discord user gets their own `UserProfile` resolved at startup, threaded through `VoiceLiveBridge` → `GeminiLiveBridge` → tool dispatch.

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

# Optional — per-user profiles
VOICE_OWNER_DISCORD_ID=1474100257762578597   # who gets destructive tools by default
VOICE_USERS_DIR=~/.hermes/voice-users         # where profiles live

# Optional — auto-leave (hangup after N seconds of silence)
DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS=900
DISCORD_VOICE_LIVE_AUTO_LEAVE_MIN_UPTIME_SECONDS=120
DISCORD_VOICE_LIVE_LEAVE_PHRASES=leave voice,disconnect from voice,end voice,stop voice,goodbye hermes

# Optional — idle prompt ("are you still there?")
DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS=300
DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS=60
DISCORD_VOICE_LIVE_IDLE_PROMPT_TEXT="Are you still there?"

# Optional — keyboard typing SFX
DISCORD_VOICE_LIVE_TYPING_SOUND=true
DISCORD_VOICE_LIVE_TYPING_SFX=/home/caps/.hermes/voice-live-typing.wav
DISCORD_VOICE_LIVE_TYPING_SFX_VOLUME=0.45
DISCORD_VOICE_LIVE_TYPING_SYNTH_FALLBACK=false

# Optional — Honcho memory
VOICE_LIVE_HONCHO_CONTEXT=true
VOICE_LIVE_HONCHO_PEER=caps               # legacy single-user mode default
VOICE_LIVE_HONCHO_MAX_CHARS=1200
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
- Two-phase idle hangup so you're not paying for an open session that's just silence.

Result: roughly **$0.03–0.06/hour** of real conversation on the Flex tier for full-duplex audio. Idle frames are not billed.

---

## 📝 Post-call transcripts

Every call writes a JSONL transcript to `~/.hermes/voice-live-notes/voice-live-YYYYMMDD-HHMMSS.jsonl` containing:

- Word-level events with timestamps
- Model + user turn boundaries
- Tool call invocations
- Idle prompt / hangup events
- Final compiled transcript at the bottom

Per-user calls also write to `~/.hermes/voice-users/<discord_id>/notes/`.

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
5. **Opencode approval passthrough** — currently the bridge uses `-y` (auto-approve inside opencode) so the voice channel never blocks on opencode's own approval prompts. A real voice-passthrough that surfaces "Allow this action? [y/n]" back to Gemini for you to answer is a planned enhancement.

---

## 🛠️ Development

```bash
git clone https://github.com/Capslockb/gemini-live-discord-bridge.git
cd gemini-live-discord-bridge
pip install -r plugin/requirements.txt

# Symlink install (good for development)
python3 installer/install.py   # pick "symlink" mode

# Compile check after editing
python3 -m py_compile plugin/bridge.py plugin/__init__.py plugin/user_profiles.py
```

### Project layout

```
gemini-live-discord-bridge/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── plugin/                    # the actual Hermes plugin
│   ├── __init__.py            #   tool registration + slash commands
│   ├── bridge.py              #   core audio pipeline + Gemini Live bridge
│   ├── user_profiles.py       #   per-Discord-user profile system
│   ├── plugin.yaml            #   Hermes plugin metadata
│   ├── requirements.txt
│   └── assets/
│       └── voice-live-typing.wav  # keyboard click SFX (Mixkit CC0, 60 ms)
├── installer/
│   └── install.py             # oneshot TUI installer
├── scripts/
│   ├── post_call_summary.py   # extract tasks/decisions from .jsonl
│   └── video-frame-feeder.py  # cross-platform screen → /frame feeder
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

MIT. Bundled `voice-live-typing.wav` is from [Mixkit](https://mixkit.co) under the Mixkit License (royalty-free, no attribution required, commercial use allowed).

---

## 🙏 Credits

- Discord voice protocol — [`discord.py`](https://github.com/Rapptz/discord.py) + [`discord-ext-voice-recv`](https://github.com/imayhaveborkedit/discord-ext-voice-recv) for the receive side.
- Opus decoding — bundled with `discord.py`.
- Gemini Live — [Google AI Studio](https://aistudio.google.com).
- Honcho memory — [Honcho](https://github.com/plastic-labs/honcho), self-hosted at home.
- Keyboard typing SFX — [Mixkit](https://mixkit.co/free-sound-effects/type/), "Hard laptop keyboard typing".
- Hermes Agent — [Nous Research](https://nousresearch.com).
- Built and battle-tested at [capslock.nl](https://capslock.nl).
