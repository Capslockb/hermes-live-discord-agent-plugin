# Video Frame Feeder

`video-frame-feeder.py` is intended to capture a local screen or window and send selected JPEG frames to the `discord-voice` bridge's `/frame` endpoint so Gemini Live can receive visual context.

## Current status

> **Frame delivery is blocked by [Issue #9](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/9).** The checked-in feeder currently conflicts with argparse's reserved `-h/--help` option, and neither the feeder nor the in-process `voice_live_frame` client sends the `X-API-Secret` required by `/frame`. Do not treat either path as operational until the executable fix and its tests are reviewed.

After Issue #9 is fixed, the bundled feeder will still mirror filtering defects owned by the canonical [`Capslockb/video-frame-feeder`](https://github.com/Capslockb/video-frame-feeder) implementation: average hash can miss global brightness transitions, and failed capture or delivery attempts advance comparison state before the bridge accepts a frame. Canonical runtime fixes belong in video-frame-feeder Issues [#11](https://github.com/Capslockb/video-frame-feeder/issues/11) and [#12](https://github.com/Capslockb/video-frame-feeder/issues/12); Hermes Live [Issue #19](https://github.com/Capslockb/hermes-live-discord-agent-plugin/issues/19) tracks only the bundled-copy synchronization or backport contract.

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
| `--stddev-min` | Minimum grayscale-pixel standard deviation. The parser default is `0`, which disables uniform-frame rejection even though the script's module text still describes a `6.0` default. |
| `--no-content-filter` | Disable aHash and standard-deviation filtering. |
| `--source-label` | URL-encoded into the `source` query parameter; it is not sent in an `X-Source-Label` header. |

## Content-aware filtering

The feeder first asks FFmpeg for an 8×8 raw grayscale thumbnail: 64 pixels and therefore 64 bytes. It computes an average hash and pixel standard deviation from that thumbnail. The full-resolution JPEG is captured only when the filter decides the frame should be sent, or when thumbnail capture fails and the fallback path is used.

The current filter has two important post-#9 limitations:

1. Average hash records whether each pixel is above that frame's own mean, not its absolute brightness. Uniform black, gray, and white thumbnails all produce the same hash, and other global brightness changes can also look unchanged. With the current `--stddev-min 0` default, uniform-frame rejection does not compensate for that behavior.
2. The loop assigns `last_hash` before full-frame capture and before HTTP acceptance. A transient capture failure, `401`, network/JSON error, size rejection, or other bridge refusal can therefore prevent the same unchanged content from being retried.

The canonical implementation work belongs in video-frame-feeder [#11](https://github.com/Capslockb/video-frame-feeder/issues/11) and [#12](https://github.com/Capslockb/video-frame-feeder/issues/12), including an absolute-luminance signal, accepted-delivery state, and deterministic synthetic-thumbnail and retry tests. Hermes Live Issue #19 must then prove an explicit import, generated-vendor, or tested-backport relationship instead of introducing a divergent second implementation.

Until those fixes are reviewed and synchronized, `--no-content-filter` is the only diagnostic bypass for suspected filter suppression; it increases frame volume and cost and does not repair Issue #9's parser or authentication blockers.

## Troubleshooting

- **Argument conflict mentioning `-h`:** this is the known startup blocker in Issue #9. It occurs before any capture or HTTP request.
- **`401 Unauthorized`:** the current clients omit the required `X-API-Secret`. Copying `~/.hermes/control.secret` is not a valid workaround: the runtime defaults to `~/.hermes/voice-live-control-secret`, and the feeder reads neither file on the current head.
- **Remote/Tailscale endpoint fails:** the sidecar listens only on loopback. Keep it loopback-only; any remote transport needs a separately reviewed authenticated tunnel or proxy design.
- **Camera state changes produce no awareness message:** this is Issue #10. It is independent of frame capture and authentication.
- **A dark/light or blank-screen transition is skipped after the runtime fix:** this is the canonical average-hash defect in video-frame-feeder #12, mirrored by Hermes Live #19. `--stddev-min 0` is already the parser default and does not fix it; use `--no-content-filter` only as a temporary diagnostic measure.
- **A frame is never retried after capture or bridge failure:** this is the canonical accepted-delivery-state defect in video-frame-feeder #11, mirrored by Hermes Live #19. Change the screen content or temporarily disable filtering until the reviewed fix is synchronized.
- **Too many frames or high CPU after the runtime fix:** increase `--min-change`, for example to `8` or `12`, but validate that meaningful brightness and content transitions are still delivered.
- **`x11 not found` or `Unable to get screen`:** run the feeder on a machine with a usable physical or virtual display and the appropriate FFmpeg capture backend.

## See also

- `voice_live_frame`: currently blocked by the same missing-auth propagation tracked in Issue #9.
- `voice_live_video_status`: read-only status inspection; it does not repair or authenticate frame delivery.
- Issue #10: camera state-awareness coroutine calls are not awaited.
- video-frame-feeder #11 and #12: canonical retry-state and brightness-filtering implementation.
- Hermes Live Issue #19: bundled-copy synchronization and drift prevention.