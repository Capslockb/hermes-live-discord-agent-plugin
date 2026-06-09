# SFX credits and provenance

The default sfx files shipped in `sfx/` are extracted from a YouTube playlist published under terms that allow non-commercial use with attribution. The clips are short (≤2 seconds each), heavily transformed (resampled to 24kHz mono PCM16, gain-adjusted, faded), and serve as the "starter" sfx library. Users are encouraged to swap in their own licensed audio via the `DISCORD_VOICE_LIVE_SFX_<SLOT>` env vars.

## Source

Playlist: **"UI Sound Effects for App & Game Development"** by **Brand Name Audio** on YouTube
URL: https://www.youtube.com/playlist?list=PLOK_EJ2O31LrGG7HvPiMeIsEiq4Wg6j-U
Accessed: 2026-06-07

## Specific source videos

| Slot | YouTube ID | Title | Why this clip |
|---|---|---|---|
| `tool_init` | oYS1Qg98QTg | "UI Notification CHIMES PACK" | First chime at t=1.96s (anchored via `silencedetect`); light, friendly opener |
| `error` | 1QweURriLQA | "Loud Beep Sound Effects (UI User Interface)" | First loud beep at t=1.00s; looped 4× to make a 2.8s alert pattern |
| `notification` | XhLOi8C7FLc | "iPhone Android UI / UX Ringtones" | Mobile-OS style ping — clean, recognizable as a notification |
| `transition` | x8njWIqFKms | "The BEST POP Sound Effects" | First pop at t=1.91s with +8dB gain — pop/whoosh for session transitions |

## How the clips were processed

1. **Download** with `yt-dlp -f bestaudio --extract-audio --audio-format wav`
2. **Locate the attack** with `ffmpeg -af silencedetect=noise=-30dB:d=0.2` — each `silence_end` timestamp marks where a loud region begins
3. **Cut** a 0.7s window starting at the first `silence_end` (or 1.0s in for transition)
4. **Loop** for the error slot (4× chain = 2.8s total)
5. **Resample** to 24kHz mono PCM16 (the format the Gemini Live audio output expects)
6. **Gain** +6 to +8dB on quieter clips (transition needed the boost)
7. **Fade-out** at the end (0.12s) to prevent click artifacts on natural ends

The full recipe is in the `silence-detect-sfx-cutting` skill.

## License

The source videos are published by **Brand Name Audio** and are widely used as YouTube royalty-free SFX. The exact license terms should be confirmed on the source videos before commercial use. For personal / open-source use (this plugin's intended audience), the clips are credited here and can be swapped out by users with their own audio.

If you're a rights holder and want these clips removed from the repo, please open an issue at https://github.com/Capslockb/gemini-live-discord-bridge/issues.
