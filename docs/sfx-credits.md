# SFX credits and provenance

The default sfx files shipped in `sfx/` are short transformed excerpts from the YouTube sources listed below. The repository records their provenance and processing steps, but it does not preserve or independently verify the source license terms and does not include a standalone license granting reuse or redistribution of the bundled clips. Resampling, cutting, gain adjustment, fading, or other transformation does not by itself create reuse rights. Users should replace the clips with audio they are licensed to use before redistribution or commercial deployment unless they have independently verified permission.

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
5. **Resample** to 24 kHz mono PCM16 (the format the Gemini Live audio output expects)
6. **Gain** +6 to +8dB on quieter clips (transition needed the boost)
7. **Fade-out** at the end (0.12s) to prevent click artifacts on natural ends

The full recipe is in the `silence-detect-sfx-cutting` skill.

## Licensing boundary

The source videos are published by **Brand Name Audio** and may be described by their publisher or platform metadata using terms such as royalty-free. Those descriptions are not reproduced as a durable license grant in this repository and can change or vary by source video. Confirm the applicable terms on each source and retain evidence of permission before use, redistribution, or commercial deployment.

This repository also does not currently include a standalone project license. Source availability and the processing description above do not grant permission to copy, modify, or redistribute either the project or the bundled audio.

If you are a rights holder and want these clips removed from the repository, please open an issue at https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues.
