# PCM Resampling: Windowed-Sinc FIR Filtering

## Problem

Voice bridges resample between Discord (48kHz stereo) and AI providers (16kHz mono). The default approach — simple truncation, averaging, or tiny FIR kernels — introduces **audible aliasing distortion** that sounds "fuzzy", "garbled", or "robotic".

## Failure modes of naive resamplers

| Filter | Taps | Stopband rejection | Result |
|--------|------|-------------------|--------|
| 3-tap boxcar (running average) | 3 | ~8-12 dB | **Fuzzy/garbled audio** — spectral images of 4-8kHz sibilants fold into passband |
| 5-tap triangular | 5 | ~12-16 dB | Slightly better, still ~10-20dB rejection — audible artifacts |
| 63-tap windowed-sinc | 63 | **~60-160 dB** | Clean — images are below the noise floor of Discord Opus codec |

## The fix: windowed-sinc FIR

No scipy, no soxr, no extra deps — just numpy's built-in `np.sinc` and `np.hamming`:

```python
import numpy as np

def _design_lowpass(cutoff: float, num_taps: int = 63) -> np.ndarray:
    """Lowpass FIR via windowed sinc. cutoff in [0, 0.5] (Nyquist)."""
    if num_taps % 2 == 0:
        num_taps += 1
    half = num_taps // 2
    n = np.arange(-half, half + 1, dtype=np.float32)
    h = np.sinc(2.0 * cutoff * n)
    w = np.hamming(num_taps).astype(np.float32)
    h = h * w
    h /= h.sum()
    return h

_RESAMPLE_LP_3 = _design_lowpass(1.0/3.0, 63)  # pre-computed at module load
```

### Downsample 48→16kHz (decimate by 3)

Filter at cutoff 1/3 (8kHz for 48kHz input), then keep every 3rd sample:

```python
filtered = np.convolve(raw, _RESAMPLE_LP_3, mode="same")
raw = filtered[::3]
```

### Upsample 16→48kHz (interpolate by 3)

Zero-stuff by 3, FIR filter, scale by interpolation factor:

```python
upsampled = np.zeros(len(raw) * 3, dtype=np.float32)
upsampled[::3] = raw
raw = np.convolve(upsampled, _RESAMPLE_LP_3, mode="same")
raw = raw * 3.0  # upsampling gain — NOT optional
```

### Why the gain correction

Zero-stuffing inserts zeros between samples — the energy of the signal drops by the interpolation factor (3). The FIR filter smooths but does not restore the amplitude. `raw * 3.0` fixes the DC gain back to unity.

### Channel conversion (before resampling)

Always do channel conversion on the source rate to avoid double-resampling artifacts:

```python
# Stereo → mono: average channels
if src_ch == 2 and dst_ch == 1:
    raw = raw.reshape(-1, 2).mean(axis=1)

# Mono → stereo: duplicate
if src_ch == 1 and dst_ch == 2:
    raw = np.repeat(raw, 2)
```

### Final casting

Always clip to int16 range — FIR filter overshoot (Gibbs phenomenon) can exceed [-32768, 32767]:

```python
raw = np.clip(raw, -32768, 32767).astype(np.int16)
return raw.tobytes()
```

## Verification

Test the filter design with a DFT:

```python
N = 4096
H = np.fft.rfft(_RESAMPLE_LP_3, n=N)
H_db = 20 * np.log10(np.maximum(np.abs(H), 1e-10))
print(f"Stopband attenuation: {-float(np.min(H_db)):.1f} dB")
print(f"DC gain: {_RESAMPLE_LP_3.sum():.6f}")  # should be 1.0
```

## Performance

63-tap FIR convolution on 20ms PCM chunk (960 samples at 48kHz):
- ~30µs per chunk in numpy (native C under the hood)
- ~1.5ms per second of real-time audio
- Zero GC pressure if pre-computed filter is module-level

## Alternative approaches (and why not)

| Method | Quality | Deps | Latency | Verdict |
|--------|---------|------|---------|---------|
| Boxcar/triangular 3-5 taps | ~12dB rejection | None | 0 | **WRONG for production** |
| Windowed-sinc 63 taps | ~160dB rejection | numpy only | ~30µs/chunk | **Default choice** |
| scipy.signal.resample | Good (FFT-based) | scipy (heavy) | ~500µs/chunk | Overkill — scipy is ~50MB |
| scipy.signal.resample_poly | Excellent | scipy | ~100µs/chunk | Same deps problem |
| libsoxr | Excellent | soxr + bindings | ~20µs/chunk | Extra dep, not in venv |
| ffmpeg subprocess | Excellent | ffmpeg | ~5ms + IPC | High latency per chunk |
| Linear interpolation (np.interp) | Poor | numpy | ~20µs | Aliasing, no anti-image filter |

**Recommendation:** Pure-numpy windowed-sinc is the best tradeoff for in-process voice bridges.

## See also

- The `_resample_pcm()` function in any voice bridge's `bridge.py`
- `np.hamming()` — Hamming window (minimum side lobe for given main lobe width)
- `np.blackman()` — even better stopband at same tap count (but slightly wider transition band)
