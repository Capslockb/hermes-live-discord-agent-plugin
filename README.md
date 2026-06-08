# Hermes Live Discord Agent Plugin

![Hermes Live Banner](docs/banner.png)

> **Drop a real-time multimodal AI into any Discord voice channel.**
> Speak to it. It answers in a human voice. Mid-conversation it can run tools, check email, queue Spotify, dim the lights, or hand off a coding task to **Codex / OpenCode / Gemini‑CLI / NumaSec / Hermes (API)**.
> Full-duplex audio · vision · 40+ function tools · per-user memory · post-call transcripts.
> Built on [Google Gemini Multimodal Live](https://ai.google.dev/api/live), packaged as a [Hermes Agent](https://hermes‑agent.nousresearch.com) plugin. **Open source. MIT. Yours.**

[![Discord](https://img.shields.io/badge/voice-discord-5865F2?logo=discord&logoColor=white)](https://github.com/Capslockb/hermes-live-discord-agent-plugin)
[![License](https://img.shields.io/badge/license-MIT-22d3ee)](LICENSE)
[![Hermes](https://img.shields.io/badge/hermes-agent-7c3aed?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMDAgMTAwIj48dGV4dCB4PSI1MCIgeT0iNzAiIGZvbnQtc2l6ZT0iODAiIHRleHQtYW5jaG9yPSJtaWRkbGUiPvCfjrU8L3RleHQ+PC9zdmc+)](https://hermes-agent.nousresearch.com)
[![Gemini](https://img.shields.io/badge/gemini-3.1--flash--live-4285F4?logo=google)](https://aistudio.google.com)

---

> 🆕 **New name, new site.** This plugin is now branded **Hermes Live Discord Agent Plugin** and has a public promo site at <https://capslockb.github.io/hermes-live-discord-agent-plugin/> with the feature showcase, roadmap, and collaboration info. The internal plugin directory is still `discord-voice` — renaming it would force refactors across the Hermes plugin registry, the autostart filename, and the import paths.

---

## ✦ Why this release matters

Hermes can now **hold a real conversation with you in voice**. Not a 30-second demo — sub-second latency, hour-long sessions, remembers what you talked about last time via [Honcho](https://github.com/plastic-labs/honcho) memory.

Mid-conversation, it can:

- 🔍 **Search the web** and read the answer aloud
- 📁 **Open your files**, review code, suggest fixes
- 📬 **Check your email** and summarize what matters
- 🎵 **Queue Spotify**, dim the lights (Home Assistant)
- 🧠 **Delegate and track** Codex / OpenCode / NumaSec / Hermes (API) sessions in the background
- 👁️ **See your screenshare** and walk you through a bug

**Cost: ~$0.03–0.06 / hour of voice** on Gemini's Flex tier. Less than a cup of coffee for a work day.

**Built in one session. One developer. Shipped.**

---

## ✦ Quick install

```bash
git clone https://github.com/Capslockb/hermes-live-discord-agent-plugin.git
cd hermes-live-discord-agent-plugin
python3 installer/install.py     # interactive (add --yes for defaults)
```

The installer walks through: system preflight → API keys → install mode → plugin deployment → `.env` merge → optional autostart.

**Requirements:** Python 3.10+, Hermes Agent (recommended), Discord bot token + Gemini API key.

---

## ✦ What you can do in voice

| You say… | Gemini does… |
|---|---|
| "Find me a focus playlist" | Calls `spotify_playlists` → creates a custom playlist from your liked tracks |
| "Check my inbox" | Calls `local_email_list` → reads unread sender/subject |
| "Send an email to …" | Calls `local_email_send` with auto‑corrected address |
| "Refactor the auth module" | Calls `local_delegate_suggest` → picks best CLI → assembles prompt → spawns task |
| "What's my GitHub?" | Calls `local_github_repo_list` → lists repos |
| "Suggest repos for …" | Calls `local_github_suggest_repos` → searches GitHub |
| "Turn off the lights" | Calls `local_homeassistant_call_service` |
| "What's the weather?" | Calls `local_weather` |
| "🔊 [starts typing]" *(tool running)* | Plays a real mechanical‑keyboard click via the typing SFX |

---

## ✦ Features

<details>
<summary><strong>🎛️ 5‑CLI delegation framework</strong> — OpenCode, Codex, Gemini CLI, Numasec, Hermes API</summary>

When you ask for a coding/deployment/security task, Gemini: (1) calls `local_delegate_suggest` — analyzes size/scope/complexity, checks rate limits, suggests best platform + ETA, (2) reads the assembled plan back to you for confirmation, (3) calls `local_delegate_assemble` — builds a platform‑optimized system prompt with sub‑goals and constraints, (4) calls `local_delegate_execute` — spawns the CLI in a tmux window or POSTs to the Hermes API server. Progress is webhook‑pushed and the agent can poll via `opencode_status`.

**Rate‑limit tracking:** rolling 1‑hour windows per platform. If Codex is at its limit, Gemini says *"Codex is almost at rate limits — want to run this in OpenCode instead?"* **Context‑fit warnings:** if the estimated prompt + project tree exceeds a platform's context limit, Gemini warns you before execution.
</details>

<details>
<summary><strong>🧰 40+ Gemini function tools</strong> — all callable by voice</summary>

| Family | Tools | Gating |
|---|---|---|
| Spotify | play, pause, skip, volume, search, queue, **playlists** (create / add_items / list) | owner‑default |
| Web | search, extract | all users |
| Local helpers | weather, translate, time, remind, YouTube, systemd | all users |
| Email | list, read, **send** (with STT auto‑correction), reply | all users |
| Honcho | search past facts & decisions | all users |
| Home Assistant | entity_list, get_state, call_service, get_services | HASS_TOKEN |
| System inspect | read, grep (allowlisted paths) | **owner‑only** |
| OpenCode | run, status, list, send, stop | **owner‑only (run/stop)** |
| GitHub | repo_list, issues, prs, issue_create, note, notes_read, **suggest_repos** | all users (gh auth) |
| Onboarding | get_questions, answer | all users (first session) |
| Delegation | suggest, assemble, execute, calibrate_eta | all users |
</details>

<details>
<summary><strong>👥 Per‑user profiles</strong> — isolated memory, tools, and opencode sessions</summary>

Every Discord user gets their own profile at `~/.hermes/voice‑users/<id>.yaml`, auto‑created on first contact. Each profile owns:

- **Honcho peer name** — memory is fully isolated per user
- **Tool allowlist** — new users get safe defaults (Spotify, web, email, helpers). Destructive tools (opencode_run, sysinspect, etc.) are **owner‑only** by default
- **System prompt overrides** — custom instructions per user
- **Opencode namespace** — tmux windows, log files, and registry are all keyed per user. Cross‑user session access denied at the dispatch level
- **Onboarding answers** — name, timezone, work, interests, communication style, pet peeves captured during the first session

**Owner detection:** set `VOICE_OWNER_DISCORD_ID` env var. Default: B's snowflake `1474100257762578597`.
</details>

<details>
<summary><strong>🔊 Real keyboard typing SFX</strong> — plays while a tool is running</summary>

When Gemini invokes a tool, the bridge plays a 180ms **real mechanical keyboard click** (sourced from YouTube, single keypress) at 5‑15 Hz with random jitter. The result sounds like someone actually typing — not a click track. Set `DISCORD_VOICE_LIVE_TYPING_SFX` to a custom WAV path, or `DISCORD_VOICE_LIVE_TYPING_SOUND=false` to disable.
</details>

<details>
<summary><strong>📡 Webhook dispatcher</strong> — 6 event classes, per‑class Discord webhook URLs</summary>

| Event class | Env var | Fires on… |
|---|---|---|
| `voice.transcript` | `…WEBHOOK_TRANSCRIPT` | every Gemini input/output line |
| `opencode.status` | `…WEBHOOK_OPENCODE_STATUS` | started / progress / milestone / finished / stopped |
| `opencode.transcript` | `…WEBHOOK_OPENCODE_TRANSCRIPT` | live opencode log tail (throttled) |
| `email.sent` | `…WEBHOOK_EMAIL` | email sent via local_email_send |
| `bridge.status` | `…WEBHOOK_BRIDGE_STATUS` | bridge connect / disconnect |
| `tool.called` | `…WEBHOOK_TOOL_CALLED` | any tool invocation (throttled) |

Throttled at 2s/event/webhook via `…WEBHOOK_THROTTLE_SECONDS`. Embeds disable @everyone pings. 6 webhook URLs already configured in `.env` (from criterion #17).
</details>

<details>
<summary><strong>📧 Email (read, send, reply) + important‑email reminders</strong></summary>

**Local email tools** (`local_email_list`, `local_email_read`, `local_email_send`, `local_email_reply`) use the Gmail API via the Hermes `google_api.py` wrapper. STT auto‑correction fixes common voice transcription errors — `"alice at example dot com"` → `alice@example.com`, with the agent saying *"I heard that as… corrected to… confirm?"* before sending.

**Background email reminder poller** checks the inbox every 5 minutes and voice‑reminds you about important non‑spam messages. Filters out newsletters, CI notifications, PR messages, receipts, and automated senders — only reminds for real human‑to‑human emails. Throttled to 3 reminders/hour. Persistently remembers seen IDs so restarts don't re‑nag.
</details>

<details>
<summary><strong>🤖 OpenCode watcher</strong> — voice updates during long coding tasks</summary>

A background async task polls the opencode log every 5s and injects progress into the Gemini session so the agent speaks it aloud. Throttles to one update per 30s, with **milestone detection** that triggers immediate updates on errors, test failures, compile successes, and completions. Sends a final summary when the tmux window dies. Respects "user currently speaking" — drops updates rather than barging in.
</details>

<details>
<summary><strong>🎥 Discord video awareness</strong> — Gemini knows when you're sharing</summary>

When you turn on your camera or start screen sharing, the bridge tells Gemini *"<Name> started screen sharing. I can't see the shared screen automatically (Discord bots don't get video streams), but they can use the /frame command to share a screenshot, or run video‑frame‑feeder.py to push frames. Until then, I just know sharing is active."* Gemini can then ask you to describe verbally or push a frame.
</details>

<details>
<summary><strong>🧷 Per‑user onboarding Q&A</strong> — first session learns about you</summary>

A brand‑new user's first `/voice‑live` triggers a system‑prompt reminder telling Gemini to walk through 6 questions (name, timezone, work, interests, communication style, pet peeves). Answers are persisted to the profile YAML and mirrored to top‑level fields. Subsequent calls use these to personalize the conversation. The agent also captures `communication_style` + `pet_peeves` and injects them as behavioral guidance (#28 speech mirroring).
</details>

<details>
<summary><strong>💵 Wallet‑safe</strong> — cost optimization & idle hangup</summary>

- `mediaResolution` omitted (not supported by current models → was causing 1007 reconnect loops)
- `turnCoverage: TURN_INCLUDES_ONLY_ACTIVITY` — frames billed only during speech
- 1 fps cap + 512 KB max + audio‑gated frame uploads
- Two‑phase idle hangup (prompt → grace → leave)
- Result: **~$0.03–$0.06/hour** of real conversation on Flex tier
</details>

---

## ✦ Configuration

All env vars live in `~/.hermes/.env`. The plugin also reads `~/.hermes/config.yaml` for structured settings.

<details>
<summary><strong>Requirements</strong></summary>

```
DISCORD_BOT_TOKEN=...
GEMINI_API_KEY=...
DISCORD_ALLOWED_USERS=1474100257762578597
```
</details>

<details>
<summary><strong>Model selection</strong></summary>

```
GEMINI_MODEL=models/gemini-3.1-flash-live-preview
GEMINI_LIVE_MODEL_FALLBACKS=models/gemini-3.1-flash-live-preview,...
DISCORD_VOICE_LIVE_VOICE=Aoede          # default, TTS voice name
```
</details>

<details>
<summary><strong>Idle hangup</strong></summary>

```
DISCORD_VOICE_LIVE_AUTO_LEAVE_QUIET_SECONDS=900
DISCORD_VOICE_LIVE_IDLE_PROMPT_SECONDS=120
DISCORD_VOICE_LIVE_IDLE_PROMPT_GRACE_SECONDS=60
DISCORD_VOICE_LIVE_IDLE_PROMPT_TEXT=Are you still there?
```
</details>

<details>
<summary><strong>Webhooks</strong></summary>

```
DISCORD_VOICE_LIVE_WEBHOOK_TRANSCRIPT=...
DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_STATUS=...
DISCORD_VOICE_LIVE_WEBHOOK_OPENCODE_TRANSCRIPT=...
DISCORD_VOICE_LIVE_WEBHOOK_EMAIL=...
DISCORD_VOICE_LIVE_WEBHOOK_BRIDGE_STATUS=...
DISCORD_VOICE_LIVE_WEBHOOK_TOOL_CALLED=...
DISCORD_VOICE_LIVE_WEBHOOK_THROTTLE_SECONDS=2
```
</details>

<details>
<summary><strong>Per‑user profiles</strong></summary>

```
VOICE_OWNER_DISCORD_ID=1474100257762578597
VOICE_USERS_DIR=~/.hermes/voice‑users
```
</details>

<details>
<summary><strong>Opencode watcher</strong></summary>

```
DISCORD_VOICE_LIVE_OPENCODE_WATCHER=true
DISCORD_VOICE_LIVE_OPENCODE_WATCHER_POLL_SECONDS=5
DISCORD_VOICE_LIVE_OPENCODE_WATCHER_MIN_VOICE_GAP_SECONDS=30
DISCORD_VOICE_LIVE_OPENCODE_WATCHER_INITIAL_DELAY_SECONDS=60
```
</details>

<details>
<summary><strong>Email reminders</strong></summary>

```
DISCORD_VOICE_LIVE_EMAIL_REMINDER_ENABLED=true
DISCORD_VOICE_LIVE_EMAIL_REMINDER_POLL_SECONDS=300
DISCORD_VOICE_LIVE_EMAIL_REMINDER_MAX_PER_HOUR=3
```
</details>

<details>
<summary><strong>Typing SFX</strong></summary>

```
DISCORD_VOICE_LIVE_TYPING_SOUND=true
DISCORD_VOICE_LIVE_TYPING_SFX=/home/caps/.hermes/voice‑live‑typing.wav
DISCORD_VOICE_LIVE_TYPING_SFX_VOLUME=0.45
DISCORD_VOICE_LIVE_TYPING_SYNTH_FALLBACK=false
```
</details>

<details>
<summary><strong>Delegation agent (API server)</strong></summary>

```
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8088
API_SERVER_KEY=Redleak@CLB25!
```
</details>

---

## ✦ Commands

| Slash command | What it does |
|---|---|
| `/voice‑live` | Join your current voice channel and start the bridge (native Discord Application Command) |
| `/voice‑live‑leave` | Stop the bridge |

Plugins also expose tool calls for agent‑mediated use via Hermes chat.

---

## ✦ Architecture

The bridge is **in‑process** — it lives inside the Hermes gateway's asyncio loop. No external services.

```
  Discord user ──► Discord UDP ──► [NaCl/Opus decode] ──► 16kHz PCM ──► Gemini WSS
  Discord user ◄── Discord UDP ◄── [Opus encode] ◄── 24kHz PCM ◄── Gemini WSS
  
  Tool dispatch:
    Gemini calls tool → bridge executor → synchronous runner → toolResponse → Gemini speaks
  
  Control API:
    GET  /health → bridge state JSON
    POST /frame  → push video frame to Gemini
    GET  /notes  → post‑call transcript
    GET  /say?text=… → inject text into the session
```

---

## ✦ Development

```bash
git clone https://github.com/Capslockb/hermes-live-discord-agent-plugin.git
cd hermes-live-discord-agent-plugin
pip install -r plugin/requirements.txt
python3 installer/install.py          # pick "symlink" mode
python3 -m py_compile plugin/*.py      # compile check

# Regression suite
python3 scripts/regression_test_user_isolation.py   # 50 checks
python3 scripts/regression_test_opencode_watcher.py  # 13 checks
python3 scripts/regression_test_criteria_17_18_19.py # 50 checks
python3 scripts/regression_test_criteria_31_32.py    # 43 checks
python3 scripts/regression_test_criterion_22.py      # 22 checks
```

---

## ✦ Project layout

```
hermes-live-discord-agent-plugin/
├── README.md
├── CHANGELOG.md
├── plugin/                        # the Hermes plugin
│   ├── __init__.py                # tool registration + slash commands
│   ├── bridge.py                  # core audio pipeline + Gemini Live
│   ├── user_profiles.py           # per‑Discord‑user profile system
│   ├── delegation_agent.py        # multi‑CLI delegation framework
│   ├── webhook_dispatcher.py      # per‑event‑class Discord webhooks
│   ├── plugin.yaml
│   ├── requirements.txt
│   └── assets/
│       └── voice-live-typing.wav  # mech keyboard click (180ms)
├── installer/
│   └── install.py
├── scripts/
│   ├── post_call_summary.py       # extract tasks from .jsonl
│   ├── video-frame-feeder.py      # cross‑platform screen feeder
│   └── regression_test_*.py       # 6 test suites
└── docs/
    ├── ARCHITECTURE.md        # markdown source
    ├── CONFIGURATION.md
    ├── KNOWN_BUGS.md
    ├── index.html             # promo site + GitHub Pages root
    ├── architecture.html      # rendered doc pages
    ├── configuration.html
    ├── known-bugs.html
    ├── changelog.html
    ├── assets/style.css       # shared stylesheet
    └── diagrams/              # ASCII dataflow diagrams
```

---

## ✦ License

MIT. Bundled `voice‑live‑typing.wav` is from Mixkit (CC0‑equivalent, no attribution required, commercial use allowed).

---

## ✦ Credits

- Discord voice protocol — [discord.py](https://github.com/Rapptz/discord.py) + [discord‑ext‑voice‑recv](https://github.com/imayhaveborkedit/discord-ext-voice-recv)
- Opus decoding — bundled with discord.py
- Gemini Live — [Google AI Studio](https://aistudio.google.com)
- Honcho memory — [Honcho](https://github.com/plastic-labs/honcho), self‑hosted
- Hermes Agent — [Nous Research](https://nousresearch.com)
