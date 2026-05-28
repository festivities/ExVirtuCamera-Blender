# ExVirtuCamera-Blender - Agent Manual

This file is the living context for new sessions and other agents. Update it whenever behavior, constraints, or workflows change.

## Project Overview
ExVirtuCamera-Blender is a Blender addon that streams the 3D viewport to a mobile client. The capture path is OS-level screen capture via `mss` and a SharedMemory buffer shared with a bridge subprocess that hosts `vc_core.pyd`.

## Current Goal
Prevent bridge crashes during rapid zoom/pan by eliminating SharedMemory reallocation races and stabilizing capture resolution updates.

## Key Files and Roles
- Blender addon entry and capture logic: [virtucamera_blender.py](virtucamera_blender.py)
- Bridge process and SharedMemory handling: [virtucamera/vc_bridge_server.py](virtucamera/vc_bridge_server.py)
- Core server and protocol layer: [virtucamera/vc_base.py](virtucamera/vc_base.py), [virtucamera/vc_core.py](virtucamera/vc_core.py)
- User docs: [README.md](README.md)
- Implementation notes and rationale: [implementation_plan.md](implementation_plan.md)

## Capture Pipeline (High Level)
1. Blender side computes the camera rect in the 3D viewport.
2. The rect is aligned to multiples of 16 to match the MJPEG encoder block size.
3. `mss` captures the screen region using OS coordinates.
4. The BGRA bytes are written into a preallocated SharedMemory buffer.
5. The bridge process reads the SharedMemory buffer and passes it to `vc_core.pyd`.

## SharedMemory Strategy (Important)
The SharedMemory buffer is preallocated once for the full virtual screen size and reused for the entire session. Resolution changes only update the capture resolution; the buffer itself is never reallocated during zoom/pan.

This prevents a race where the C++ encoder reads a buffer that was resized or freed mid-frame.

## Implemented Stabilization Changes
- Preallocate a single SharedMemory buffer sized to the virtual screen, aligned to multiples of 16. [virtucamera_blender.py](virtucamera_blender.py)
- Debounce `set_capture_resolution` calls to 200ms and fall back to the last committed resolution during rapid zoom. [virtucamera_blender.py](virtucamera_blender.py)
- Write only the active frame bytes into SharedMemory (`frame_size` slice), not the full buffer. [virtucamera_blender.py](virtucamera_blender.py)
- Clear cached ctypes arrays before closing SharedMemory and guard `get_capture_buffer` to current frame size. [virtucamera/vc_bridge_server.py](virtucamera/vc_bridge_server.py)

## Constraints and Caveats
- `mss` capture requires the Blender viewport to remain visible and unobstructed.
- During rapid zoom, the capture crop may lag slightly due to the debounce.
- Resolution is aligned to multiples of 16; do not remove this alignment.

## Diagnostic Notes
- Bridge logs: `virtucamera/bridge_sys.log`
- Debug logs: `virtucamera/bridge_debug.log`
- If you see `BufferError: cannot close exported pointers exist`, verify `_cached_c_array` is cleared before closing SHM.
- If the bridge crashes with `STATUS_ACCESS_VIOLATION`, confirm SHM is not being reallocated in `_capture_timer`.

## Manual Verification Plan
1. Deploy the addon to your Blender scripts directory.
2. Start Serving and connect from the iOS app.
3. Rapidly zoom for 10+ seconds; confirm the stream stays alive and the bridge does not crash.
4. Check `bridge_sys.log` for no `BufferError` spam and verify `_shm_counter` stays at 1.
5. Rapidly pan; confirm stability.
6. Stop Serving and start again; verify recovery works.

## How to Change the Debounce
The debounce interval lives in `_capture_timer` in [virtucamera_blender.py](virtucamera_blender.py). The current value is 0.2 seconds. If you change it, document the new value here and in [implementation_plan.md](implementation_plan.md).

## Blender API Reference
Blender 5.1 API docs are available at:
- D:\dev\projects\festive-utils\blender\blenderDocs

## Agent Guardrails
- Do not reintroduce SharedMemory reallocation during capture.
- Always align capture resolution to multiples of 16.
- Keep the SHM write size matched to the active frame size.
- Avoid changing capture mode or pixel format without updating both sides of the bridge.
- Use `py` for Python commands in this workspace.

## When You Make Changes
- Update this manual with any new constraints, workflow steps, or behavior changes.
- Keep notes brief and factual; avoid duplicating code.
