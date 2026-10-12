# vc_core.cp39-win_amd64.pyd: protocol and implementation spec

Target: `virtucamera/vc_core.cp39-win_amd64.pyd`, sha256 `9ffbb6c9911f7f336fc46fb9f8805a00b230aa99480fbe85fa2f2c74566ad552`
(identical to `data/virtucamera_blender_original/virtucamera/vc_core.cp39-win_amd64.pyd`).
Built by Cython 0.29.23 from `vc_core.pyx` for CPython 3.9 (MSVC, imports `python39.dll`).
Reported server version: `VCServer.SERVER_VERSION == (2, 1, 2)` (the wrapper in `virtucamera/vc_core.py` says 1.1.0; see section 12).

Revision note (this pass): sections 5.3, 5.4, 6.2 to 6.6, 7.3, 7.4, 9, 10, 11 and 12 were updated. Section 11 items were closed or moved into their sections. Experiments used the RE copy `data/virtucamera_blender_original/virtucamera/` because the repo working tree no longer contains `virtucamera/` (section 12).

## 0. Licence notice (read first)

`virtucamera/LICENSE.txt` and the header of `vc_base.py` state: "You may not decompile, disassemble, reverse engineer or modify any portion of the Software." This analysis disassembled the binary. This document records protocol facts and addresses only. It does not reproduce code. Whether this use is acceptable is a decision for the repo owner before any reimplementation is written from this spec. Flagged to the main agent.

## Evidence legend

- **[V-IDA]**: verified in the IDA database (address given; the database is `data/re/vc_core.cp39-win_amd64.pyd.i64`, gitignored). The IDB holds renames only; the binary is unmodified.
- **[V-RUN]**: verified by a loopback run against the real binary (harness in `data/re/harness/`, forced to `127.0.0.1`; all child processes killed). Experiment names are listed in the appendix.
- **[INFERRED]**: reasoned from strings, names, or code shape; not directly confirmed.
- **[OPEN]**: not resolved by either method.

Method summary: the 459 Cython `__pyx_n_s_*` string-table entries were recovered from `.data` and renamed `N_<text>` in the IDB (a `<` in a literal shows as `_`, so `N__H` is the literal `"<H"`). `tp_methods` and `tp_getset` were parsed from the `VCServer` type object at `0x180070B60`. The C-level vtable is filled at runtime in the type-init function `sub_18000A940` and lives at `0x18007EA00` (slot `+N` = `0x18007EA00 + N`). The session loop is `VCc_client_session` at `0x180017720`, reached through vtable slot `+232`. Cython `error` markers give `vc_core.pyx` line numbers (for example `v9 = 2212`), which are cited below as "pyx N". Per-run encoder-child data came from a `sitecustomize` probe in the child process (`data/re/harness/hook/`), not from the binary.

## 1. Module overview

**Classes** (module `virtucamera.vc_core` when imported as a package member):
- `VCServer`: `tp_basicsize 0x2B8`, type object `0x180070B60` [V-IDA]. 46 Python methods (tp_methods at `0x18006D960`), 21 getset properties (tp_getset at `0x18006EB10`) [V-IDA]. Has cdef attributes (not Python-visible) including the 33 token objects: 28 request tokens and 5 error codes (section 5).
- `_Shm`: named shared-memory block with `buf`, `ptr`, `name`, `size`, `close`, `unlink` (mmap-based; the docstring is copied from `multiprocessing.shared_memory`) [V-RUN dir()].
- `_Event`, `_Lock` (`_Lock` subclasses `multiprocessing.synchronize.Lock`): `share(process_handle)` and `from_share` for cross-process use [V-RUN dir(), V-IDA strings].

**Module attributes** [V-RUN `dir(vc_core)`]: `_IS_WINDOWS`, `_ep`, `_sp`, `_winapi`, `av`, `mss`, `ifaddr`, `qrcode`, `png`, `mmap`, `ctypes`, `array`, `pickle`, `subprocess`, `signal`, `synchronize`, `reduction`, `queue`, `secrets`, `socket`, `struct`, `threading`, `time`, `timeit`, `traceback`, `io`, `os`, `pathlib`, `errno`, `stat`, `sys`, `__pyx_unpickle_VCServer`. `IDLE_LIFESPAN` is not a module attribute [V-RUN].

**Imports used at runtime** [V-IDA strings + module attrs]:
- Relative import of `VCBase` inside `VCServer.__init__`: the constructor fails with "attempted relative import with no known parent package" when the module is loaded as top-level `vc_core` [V-RUN]. Load it as `virtucamera.vc_core` with the repo root on `sys.path`.
- `ifaddr.get_adapters` (QR IP list), `qrcode` + `png` (QR image), `mss` (declared; the CAPMODE_SCREENSHOT path is not used by the bridge), `av` (encoder, child process), `socket`, `threading`, `queue`, `subprocess` (`Popen`, `PIPE`, `communicate`), `multiprocessing` helpers (`synchronize`, `reduction`), `mmap`, `ctypes` (`from_buffer`, `addressof`, `c_uint8`), `struct` (`pack`, `unpack`), `pickle` (`loads`, `dumps`), `timeit.default_timer`, `time.sleep`, `PySide2`/`PySide` (QImage/QPixmap only for `get_qr_image_qt`, not tested) [V-IDA strings].

**Threads and processes** [V-IDA + V-RUN]:
1. Accept/session thread: `start_serving` creates `threading.Thread(target=_handle_tcp_socket, daemon=True)` [V-IDA `start_serving` at `0x1800471D0`]. `_handle_tcp_socket` (`0x180014CA0`) calls `VCc_client_session` (`0x180017720`) once per command, in a loop [INFERRED: the session body returns `None` after each command at label `LABEL_56`; the caller loop was not decompiled in full]. Python traceback names: `_handle_tcp_socket` -> `_proccess_command` [V-RUN].
2. Autosend thread: `_start_autosend` (`0x180035FB0`) starts `_autosend_loop` (`0x180036550`), which calls `get_capture_buffer` once per frame [V-IDA; V-RUN 180 calls per 6 s at 30 fps, section 6.2].
3. Stop thread: `_stop_serving_thread` (`0x18003D600`) [V-IDA].
4. Encoder child process: started as `python_executable -c "from vc_core import _ep; _ep()"` [V-RUN]. The parent builds its environment from `os.environ` with `PYTHONPATH` set from `sys.path` (`sub_180035480`) [V-IDA]. The child exits within 2 s of STOP_STREAMING (token 151) and is gone after `stop_serving` [V-RUN exp38]. The child receives its arguments as one pickled 16-element tuple and then per-frame pickled tuples (section 6.4) [V-RUN].

## 2. Public API

Constructor: `VCServer(platform, plugin_version, vcbase, event_mode, main_thread_func=None, python_executable=None)` [V-IDA docstring, V-RUN].
- `vcbase` must be an instance of a `virtucamera.vc_base.VCBase` subclass; otherwise `ValueError: 'vcbase' must be an instance of an VCBase subclass` [V-RUN].
- `platform` is a free string. It appears in the SERVER_INFO reply (section 5). `plugin_version` is a 3-tuple. It is echoed by PLUGIN_VERSION (token 8) [V-RUN].
- `event_mode`: `EVENTMODE_PUSH` (0) or `EVENTMODE_PULL` (1). Default not verified; the wrapper always passes it.
- `python_executable`: needed for the encoder child (section 6). The bridge passes `sys.executable` of the Python 3.9 bridge.

Constants [V-RUN class dict]: `SERVER_VERSION = (2, 1, 2)`; `EVENTMODE_PUSH = 0`, `EVENTMODE_PULL = 1`; `CAPMODE_SCREENSHOT = 0`, `CAPMODE_BUFFER = 1`, `CAPMODE_BUFFER_POINTER = 2`; `CAPFORMAT_UBYTE_RGB = 0`, `CAPFORMAT_UBYTE_BGR = 1`, `CAPFORMAT_UBYTE_RGBA = 2`, `CAPFORMAT_UBYTE_BGRA = 3`.

Methods (semantics from docstrings [V-RUN] plus the observed behaviour):
- `start_serving(port) -> bool`: binds `0.0.0.0:port`, listens, starts the accept thread [V-IDA `0x1800471D0`; the bind address string `0.0.0.0` is referenced there]. Errors with `Can't start server. TCP port %d already in use.` [V-IDA string]. Logs `Listening on TCP port %d` [V-RUN].
- `stop_serving()`: returns immediately. `server_did_stop` fires when shutdown completes [V-RUN exp1, exp10].
- `execute_pending_events()`: PULL mode only. Runs queued callbacks on the calling thread [V-RUN, section 8].
- `get_qr_string() -> str`: `"<port>_<ip>_<ip>..."` [V-RUN]. Raises `Exception: VCServer has not started serving, can't generate QR` when not serving [V-RUN].
- `get_qr_image_raw(box_size) -> list`: greyscale rows (length 123 for `box_size=3`) [V-RUN].
- `write_qr_image_png(path, box_size)`: writes a PNG [V-RUN].
- `get_qr_image_qt(...)`: returns a QPixmap via PySide; not tested.
- `set_capture_resolution(width, height)`: Python-level setter stores the values as given, with no validation or clamp, when not capturing (`0x0`, `-5x16`, `16384x64` were all stored) [V-RUN exp33]. While capturing, a size change restarts the encoder (section 6.3) [V-IDA `VC_set_capture_resolution` `0x18003E910`, V-RUN exp30]. Error string when called before capture: `Invalid capture resolution, set_capture_resolution() must be called before starting capturing` [V-IDA].
- `set_capture_mode(mode, pixel_format)`: must be called from `capture_will_start`. Error: `Invalid capture_mode, set_capture_mode() must be called before starting capturing` [V-IDA]; with no call, START raises this as a Python `ValueError` in the callback thread and sends no error byte, and `is_capturing` stays False [V-RUN exp31].
- `set_vertical_flip(flag)`: enables a `vflip` node in the encoder filter graph (section 6.4) [V-RUN exp30].
- `update_script_labels()`: pushes SCRIPT_INFO (7) to the connected client without a request (section 5.5) [V-RUN exp27].

Properties (getset, all read-only in practice) [V-IDA getset table + V-RUN]: `is_event_loop_running`, `platform`, `plugin_version`, `event_mode`, `vcbase`, `main_thread_func`, `current_camera`, `capture_mode`, `capture_format`, `capture_width`, `capture_height`, `use_vflip`, `is_starting`, `is_stopping`, `is_serving`, `is_connected`, `is_capturing`, `server_port`, `client_ip`, `client_port`, `python_executable`. The state flags are C ints at struct offsets `+0x17C` (is_starting), `+0x180` (is_stopping), `+0x184` (is_serving), `+0x188` (is_connected), `+0x18C` (is_capturing) [V-IDA getter code].

Not present in the binary: `set_shm_name`, `get_shm_name`, `restart_bridge`, `bridge_alive`. Those are wrapper-only (`virtucamera/vc_core.py`).

Cython checksum member list [V-IDA string at `0x180065970`]: the pickling-checksum error lists the class members, including `_ALPHA_BITRATE_RATIO`, `_ENC_MAX_RESOLUTION`, `_JOIN_TIMEOUT`, `_QUEUE_MAX_SIZE`, `_enc_bitrate`, `_enc_fps`, `_enc_vflip`, `_buff_size`, `_pixel_format`, `_is_autosend`, `_is_encoding`, `_socket_lock`, `_shm_lock`, `_tcp_srv_socket`, `_tcp_clt_socket`, `_scene_cameras`, `_callback_queue`. These are internal member names; the offsets are not mapped.

Python method qualnames seen in tracebacks [V-RUN exp27, exp28]: `_start_streaming`, `_start_encoding`, `_restart_encoding`, `_stop_encoding`, `_start_subprocess`, `_wait_forked_process`, `_encode_frame`, `_stop_streaming`, `_stop_autosend`, `_async_callback`, `_stop_serving_thread`.

## 3. Discovery and pairing

- QR payload: a single ASCII string `"<port>_<ip1>_<ip2>_..."` (placeholders; IPs are the host's IPv4 addresses, joined by `_`) [V-RUN].
- IP source: `ifaddr` adapter list [INFERRED from `get_adapters` string and module import]. The binary filters `0.0.0.0`, `127.0.0.1`, and `169.254.` (link-local) [V-IDA strings].
- No UDP discovery: the string table contains no `DGRAM`, `sendto`, `recvfrom`, `broadcast`, `multicast`, `mdns`, or `ssdp` [V-IDA]. Pairing is QR-only, then the app connects TCP to `<ip>:<port>`.
- The QR is only generated while serving (section 2).

## 4. Transport

- One listening TCP socket, `SOCK_STREAM`, bound to `0.0.0.0:<port>` [V-IDA `start_serving`]. `SO_REUSEADDR` is set [V-IDA strings; INFERRED from the `setsockopt` call].
- The app's control connection and the viewport video share this one TCP connection. No second port was observed. Frames arrived on the control socket [V-RUN, section 6].
- One session at a time: while one session is blocked, a second connection times out [V-RUN exp13, INFERRED single-session accept].
- Handshake: none on the wire. The server sends nothing after connect and waits for the client's first byte [V-RUN exp1]. Whether the app sends SERVER_INFO first is not verified (section 11).
- Heartbeats: none. Idle connections produced no traffic [V-RUN exp1].
- Version check on the wire: none. The app learns the server version from SERVER_INFO (token 0) and the plugin version from PLUGIN_VERSION (token 8) (section 5).
- Socket timeouts: every socket timeout vc_core sets is `settimeout(None)` (blocking) [V-RUN exp32; sites V-IDA `0x18004820E` (start_serving), `0x180015896` (handle_tcp_socket), `0x18003BC9E`, `0x1800540BC` (child)]. No finite socket timeout was observed.

## 5. Message framing and every message type

### 5.1 Framing rules

**Client to server.** `[token: u8][payload]`. The payload is fixed-size per token (table 5.3) or length-prefixed as stated in 7.3. No length prefix is used for client-to-server payloads except the `u16` count in 59 and 60. Names are not length-prefixed (the camera is selected by index, see token 52).

**Server to client.** Every server message starts with a token byte, which is the echo of the request token or an error code:
- Variable-length replies: `[token][u32 little-endian length][payload]` (length = payload bytes). The C helper builds the header with `struct.pack("<I", len(payload))` and sends header + payload [V-IDA `VCc_tcp_send_with_len`, `0x18003AD50`; V-RUN].
- Fixed-length replies: `[token][payload]` with no length prefix [V-RUN: CAMERA_MATRIX 128 bytes, SCENE_STATUS 32 bytes, PLAY_FPS 8 bytes, PLUGIN_VERSION 6 bytes].
- Error replies: a single byte, 200 to 204 (table 5.4), some followed by one index byte (203).

All integers are little-endian (`<`). Strings are ASCII, not NUL-terminated.

**Helpers** [V-IDA]: `VCc_tcp_recv(n)` (`0x18003A4D0`) loops `recv(n - len(buf))` under `_socket_lock` until `n` bytes; `VCc_tcp_send(prefix, struct)` (`0x18003B230`) sends `prefix + payload` under the same lock; `VCc_tcp_send_with_len(prefix, data)` (`0x18003AD50`) adds the `<I` header. The vtable slots are `+32` (send), `+40` (send with length), `+48` (recv).

### 5.2 Dispatch

The session body `VCc_client_session` (`0x180017720`, vtable slot `+232`) reads the first byte as a token and compares it against the 28 request-token objects stored in the instance at struct slots `a1[54]..a1[81]` (`+0x1B0`..`+0x288`). The 5 error-code objects sit in `a1[82]..a1[86]` (`+0x290`..`+0x2B0`) and are sent, not compared. The comparison is `sub_180003ED0(token, slot, 2)`, a rich comparison with `Py_EQ` [V-IDA]. Those objects are created by `VCc_vt0_init_cmds` (`0x18004AD20`), which calls `VCc_cmd_token(idx)` (`0x18004D5C0`). That helper is `struct.pack("<B", idx)`, so every token is one byte equal to its index [V-IDA].

Token values in the init order: ASK 0..8; SET 50..60; DO 150..157; ERR 200..204 [V-IDA]. Slot-to-token map: `a1[54+k]` is token `k` for `k` in 0..8; `a1[63+k]` is token `50+k` for `k` in 0..10; `a1[74+k]` is token `150+k` for `k` in 0..7 [V-IDA, from the compare sites].

**Unknown tokens** are consumed as a single byte with no reply, and the session continues [V-RUN exp29: bytes `55` and `FF` were skipped; the next request was answered].

### 5.3 Request tokens (client to server)

Name column: "verified" means the name is confirmed by behaviour [V-RUN]; "inferred" means the name is from the enum group and the vcbase callback. Callbacks are vcbase methods fired through `_async_callback` [V-IDA `0x18003C690`].

| Tok | Name | Client payload | Server reply | vcbase callbacks | Conf. |
|---|---|---|---|---|---|
| 0 | SERVER_INFO | none | `00` `u32 n` `<3H` SERVER_VERSION + ASCII `"<platform>_<COMPUTERNAME> - <platform>"` | none | verified (layout and template) |
| 1 | SCENE_STATUS | none (needs camera) | `01` `<4d` (current_frame, range_start, range_end, camera focal length); no length | get_playback_state, get_camera_focal_length | verified |
| 2 | SCENE_CAMERAS | none | `02` `u32 n` `<h` current index (-1 if none) + `"$".join(names)` | get_scene_cameras | verified |
| 3 | CAMERA_MATRIX | none (needs camera) | `03` `<16d` (128 bytes, get_camera_transform order); no length | get_camera_exists, get_camera_transform | verified |
| 4 | VIEWPORT_IMG | none | `04` `u32 n` VP9 frame (section 6); `C9` if not streaming | get_capture_buffer (via capture path) | verified |
| 5 | PLAY_FPS | none | `05` `<d` fps; no length | get_playback_fps | verified |
| 6 | CAMERA_HAS_KEYS | none (needs camera) | `06` `u8` flags (bit0 any keys, bit1 transform keys, bit2 flen keys) | get_camera_has_keys | verified (4 combinations) |
| 7 | SCRIPT_INFO | none | `07` `u32 n` + `"%".join(labels)` | get_script_labels | verified |
| 8 | PLUGIN_VERSION | none | `08` `<3H` plugin_version (as passed to the constructor); no length | none | verified (3 versions) |
| 50 | SET_PLAYBACK_RANGE | `<2d` (start, end) | echo `32` | set_playback_range(start, end) | verified |
| 51 | SET_CURRENT_TIME | `<d` frame | echo `33` | set_frame(frame) | verified |
| 52 | SET_CURRENT_CAMERA | `<H` index into the last SCENE_CAMERAS list | echo `34` | get_camera_exists(name), current_camera_changed(name) | verified; requires a prior token 2 |
| 53 | SET_CAMERA_MATRIX | `<16d` | echo `35` | set_camera_transform(cam, 16 values in payload order) | verified |
| 54 | SET_CAMERA_MATRIX_AT_TIME | `<17d` = 16 matrix + frame | echo `36` | set_camera_transform, set_frame(17th value) | verified |
| 55 | SET_FOCAL_LENGTH | `<d` | echo `37` | set_camera_focal_length(cam, f) | verified |
| 56 | SET_FOCAL_LENGTH_AT_TIME | `<2d` = focal, frame | echo `38` | set_camera_focal_length, set_frame(frame) | verified |
| 57 | SET_CAMERA_ALL | `<17d` = 16 matrix + focal | echo `39` | set_camera_transform, set_camera_focal_length | verified |
| 58 | SET_CAMERA_ALL_AT_TIME | `<18d` = 16 matrix + focal + frame | echo `3A` | set_camera_transform, set_camera_focal_length, set_frame | verified |
| 59 | SET_CAMERA_MATRIX_KEYS | `u16 n` + n x 136 bytes: `[16 matrix doubles, frame double]` per key (section 7.3) | echo `3B`; no other reply | set_camera_transform_keys(cam, frames, matrices) | verified (layout proven, section 7.3) |
| 60 | SET_CAMERA_FLEN_KEYS | `u16 n` + n x 16 bytes: `[focal double, frame double]` per key (section 7.3) | echo `3C`; no other reply | set_camera_flen_keys(cam, frames, focal lengths) | verified (layout proven, section 7.3) |
| 150 | START_STREAMING | `<ffH??` (12 bytes): fps (f32), Mbits/s (f32), u16 (no observed effect), opaque (bool), autosend (bool) | echo `96`; no other reply on success | capture_will_start, look_through_camera(cam); then frames | verified (section 6.6) |
| 151 | STOP_STREAMING | none | echo `97` | capture_did_end; is_capturing becomes False | verified (echo `97` and encoder child exit, V-RUN exp22, exp38) |
| 152 | START_PLAYBACK | 1 byte bool (forward) | echo `98` | start_playback(fwd) | verified |
| 153 | STOP_PLAYBACK | none | echo `99` | stop_playback() | verified |
| 154 | (no-op) | none (no payload byte is read) | none | none | verified (no-op; section 5.3 note) |
| 155 | REMOVE_CAMERA_KEYS | none | echo `9B` | remove_camera_keys(cam) | verified |
| 156 | CREATE_NEW_CAMERA | none | echo `9C` | get_camera_transform(''), get_camera_focal_length(''), create_new_camera(), set_camera_transform(new, m), set_camera_focal_length(new, f) | verified |
| 157 | EXECUTE_SCRIPT | 1 byte index (`<B`) | success: `9D` + index byte; failure: `CB` + index byte | execute_script(idx, cur_cam) | verified (both replies, V-RUN exp27) |

Notes for the table:
- "Echo" = the server writes the request token back immediately after reading it, before reading the payload. Evidence: `35 C8` (echo, then error because the camera is missing), the START echo `96`, and the 59/60 echoes `3B` and `3C` even when the count is 0 [V-RUN exp18].
- **Token 154 is a no-op.** Its branch (slot `a1[78]`) jumps straight to the return-None exit (`LABEL_56`) [V-IDA]. It reads no payload and sends nothing [V-RUN exp29: `154` followed by `01` gave `C8` (the SCENE_STATUS error), and `154` followed by `02` gave the SCENE_CAMERAS reply]. An earlier test that appeared to show a SERVER_INFO reply was the next byte `00` being read as token 0.
- Camera-scoped requests (1, 3, 6, 53-60, 150, 155, 157) need a current camera. Without one the server answers `C8` [V-RUN]. Camera selection is per connection: a disconnect resets it to `''` and fires `current_camera_changed('')` [V-RUN exp7].
- Token 52 needs a prior SCENE_CAMERAS (token 2) to populate the list (`_scene_cameras` at struct `+0x88` is written by `_send_scene_cameras`, `0x1800257A0`, and read by `_set_current_camera`, `0x180024AD0`) [V-IDA]. Without it, the handler returns and no callback fires [V-RUN].
- For tokens 53 and 54 the matrix is passed exactly as sent (16 values in the given order). The binary does no transposition [V-RUN].
- Client-side wrong payload lengths desynchronise the stream. A short START (no payload) blocks the session until 12 bytes arrive, which swallows the next commands [V-RUN exp9].

### 5.4 Error replies (server to client)

| Byte | Name | Trigger and evidence |
|---|---|---|
| 200 (`C8`) | MISSING_CAMERA | Camera-scoped request with no current camera [V-RUN]. Referenced by `set_current_camera` (`sub_180024AD0`, load at `0x180025645`) and `sub_180039860` (load at `0x180039B4E`) [V-IDA]. |
| 201 (`C9`) | NOT_STREAMING | VIEWPORT_IMG when not streaming [V-RUN]. Loaded in `VC__send_viewport_img` at `0x180036F2F` [V-IDA]. |
| 202 (`CA`) | NO_ALPHA (name from checksum list, INFERRED) | **Never sent by this build.** The only non-stack loads of the slot (`+0x2A0`) are in the init function `VCc_vt0_init_cmds` [V-IDA, full-segment displacement scan]. No trigger was found. |
| 203 (`CB`) | EXECUTE_SCRIPT | `execute_script` returns False: reply `CB` then the index byte (`CB 01`) [V-RUN exp27]. Loaded in `VC__execute_script` at `0x180026E85` [V-IDA]. |
| 204 (`CC`) | FFMPEG (name from checksum list, INFERRED) | START while `capture_mode == CAPMODE_SCREENSHOT` (0): reply `96 CC` (echo then error) [V-RUN exp31]. Loaded in `sub_18002A1A0` (`_start_streaming`) at `0x18002B0B7`, branch at pyx 1780-1781 [V-IDA]. Other capture modes and formats did not produce it (section 6.4). |

The `_CMD_*` names in the Cython checksum message are: `_CMD_ASK_*` (9), `_CMD_DO_*` (8), `_CMD_ERR_*` (5: EXECUTE_SCRIPT, FFMPEG, MISSING_CAMERA, NOT_STREAMING, NO_ALPHA), `_CMD_SET_*` (11) [V-IDA strings]. The enum is alphabetical in the list, not in value order. The value-to-name mapping for 203 and 204 is confirmed by the trigger tests above.

### 5.5 Other server-initiated messages

- `client_connected(ip, port)`: on accept [V-RUN].
- `client_disconnected()`: on EOF or error [V-RUN].
- `current_camera_changed(name)`: on a successful token 52, and on disconnect with `''` [V-RUN].
- `server_did_stop()`: after `stop_serving` completes [V-RUN].
- **Unsolicited SCRIPT_INFO**: `update_script_labels()` called from the main thread sends `07 <u32 len> <labels joined by "%">` to the connected client with no request. Observed: `07 0a 00 00 00` + `Alpha%Beta` [V-RUN exp27]. With no client connected it sends nothing [INFERRED from the connection state, not tested].

## 6. Video path

### 6.1 Frame packets

Each frame is one VIEWPORT_IMG packet: `04` + `u32 n` + the encoded VP9 frame (`n` bytes). No container, no extra header [V-RUN, stream parse]. Stream of 178 frames in 6 s at fps=30 (autosend on): 176 inter frames (`0x86`), 2 keyframes (`0x82`), at frames 0 and 128 [V-RUN exp36, exp5].

VP9 uncompressed header decoded by hand from the first keyframe `82 49 83 42 00 03 F0 01 F6 ...`: frame_marker `10`, profile 0, keyframe, show_frame=1, sync code `49 83 42`, width-1 = `0x003F` (64), height-1 = `0x001F` (32) [V-RUN].

### 6.2 Capture and pacing

- Capture callbacks: `capture_will_start()` once per START; then `get_capture_buffer(cam)` each frame in CAPMODE_BUFFER (`VC__async_get_capture_buffer`, `0x180038190`) [V-RUN: 180 calls for 6 s at 30 fps]. `capture_did_end()` on stop [V-RUN exp22].
- The buffer returned by `get_capture_buffer` must be WRITABLE. A `bytes` object raises `TypeError: underlying buffer is not writable` in `_send_viewport_img` and kills the autosend thread [V-RUN exp10]. Return a `bytearray` or ctypes array.
- The buffer size is `capture_width * capture_height * 4` for the 4-byte formats, read from the current `capture_width`/`capture_height` [V-RUN, bridge code].
- **CAPMODE_BUFFER_POINTER (2)**: the callback `get_capture_pointer` must return a valid address. Returning `0` crashed the process (exit 139) [V-RUN exp28]. Not otherwise tested.
- Pacing: fixed rate from the START fps (30 requested, about 29.5 to 30 observed) [V-RUN]. Implemented as a timer loop in `_autosend_loop`. No ack from the app is awaited in autosend mode [V-RUN].
- Autosend off (pull): with `autosend=0` START produced no frames at all (zero packets in 3 s) [V-RUN exp22]. An ASK 4 request blocks the session. The binary waits in `Event.wait` with no timeout. In PULL mode `execute_pending_events` blocked in `Event.wait` for more than 3 s (exp12). Keep `autosend=1` in the bridge.
- Pixel format: the child's pixel-format argument is the capture format name (`bgra` for CAPFORMAT_UBYTE_BGRA, `rgba` for CAPFORMAT_UBYTE_RGBA) [V-RUN exp23, exp26]. `use_vflip=False` by default [V-RUN].
- Which process writes the bytes: **the encoder child writes the VIEWPORT_IMG packets** to the TCP connection. Its pickled arguments include the single-byte token `b'\x04'` and a 628-byte blob that is a duplicated Windows socket (WSAPROTOCOL_INFOW share) [V-RUN exp23]. The parent does not write frame bytes itself [V-RUN, child args; INFERRED that the parent never writes `04` packets, not separately tested].

### 6.3 Resolution

- **Multiples of 16 are NOT required by `vc_core`.** Tested captures 50x34 and 51x33 (odd) and 64x32 each produced keyframes with those exact dimensions, at about 30 fps [V-RUN exp15]. The codec is VP9, not MJPEG: the binary contains `libvpx-vp9`, `webm`, `yuv420p`, and no `mjpeg` string [V-IDA]. AGENTS.md's 16-alignment guardrail is not required by this binary. It may still be required by the Blender-side buffer or the app's decoder (untested; no phone test).
- **Encoder-side clamp (`_ENC_MAX_RESOLUTION`, value 720 INFERRED from behaviour)**: at encoder start the longest side is capped at 720. Both sides are scaled by `720 / max(w, h)` through a bilinear `scale` filter node [V-RUN exp34, exp35]. Observed (requested -> child stream size -> VP9 keyframe size):
  - 1000x1000 -> 720.0 x 720.0 -> 720x720
  - 640x2000 -> 230.4 x 720.0 -> 230x720
  - 300x2000 -> 108.0 x 720.0 -> 108x720
  - 1280x720 -> 720.0 x 405.0 -> 720x405
  - 720x1280 -> 405.0 x 720.0 -> 405x720
  - 721x100 -> 720.0 x 99.86 -> 720x99
  - 720x720 -> no scale node, 720x720
  - 4000x3000 -> 720 x 540; 8192x64 -> 720 x 5.625 (from the child width/height) [V-RUN exp34]
  The child passes the scaled sizes as floats; the VP9 keyframe carries the floor of the scaled size [V-RUN exp35].
- **Mid-stream resolution change** (`set_capture_resolution` while capturing, from `get_capture_buffer`) [V-RUN exp30 part B]:
  - A changed size stops the encoder and starts a new one (`VC_set_capture_resolution` calls vtable `+160` then `+184`, `0x18002CFB0` and `0x18002C5D0`) [V-IDA]. Observed: each change started a new encoder child (3 `AV_OPEN` events for 2 changes), each with the new size in its `ADD_STREAM`/`STREAM_SET` values [V-RUN].
  - The first packet after each change is a keyframe with the new dimensions in its VP9 header (`64x32` -> `48x40` at packet 27, `48x40` -> `50x34` at packet 54) [V-RUN]. The size is therefore in-band (no separate size message on the socket).
  - Debounce: none in `vc_core`. The restart is synchronous, `set_capture_resolution` has no timer calls [V-IDA], and each change at a 30-frame spacing restarted [V-RUN]. The 200 ms debounce is in the Blender addon only (AGENTS.md).
  - A call with an unchanged size stores the values without restart [V-IDA; not run].

### 6.4 Encoder (child process)

Child command: `python -c "from vc_core import _ep; _ep()"` run with the configured `python_executable` [V-RUN]. The encoder option builder is `VCc_enc_options` (`0x1800521A0`) and the encoder open is `VCc_enc_open` (`0x18000AFE0`) [V-IDA].

**Child argument tuple** (one pickled tuple sent at START, 16 elements) [V-RUN exp23, exp24]:
`(capture_w, capture_h, 'bgra'|'rgba', out_w, out_h, 'yuv420p', fps, mbits, use_vflip, b'\x04', ('wnsm_<random>', 8192), <628-byte socket share>, <22-byte share>, <22-byte share>, (<22-byte>, <22-byte>, <22-byte>), (<22-byte>, <22-byte>, <22-byte>))`; `out_w, out_h` were 64 and 32 in every run with a 64x32 capture; behaviour at larger sizes was not checked (the 720 clamp happens later, in the child).
- `fps` and `mbits` are the raw START floats (30.0, 8.0). `opaque` and `u16` are not in the tuple [V-RUN].
- After the tuple, the child receives one small pickled 4-tuple per frame (`(int, 1, 1, None)`). Its meaning was not decoded [OPEN].

**PyAV calls made by the child** [V-RUN exp26, via `av.open` and `add_stream` wrappers in `hook/sitecustomize.py`]:
- `av.open(BytesIO, mode='wb', format='webm')`.
- `container.add_stream('libvpx-vp9', rate=fps, options={...})`, then `width`, `height`, `pix_fmt` set on the stream.
- Options dict, exact keys and values (30 fps, 8.0 Mbits example): `end-usage: 'cbr'`, `target-bitrate: '8.000M'`, `undershoot-pct: '95'`, `lag-in-frames: '0'`, `quality: 'realtime'`, `speed: '15'`, `row-mt: '1'`, `tile-columns: '2'`, `tile-rows: '2'`, `frame-parallel: '1'`. No keyframe-interval or GOP key is set [V-RUN].
- **Bitrate formula**: `target-bitrate = "%.3fM" % Mbits` (string; `8.0 -> '8.000M'`, `2.5 -> '2.500M'`, `4.0 -> '4.000M'`) [V-RUN exp26, exp23; the `%.3fM` format string is at `0x1800650E4`, V-IDA]. This corrects the earlier INFERRED `int(Mbits * 1e6)`.
- `rate` is the START fps as a float (`30.0`, `15.5`).
- `width`/`height` are the clamped sizes from section 6.3 (floats).
- `pix_fmt = 'yuv420p'` always [V-RUN].
- Keyframe interval: keyframes at packets 0 and 128 in a 178-frame run; no option sets it, so libvpx's default applies [V-RUN exp36; INFERRED default].

**Filter graph** [V-RUN exp26, exp30, exp35]: the child builds a libavfilter graph only when a transform is needed:
- No vflip and no resize: no graph (frames go straight to the encoder).
- Resize (output size differs from capture size): `buffer(width, height, format) -> scale(width, height, flags='bilinear') -> buffersink`.
- `use_vflip` True: `buffer(width, height, format) -> vflip -> buffersink` [V-RUN]. With the resize too, the `scale` node is expected before `vflip` [INFERRED order; not tested].
- `format` is the capture format name, `bgra` or `rgba`. The `scale` node is the only use of `bilinear`.

**Capture format and vflip checks** [V-RUN exp30, exp28]:
- RGBA (2) with vflip False: row 0 of the memory buffer lands at the top of the output (red-in-row-0 decoded as red at top).
- RGBA (2) with vflip True: the output is flipped (top row becomes the last memory row).
- BGRA (3), vflip False: row 0 at top, channel order B,G,R,A correct.
- RGB (0) and BGR (1): the child fails with `ValueError: invalid format 'rgb'` (or `'bgr'`) at `VideoFrame` creation (`vc_core.pyx` line 364). The START echo is sent and no frames arrive. The string `Invalid encoding input_format. Supported values: 'rgb', 'bgr', 'rgba', 'bgra'` exists but is not what fires [V-RUN]. **Do not use RGB or BGR capture formats.**
- Alpha: the input is `bgra`/`rgba` but the output is `yuv420p`, so the alpha channel is dropped by the pixel conversion. No `yuva420p` or alpha-plane string exists in the binary [V-IDA strings].

### 6.5 Packetization and framing of the video

Frames go out on the shared TCP connection as `04 <u32 len> <vp9>` [V-RUN]. The child writes them to the duplicated socket (section 6.2). Each packet is one encoded frame; the 16-byte and 380-byte packets seen in the 64x32 gradient test are complete VP9 frames [V-RUN exp25].

### 6.6 START (token 150): u16 and Opaque

- Payload `<ffH??`: fps (f32), Mbits/s (f32), u16, opaque (bool byte), autosend (bool byte). The session echoes `96`, reads 12 bytes, and runs `_start_streaming(payload)` (`sub_18002A1A0`, which uses the literal `"<ffH??"`) through `_async_callback` [V-IDA, pyx 2222-2232].
- Log line: `Starting Viewport Capture. %.2f fps, %.2f Mbits/s, Opaque: %d, Autosend: %d` (string `0x180064FB0`) [V-IDA; V-RUN].
- **Opaque**: no observable effect in this build.
  - Child argument tuple identical for opaque 0 and 1 [V-RUN exp23, exp24].
  - ADD_STREAM options identical for opaque 0 and 1 [V-RUN exp36].
  - First 5 VP9 packets byte-identical (sha256) for opaque 0 and 1, for a BGRA capture with varying alpha (35 packets each, sizes 380, 16, 16, 16, 16) [V-RUN exp25], and for an RGBA capture [V-RUN exp37].
  - No extra packet and no alpha plane. No `CA` byte was ever sent.
  - The Cython member `_ALPHA_BITRATE_RATIO` exists [V-IDA checksum list] but its use was not found in the reached code path. It is not used by the options (`target-bitrate` is from Mbits only) [V-RUN]. **OPEN: the code that reads `_ALPHA_BITRATE_RATIO` is not located.**
- **u16**: no observable effect. Child tuple, ADD_STREAM options, and keyframe positions (0 and 128 over 178 frames) are identical for u16 = 0, 10, 0x1234 [V-RUN exp25, exp36]. It is not a keyframe interval. Its consumer was not located [OPEN, INFERRED unused in this path].
- Bitrate: section 6.4 (`'%.3fM' % Mbits`).
- Frame pacing: `fps` from the payload (section 6.2).
- START reply: the echo only (`96`) on success [V-RUN]. Error replies: `CC` for SCREENSHOT mode (section 5.4).

## 7. Playback, frame sync, transforms, recording, scripts

### 7.1 Playback and frame

- Frame number is a double. `SET_CURRENT_TIME` (51) and the `_at_time` setters deliver it as `set_frame(frame)` [V-RUN].
- Playback range: `SET_PLAYBACK_RANGE` (50) delivers `(start, end)`. `SCENE_STATUS` (1) reports `current_frame`, `range_start`, `range_end`, `focal_length` [V-RUN].
- FPS: PLAY_FPS (5) returns `get_playback_fps()` as `<d` [V-RUN].
- Playback control: START_PLAYBACK (152, 1-byte forward flag) and STOP_PLAYBACK (153). Observed callbacks: `start_playback(True)`, `stop_playback()` [V-RUN].

### 7.2 Transforms

- Matrix: 16 doubles, delivered in the order received. The docstring convention (Y-up, row-major translation in the last row `[12..14]`) is not applied by the binary [V-RUN: the 16-value test came back in order].
- Focal length: `<d` in SET_FOCAL_LENGTH (55) and with frame in 56.
- Combined setters: 57 (matrix + focal) and 58 (matrix + focal + frame). The order inside the payload is matrix first, then focal, then frame [V-RUN].
- Scene camera list: SCENE_CAMERAS (2) returns `$`-joined names. The current index is `<h` (-1 when none) [V-RUN].

### 7.3 Recording flow: keyed transforms and focal lengths

Both tokens need a current camera (section 5.3). The camera name is not sent; the server passes the current camera to the callback [V-RUN: callback receives `'Camera'`]. Both tokens start with `u16 n` (little-endian). Both are proven by a loopback run whose callback receives exactly the values sent [V-RUN exp18, exp17].

**Token 59 (SET_CAMERA_MATRIX_KEYS)**:
- Wire: `59` `u16 n` then `n` records of 136 bytes. Each record is `[m0..m15, frame]`: 16 doubles (matrix in the token-53 order), then one double (the keyframe frame number).
- Callback: `set_camera_transform_keys(camera, [frame_0 .. frame_{n-1}], [[m0..m15], ...])`.
- Proof (exp18, PROOF 59): `n=3`, frames `[1.0, 2.0, 3.0]`, matrices `[100.5 + j]`, `[200.5 + j]`, `[300.5 + j]` for j=0..15. Callback received frames `[1.0, 2.0, 3.0]` and the three matrices exactly.
- Negative control (NEG 59): record `[frame, m0..m15]` gives frames `[115.5, 215.5, 315.5]` and shifted matrices. A column layout (all frames, then all matrices) gives garbled values (exp17).
- Decompile: branch at `a1[72]` (pyx 2210-2212). Helper vtable `+224` = `sub_18001ED80`: `tcp_recv(2)` -> `struct.unpack("<H")` -> `n`; `tcp_recv(136*n)`; `struct.unpack("<%dd" % (17*n))`; split into frames and matrices [V-IDA; split details INFERRED from the 17-stride constants, outcome V-RUN].

**Token 60 (SET_CAMERA_FLEN_KEYS)**:
- Wire: `60` `u16 n` then `n` records of 16 bytes. Each record is `[focal_length, frame]`: focal length first, then the frame number (both doubles).
- Callback: `set_camera_flen_keys(camera, [frame_0 .. frame_{n-1}], [focal_0 .. focal_{n-1}])`.
- Proof (exp18, PROOF 60): `n=3`, record `[flen, frame]` with focal lengths `[35, 50, 85]` and frames `[1, 2, 3]`. Callback received keys `[1.0, 2.0, 3.0]` and values `[35.0, 50.0, 85.0]`.
- Negative control (NEG 60): frame-first records swap the two lists.
- Single record (ONE 60): `[flen=40, frame=7]` gives keys `[7.0]`, values `[40.0]`.
- Decompile: branch at `a1[73]` (pyx 2216-2219). Helper vtable `+216` = `sub_180020150`: `tcp_recv(2)`, `tcp_recv(16*n)`, unpack `"<%dd" % (2*n)`, then two slices; the slice starts are INFERRED from the constants, the outcome is V-RUN [V-IDA].

**Zero count (n=0), both tokens**: the echo is sent, then the helper raises `TypeError: 'NoneType' object is not iterable` at pyx 2212 (token 59) or pyx 2218 (token 60). The serving thread dies, no callback fires, and a fresh connection gets no reply (probe timeout) [V-RUN exp18 block B and C]. A reimplementation should not copy the crash: treat `n=0` as an empty list and do not stop the server. What the app sends for an empty recording is not known [OPEN].

**Byte order and the matrix**: all doubles are little-endian IEEE-754. No transposition is applied [V-RUN].

**Other recording tokens**:
- REMOVE_CAMERA_KEYS (155): no payload; calls `remove_camera_keys(cam)` [V-RUN].
- CREATE_NEW_CAMERA (156): reads the current camera's transform and focal length (name `''` when none) and calls `create_new_camera()`, then sends the transform and focal of the new name to the vcbase setters [V-RUN]. The app receives no new reply beyond the token echo.

### 7.4 Custom scripts

- Labels: SCRIPT_INFO (7) returns `"%".join(get_script_labels())` [V-RUN `Alpha%Beta`]. `update_script_labels()` pushes the same frame to the connected client without a request (section 5.5) [V-RUN exp27].
- Execution: EXECUTE_SCRIPT (157) reads a 1-byte index and calls `execute_script(index, current_camera)`.
  - Success (callback returns True): reply `9D` + index byte [V-RUN `9D 01`].
  - Failure (callback returns False): reply `CB` + index byte [V-RUN `CB 01`, exp27].

## 8. PUSH vs PULL

- PUSH (event_mode 0): callbacks run without `execute_pending_events` (they fired during plain socket tests) [V-RUN exp1-exp10]. The thread that runs them is not identified; the binary queues them through `_async_callback` (`0x18003C690`) and `_callback_except_wrap` (`0x18003D140`).
- PULL (event_mode 1): callbacks queue on `_callback_queue` and run only inside `execute_pending_events()`. `client_connected` did not fire until the first call [V-RUN exp12].
- `execute_pending_events()` drains the queue, then returns. It can block (section 6.2); avoid viewport pull requests in PULL mode.
- The bridge uses PUSH (`event_mode=0`) and does not call `execute_pending_events`. The original Blender addon used PULL with a timer (`data/virtucamera_blender_original/virtucamera_blender.py`, not re-checked here).

## 9. Timeouts, heartbeats, disconnect, stop

- Heartbeats: none (section 4).
- Socket timeouts: all observed vc_core sockets use `settimeout(None)` [V-RUN exp32]. The `settimeout` call sites are listed in section 4.
- Thread join timeout (`_JOIN_TIMEOUT`): **5 seconds**. Observed `join(5)` and `join(5.0)` calls in the stop path [V-RUN exp32]. The join call sites are `VC__stop_serving_thread` (`0x18003DBF3`) and `VCc_stop_autosend` (`0x18002C3A3`) [V-IDA].
- Disconnect detection: `recv` returning b'' ends the session; `ECONNABORTED` and `errno` are referenced in `_handle_tcp_socket` [V-IDA strings]. On disconnect the server logs `Disconnected from: %s:%d`, fires `client_disconnected`, and resets the current camera (`current_camera_changed('')`) [V-RUN].
- Stop sequence (observed): `stop_serving` -> `_stop_serving_thread` -> autosend stops (`VCc_stop_autosend`, `0x18002C380`, vtable +200), the encoder child exits (within 2 s of STOP_STREAMING, exp38), the listener and the connection close (`Stopped serving`), then `server_did_stop` fires [V-RUN exp10, exp1, exp38]. A `stop_serving` issued while a viewport pull request was blocked did not finish (exp9); the server stayed `is_serving=True`.
- Stop after a failed START: if the encoder child cannot be spawned (`python_executable` does not exist), START echoes `96`, the spawn raises `FileNotFoundError` in the callback thread (`_start_subprocess`, pyx 1537), and `is_capturing` is True. The later `stop_serving` then raises `AttributeError: 'NoneType' object has no attribute 'join'` in `_stop_autosend` (no autosend thread exists) [V-RUN exp27]. A reimplementation should not copy the stop-path crash.
- `IDLE_LIFESPAN`: `N_IDLE_LIFESPAN` is referenced at `0x18000CA16` in `VCc_enc_open`, next to the argument names `args`, `args_data`, `args_len` and the share names [V-IDA]. The parent passing it to the child is INFERRED. Its numeric value was not observed in the child argument tuple [OPEN].
- `_QUEUE_MAX_SIZE`: no `queue.Queue` was constructed during START/STOP in the parent [V-RUN exp32]; the value is not located [OPEN].
- `_ENC_MAX_RESOLUTION`: behaviour is the 720 longest-side clamp (section 6.3). The constant's value is inferred from that behaviour [INFERRED 720].

## 10. Reimplementation verdict

**Verdict: feasible for the control protocol in pure Python 3.11+. The video path needs a VP9 encoder reachable from that Python. The 3.9 bridge can be dropped, but only after the blockers below are closed.**

What is solid enough to implement now:
- TCP listener on `0.0.0.0:port`, one session at a time, token-per-byte dispatch, unknown bytes skipped, all 0 to 8 ASK replies, all SET/DO payload layouts including 59 and 60 (section 7.3), the `04 <u32> <vp9>` frame packets, the error replies 200, 201, 203, 204 (section 5.4), the unsolicited SCRIPT_INFO push, and the QR string.
- Callbacks: the vcbase interface is the same as `virtucamera/vc_base.py` (verified by names and arguments).
- Encoder settings (section 6.4): libvpx-vp9, `cbr`, `target-bitrate = "%.3fM" % Mbits`, `undershoot-pct 95`, `lag-in-frames 0`, `quality realtime`, `speed 15`, `row-mt 1`, `tile-columns 2`, `tile-rows 2`, `frame-parallel 1`, `yuv420p`, rate = fps.
- Resize: 720 longest-side clamp, bilinear scale, VP9 keyframe on each change (section 6.3).

Copy exactly (verified behaviour the app may rely on):
1. The echo of every request token before its payload is read (section 5.3).
2. START's 12-byte payload size, and no extra START reply on success.
3. Frame packets as `04 <u32> <vp9>` at the frame rate, with a VP9 keyframe at the start of every encoder (START or resize) and at packet 128 (keyframe interval default).
4. STOP_STREAMING echo `97`.
5. The 59 and 60 wire layouts (section 7.3).
6. EXECUTE_SCRIPT replies `9D idx` and `CB idx`.
7. Token 154 is a no-op (no reply, no payload).
8. Opaque and u16 are accepted and ignored (section 6.6).

Do not copy (defects in the original):
- `n = 0` in tokens 59 and 60 kills the serving thread (section 7.3).
- A START with a missing encoder executable leaves the stop path raising (section 9).
- A BUFFER_POINTER capture that returns a null pointer crashes the process (section 6.2).
- RGB and BGR capture formats fail in the encoder (section 6.4). Only RGBA and BGRA work.

Blockers and gaps:
1. VP9 encoder. Frames must be VP9 keyframes and inter frames, yuv420p, from libvpx with the options in 6.4. The bundled `av` package is a CPython 3.9 binary (`_core.cp39-win_amd64.pyd`, `avcodec-pyav-58.dll`). It cannot be imported by Blender's Python 3.11+. The bundled FFmpeg 4 DLL does contain libvpx (`libvpx-vp9`, `vpx_codec_enc_cfg`) [V-IDA DLL strings], so a Python 3.11+ build could load it via ctypes, or a PyAV wheel for that Python could be used. Neither was tested (INFERRED: recent PyAV wheels bundle libvpx).
2. The app is unmodified, so the frames must stay VP9 with the same header shape. Switching to MJPEG or H.264 would need an app-side test (no phone test was run).
3. Open protocol items: IDLE_LIFESPAN and _QUEUE_MAX_SIZE values, the consumers of u16 and `_ALPHA_BITRATE_RATIO`, the per-frame child message format, and the empty-recording case (section 11).
4. Pull-mode viewport requests block (6.2). Autosend must be used.

Dependencies (for the reimplementation):
- `mss`, `ifaddr`, `qrcode`, `png` are pure Python and bundled (`virtucamera/third_party/`).
- `av` (VP9 encode) is the only binary dependency. The bundled cp39 copy is unusable from Blender's Python; a cp311+ PyAV or a libvpx/FFmpeg shared library reached from Python 3.11+ is needed.
- Encoder child process is not needed if the encode runs in-process (threading only). The bridge's `_Shm`/`_Event` IPC is unnecessary when frames are encoded in the same process.

## 11. Open questions

1. `IDLE_LIFESPAN` value and the child's idle-exit behaviour (9).
2. `_QUEUE_MAX_SIZE` value and whether it bounds `_callback_queue` (9).
3. The consumer of `u16` in START (6.6). Opaque and `_ALPHA_BITRATE_RATIO` have no observed effect; the code that reads `_ALPHA_BITRATE_RATIO` was not located (6.6).
4. The meaning of the per-frame 4-tuple `(int, 1, 1, None)` sent by the parent to the child during streaming (6.4).
5. What the app sends for an empty keyframe recording (`n = 0`), since the binary crashes on it (7.3).
6. Whether the app sends SERVER_INFO (token 0) first, and whether it expects anything before START (4). Could be answered by wireshark-mcp on a real capture (not run; no real peers were contacted).
7. Behaviour of `set_capture_resolution` with negative or zero sizes once streaming (6.3; stored without validation when idle, exp33).
8. Whether the app's decoder accepts odd or non-16-multiple dimensions (not tested; no phone test).
9. Whether the autosend thread is a `threading.Thread` (INFERRED from the attribute name).
10. Whether the exact SERVER_INFO string template holds on another host (one sample, platform `harness`) [V-RUN].
11. Whether the parent ever writes VIEWPORT_IMG packets itself (INFERRED no; 6.2).
12. Why SCREENSHOT mode is rejected with error 204 (5.4). The trigger is verified; the reason is INFERRED (the screenshot path is not used by the bridge).

Resolved in this pass (moved to the sections above): the 59/60 payloads (7.3), token 154 (5.3), START `u16`/Opaque (6.6), error bytes 202 to 204 (5.4), SERVER_INFO template (5.3), which process writes the video (6.2), the resolution-change behaviour and debounce (6.3), vflip and RGBA filter behaviour (6.4), the bitrate formula (6.4), the timeout values (9), and the unsolicited SCRIPT_INFO push (5.5).

## 12. Guardrails and facts for the main agent

- Licence clause (section 0): the decision to continue this RE, and to use the spec for a reimplementation, rests with the repo owner.
- Repo state (observed during this pass): the repo working tree no longer contains `virtucamera/` (the module and the `third_party` folder). `git status` lists modified `.gitignore`, `README.md`, `blender_manifest.toml`, `build.bat`, and 181 staged deletions under `virtucamera/`. Root `__init__.py` and `virtucamera_blender.py` are present. This pass made no git changes. Experiments used the RE copy `data/virtucamera_blender_original/virtucamera/` (sha256 matches). Check the repo state before any re-run that expects `virtucamera/` in the tree.
- `SERVER_VERSION` in the binary is `(2, 1, 2)`. The wrapper's `SERVER_VERSION` is `(1, 1, 0)`, and its `plugin_version` is what ASK 8 echoes to the app. Those are two different values. The app sees SERVER_INFO's `(2,1,2)` and PLUGIN_VERSION's plugin value.
- 16-alignment (AGENTS.md "Resolution is aligned to multiples of 16"): not required by `vc_core` (section 6.3). Recommend the main agent re-check whether any other part of the pipeline needs it before removing it. Do not change AGENTS.md from this worker.
- The request payload for START (`<ffH??`, 12 bytes) must be sent with the token, or the session blocks. The bridge must pass the 12-byte payload.
- `get_capture_buffer` must return a writable buffer (the bridge's ctypes array is fine; `bytes` is not).
- Camera state is per connection. A reconnect clears the current camera and the scene list must be requested again (token 2) before token 52.
- `autosend=1` is required for viewport frames. `autosend=0` pulls can hang the session.
- `set_capture_mode` must be called in `capture_will_start`. Use CAPFORMAT_UBYTE_BGRA (3) or CAPFORMAT_UBYTE_RGBA (2); avoid RGB and BGR (6.4). Avoid SCREENSHOT mode, which returns error 204 (5.4).
- Mid-stream size changes restart the encoder and emit a keyframe. The bridge should expect one keyframe per resize and should not change size faster than the encoder can restart (6.3).
- Pacing is internal and follows START's fps.

## Appendix: reproduction

- Harness and experiments: `data/re/harness/` (gitignored). `common.py` now points `BASE` at the RE copy `data/virtucamera_blender_original`. It forces `bind("0.0.0.0")` to `127.0.0.1` in a socket subclass. Run with `PYTHONDONTWRITEBYTECODE=1 py -3.9`.
  - Layout proofs: `exp17_keys_layout.py`, `exp18_keys_proof.py` (blocks A, B, C), `exp19_isolate.py`, `exp20_trace_block.py`.
  - START and child data: `exp22_start.py`, `exp23_child_tuple.py`, `exp24_child_runs.py`, `summarize_child.py`, `exp25_opaque_bytes.py`, `exp37_opaque_rgba.py`, `exp26_child_av.py`, `exp36_u16_gop.py`.
  - Filter, orientation, resize: `exp30_resize_flip.py`, `exp34_clamp_start.py`, `exp35_clamp_rule.py`, `exp33_clamp.py`, `exp28_fmt_sweep.py`.
  - Errors and tokens: `exp27_errors.py` (203, 154, SCRIPT_INFO push, bad python_executable), `exp29_154_unknown.py`, `exp31_204.py`.
  - Timeouts and lifetime: `exp32_timeouts.py`, `exp38_child_lifetime.py`, `child_list.py`.
  - Child-process probe: `hook/sitecustomize.py` (loaded through the parent's `sys.path`, which the parent copies into the child's `PYTHONPATH`). It logs pickle payloads, `av.open`/`add_stream`/stream sets, filter-graph `add` calls, and socket/thread/queue timeouts. It is RE-only and inert without `VC_HOOK_LOG`.
- Scratch decompiles and raw outputs: session scratchpad `re2/` (not in the repo): `session_loop.c`, `recv_pyobj_224.c` (token 59 helper), `flen_helper_216.c` (token 60 helper), `start_streaming_18002A1A0.c`, `set_capture_resolution.c`, `send_server_info.c`, `enc_options.c`, `all_strings.txt`.
- IDB: `data/re/vc_core.cp39-win_amd64.pyd.i64` (renames only; the copy `data/re/vc_core.cp39-win_amd64.pyd` is byte-identical to the original).
- Addresses above are for the unmodified binary (image base `0x180000000`).
