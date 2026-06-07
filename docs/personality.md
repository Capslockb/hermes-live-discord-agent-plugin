# Personality — system prompt, ping-pong rhythm, boredom switch

The system prompt lives in `bridge.py:BASE_SYSTEM_PROMPT` and is prepended to every Gemini Live session. It is **not** a documentation file — it's a set of behavioral contracts the model is told to follow.

## Sections of the prompt (in order)

1. **Identity** — "You are S0RA, the AI companion of Capslockb (he calls you B)."
2. **Capabilities** — Spotify, web search, Gmail, Home Assistant, video awareness.
3. **VIDEO / SCREEN-SHARE guard** — strict conditional: "Only describe video you have actually received in the current turn."
4. **FIRST-TURN BEHAVIOUR** — "do NOT generate any audio. Wait for the user to speak first."
5. **PINGPONG RHYTHM** — split into question rounds and development rounds.
6. **FORMAT & ANSWER SHAPE** — answer first, then bullets; emotion is seasoning not the meal.
7. **CALL-OUT MODE** — puncture nonsense, move the work forward.
8. **PROACTIVE TOOL USE** — suggest tools before being asked.
9. **PROACTIVE ENGAGEMENT** — drive the conversation; if it's stalling, SAY IT.
10. **BOREDOM SWITCH** — escalate into NAG MODE if the chat drags.
11. **EDGE & COMEDY** — push boundaries, match B's dry sarcastic style.
12. **GF STATE / BOREDOM** — when B is checked out, shift energy: games, music, random maintenance.
13. **VOCAL EXPRESSION** — at most one inline speech tag per reply.
14. **TOOL BEHAVIOUR** — typing sound is normal, don't apologize for tool use.

## Why the prompt is **so** long

Each section addresses a specific regression observed in earlier sessions. The model collapses to "polite assistant" if any one of them is missing.

| Section | Regression it fixes |
|---|---|
| VIDEO guard | "I see you're sharing your screen" hallucination (criterion #33, #34) |
| FIRST-TURN | First-turn token burn (criterion #34) |
| PINGPONG | Monologue-style lectures when the question is still fuzzy |
| FORMAT | "Just laughing and not formatting answers" — emotion replacing substance |
| CALL-OUT | Hand-waving gets rubber-stamped instead of challenged |
| PROACTIVE TOOL | Tools forgotten unless prompted |
| PROACTIVE ENGAGEMENT | Long pauses with no nudge to keep moving |
| BOREDOM SWITCH | Stalls silently instead of escalating |
| VOCAL EXPRESSION cap | "<laugh> <laugh> <laugh>" spam |

## How to edit the prompt

The prompt is a single Python string concatenation. Edit `BASE_SYSTEM_PROMPT` in `bridge.py` (around line 162-177). After editing:

1. Compile-check: `python -m py_compile bridge.py`
2. Restart the gateway: `systemctl --user restart hermes-gateway`
3. Test by joining voice and triggering the relevant behavior

**Do not** add hedging like "be helpful and harmless" — the model interprets that as permission to revert to assistant defaults.

## Honcho context injection

The static prompt is appended with a per-session "Honcho context" block fetched from the Honcho memory server (see `HONCHO_CONTEXT_ENABLED` env var). This block contains:

- The user's recent session summaries
- Honcho's representation of B's peer card
- Capped at `HONCHO_CONTEXT_MAX_CHARS` (default 1200)

The Honcho block is **dynamic** — it varies per session — but the BASE_SYSTEM_PROMPT is **static** and identical across users. Per-user customization goes through Honcho, not the prompt.
