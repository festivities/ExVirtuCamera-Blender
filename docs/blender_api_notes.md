# Blender API notes for the ExVirtuCamera rework (4.4 to 5.2)

Research notes. No addon code was changed. Bench scripts live in the session scratchpad, not in the repo.

## Labels

- **VERIFIED-5.2**, **VERIFIED-5.1**, **VERIFIED-4.5**: run on that build.
- **VERIFIED-4.4**: run on Goo Engine 4.4.3 (`E:\Program Files\goo-engine-4.4.3\blender.exe`). Stock 4.4 is not installed. Goo is a 4.4.3 fork and was used as the 4.4 build, per the coordinator.
- **DOCS**: from the manuals or API docs only.
- **UNVERIFIED**: not tested, or no build available.

## Test setup

| Build | Path | Python | numpy |
|---|---|---|---|
| 5.2.0 LTS | `C:\Apps\blender\blender.exe` | 3.13.13 | 2.3.4 |
| 5.1.1 | `C:\Apps\blender-5.1\blender.exe` | 3.13.9 | 2.3.4 |
| 4.5.12 LTS | `C:\Apps\blender-4.5.12\blender.exe` | 3.11.11 | 1.26.4 |
| 4.4.3 Goo Engine | `E:\Program Files\goo-engine-4.4.3\blender.exe` | 3.11.11 | 1.26.4 |
| 5.0 | not installed | UNVERIFIED | UNVERIFIED |

- Host: Windows 11, RTX 4070 SUPER (OpenGL; Vulkan tested on 5.2 only), 1920x1080 primary display.
- GPU work needs a GUI instance. Headless `-b` raises `SystemError` on gpu drawing and on `gpu.types.Buffer` creation (5.2 and 4.5/Goo). GUI benches ran as a timer-driven generator that quit itself.
- Timings are from several runs on one machine. Ranges are shown where runs varied. Treat them as orders of magnitude.

## 1. Bundled Python and numpy

| Blender | Python | numpy | `bgl` | Label |
|---|---|---|---|---|
| 4.4 | 3.11.11 | 1.26.4 | importable | VERIFIED-4.4 (Goo 4.4.3) |
| 4.5 | 3.11.11 | 1.26.4 | importable | VERIFIED-4.5 |
| 5.0 | not determined | not determined | not determined | UNVERIFIED |
| 5.1 | 3.13.9 | 2.3.4 | not importable | VERIFIED-5.1 |
| 5.2 | 3.13.13 | 2.3.4 | not importable | VERIFIED-5.2 |

- Stock 4.4 may differ from Goo 4.4.3. Not checked.
- Check the interpreter with `sys.version` and `numpy.__version__` at runtime, not by version number.

## 2. Viewport pixel capture

### Availability

- (a) `SpaceView3D.draw_handler_add(..., 'WINDOW', 'POST_PIXEL')` with `gpu.state.active_framebuffer_get().read_color(...)`: VERIFIED on 4.4, 4.5, 5.2 (GUI). 5.1 not GUI-tested.
- (b) `gpu.types.GPUOffScreen` + `draw_view3d(scene, view_layer, view3d, region, view_matrix, projection_matrix, *, do_color_management=False, draw_background=True)`: VERIFIED on 4.4, 4.5, 5.2 (GUI). The docs signature matches. Camera matrices used: `cam.matrix_world.inverted()` and `cam.calc_matrix_camera(depsgraph, x=W, y=H)`.
- (c) `GPUTexture.read()` on `off.texture_color`: VERIFIED on 4.4, 4.5, 5.2. Returns a `Buffer`, no `data=` argument.
- OpenGL vs Vulkan: (a), (b), (c) all ran on 5.2 Vulkan (`gpu.platform.backend_type_get()` returned `VULKAN`). Numbers are similar to OpenGL. Vulkan not tested on 4.4/4.5.

### Output format

| | (a) POST_PIXEL | (b) GPUOffScreen | (c) GPUTexture.read |
|---|---|---|---|
| Pixel format | UBYTE, display-transformed sRGB (VERIFIED: 0.5 background reads 188 on 5.2/4.5, 187 on Goo; matches (b) with `do_color_management=True`) | UBYTE linear by default (0.5 reads 128; FLOAT reads 0.502). With `do_color_management=True` it reads 187 (display transform) | Texture data, same format as the offscreen draw (inferred, not separately tested) |
| Orientation | Bottom-up: row 0 is the bottom (VERIFIED, object at z=+3 lands at mean row 630 to 698 of H) | Bottom-up (VERIFIED, mean row 528 to 549 of 720) | Same as (b) (inferred) |
| Overlays | Included. Toggling overlays changes about 210k to 264k pixels in the full region | Honored. Toggling changes about 45k to 58k pixels of 921,600 | Same as (b) |
| Gizmos | Navigation gizmo included: toggling changes about 4k pixels inside its bbox (top-right) | Not drawn (not explicitly tested) | - |
| Viewport obscured | Minimized window: 0 POST_PIXEL frames out of 20 `tag_redraw()` (VERIFIED on 5.2, 4.5, Goo). Covered by another window: UNVERIFIED | Works while minimized: offscreen read returned a value (VERIFIED). Independent of window visibility by construction | Same as (b) |
| Not in camera view | Reads what the viewport shows (VERIFIED: PERSP view captured) | Always renders the scene camera you pass in | Same as (b) |

### Readback cost (ms, avg; p95 in brackets where measured)

Draw excludes readback. The draw is async, so the read includes GPU sync.

| Path | Build | 1280x720 | 1920x1080 (or full region) |
|---|---|---|---|
| (a) POST_PIXEL read_color | 5.2 OpenGL | 1.2 to 2.0 [2.3 to 5.4] | 1.1 to 4.5 (1920x959 region) |
| (a) | 5.2 Vulkan | 1.3 | 1.8 (1920x959) |
| (a) | 4.5 | 1.8 | 2.8 (1585x882) |
| (a) | 4.4 Goo | 0.75 to 2.3 | 2.6 to 2.9 (1586x883) |
| (b) draw_view3d + clear | 5.2 | 0.9 to 1.1 | 1.2 to 1.3 |
| (b) draw_view3d + clear | 4.5 | 0.44 | 0.51 |
| (b) draw_view3d + clear | 4.4 Goo | 0.6 | 0.6 |
| (b) fb.read_color UBYTE | 5.2 | 0.76 to 1.03 | 1.5 to 1.6 |
| (b) fb.read_color UBYTE | 4.5 | 1.03 | 2.0 |
| (b) fb.read_color UBYTE | 4.4 Goo | 1.7 to 2.2 | 3.3 |
| (c) GPUTexture.read | 5.2 | 0.47 to 0.57 | 0.95 to 1.24 |
| (c) GPUTexture.read | 4.5 | 0.54 | 1.03 |
| (c) GPUTexture.read | 4.4 Goo | 0.52 to 0.70 | 1.10 |

Use (b) for a camera-framed stream. It does not depend on the viewport being on screen or in camera view. Use (a) only if the stream must match what the user sees.

### Minimal code

```python
off = gpu.types.GPUOffScreen(W, H)
view = cam.matrix_world.inverted()
proj = cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(), x=W, y=H)
with off.bind():
    fb = gpu.state.active_framebuffer_get()
    fb.clear(color=(0.0, 0.0, 0.0, 1.0))
    off.draw_view3d(scene, view_layer, space, region, view, proj, do_color_management=True)
    buf = fb.read_color(0, 0, W, H, 4, 0, 'UBYTE')   # `space` is area.spaces.active, `region` the WINDOW region
```

Headless (`-b`) cannot run any of this. Test in a GUI instance.

## 3. Readback to raw bytes

### Buffer protocol

- 5.2 (VERIFIED): `memoryview(buf)` works (format `B` for UBYTE, `f` for FLOAT, writable). `np.frombuffer(buf, ...)` works for flat and multi-dim buffers. Multi-dim `read_color` results have C-order strides `(5120, 4, 1)`.
- 4.5 and 4.4 Goo (VERIFIED): flat 1D buffers work. Multi-dim `(H, W, 4)` buffers report Fortran-style strides `(1, 720, 921600)` and are **not C-contiguous**:
  - `np.frombuffer` raises `BufferError` (UBYTE and FLOAT).
  - `memoryview(...).cast('B')` raises.
  - `memoryview(...).tobytes()` works but costs 12 to 18 ms at 720p.
- So: **use flat 1D buffers** (`(W*H*4,)`) on all versions.

### read_color into a preallocated buffer

- `read_color(..., data=buf)` returns the same object on 5.2, 4.5, 4.4 (VERIFIED).
- A flat 1D `data=` buffer matches the multi-dim `to_list()` order (row-major, bottom-up) on all three (VERIFIED).
- `GPUTexture.read()` has no `data=` argument.

### Copy cost (1280x720 RGBA, 3,686,400 bytes)

| Destination | 5.2 | 4.5 | 4.4 Goo |
|---|---|---|---|
| `bytearray(src)` new | 0.14 ms | 0.14 ms | 0.43 ms |
| `bytearray` preallocated slice | 0.27 ms | 0.51 ms | 0.52 ms |
| numpy preallocated slice | 0.15 ms | 0.15 ms | 0.14 ms |
| numpy `frombuffer` view (no copy) | 0.001 ms | 0.001 ms | 0.0015 ms |
| `SharedMemory.buf[:n] = src` | 0.73 ms | 0.74 ms | 1.15 ms |

Numbers are from one run per build.

### Recommended path

```python
buf = gpu.types.Buffer('UBYTE', (W * H * 4,))          # allocate once per size
fb.read_color(0, 0, W, H, 4, 0, 'UBYTE', data=buf)      # inside off.bind()
src = np.frombuffer(buf, dtype=np.uint8)                # zero-copy, rows bottom-up
dst = np.ndarray((W * H * 4,), np.uint8, buffer=shm.buf)  # view over SharedMemory (not measured)
dst[:] = src                                           # one copy
```

- Rows are bottom-up. The current pipeline's `mss` path may be top-down. Check the bridge and encoder assumptions before swapping (see guardrails below).
- Avoid `to_list()`. It is slow and was only used as a fallback in the bench.

## 4. Threading and timers

### Rules (DOCS)

- `info_gotchas_threading.rst`: Python threads are not supported with bpy. Blender's own docs say no code, including the main thread, may use bpy while threads run. Threads should be joined before bpy is used again.
- `bpy.app.handlers` docs: handlers that alter data read by the viewport can crash Blender if run from another thread.
- Practical rule: a network thread touches only pure Python (sockets, `queue.SimpleQueue`). Every bpy call happens inside a main-thread timer or handler. The measured pattern below follows this rule. It does not call bpy from the thread. The docs' stronger "no bpy while threads run" wording still applies to the main thread, and only the queue poll runs there.

### bpy.app.timers at interval 0 (VERIFIED on 5.2, 4.5, Goo)

| Interval | Calls/s (idle GUI) | CPU (process_time) |
|---|---|---|
| 0.05 (baseline) | ~19 | 0.8 to 2.3% |
| 0.0 | 180 to 240 | 0 to 10% (noisy) |
| 0.001 | 170 to 185 | 0 to 5.5% |
| 0.005 | 170 to 185 | 0 to 9.4% |

- Blender's idle loop wakes about every 5 ms, so interval 0 does not run faster than about 200/s when idle (inferred from the call rate).
- CPU differences are within noise. No clear cost of interval 0 over a 50 ms timer.

### Handoff latency (producer thread every 5 ms, timer drains a `SimpleQueue`)

- Interval 0.0: mean 2.2 to 2.9 ms, p95 4.8 to 5.5 ms, max 5.6 to 6.1 ms.
- Interval 0.001 and 0.005: same range. Interval 0 gives no clear gain.
- Latency under active input (mouse events) was not measured. Expect lower.

```python
import queue, bpy
q = queue.SimpleQueue()          # thread: q.put(frame_bytes); no bpy calls in the thread

def _drain():
    try:
        while True:
            item = q.get_nowait()
            ...                  # bpy calls go here, main thread only
    except queue.Empty:
        pass
    return 0.0                    # re-run next loop; use 0.001 if CPU matters

bpy.app.timers.register(_drain, first_interval=0.0)
```

## 5. Animation: legacy and slotted actions

### Presence of `Action.fcurves` (VERIFIED)

- 4.4 (Goo) and 4.5: `Action.fcurves` exists. `is_action_legacy` exists.
- 5.1 and 5.2: `Action.fcurves` is **absent** from the RNA. `slots`, `layers` present.
- 5.0: not installed. Removal happened by 5.1. Whether 5.0 removed it is UNVERIFIED.

### Helpers by version (VERIFIED by listing RNA and `bpy_extras.anim_utils`)

| Name | 4.4 Goo | 4.5 | 5.1 | 5.2 |
|---|---|---|---|---|
| `bpy_extras.anim_utils.action_get_channelbag_for_slot(action, slot)` | yes | yes | yes | yes |
| `anim_utils.action_ensure_channelbag_for_slot` | no | no | yes | yes |
| `anim_utils.animdata_get_channelbag_for_assigned_slot` | no | no | yes | yes |
| `Action.fcurve_ensure_for_datablock(datablock, data_path, *, index=0, group_name="")` | yes | yes | yes | yes |
| `ActionChannelbagFCurves.new / find / remove / clear` | yes | yes | yes | yes |
| `ActionChannelbagFCurves.ensure / new_from_fcurve` | no | no | yes | yes |
| `ActionKeyframeStrip.channelbag(slot, ensure=...)`, `key_insert` | yes | yes | - | yes |

`Action.fcurve_ensure_for_datablock` is the only helper that works everywhere. The docs say it creates the layer, strip and slot, and assigns the slot. The action must already be assigned to the datablock. Verified behavior: 6 fcurves in 0.02 to 0.05 ms.

### Bulk keyframe insert (VERIFIED on 5.2, 4.5, 4.4 Goo)

Setup (once per action): `obj.animation_data_create(); obj.animation_data.action = act`.

```python
import numpy as np

PATHS = [("location", 0), ("location", 1), ("location", 2),
         ("rotation_euler", 0), ("rotation_euler", 1), ("rotation_euler", 2)]

def fcurves_for(act, obj):
    return [act.fcurve_ensure_for_datablock(obj, p, index=i) for p, i in PATHS]

def bulk_keys(fc, frames, values):      # float32 arrays, length N
    n = len(frames)
    co = np.empty(2 * n, dtype=np.float32)
    co[0::2] = frames
    co[1::2] = values
    kps = fc.keyframe_points
    kps.add(n)
    kps.foreach_set("co", co)
    fc.update()
```

- 6 channels x 1000 keys: 0.42 ms (4.5), 0.71 ms (4.4 Goo), 0.72 ms (5.2). Round-trip verified with `foreach_get`.
- Per-key `keyframe_points.insert(..., options={'FAST'})`: 1000 keys on one channel took 1.46 to 1.58 ms. Slower per key.

### Euler filter without Graph Editor (VERIFIED on all three builds)

- `mathutils.Euler.make_compatible(other)` modifies in place and returns `None`. Example: `(0,0,-3.1)` made compatible with `(0,0,3.1)` gives z = 3.1832.

```python
prev = None
for e in rot_samples:                    # mathutils.Euler
    if prev is not None:
        e.make_compatible(prev)          # in place
    prev = e.copy()
```

- The current addon uses `bpy.ops.graph.euler_filter` with `selected_visible_fcurves`. The override key is accepted on 4.4, 4.5 and 5.2 (VERIFIED). Whether the operator honors it on slotted fcurves is UNVERIFIED. It needs a GRAPH_EDITOR area. Prefer `make_compatible` in Python.

## 6. Context needs from a timer callback

Tested from `bpy.app.timers` with no override (window and screen present, `bpy.context.area` is None). VERIFIED on 5.2, 4.5, 4.4 Goo. 5.1 not GUI-tested.

| Call | No override | Notes |
|---|---|---|
| `bpy.ops.object.camera_add()` | OK | Non-operator: `bpy.data.cameras.new` + `bpy.data.objects.new` + `scene.collection.objects.link` (OK) |
| `bpy.ops.screen.animation_play(reverse=False, sync=True)` | OK, 14 frames in 0.6 s | `animation_cancel(restore_frame=False)` also OK |
| same with `temp_override(window=, screen=, area=, region=)` | OK | Use if a poll fails in another state |
| `space.region_3d.view_perspective = 'CAMERA'` | OK | Direct property, no operator needed |
| `scene.frame_set(n)` | OK | Non-operator frame change |

- `screen.is_animation_playing` exists on 4.4, 4.5, 5.1, 5.2 (VERIFIED).

## 7. Extension packaging

### Manifest keys (DOCS: 5.2 `advanced/extensions/getting_started.rst`)

- Required: `schema_version`, `id`, `version`, `name`, `tagline`, `maintainer`, `type`, `blender_version_min`, `license`.
- Optional: `website`, `tags`, `blender_version_max` (versions from that one up are unsupported), `platforms`, `wheels`, `copyright`, `[permissions]`, `[build]`.
- `platforms` values: `windows-x64`, `windows-arm64`, `macos-x64`, `macos-arm64`, `linux-x64`. Omitted means all.
- `[permissions]` keys: `files`, `network`, `clipboard`, `camera`, `microphone`. Each takes a short reason string with no trailing period.
- `license` takes `"SPDX:<id>"`. The current `"SPDX:WTFNMFPL"` is not checked against the SPDX list (UNVERIFIED).
- The 4.4 manual uses the same keys (DOCS).

### Current `blender_manifest.toml`

- `blender_version_min = "4.4.0"`, `network` permission present. No `platforms`, no `wheels`, no `blender_version_max`.
- The addon uses `ctypes.windll`, so it is Windows-only at runtime. Add `platforms = ["windows-x64"]` unless the rework is cross-platform.

### Wheels (DOCS: 5.2 `advanced/extensions/python_wheels.rst`)

- Wheels are bundled unmodified from PyPI, with their dependencies, under `./wheels/`. Filenames follow the binary wheel spec.
- Install extracts them into the extension's own `site-packages`.
- `blender --command extension build --split-platforms` makes one zip per platform.
- The examples use one CPython per list (cp311 for 4.4, cp313 for 5.2). The docs do not say whether one manifest may list cp311 and cp313 wheels together. UNVERIFIED. Test with an installed extension before relying on it. Safer: one extension build per Blender line.

### PyAV

- Vendored copy: `virtucamera/third_party/av` is a Windows **cp39** build (`*.cp39-win_amd64.pyd` plus FFmpeg DLLs). It is for the Python 3.9 bridge only.
- PyPI JSON (WebFetch summary, partial file list): latest `av` 19.0.1 requires Python >=3.12. Likely no cp311 wheel for the latest version. Exact wheel names UNVERIFIED (response truncated).
- Pin separate `av` versions per Blender line if the Blender side ever needs it.

## 8. Current `virtucamera_blender.py` against 4.4 to 5.2

Checked by grep and reading. The addon was not loaded in Blender.

- `bgl`: not used. No 5.x break.
- `action.fcurves` (lines 19 to 45): reached only through `hasattr(action, "fcurves")` and `is_action_legacy` guards. Safe on 5.1/5.2 (`fcurves` absent). The layered branch walks all layers, strips and channelbags with no slot filter.
- `temp_override(selected_visible_fcurves=...)` (around line 690): key accepted on 4.4, 4.5, 5.2. Operator effect UNVERIFIED.
- `ctypes.windll` (lines 406 to 423): Windows-only. Set `platforms` if kept.
- `mss` is vendored (`virtucamera/third_party/mss`, 6.1.0), imported lazily (line 300).
- `context.preferences.addons[__package__]` (line 613): extension package key. Not verified in a loaded addon.
- `bpy.context.temp_override`: 4.1+, fine on 4.4 to 5.2.
- `screen.animation_play` and `animation_cancel` from timers: work without override (VERIFIED).
- No deprecated API found in the used set: `is_animation_playing`, `online_access`, `use_lock_interface` exist on 4.4, 4.5, 5.1, 5.2.
- The mss capture path needs a visible viewport. Replace it with (b) for the rework.

## 9. Goo Engine 4.4.3 differences from stock builds

- Maximize (`bpy.ops.screen.screen_full_area()`) crashed Goo with `EXCEPTION_ACCESS_VIOLATION` in `ui_handle_menus_recursive`. Reproduced once. Worked on 5.2. Not tried on stock 4.5. The bench skips the maximize on Goo.
- Multi-dim `read_color` buffers are non-C-contiguous on Goo. Stock 4.5 is the same. Stock 4.4 not tested. This is a 4.x vs 5.x difference, not Goo-specific.
- Background readback: 0.5 reads 127 (linear) and 185 (managed), FLOAT 0.498. Stock 4.5 and 5.2 read 128, 187 and 0.502. Cause not identified (could be 4.4 vs 4.5 rather than the fork).
- `read_color` UBYTE at 720p was slower on Goo (1.7 to 2.2 ms) than stock 4.5 (1.0 ms) and 5.2 (0.8 to 1.0 ms).
- Same as 4.5: `bgl` importable, `Action.fcurves` present, `action_ensure_channelbag_for_slot` absent, `fcurve_ensure_for_datablock` present.

## 10. Guardrails and design notes for the main agent

- Capture path: (b) `GPUOffScreen` + `draw_view3d` is the candidate. It works while minimized and needs no `mss`. Set `do_color_management=True` to match the viewport. Read into a flat `UBYTE` buffer.
- Row order: readback is bottom-up. The AGENTS.md rule says no vertical flip in the pixel format, so confirm what the bridge and `vc_core` expect before shipping. Any change to row order must update both sides together.
- Buffer size: one flat buffer per resolution (aligned to 16). Resolution changes must not reallocate SharedMemory. Buffer size reallocation is outside SHM and not measured.
- Versioning: use `Action.fcurve_ensure_for_datablock` for inserts (works 4.4 to 5.2). Use the `_iter_action_fcurves` helper for reads.
- Python: 3.11 on 4.4/4.5, 3.13 on 5.1/5.2. Wheels per version.
- Timers: keep interval 0 for the queue drain. Latency about 2 to 3 ms mean, about 5 ms p95 when idle.

## Could not verify

- 5.0: Python version, numpy version, and whether `Action.fcurves` was removed there (5.0 not installed).
- Stock 4.4: not installed. All 4.4 results are Goo Engine 4.4.3.
- POST_PIXEL when covered by another window (only minimized was tested).
- Gizmos in (b). Not tested.
- Whether `graph.euler_filter` honors `selected_visible_fcurves` on slotted actions.
- One extension with both cp311 and cp313 wheels (docs silent, not tested).
- PyAV wheel names per CPython tag (PyPI response truncated).
- SharedMemory write via a numpy view over `shm.buf` (not measured separately; expected near the 0.15 ms numpy copy).
- Vulkan on 4.5 and 4.4.
- Latency under input events.
- The `mss` path's row order.
