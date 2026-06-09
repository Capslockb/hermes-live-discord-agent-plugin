# Video Frame Feeder

The `video-frame-feeder.py` is a companion script that captures your local screen and pushes frames to the `discord-voice` bridge's `/frame` endpoint. This allows Gemini Live to "see" what you are doing in real-time.

## ⚠️ Critical Constraint: How it Works
**Discord bots cannot see user screen-shares natively.** 

If you share your screen within the Discord app, the bot **cannot** see it. To give the bot vision, you must run this feeder on a machine that has a real display (e.g., your laptop). The feeder captures the display locally and sends the image data over HTTP to the bridge.

- **Correct Flow:** Laptop (Feeder) $\rightarrow$ HTTP POST $\rightarrow$ Bridge $\rightarrow$ Gemini Live.
- **Wrong Flow:** Laptop $\rightarrow$ Discord Screen Share $\rightarrow$ Bot (Blind).

## Installation

### Automatic
Running `install.sh` automatically copies the feeder to `~/.hermes/scripts/video-frame-feeder.py` and sets the correct permissions.

### Manual
If you need to install it manually:
```bash
mkdir -p ~/.hermes/scripts/
cp scripts/video-frame-feeder.py ~/.hermes/scripts/video-frame-feeder.py
chmod +x ~/.hermes/scripts/video-frame-feeder.py
```

## Usage & CLI Flags

### Quick Start
Run the feeder by pointing it to your bridge's Tailscale or local URL:
```bash
python3 ~/.hermes/scripts/video-frame-feeder.py --endpoint http://<your-bridge-tailscale-url>:18943/frame --source-label my-laptop
```

### CLI Flags
| Flag | Description |
|---|---|
| `--endpoint` | The URL of the bridge's `/frame` endpoint (required). |
| `--source` | Screen source (e.g., `0` for primary, `DISPLAY` env var). |
| `--min-change` | Minimum Hamming distance (0-64) to trigger a send. Default: `4`. |
| `--stddev-min` | Minimum standard deviation of pixels to consider "content". Default: `0`. |
| `--no-content-filter` | Disable perceptual hashing and stddev checks. |
| `--source-label` | Label for this feed (e.g., `my-laptop`), sent in the `X-Source-Label` header. |
| `--once` | Capture a single frame and exit. |

## Content-Aware Filtering
To save bandwidth and tokens, the feeder uses a perceptual hash (aHash). It generates a 64-bit grayscale thumbnail of the screen and compares it to the previous frame. If the Hamming distance is below `--min-change`, the frame is dropped. This prevents the feeder from spamming identical static images (like a code editor) while still reacting to movement.

## Troubleshooting

- **Black frames / No frames sent:** The content filter might be too aggressive. Try `--stddev-min 0` or `--no-content-filter`.
- **Too many frames / High CPU:** Increase `--min-change` (e.g., to `8` or `12`).
- **Bridge Auth Error:** Ensure `~/.hermes/control.secret` on the feeder machine matches the one on the bridge machine (or is created by `install.sh`).
- **"x11 not found" / "Unable to get screen":** This is a headless host. You **must** run the feeder on a machine with a physical or virtual display (X11/Wayland).

## See Also
- `voice_live_frame` tool: Allows the agent to manually request a frame move from chat.
- `voice_live_video_status` tool: Check the current count of accepted vs. dropped frames.
