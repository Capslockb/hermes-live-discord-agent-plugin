# Voice persona and session behavior

The bridge applies a conversational voice persona to Gemini Live sessions. This page documents the user-visible behavior and supported configuration boundary. Internal prompt text, private coordination rules, and model-control wording are intentionally not reproduced in public documentation.

## User-visible behavior

The configured persona is intended to:

- wait for the user to speak before producing first-turn audio;
- prefer natural back-and-forth conversation over long monologues;
- keep answers structured and action-oriented;
- use available tools when they materially help the request;
- avoid claiming to see video or screen content unless a frame was actually delivered in the current interaction;
- keep inline vocal-expression tags limited so speech remains intelligible.

These are behavioral goals rather than delivery guarantees. Provider behavior, network conditions, tool availability, and the current runtime state can affect the result.

## Video-awareness boundary

Prompt-level video guidance does not prove that the model received a frame. The bundled frame-delivery paths remain blocked by the startup and authentication defects tracked in [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9). Treat video awareness as unavailable unless authenticated frame delivery has been independently verified.

## Honcho context

Optional Honcho context can append bounded, per-session user context when a valid peer is configured and the Honcho service is available.

- `VOICE_LIVE_HONCHO_CONTEXT` controls whether context lookup is attempted. The current default is enabled.
- `VOICE_LIVE_HONCHO_MAX_CHARS` limits the appended context size. The current default is `1200` characters.
- The selected peer comes from the supported Hermes/Honcho configuration or an explicit voice-live peer setting.
- Missing configuration, authentication failure, unavailable services, or lookup failure produces no additional context rather than failing the voice session.

The injected context is dynamic and may differ by selected peer. It does not rewrite the static base persona.

## Changing persona behavior

Persona changes modify executable runtime behavior and should be handled through a focused reviewed pull request. Validate syntax, restart behavior, voice-session behavior, tool boundaries, and video-awareness claims on the exact proposed commit. Do not publish internal prompt bodies, private operator instructions, authorization phrases, or hidden coordination rules in README or documentation files.
