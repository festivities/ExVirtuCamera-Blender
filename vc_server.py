"""VirtuCamera 1 server in pure Python: TCP protocol and VP9 video.

No bpy import. Protocol reference: docs/vc_core_protocol.md (section numbers
below refer to it). Host callbacks run on the main thread through
execute_pending_events(). The session and encoder threads never call the host
directly. Behavior the spec leaves open was checked against the original
binary with tests/regen_conformance_fixture.py.
"""
import os
import queue
import socket
import struct
import threading
import time
import traceback
from fractions import Fraction

import numpy as np

BIND_HOST = "0.0.0.0"  # spec 4. Tests set 127.0.0.1 to stay on loopback.
SERVER_VERSION = (2, 1, 2)  # spec 2 and 5.3 (token 0)
ERR_MISSING_CAMERA = 0xC8  # spec 5.4 (200)
ERR_NOT_STREAMING = 0xC9  # spec 5.4 (201)
ERR_EXECUTE_SCRIPT = 0xCB  # spec 5.4 (203). Name inferred; observed as the EXECUTE_SCRIPT False reply.
ACK_EXECUTE_SCRIPT = 0x9D  # spec 5.3 token 157 echo
JOIN_TIMEOUT = 3.0
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


class _End(Exception):
    """Ends the session: client closed, stop requested, or protocol desync."""


class _Call(object):
    """One host call. Getters wait on `done`. Setters never wait."""

    __slots__ = ("name", "args", "done", "ok", "result")

    def __init__(self, name, args):
        self.name = name
        self.args = args
        self.done = threading.Event()
        self.ok = False
        self.result = None


def _var(payload):
    """Variable-length tail: u32 LE byte count, then the payload (spec 5.1)."""
    return struct.pack("<I", len(payload)) + payload


class VCServer(object):
    SERVER_VERSION = SERVER_VERSION

    def __init__(self, host, plugin_version=(2, 0, 0), platform="Blender"):
        self._host = host
        self._plugin_version = tuple(plugin_version)
        self._platform = platform
        self._calls = queue.Queue()  # host calls, drained by execute_pending_events()
        self._send_lock = threading.Lock()  # one writer: session replies and video packets
        self._frame_lock = threading.Lock()
        self._latest = None  # (rgba, width, height) from submit_frame
        self._stop_evt = threading.Event()
        self._listener = None
        self._port = None
        self._accept_thread = None
        self._conn = None
        self._client_ip = None
        self._cam = ""  # per connection (spec 5.3, token 52)
        self._scene_cameras = []  # from token 2, indexed by token 52. Kept across connections (observed).
        self._enc_thread = None
        self._enc_stop = None
        self._capture_fps = 0.0

    # ---- public API -------------------------------------------------------

    @property
    def is_serving(self):
        return self._listener is not None

    @property
    def is_connected(self):
        return self._conn is not None

    @property
    def is_capturing(self):
        return self._enc_thread is not None

    @property
    def client_ip(self):
        return self._client_ip

    @property
    def current_camera(self):
        return self._cam

    @property
    def capture_fps(self):
        return self._capture_fps

    def start_serving(self, port):
        if self._listener is not None:
            return False
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((BIND_HOST, port))
            listener.listen(1)
        except OSError as e:
            listener.close()
            print("VirtuCamera: can't start server on port %d: %s" % (port, e))
            return False
        listener.settimeout(0.5)  # accept() wakes up to check the stop flag
        self._stop_evt = threading.Event()
        self._port = port
        self._listener = listener
        self._accept_thread = threading.Thread(target=self._accept_loop, args=(listener,), daemon=True)
        self._accept_thread.start()
        print("VirtuCamera: listening on port %d" % port)
        return True

    def stop_serving(self):
        listener, thread = self._listener, self._accept_thread
        if listener is None:
            return
        self._stop_evt.set()
        conn = self._conn
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if thread is not None:
            thread.join(JOIN_TIMEOUT)
        self._end_capture()
        listener.close()
        self._listener = None
        self._accept_thread = None
        self._port = None
        self._post("server_did_stop")

    def execute_pending_events(self):
        """Main thread only. Runs queued host calls in the order they were queued."""
        while True:
            try:
                call = self._calls.get_nowait()
            except queue.Empty:
                return
            try:
                call.result = getattr(self._host, call.name)(*call.args)
                call.ok = True
            except Exception:
                traceback.print_exc()
            call.done.set()

    def submit_frame(self, rgba, width, height):
        """Main thread. rgba: uint8 buffer of width*height*4 bytes, rows bottom-up. Copied."""
        frame = np.frombuffer(rgba, dtype=np.uint8, count=width * height * 4)
        frame = frame.reshape(height, width, 4).copy()
        with self._frame_lock:
            self._latest = (frame, width, height)

    def update_script_labels(self):
        """Main thread only. Pushes SCRIPT_INFO (token 7, spec 7.4) to a connected app.

        Calls the host directly because this already runs on the main thread.
        """
        if self._conn is None:
            return
        try:
            labels = self._host.get_script_labels()
        except Exception:
            traceback.print_exc()
            return
        body = "%".join(labels).encode("utf-8")
        try:
            self._send(bytes([7]) + _var(body))
        except OSError:
            pass

    def qr_string(self):
        """'<port>_<ip1>_<ip2>...' with IPv4 addresses from the local host name."""
        if self._port is None:
            raise RuntimeError("VCServer has not started serving")
        try:
            infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        except OSError:
            infos = []
        ips = []
        for info in infos:
            ip = info[4][0]
            if ip.startswith(("127.", "169.254.")) or ip == "0.0.0.0" or ip in ips:
                continue
            ips.append(ip)
        return "_".join([str(self._port)] + ips)

    # ---- accept and session (session thread) ------------------------------

    def _accept_loop(self, listener):
        while not self._stop_evt.is_set():
            try:
                conn, addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self._session(conn, addr)  # one client at a time

    def _session(self, conn, addr):
        conn.settimeout(0.5)  # recv wakes up to check the stop flag
        with self._send_lock:
            self._conn = conn
        self._client_ip = addr[0]
        self._cam = ""
        self._post("client_connected", addr[0], addr[1])
        try:
            while True:
                tok = self._recv(1)[0]
                handler = _HANDLERS.get(tok)
                if handler is None:
                    continue  # unknown bytes are skipped silently, as the original does (spec 5.2)
                if tok in _ECHO_TOKENS:
                    self._send(bytes([tok]))
                handler(self, tok)
        except (_End, OSError) as e:
            print("VirtuCamera: session closed: %s" % e)
        except Exception:
            traceback.print_exc()
        finally:
            self._end_capture()
            with self._send_lock:
                self._conn = None
            self._client_ip = None
            self._cam = ""
            self._post("current_camera_changed", "")
            self._post("client_disconnected")
            conn.close()

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            if self._stop_evt.is_set():
                raise _End("stop requested")
            try:
                chunk = self._conn.recv(n - len(buf))
            except socket.timeout:
                continue
            if not chunk:
                raise _End("client closed")
            buf += chunk
        return buf

    def _unpack(self, fmt):
        return struct.unpack(fmt, self._recv(struct.calcsize(fmt)))

    def _send(self, data):
        view = memoryview(data)
        with self._send_lock:
            conn = self._conn
            if conn is None:
                raise OSError("no client")
            while view:
                try:
                    view = view[conn.send(view):]
                except socket.timeout:
                    if self._stop_evt.is_set():
                        raise OSError("stop requested")

    def _post(self, name, *args):
        """Queue a host setter. Fire-and-forget, FIFO."""
        self._calls.put(_Call(name, args))

    def _ask(self, name, fallback, *args):
        """Queue a host getter and block until the main thread answers.

        A host exception gives `fallback`. Stop requests interrupt the wait.
        """
        call = _Call(name, args)
        self._calls.put(call)
        while not call.done.wait(0.05):
            if self._stop_evt.is_set():
                raise _End("stop requested")
        return call.result if call.ok else fallback

    def _camera(self):
        """Selected camera that exists, or None after replying C8 (spec 5.3, checked against the original).

        Order matches the original: no selection gives C8 with no host call. Otherwise
        get_camera_exists is asked first.
        """
        if not self._cam:
            self._send(bytes([ERR_MISSING_CAMERA]))
            return None
        if not self._ask("get_camera_exists", False, self._cam):
            self._send(bytes([ERR_MISSING_CAMERA]))
            return None
        return self._cam

    # ---- ASK handlers (tokens 0-8). No echo: replies start with the token. --

    def _server_info_body(self):
        computer = os.environ.get("COMPUTERNAME") or socket.gethostname()  # spec 5.3, token 0
        text = "%s_%s - %s" % (self._platform, computer, self._platform)
        return struct.pack("<3H", *SERVER_VERSION) + text.encode("utf-8")

    def _ask_server_info(self, tok):
        self._send(bytes([tok]) + _var(self._server_info_body()))

    def _ask_scene_status(self, tok):
        cam = self._camera()
        if cam is None:
            return
        frame, start, end = self._ask("get_playback_state", (0.0, 0.0, 0.0))
        focal = self._ask("get_camera_focal_length", 0.0, cam)
        self._send(bytes([tok]) + struct.pack("<4d", frame, start, end, focal))

    def _ask_scene_cameras(self, tok):
        names = list(self._ask("get_scene_cameras", []))
        self._scene_cameras = names
        index = names.index(self._cam) if self._cam in names else -1
        body = struct.pack("<h", index) + "$".join(names).encode("utf-8")
        self._send(bytes([tok]) + _var(body))

    def _ask_camera_matrix(self, tok):
        cam = self._camera()
        if cam is None:
            return
        matrix = self._ask("get_camera_transform", IDENTITY, cam)
        self._send(bytes([tok]) + struct.pack("<16d", *matrix))

    def _ask_viewport_img(self, tok):
        # While streaming, the encoder's autosend packets are the reply (spec 6.2).
        # ponytail: no pull mode. autosend=0 never sends frames, the original hangs instead (spec 6.2).
        if not self.is_capturing:
            self._send(bytes([ERR_NOT_STREAMING]))

    def _ask_play_fps(self, tok):
        fps = self._ask("get_playback_fps", 0.0)
        self._send(bytes([tok]) + struct.pack("<d", fps))

    def _ask_camera_has_keys(self, tok):
        cam = self._camera()
        if cam is None:
            return
        transform_keys, flen_keys = self._ask("get_camera_has_keys", (False, False), cam)
        # spec 5.3 token 6: bit0 any keys, bit1 transform keys, bit2 focal length keys
        flags = (1 if (transform_keys or flen_keys) else 0) | (2 if transform_keys else 0) | (4 if flen_keys else 0)
        self._send(bytes([tok, flags]))

    def _ask_script_info(self, tok):
        labels = self._ask("get_script_labels", [])
        self._send(bytes([tok]) + _var("%".join(labels).encode("utf-8")))

    def _ask_plugin_version(self, tok):
        self._send(bytes([tok]) + struct.pack("<3H", *self._plugin_version))

    # ---- SET handlers (tokens 50-60). Echo sent first, then the payload. ----

    def _set_playback_range(self, tok):
        start, end = self._unpack("<2d")
        self._post("set_playback_range", start, end)

    def _set_current_time(self, tok):
        (frame,) = self._unpack("<d")
        self._post("set_frame", frame)

    def _set_current_camera(self, tok):
        (index,) = self._unpack("<H")
        # Needs a SCENE_CAMERAS list. An index past the list is ignored here (the original's pump dies).
        if index >= len(self._scene_cameras):
            return
        name = self._scene_cameras[index]
        if not self._ask("get_camera_exists", False, name):
            self._send(bytes([ERR_MISSING_CAMERA]))
            return
        self._cam = name
        self._post("current_camera_changed", name)

    def _set_camera_matrix(self, tok):
        matrix = self._unpack("<16d")
        cam = self._camera()
        if cam is not None:
            self._post("set_camera_transform", cam, matrix)

    def _set_camera_matrix_at_time(self, tok):
        vals = self._unpack("<17d")
        cam = self._camera()
        if cam is not None:
            self._post("set_camera_transform", cam, vals[:16])
            self._post("set_frame", vals[16])

    def _set_focal_length(self, tok):
        (focal,) = self._unpack("<d")
        cam = self._camera()
        if cam is not None:
            self._post("set_camera_focal_length", cam, focal)

    def _set_focal_length_at_time(self, tok):
        focal, frame = self._unpack("<2d")
        cam = self._camera()
        if cam is not None:
            self._post("set_camera_focal_length", cam, focal)
            self._post("set_frame", frame)

    def _set_camera_all(self, tok):
        vals = self._unpack("<17d")
        cam = self._camera()
        if cam is not None:
            self._post("set_camera_transform", cam, vals[:16])
            self._post("set_camera_focal_length", cam, vals[16])

    def _set_camera_all_at_time(self, tok):
        vals = self._unpack("<18d")
        cam = self._camera()
        if cam is not None:
            self._post("set_camera_transform", cam, vals[:16])
            self._post("set_camera_focal_length", cam, vals[16])
            self._post("set_frame", vals[17])

    def _set_camera_transform_keys(self, tok):
        # Spec 7.3: u16 n, then n records of [m0..m15, frame] (17 doubles each).
        (n,) = self._unpack("<H")
        if not n:
            return  # n=0 is a no-op with no host call (the original crashes here, spec 7.3)
        vals = self._unpack("<%dd" % (17 * n))
        frames = list(vals[16::17])
        matrices = [vals[i:i + 16] for i in range(0, 17 * n, 17)]
        cam = self._camera()
        if cam is not None:
            self._post("set_camera_transform_keys", cam, frames, matrices)

    def _set_camera_flen_keys(self, tok):
        # Spec 7.3: u16 n, then n records of [focal, frame] (2 doubles each).
        (n,) = self._unpack("<H")
        if not n:
            return  # n=0 is a no-op with no host call (the original crashes here, spec 7.3)
        vals = self._unpack("<%dd" % (2 * n))
        cam = self._camera()
        if cam is not None:
            self._post("set_camera_flen_keys", cam, list(vals[1::2]), list(vals[0::2]))

    # ---- DO handlers (tokens 150-157) -------------------------------------

    def _do_start_streaming(self, tok):
        # u16 and opaque: meaning unknown (spec 6.4, OPEN). Read and ignored.
        fps, mbits, _u16, _opaque, autosend = self._unpack("<ffH??")
        # Untrusted floats: 0, NaN or inf would kill the encoder thread (1/fps, int(bit_rate)).
        fps = fps if 1.0 <= fps <= 240.0 else 30.0
        mbits = mbits if 0.01 <= mbits <= 1000.0 else 10.0
        cam = self._camera()
        if cam is not None:
            self._post("capture_will_start")
            self._post("look_through_camera", cam)
            self._start_capture(fps, mbits, autosend)

    def _do_stop_streaming(self, tok):
        self._end_capture()

    def _do_start_playback(self, tok):
        (forward,) = self._unpack("<?")
        self._post("start_playback", forward)

    def _do_stop_playback(self, tok):
        self._post("stop_playback")

    def _do_noop(self, tok):
        # Token 154 is a no-op: no payload read, no reply (spec 5.3, verified). Only the echo is skipped.
        pass

    def _do_remove_camera_keys(self, tok):
        cam = self._camera()
        if cam is not None:
            self._post("remove_camera_keys", cam)

    def _do_create_new_camera(self, tok):
        # Copies the current camera's transform and focal length to the new camera (spec 7.3).
        # Observed: the new camera becomes the current camera with no callback. Uses '' when none.
        cam = self._cam
        matrix = self._ask("get_camera_transform", IDENTITY, cam)
        focal = self._ask("get_camera_focal_length", 0.0, cam)
        new = self._ask("create_new_camera", None)
        if new:
            self._post("set_camera_transform", new, matrix)
            self._post("set_camera_focal_length", new, focal)
            self._cam = new

    def _do_execute_script(self, tok):
        # No echo. Reply is 9D <index> on success, CB <index> when the host returns False.
        (index,) = self._unpack("<B")
        ok = self._ask("execute_script", False, index, self._cam)
        self._send(bytes([ACK_EXECUTE_SCRIPT if ok else ERR_EXECUTE_SCRIPT, index]))

    # ---- video (encoder thread) -------------------------------------------

    def _start_capture(self, fps, mbits, autosend):
        self._end_capture()
        self._capture_fps = fps
        self._enc_stop = threading.Event()
        self._enc_thread = threading.Thread(
            target=self._encode_loop, args=(self._enc_stop, fps, mbits, autosend), daemon=True)
        self._enc_thread.start()

    def _end_capture(self):
        thread = self._enc_thread
        if thread is None:
            return
        self._enc_thread = None
        self._enc_stop.set()
        thread.join(JOIN_TIMEOUT)
        self._post("capture_did_end")

    def _encode_loop(self, stop, fps, mbits, autosend):
        try:
            import av  # lazy: the module imports without PyAV (tests and no-video runs)
        except ImportError as e:
            print("VirtuCamera: PyAV missing, video disabled: %s" % e)
            return
        period = 1.0 / fps
        next_t = time.perf_counter()
        ctx = None
        size = None
        pts = 0
        try:
            while not stop.is_set():
                next_t += period  # no drift: each tick is one period after the previous tick
                with self._frame_lock:
                    latest = self._latest
                if latest is not None and autosend:
                    rgba, w, h = latest
                    if size != (w, h):  # size change: reopen the codec, first packet is a keyframe
                        ctx = _open_vp9(av, w, h, fps, mbits)
                        size, pts = (w, h), 0
                    frame = av.VideoFrame.from_ndarray(rgba[::-1], format="rgba")  # bottom-up input
                    frame = frame.reformat(format="yuv420p")
                    frame.pts = pts
                    pts += 1
                    for packet in ctx.encode(frame):
                        self._send(b"\x04" + _var(bytes(packet)))  # spec 6.1 frame packet
                delay = next_t - time.perf_counter()
                if delay > 0:
                    stop.wait(delay)
                else:
                    next_t = time.perf_counter()  # fell behind: resync, do not burst
        except OSError:
            pass  # client went away
        except Exception:
            traceback.print_exc()


def _open_vp9(av, width, height, fps, mbits):
    """libvpx-vp9 CBR settings from spec 6.4 and docs/encoder_spike.md (3a, 3c)."""
    rate = int(round(fps))
    bit_rate = int(mbits * 1e6)
    ctx = av.CodecContext.create("libvpx-vp9", "w")
    ctx.width, ctx.height = width, height
    ctx.pix_fmt = "yuv420p"
    ctx.time_base = Fraction(1, rate)
    ctx.framerate = rate
    ctx.bit_rate = bit_rate
    ctx.options = {
        "quality": "realtime",
        "speed": "15",
        "lag-in-frames": "0",
        "row-mt": "1",
        "tile-columns": "2",
        "tile-rows": "2",
        "frame-parallel": "1",
        "undershoot-pct": "95",
        "minrate": str(bit_rate),  # min == max == target: libvpx CBR (spike 3c)
        "maxrate": str(bit_rate),
    }
    ctx.open()
    return ctx


# Tokens that echo the request byte before the payload. Token 154 is a no-op with no echo,
# and token 157 replies with its result instead (spec 5.3).
_ECHO_TOKENS = set(range(50, 61)) | {150, 151, 152, 153, 155, 156}

_HANDLERS = {
    0: VCServer._ask_server_info,
    1: VCServer._ask_scene_status,
    2: VCServer._ask_scene_cameras,
    3: VCServer._ask_camera_matrix,
    4: VCServer._ask_viewport_img,
    5: VCServer._ask_play_fps,
    6: VCServer._ask_camera_has_keys,
    7: VCServer._ask_script_info,
    8: VCServer._ask_plugin_version,
    50: VCServer._set_playback_range,
    51: VCServer._set_current_time,
    52: VCServer._set_current_camera,
    53: VCServer._set_camera_matrix,
    54: VCServer._set_camera_matrix_at_time,
    55: VCServer._set_focal_length,
    56: VCServer._set_focal_length_at_time,
    57: VCServer._set_camera_all,
    58: VCServer._set_camera_all_at_time,
    59: VCServer._set_camera_transform_keys,
    60: VCServer._set_camera_flen_keys,
    150: VCServer._do_start_streaming,
    151: VCServer._do_stop_streaming,
    152: VCServer._do_start_playback,
    153: VCServer._do_stop_playback,
    154: VCServer._do_noop,
    155: VCServer._do_remove_camera_keys,
    156: VCServer._do_create_new_camera,
    157: VCServer._do_execute_script,
}
