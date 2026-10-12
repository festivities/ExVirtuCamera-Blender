# ExVirtuCamera-Blender - Agent Manual

Living context for new sessions and agents. Assume no prior context: everything a session needs to continue is here or in `docs/`. Update this file whenever behavior, constraints, or workflows change. If a prompt needs no update here, say so.

## Project overview
A Blender extension (4.4 to 5.2, Windows x64) that makes the **VirtuCamera 1** iOS app work with modern Blender. The app is unmodified and abandoned upstream. The goal is VirtuCamera 1; the owner does not want VirtuCamera 2 (paid).

History:
- **Original** (not in git, kept in `data/virtucamera_blender_original/`): VirtuCamera's own addon for Blender ≤3.3. The server was a closed Cython module, `vc_core.cp39-win_amd64.pyd`, locked to Python 3.9.
- **v1 of this repo** (commits up to `c85c94d` on `main`): wrapped the `.pyd` in a Python 3.9 subprocess bridged over JSON/TCP, captured the viewport with `mss` and passed frames through SharedMemory. Retired; do not reintroduce any of it.
- **v2 (current, branch `v2`)**: a pure-Python reimplementation of the VirtuCamera 1 server running inside Blender, written from a protocol spec obtained by reverse engineering the `.pyd`. No Python 3.9, no subprocess, no `.pyd`, no `mss`, no SharedMemory.

## Status (2026-10-13)
- v2 is complete and works. The owner verified it with the phone on Goo Engine 4.4.3: stream, camera control, recording, scripts and reconnects.
- Automated: loopback tests 18/18 on Python 3.13 (Blender 5.2) and 3.11 (Blender 4.5); GUI end-to-end 42/42 on Blender 5.2, 5.1, 4.5.12 and Goo 4.4.3. A fresh Goo profile once took ~2 s for the first offscreen draw (shader compile); later frames ran at full rate.
- Known cosmetic point: the stream uses the viewport's overlay settings, so the 3D cursor shows in the stream when overlays are on (as in the original, which streamed the viewport). Left as is.
- Possible future work, none requested: Linux/macOS builds (needs per-platform av wheels and testing), an option to hide overlays in the stream.

## Repository layout
| Path | Purpose |
|---|---|
| `__init__.py` | Re-exports `register`/`unregister`. |
| `vc_server.py` | Protocol server + VP9 encoder. **No `bpy`.** Plain Python 3.11+, needs numpy, and PyAV for video. |
| `virtucamera_blender.py` | Everything `bpy`: host callbacks, GPU capture, QR, UI panel, operators, preferences, registration. |
| `blender_manifest.toml` | Extension manifest: id `exvirtucamera_blender`, version 2.0.0, `blender_version_min = "4.4.0"`, `platforms = ["windows-x64"]`, wheels `av-18.1.0-cp311-abi3-win_amd64.whl` and `qrcode-8.2-py3-none-any.whl`, network permission. `[build] paths_exclude_pattern` keeps docs, tests, data, wheels sources, `.md`, `.bat` out of the zip. |
| `build.bat` | Downloads the wheels into `wheels\` (gitignored) and builds `exvirtucamera_blender-<version>.zip` with Blender 5.2 `--factory-startup --command extension build`. |
| `tests/test_vc_server.py` | Loopback protocol + video tests, no Blender. |
| `tests/vc1_conformance.py`, `tests/regen_conformance_fixture.py`, `tests/fixtures/vc1_original_transcript.json` | Conformance: a scripted session replayed against the original `.pyd` (Python 3.9) was recorded into the fixture; the test replays it against `vc_server`. |
| `tests/run_e2e.ps1`, `tests/e2e_blender.py`, `tests/e2e_client.py`, `tests/fixtures/scripts/` | GUI end-to-end: installs the built zip into a throwaway user dir, runs a GUI Blender, drives it with a fake VirtuCamera 1 client over loopback. |
| `docs/vc_core_protocol.md` | **Source of truth for the wire protocol.** |
| `docs/blender_api_notes.md` | Measured Blender 4.4 to 5.2 API facts (GPU readback, buffers, timers, slotted actions, packaging). |
| `docs/encoder_spike.md` | PyAV/libvpx-vp9 findings (wheel choice, option names, timings). |
| `.claude/agents/` | Subagent definitions (see Workflow). |
| `data/` (gitignored, local only) | `virtucamera_blender_original/` (VC1 addon + `.pyd`), `virtucamera2_blender_original/` (VC2 addon, reference only), `re/` (IDA database `vc_core.cp39-win_amd64.pyd.i64`, loopback RE harness). Not present on a fresh clone. |

Licence note: PyVirtuCamera's licence forbids reverse engineering. The owner chose to proceed for interoperability. Never copy code or text from the originals into the repo; describe behavior only.

## Protocol in brief (details in docs/vc_core_protocol.md)
- One TCP listener on `0.0.0.0:<port>` (default 23354), one client at a time. Pairing is QR-only: the QR string is `"<port>_<ip1>_<ip2>..."` (IPv4, no loopback/link-local).
- One-byte request tokens: ASK 0 to 8, SET 50 to 60, DO 150 to 157; errors 200 to 204 (C8 missing camera, C9 not streaming, CB script failed, CC screenshot mode). Little-endian. SET/DO echo the token before reading the payload; ASK replies start with the token; variable-size replies are `token u32len payload`. Unknown bytes are skipped silently. Token 154 is a no-op with no echo. 157 replies `9D idx` (ok) or `CB idx` (failed) with no echo.
- Camera selection is per connection: token 52 indexes the list from the last token 2. Disconnect fires `current_camera_changed('')`.
- Keyframe batches: 59 = `<H n` + n × (16 matrix doubles + frame double); 60 = `<H n` + n × (focal, frame). `n = 0` is a no-op (the original crashed).
- START (150) payload `<ffH??`: fps, Mbit/s, u16 (no effect), opaque (no effect), autosend. Video packets are `04 <u32 LE len> <raw VP9 frame>` (libvpx-vp9, yuv420p, realtime, CBR), pushed at the START fps. Keyframe at encoder start and on each size change.
- `update_script_labels` pushes an unsolicited `07 <u32 len> "%"-joined labels`.
- Server version reported: 2.1.2 (the original's). Plugin version: 2.0.0.

## Architecture (v2)
Threads:
1. **Accept/session thread** (`vc_server`): reads tokens, replies, never touches `bpy`.
2. **Encoder thread** (`vc_server`): while streaming, at the START fps, takes the latest submitted frame, flips it (bottom-up to top-down), converts RGBA to yuv420p, encodes VP9 and sends `04` packets. Size change reopens the codec (keyframe at the new size).
3. **Blender main thread**: a `bpy.app.timers` timer at interval 0 calls `server.execute_pending_events()`, which runs queued host callbacks in order. While streaming, a second timer at the stream fps renders the camera offscreen and calls `server.submit_frame(...)`; it subtracts the render time from the next interval.

`vc_server.VCServer` contract:
```
VCServer(host, plugin_version=(2,0,0), platform="Blender")
  start_serving(port) -> bool        stop_serving()
  execute_pending_events()           # main thread only; runs queued host calls in order
  submit_frame(rgba, width, height)  # main thread; rgba = uint8 buffer, W*H*4, rows bottom-up; copied
  update_script_labels()             # main thread; pushes token 7 to a connected app
  qr_string() -> str
  properties: is_serving, is_connected, is_capturing, client_ip, current_camera, capture_fps
```
`host` implements the VC1 callback names without the `vcserver` argument: `get_playback_state, get_playback_fps, set_frame, set_playback_range, start_playback, stop_playback, get_scene_cameras, get_camera_exists, get_camera_has_keys, get_camera_focal_length, get_camera_transform, set_camera_focal_length, set_camera_transform, set_camera_flen_keys, set_camera_transform_keys, remove_camera_keys, create_new_camera, capture_will_start, capture_did_end, look_through_camera, client_connected, client_disconnected, current_camera_changed, server_did_stop, get_script_labels, execute_script`. Getters block the session thread until the main thread answers (a host exception returns a fallback value). Setters are queued and the session continues.

Capture: `GPUOffScreen` + `draw_view3d(..., do_color_management=True)` from the selected camera (falls back to the scene camera), using the first VIEW_3D area (one in camera view preferred) for shading/overlays. Fixed stream size: render aspect, long edge from the addon preference `stream_long_edge` (default 720, 256 to 1920), even dimensions (16:9 gives 720×404). Readback with `fb.read_color(..., 'UBYTE', data=flat_buffer)` into one flat `gpu.types.Buffer`, then `np.frombuffer`. Works with the window covered or minimized.

Scene properties: `vc_port`, `vc_scripts_dir`. Scripts: `.py` files in the folder; a numeric `N_` prefix sorts numerically and is stripped from the label. They run with globals `bpy`, `mathutils`, `vc_selcam`.

## Behavior decisions (keep unless the owner asks)
- Cameras listed and accepted only if `type == 'CAMERA'` and visible.
- Playback fps is `fps / fps_base`.
- Transforms: `view_layer.update()` before reading `matrix_local`. Wire matrix = `(V @ M).transposed()` flattened, with `V` mapping Blender Z-up to VirtuCamera Y-up (`y' = z, z' = -y`). The camera's own axes are not converted.
- Recorded transform keys go on `location` and `rotation_euler` (Euler continuity via `to_euler(order, previous)`); focal keys on the camera data's `lens`.
- `create_new_camera` places the camera at the 3D cursor; the server then copies the current camera's transform and focal length to it and makes it current.
- START fps outside 1 to 240 falls back to 30; Mbit/s outside 0.01 to 1000 falls back to 10 (also catches NaN/inf).
- No pull mode: ASK 4 while not streaming replies C9; while streaming the pushed packets are the reply.

## Guardrails
- `vc_server.py` must never import `bpy`. `bpy` is used only on the main thread.
- Use flat 1-D `gpu.types.Buffer`s for readback (multi-dim buffers are not C-contiguous on 4.x). Readback rows are bottom-up.
- F-Curves: never touch `action.fcurves` directly (absent on 5.1+). Read through the slot's channelbag (`bpy_extras.anim_utils.action_get_channelbag_for_slot`) with the legacy fallback guarded by `is_action_legacy` **and** `hasattr(action, "fcurves")` (an empty action on 5.2 reports `is_action_legacy` True without `fcurves`). Create with `Action.fcurve_ensure_for_datablock`. No `graph.euler_filter`.
- libvpx options: dashed FFmpeg names only (`lag-in-frames`, `row-mt`, ...), string values; bitrate via `ctx.bit_rate`; CBR via `minrate = maxrate = bit_rate`. Underscore names are silently ignored (the original made that mistake).
- Encode on the encoder thread only (PyAV releases the GIL while encoding). Keep pure-Python busy loops off the main thread while streaming.
- One av wheel per build: extension site-packages is shared per Python. The abi3 wheel covers Python 3.11 (4.4/4.5) and 3.13 (5.1/5.2). It is a GPL-enabled FFmpeg build (noted in README).
- Even stream dimensions (yuv420p). Multiples of 16 are not required.
- QR: `ImagePreview.image_pixels_float` rows are bottom-up; flip the `qrcode` matrix.
- Never trust wire values; validate at the protocol boundary.
- Never modify Blender's installed extension folders (`C:\Apps\blender\portable\extensions\...`, Goo's) or the user's running Blender. Test installs go to a scratch user dir via `BLENDER_USER_RESOURCES`/`CONFIG`/`SCRIPTS`/`EXTENSIONS`/`DATAFILES` with `--factory-startup`; verify the redirect before installing. `build.bat` uses `--factory-startup` so the user's add-ons never load during a build.
- Network experiments: loopback only.
- Run Python with `PYTHONDONTWRITEBYTECODE=1`; never commit `__pycache__`.
- Use `py` (`py -3.11`, `py -3.13`, `py -3.9` for the original) for Python. System Pythons lack numpy/av; use Blender's bundled Python for tests. In PowerShell, `py` may resolve to an MCP venv, and `build.bat` must be run as `& .\build.bat`.
- Keep host names, IPs and user paths out of committed files.
- Known noise: disabling the extension after serving prints `PermissionError`/"Failed to remove" for `av.libs` DLLs (Windows DLL lock). Harmless.

## Build and test
1. Build: `& .\build.bat` (PowerShell) → `exvirtucamera_blender-2.0.0.zip` (about 27.6 MB: the two wheels plus four files).
2. Loopback tests: extract `wheels\av-18.1.0-cp311-abi3-win_amd64.whl` somewhere (`py -3.13 -m zipfile -e <whl> <dir>`), set `VC_AV_PATH=<dir>` and `PYTHONDONTWRITEBYTECODE=1`, then run `C:\Apps\blender\5.2\python\bin\python.exe -s tests\test_vc_server.py` and the same with `C:\Apps\blender-4.5.12\4.5\python\bin\python.exe`. Expect 18 tests OK.
3. GUI end-to-end: `pwsh tests\run_e2e.ps1 -Exe <blender.exe>` for each test build (use a different `-Port` per concurrent run). Expect `E2E <tag>: 42/42 PASS`. Options: `-Zip` (default: newest zip in the repo root), `-Out` (default `%TEMP%\exvirtucamera_e2e\<exe folder>`; **cleared at the start of each run**, so keep it a scratch folder), `-Python` (client interpreter, default Blender 5.2's bundled Python), `-AvPath`. Blender user dirs go under `-Out\user` and are verified before installing; a 120 s watchdog kills only the processes it started. The test server binds 127.0.0.1, so LAN binding is only covered by the phone check. The decoded frame is saved as `-Out\decoded_frame.png`; look at it.
4. Conformance fixture (only when the protocol understanding changes): `py -3.9 tests\regen_conformance_fixture.py` (needs `data/` and the original's dependencies).
5. Phone check (owner operates the phone): see Manual verification.

## Test builds
| Blender | Path | Python |
|---|---|---|
| 5.2 LTS | `C:\Apps\blender\blender.exe` | 3.13 |
| 5.1 | `C:\Apps\blender-5.1\blender.exe` | 3.13 |
| 4.5 LTS | `C:\Apps\blender-4.5.12\blender.exe` | 3.11 |
| 4.4 (Goo Engine 4.4.3, treat as stock 4.4) | `E:\Program Files\goo-engine-4.4.3\blender.exe` | 3.11 |

GPU code needs a GUI instance (`-b` cannot create GPU buffers). Use separate `--factory-startup` instances with a self-quitting script and a watchdog. Goo crashed once on `screen.screen_full_area()`; avoid it.

Docs: manuals at `D:\Dev\mcp\goo-mcp\assets\blender-{3.3,4.4,5.2}-manual`. Python API via blender-mcp `search_api_docs`/`get_python_api_docs`, or introspect with the builds above.

## Workflow
- **Models:** workers (implementation, research, RE) run Haiku 5.5 at xhigh effort; testers run Haiku 5.5 at high. Never use stronger models for subagents.
- **Agents:** `exvirtucamera-worker` (implementation/research/automated tests), `exvirtucamera-re` (ida-mcp reverse engineering of `data/re/`; writes only `docs/`), `exvirtucamera-tester` (manual sessions).
- **Delegation:** the main agent orchestrates, writes this file, and reviews. Briefs are self-contained and give file ownership when workers run in parallel. Workers do not commit or edit this file; they report constraint changes. Ask workers to always end with a final report; some end silently, so check their files and rerun the tests instead of waiting.
- **Reviews are main-agent-only.** Never delegate a review.
- **Testing chain:** worker implements → main agent reviews → loopback tests + GUI end-to-end → owner's phone check with explicit steps → main agent checks key screenshots/decoded frames itself.
- **Phone:** VirtuCamera 1 on iOS, operated by the owner. Testers never touch other apps.
- **Git:** commit or push only when the owner asks.

## Manual verification (phone)
1. Install the built zip, enable, Start Serving, scan the QR with VirtuCamera 1. Allow the firewall prompt on Private networks.
2. Pick a camera; the stream shows the camera view within ~1 s; moving the phone moves the camera.
3. Zoom/pan the Blender viewport and cover the window: the stream continues at a fixed size.
4. Play/scrub from the app; record a take; keys appear on location/rotation/lens; no Euler flips.
5. Tap a custom script button; it runs.
6. Disconnect/reconnect; Stop/Start Serving; all recover without restarting Blender.
