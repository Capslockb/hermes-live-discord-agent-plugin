# Personality — system prompt, ping-pong rhythm, boredom switch

The system prompt lives in `bridge.py:BASE_SYSTEM_PROMPT` and is prepended to every Gemini Live session. It is **not** a documentation file — it is a set of behavioral contracts the model is told to follow.

## Sections of the prompt (in order)

1. **Identity** — "You are S0RA, the AI companion of Capslockb (he calls you B)."
2. **Capabilities** — Spotify, web search, Gmail, Home Assistant, and prompt-level video awareness. The prompt advertises still-image and frame awareness, but the bundled frame-delivery paths are currently blocked by the startup and authentication defects tracked in [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9). Do not treat the prompt text as evidence that a frame was actually delivered.
3. **VIDEO / SCREEN-SHARE guard** — strict conditional: "Only describe video you have actually received in the current turn."
4. **FIRST-TURN BEHAVIOUR** — "do NOT generate any audio. Wait for the user to speak first."
5. **PINGPONG RHYTHM** — split into question rounds and development rounds.
6. **FORMAT & ANSWER SHAPE** — answer first, then bullets; emotion is seasoning, not the meal.
7. **CALL-OUT MODE** — puncture nonsense, move the work forward.
8. **PROACTIVE TOOL USE** — suggest tools before being asked.
9. **PROACTIVE ENGAGEMENT** — drive the conversation; if it is stalling, say so.
10. **BOREDOM SWITCH** — escalate into NAG MODE if the chat drags.
11. **EDGE & COMEDY** — push boundaries, match B's dry sarcastic style.
12. **GF STATE / BOREDOM** — when B is checked out, shift energy: games, music, random maintenance.
13. **VOCAL EXPRESSION** — at most one inline speech tag per reply.
14. **TOOL BEHAVIOUR** — typing sound is normal; do not apologize for tool use.

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

The prompt is a single Python string concatenation. Edit `BASE_SYSTEM_PROMPT` in `bridge.py`. After editing:

1. Compile-check: `python -m py_compile bridge.py`
2. Restart the gateway: `systemctl --user restart hermes-gateway`
3. Test by joining voice and triggering the relevant behavior

**Do not** add hedging like "be helpful and harmless" — the model interprets that as permission to revert to assistant defaults.

## Honcho context injection

`HONCHO_CONTEXT_ENABLED` is a Python constant, not an environment-variable name. It is controlled by `VOICE_LIVE_HONCHO_CONTEXT` (default `true`). When enabled, `_build_honcho_context()` appends a per-session Honcho block containing:

- the selected peer's Honcho representation, when available;
- card conclusions formatted as "Known facts about the user";
- at most `VOICE_LIVE_HONCHO_MAX_CHARS` characters (default `1200`).

The implementation does **not** fetch a separate list of recent session summaries for this block.

Peer selection follows the current runtime fallback chain:

1. a caller-provided per-user override;
2. `peerName` / `peer_name` from the `hermes` host or top-level `~/.hermes/honcho.json` configuration;
3. `VOICE_LIVE_HONCHO_PEER`;
4. legacy `HONCHO_PEER_NAME`;
5. `DISCORD_VOICE_LIVE_USER_ID`;
6. `user`.

The SDK path is attempted first and the HTTP path is used as a fallback. Missing configuration, authentication failure, unavailable Honcho services, or lookup failure produces no injected block rather than failing the voice session.

The Honcho block is **dynamic** and can vary by selected peer, while `BASE_SYSTEM_PROMPT` itself is static. Per-user memory customization therefore comes from the selected Honcho peer context, not from rewriting the base prompt.
