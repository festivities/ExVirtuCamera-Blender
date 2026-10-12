# VP9 encoder spike: PyAV for Blender 5.2 and Goo Engine 4.4.3

Date: 2026-10-13. Feasibility only. No addon code, AGENTS.md, or agent files were changed. Experiments live in the session scratchpad (`encoder_spike/`). Nothing was installed into a real extensions folder.

## Recommendation

- Use `av==18.1.0` as a single abi3 wheel: `av-18.1.0-cp311-abi3-win_amd64.whl` (27,595,679 bytes). It imports on Blender 5.2 (Python 3.13.13) and Goo 4.4.3 (Python 3.11.11) and has `libvpx-vp9`.
- Manifest for the addon (Windows only, one wheel):

  ```toml
  platforms = ["windows-x64"]
  wheels = [
      "./wheels/av-18.1.0-cp311-abi3-win_amd64.whl",
  ]
  ```

- Do not list `av-19.0.1-cp312-abi3` in the same extension. It is newer, but it is `cp312-abi3`, so it cannot target Python 3.11 (Goo). It works on 5.x only. Two `av` wheels also share one `site-packages` per Python version inside the extensions dir (see section 5).
- Use FFmpeg option spellings (`undershoot-pct`, `lag-in-frames`, `row-mt`, `tile-columns`, `tile-rows`, `frame-parallel`, `quality`, `speed`) and set the bit rate on the context. The original underscore keys are silently ignored (section 3).
- Blocker for the main agent: the `av` 18.1.0 wheel contains GPL components (libx264, libx265; FFmpeg built with `--enable-version3`). See "Blockers".
- Keep encoding on a worker thread. The encode call releases the GIL. A CPU-bound Python loop in the same process slows the encoder badly (section 4).

## 1. PyAV wheels (win_amd64)

| av | cp311 (Python 3.11, Goo) | cp313 (Python 3.13, Blender 5.x) | Notes |
|---|---|---|---|
| 19.0.1 (newest on index for 3.13) | none (pip: no matching distribution for `--python-version 3.11`; only a cp312 wheel exists) | `av-19.0.1-cp312-abi3-win_amd64.whl`, 28,149,519 bytes | 5.x only |
| 18.1.0 (newest on index for 3.11) | `av-18.1.0-cp311-abi3-win_amd64.whl`, 27,595,679 bytes | same file (pip returned the identical wheel for the 3.13 target) | works on both |

- The pip index for 3.11 tops out at 18.1.0, so 18.1.0 is the newest version that supports both. There is no per-version `cp313` wheel for 18.1.0. Only the abi3 wheel exists.
- Downloaded with `py -3.11 -m pip download av==X --only-binary=:all: --platform win_amd64 --python-version 3.1x`.
- The wheel is delvewheel-repaired. It ships `av.libs/` with FFmpeg 8 (avcodec 62.28.102, avutil 60.26.102) and libvpx 1.16.0.

## 2. libvpx present (inside each Blender's own Python)

Wheel unpacked into the scratchpad. Loaded with `blender.exe -b --factory-startup --python` plus `sys.path.insert`. Nothing was pip-installed into Blender.

| Build | Python | `av` | `'libvpx-vp9' in av.codecs_available` | `CodecContext.create('libvpx-vp9','w')` |
|---|---|---|---|---|
| Blender 5.2.0 LTS | 3.13.13 | 18.1.0 | True | ok |
| Blender 5.2.0 LTS | 3.13.13 | 19.0.1 (reference) | True | ok |
| Goo Engine 4.4.3 | 3.11.11 | 18.1.0 | True | ok |

## 3. Encode test (inside Blender's Python)

Setup: synthetic moving gradient, RGBA, stored bottom-up as GPU readback would give it (row 0 = bottom). 300 frames, 30 fps, 8 Mbit/s target. Conversion is `from_ndarray(rgba)` then `reformat(yuv420p)`, with a numpy `[::-1]` flip (and an unflipped baseline for timing). Encode uses `CodecContext`, `options` dict, `encode()` per frame, raw packet bytes. No container.

### 3a. Option names

PyAV 18 (FFmpeg 8) and the bundled FFmpeg 4.4 DLLs agree. `ctx.options` leftovers after `open()` show what libvpx did not consume.

| Key | Consumed | Note |
|---|---|---|
| `end_usage` / `end-usage` | NO | not an FFmpeg option (the FFmpeg wrapper appears to pick the rate mode from min/max rate, see 3c) |
| `undershoot_pct` | NO | use `undershoot-pct` |
| `lag_in_frames` | NO | use `lag-in-frames` |
| `row_mt` / `tile_columns` / `tile_rows` / `frame_parallel` | NO | use dashes |
| `target_bitrate` (str) | NO | not an option; set `ctx.bit_rate` |
| `quality=realtime`, `speed=15` | yes | same spelling works |
| `undershoot-pct`, `lag-in-frames`, `row-mt`, `tile-columns`, `tile-rows`, `frame-parallel` | yes | libvpx logs confirm `VP9E_SET_TILE_COLUMNS: 2`, `VP9E_SET_ROW_MT: 1`, etc. |
| `minrate` / `maxrate` = bit rate | yes | makes FFmpeg set libvpx min/max section to 100%; see 3c |

PyAV 18 requires option values as `str`. Passing an int raises `TypeError`.

Recommended context setup (tested):

```python
ctx = av.CodecContext.create('libvpx-vp9', 'w')
ctx.width, ctx.height = w, h
ctx.pix_fmt = 'yuv420p'
ctx.time_base = Fraction(1, 30)
ctx.framerate = 30
ctx.bit_rate = 8_000_000
ctx.options = {
    'undershoot-pct': '95', 'lag-in-frames': '0', 'quality': 'realtime', 'speed': '15',
    'row-mt': '1', 'tile-columns': '2', 'tile-rows': '2', 'frame-parallel': '1',
    'minrate': '8000000', 'maxrate': '8000000',
}
ctx.open()
```

No `gop_size` is needed. libvpx's default `kf_max_dist` is 128 (logged), and keyframes landed at 0, 128, 256.

### 3b. Measured (300 frames, 30 fps, 8 Mbit/s target)

Variant `dash_minmax` (recommended options above). Times are per frame, mean with p95 for encode.

| Size | Blender | conv flip total | flip copy | `from_ndarray` | `reformat` | encode mean / p95 / max | achieved |
|---|---|---|---|---|---|---|---|
| 1280x720 | 5.2 | 3.61 ms | 0.14 | 0.63 | 2.84 | 2.08 / 2.38 / 32.0 ms | 7.14 Mbit/s |
| 1280x720 | Goo 4.4.3 | 3.21 ms | 0.15 | 0.61 | 2.45 | 2.09 / 2.38 / 32.4 ms | 7.14 Mbit/s |
| 1920x1080 | 5.2 | 4.87 ms | 0.29 | 1.27 | 3.31 | 2.72 / 2.99 / 32.8 ms | 7.29 Mbit/s |
| 1920x1080 | Goo 4.4.3 | 4.73 ms | 0.29 | 1.26 | 3.18 | 2.66 / 2.99 / 33.0 ms | 7.29 Mbit/s |

- The numpy flip costs 0.1 ms (720p) and 0.3 ms (1080p). `reformat` dominates. The unflipped baseline was within noise (3.5 vs 3.6 ms at 720p on 5.2).
- `dash` (no min/max) reached 5.87 Mbit/s at 720p and 6.25 Mbit/s at 1080p. A pure gradient undershoots the target, so "achieved" is not a pass or fail.
- Max encode of ~32 ms is the first call (context init). Steady-state encode is under 3.8 ms p95 at both sizes.
- The original's keys (`literal` variant, nothing applied except quality and speed, no bit rate) gave 0.71 Mbit/s at 720p and 1.37 Mbit/s at 1080p, and the first packet came out at frame 24. The 24-frame lag is libvpx's default lag, so the underscore keys really were ignored.

### 3c. CBR

- `end_usage=cbr` cannot be set through PyAV 18 or the bundled FFmpeg 4.4 options (`end_usage` is unconsumed). FFmpeg's libvpx wrapper chooses the rate mode from `minrate`/`maxrate` vs `bit_rate`.
- Evidence: with min=max=bit_rate, libvpx logged `rc_2pass_vbr_minsection_pct: 100` and `maxsection_pct: 100`, and the output changed (5.87 to 7.14 Mbit/s at 720p). Min/max section values only matter for rate control in CBR, so this points to CBR.
- Not confirmed directly. The "Rate control turned to CBR" string is in the DLL, but it was not printed in the captured logs. Treat CBR as "very likely", not verified.

### 3d. Orientation

Decoding the first keyframe back (`vp9` decoder) against the upright source:

| Variant | mean abs diff vs upright | vs unflipped |
|---|---|---|
| `dash_minmax`, numpy flip | 0.88 (720p), 0.88 (1080p) | 42.96 / 42.95 |

So `[::-1]` before `from_ndarray` gives an upright picture. The no-flip path would be upside down.

### 3e. Header check

Every keyframe header parsed as: frame_marker `10`, profile 0, `show_frame=1`, sync `49 83 42`, width-1 / height-1 matching the frame size (1280x720 and 1920x1080). The first packet was a keyframe in all `dash*` runs. Keyframe indices 0, 128, 256 over 300 frames.

### 3f. Original's bit rate (open question)

The decompiled `VCc_enc_options` (scratchpad `decomp2/`) sets only the dict keys `target_bitrate`, `undershoot_pct`, etc. A grep of all decompiled encoder functions finds no `bit_rate` or `max_bit_rate` reference. On FFmpeg 4.4, `target_bitrate` and `bit_rate` are unconsumed. So the original streams probably ran at libvpx's default rate, not 8 Mbit/s. Not confirmed on a phone. The main agent needs to decide which bit rate to target.

## 4. GIL and threading

Encode runs in a `threading.Thread`. Results (1280x720, 300 frames):

| Test | Blender 5.2 | Goo 4.4.3 |
|---|---|---|
| Encode alone, per call p50 | 1.14 ms (wall 0.36 s) | 1.22 ms (wall 0.38 s) |
| Heartbeat (`sleep(1 ms)`) overshoot, idle | p50 0.54 ms | p50 0.54 ms |
| Heartbeat overshoot, during encode | p50 0.55 ms, max 1.3 ms | p50 0.54 ms, max 1.4 ms |
| numpy `sum` loop in main thread, idle / during encode | 11,087 / 9,231 per s | 11,944 / 9,884 per s |
| Pure Python spin in main thread, idle / during encode | 20.8 M / 17.3 M per s | 16.5 M / 16.4 M per s |
| Encode wall while pure-Python spinner runs | 8.5 s (p50 call 29 ms) | 4.0 s (p50 call 12.5 ms) |

- The heartbeat result is the one that matters. The encode call does not block other Python threads. The GIL is released during `encode()`.
- A CPU-bound Python loop in the same process makes the encode thread wait for the GIL after every call. Keep busy Python loops off the Blender main thread while encoding, or keep that work in C (numpy).

## 5. Resolution change

Close and reopen the codec context (no in-place resize):

| Step | Blender 5.2 | Goo 4.4.3 |
|---|---|---|
| Flush and drop 720p context | 30.0 ms | 26.1 ms |
| Create and open 1080p context | 9.4 ms | 9.7 ms |
| First encode after open (keyframe) | 37.1 ms | 34.7 ms |
| First packet | keyframe, 1920x1080 | keyframe, 1920x1080 |

- Total one-off stall is about 70 to 80 ms per resize. The next packet is a keyframe with the new size.
- Steady-state encode after the switch is back to about 3 ms.

## 6. Extension packaging with wheels

Throwaway extensions built in the scratchpad (`ext/`). User dirs redirected by env before any install:
`BLENDER_USER_RESOURCES`, `BLENDER_USER_CONFIG`, `BLENDER_USER_SCRIPTS`, `BLENDER_USER_EXTENSIONS`, `BLENDER_USER_DATAFILES` all set to `scratchpad/encoder_spike/bl_user/...`. Verified with `bpy.utils.user_resource(...)` and `preferences.extensions.repos` (the `user_default` repo directory is the scratch path on both builds) before installing.

| Test | Manifest wheels | Zip | Result on 5.2 | Result on Goo |
|---|---|---|---|---|
| A | cp311-abi3 18.1.0 | `av_spike_a-0.0.1.zip`, 27,596,578 bytes | `av` 18.1.0 from `.local/lib/python3.13/site-packages`, libvpx True | `av` 18.1.0 from `.local/lib/python3.11/site-packages`, libvpx True |
| A, `--split-platforms` | same | `av_spike_a-0.0.1-windows_x64.zip`, 27,596,666 bytes | not separately tested | not separately tested |
| B | cp311-abi3 18.1.0 + cp312-abi3 19.0.1 | `av_spike_b-0.0.1.zip`, 55,746,281 bytes | picked 19.0.1 (newest) | skipped the cp312 wheel, installed 18.1.0 |

- Install: `blender --factory-startup --command extension install-file -r user_default <zip>`. The wheel is kept as a `.whl` in the extension folder. On first enable, Blender extracts it into `extensions/.local/lib/pythonX.Y/site-packages`. Each interpreter gets its own copy, installed lazily on enable.
- Blender's build logic (`addons_core/bl_pkg/cli/blender_ext.py`, `python_versions_from_wheel`) treats an `abi3` wheel as "any Python 3.x". So the single abi3 wheel matches both Blenders.
- `--split-platforms` makes no difference for a Windows-only manifest. It just renames the zip to `-windows_x64`. Keep `platforms = ["windows-x64"]` so non-Windows builds skip the wheel.
- Per-Python handling: Blender selects the wheel per running interpreter, so the Goo build ignored the cp312 wheel automatically. Still, do not ship both `av` versions. `.local/lib/python3.13/site-packages` is shared by every extension in that user extensions dir, and the last install wins for a package name.

## 7. Fallback: ctypes into the bundled FFmpeg 4 DLLs

Viability: yes, as a fallback. Tested with plain Python 3.13 (not inside Blender).

- `virtucamera/third_party/av/`: `avcodec-pyav-58.dll` (FFmpeg 4.4, avcodec 58.91.100), `avutil-pyav-56.dll`, `swscale-pyav-5.dll`, `swresample-pyav-3.dll`. All load with `os.add_dll_directory`. `libvpx-vp9` encoder found.
- Single-frame encode at 320x240 succeeded: 3 packets (keyframe 23,211 bytes, then inter 37 and 35 bytes). Keyframe header is 320x240, sync `49 83 42`. libvpx v1.9.0.
- Method: context options set through the AVOption API (`pixel_format`, `video_size`, `time_base`, `b`), libvpx options through `av_dict`, RGBA to YUV through `sws_scale` with a negative source stride (flip for free), frames and packets through the C API. The only hand-coded layout is FFmpeg 4.x `AVFrame` (data at 0, linesize at 64, width 104, height 108, format 116) and `AVPacket` (data at 24, size at 32).
- Costs: about 200 lines of ctypes. The struct offsets are tied to FFmpeg 4.x and must be rechecked on any DLL change. Not run inside Blender. Note that `pix_fmt` is not an AVOption here (it is `pixel_format`).
- Licensing: the bundled DLLs have no `--enable-gpl` flags, so they are LGPL-only. The av 18.1.0 wheel does contain GPL code.

## Blockers and open items

1. License: `av` 18.1.0 wheel is built with `--enable-gpl --enable-version3`, bundling libx264 and libx265. Shipping it inside the addon brings GPL obligations, and the repo license `SPDX:WTFNMFPL` is not a standard SPDX identifier. The main agent must decide. The ctypes fallback avoids the GPL libraries but is fragile (FFmpeg 4.x ABI).
2. Bit rate: the original probably never applied its 8 Mbit/s target (section 3f). Decide whether to match the app's requested Mbit/s or the original behavior.
3. CBR: inferred from min/max behavior, not confirmed by a libvpx log line (section 3c).
4. Synthetic gradients undershoot the target. Real viewport content needs a separate bitrate check.
5. ctypes fallback is not tested inside Blender's Python.

## Guardrails to record

- Keep `ctx.encode()` on one worker thread. Do not run busy Python loops on the main thread while encoding (section 4).
- Set the bit rate through `ctx.bit_rate`, not `options`. Use dashed libvpx option names only.
- Do not add two `av` wheels to one extension. The extension `.local/lib` site-packages is shared.
- Resolution changes: close and reopen the context (about 70 to 80 ms). Do not reuse the context for a new size.
- The SharedMemory and capture-mode guardrails in AGENTS.md are unaffected by this spike. This is about the encoder only.

## Test notes

- One early `blender --command extension --help` run was made without the env redirect. It loaded the real portable add-ons (tracebacks only). Checked afterwards: no writes under `C:\Apps\blender\portable` after that time. All later runs used redirected dirs and `--factory-startup`.
- The `virtucamera/third_party/av` DLLs were only loaded, never modified. Nothing was committed.
- Raw outputs: scratchpad `encoder_spike/bench_5_2.json`, `bench_goo44.json`, `gil_5_2.json`, `gil_goo44.json`, `*.out`.
