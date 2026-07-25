# Video Frame Feeder

`video-frame-feeder.py` is intended to capture a local screen or window and send selected JPEG frames to the `discord-voice` bridge's `/frame` endpoint so Gemini Live can receive visual context.

## Current status

> **Frame delivery is blocked by [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).** The checked-in feeder currently conflicts with argparse's reserved `-h/--help` option, and neither the feeder nor the in-process `voice_live_frame` client sends the `X-API-Secret` required by `/frame`. Do not treat either path as operational until the executable fix and its tests are reviewed.

The control server binds to `127.0.0.1`. A direct Tailscale or other remote URL is not a supported feeder endpoint in the current runtime.

## Critical constraint: Discord screen shares are not bot video input

Discord bots cannot see a user's native Discord screen share or camera stream. The bridge can only receive frames that are explicitly posted to `/frame` by a local trusted client.

After Issue #9 is fixed, the supported flow should remain:

- **Frame path:** local display capture → authenticated loopback HTTP POST → bridge → Gemini Live.
- **Not a frame path:** Discord screen share or camera → bot. Discord supplies voice-state awareness flags, not the video stream.

## Voice-state awareness status

`_video_state_watcher()` polls Discord voice-state flags so the agent can be told that screen sharing or a camera was switched on or off. These events carry no image content.

Screen-share start/stop calls currently await the awareness helper. Camera-on and camera-off calls do not await that async helper, so the intended Gemini nudge, Honcho write, and notification path are skipped. This runtime defect is tracked in [Issue #10](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/10).

Fixing Issue #10 will restore state notifications only; it will not make Discord camera or screen-share pixels available to the bot.

## Installation

### Automatic

`install.sh` copies the feeder to `~/.hermes/scripts/video-frame-feeder.py` and marks it executable. This copy step does **not** make frame delivery operational while Issue #9 remains unresolved.

### Manual

```bash
mkdir -p ~/.hermes/scripts/
cp scripts/video-frame-feeder.py ~/.hermes/scripts/video-frame-feeder.py
chmod +x ~/.hermes/scripts/video-frame-feeder.py
```

## Usage and CLI flags

There is no supported working launch command on the current head. In particular, do not work around the parser conflict by disabling standard help, and do not expose port `18943` remotely to bypass authentication.

The current script declares these options:

| Flag | Current behavior |
|---|---|
| `--endpoint` | Bridge `/frame` URL. Defaults to `http://127.0.0.1:18943/frame`. |
| `--interval` | Seconds between capture attempts. Default `1.0`; values below `1.0` are raised to `1.0`. |
| `--source` | Capture source. Default `screen`; a platform-specific window title or X11 window ID may also be used. |
| `--x`, `--y` | Screen-capture offsets. Default `0`; used by the Linux screen path. |
| `--width` | Capture width. Default `768`. |
| `--height` | Capture height. Default `768`. The current `-h` alias conflicts with argparse help and must be removed by Issue #9. |
| `--display` | X11 display override. Defaults to `$DISPLAY` or `:0.0`. |
| `--force` | Bypass the bridge's recent-audio gate. It does not bypass authentication. |
| `--once` | Attempt one capture and exit. |
| `--min-change` | Minimum 64-bit aHash Hamming distance required to send. Default `2`. |
| `--stddev-min` | Minimum grayscale-pixel standard deviation. Default `0`, which disables uniform-frame rejection. |
| `--no-content-filter` | Disable aHash and standard-deviation filtering. |
| `--source-label` | URL-encoded into the `source` query parameter; it is not sent in an `X-Source-Label` header. |

## Content-aware filtering

The feeder first asks FFmpeg for an 8×8 raw grayscale thumbnail: 64 pixels and therefore 64 bytes. It computes an average hash and pixel standard deviation from that thumbnail. The full-resolution JPEG is captured only when the filter decides the frame should be sent, or when thumbnail capture fails and the fallback path is used.

## Troubleshooting

- **Argument conflict mentioning `-h`:** this is the known startup blocker in Issue #9. It occurs before any capture or HTTP request.
- **`401 Unauthorized`:** the current clients omit the required `X-API-Secret`. Copying `~/.hermes/control.secret` is not a valid workaround: the runtime defaults to `~/.hermes/voice-live-control-secret`, and the feeder reads neither file on the current head.
- **Remote/Tailscale endpoint fails:** the sidecar listens only on loopback. Keep it loopback-only; any remote transport needs a separately reviewed authenticated tunnel or proxy design.
- **Camera state changes produce no awareness message:** this is Issue #10. It is independent of frame capture and authentication.
- **Black frames or no content-selected frames after the runtime fix:** test with `--stddev-min 0` or `--no-content-filter`.
- **Too many frames or high CPU after the runtime fix:** increase `--min-change`, for example to `8` or `12`.
- **`x11 not found` or `Unable to get screen`:** run the feeder on a machine with a usable physical or virtual display and the appropriate FFmpeg capture backend.

## See also

- `voice_live_frame`: currently blocked by the same missing-auth propagation tracked in Issue #9.
- `voice_live_video_status`: read-only status inspection; it does not repair or authenticate frame delivery.
- Issue #10: camera state-awareness coroutine calls are not awaited.