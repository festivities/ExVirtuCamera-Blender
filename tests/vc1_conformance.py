"""Wire-level conformance script, shared by tests/test_vc_server.py and
tests/regen_conformance_fixture.py.

The second runner drives the original vc_core binary under py -3.9, so this
module must stay Python 3.9 compatible (no match statements, no X | Y hints).
It must not import vc_server or numpy.
"""
import os
import socket
import struct
import threading
import time

PLATFORM = "Blender"
PLUGIN_VERSION = (2, 0, 0)
HOST_NAME = "TESTHOST"  # the servers report this as the computer name (SERVER_INFO)
BARRIER_REQ = b"\x05"  # ASK PLAY_FPS. It is a getter, so its reply proves queued host calls ran.
BARRIER_FPS = 24.0
BARRIER_REPLY = b"\x05" + struct.pack("<d", BARRIER_FPS)
CAMERAS = ["Camera", "Cam.001", "Ghost", "Cam.002", "Cam.003"]  # "Ghost" does not exist
EXISTING = ("Camera", "Cam.001", "Cam.002", "Cam.003")
HAS_KEYS = {"Camera": (True, False), "Cam.001": (False, True), "Cam.002": (True, True)}
FOCAL = {"Camera": 35.5, "Cam.001": 50.0, "Cam.002": 85.0, "Cam.003": 24.0}
SCRIPT_LABELS = ["Alpha", "Beta"]
M = [0.5 * i for i in range(16)]  # matrix used by the SET scripts
HOST_CALLBACKS = (
    "get_playback_state", "get_playback_fps", "set_frame", "set_playback_range", "start_playback",
    "stop_playback", "get_scene_cameras", "get_camera_exists", "get_camera_has_keys",
    "get_camera_focal_length", "get_camera_transform", "set_camera_focal_length", "set_camera_transform",
    "set_camera_flen_keys", "set_camera_transform_keys", "remove_camera_keys", "create_new_camera",
    "capture_will_start", "capture_did_end", "look_through_camera", "client_connected",
    "client_disconnected", "current_camera_changed", "server_did_stop", "get_script_labels",
    "execute_script",
)


class FakeHost(object):
    """Deterministic VC1 host. Methods take no vcserver argument.

    Each callback is logged as [name, args...]. Tests drain the log with drain().
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._log = []

    def _rec(self, name, *args):
        with self._lock:
            self._log.append([name] + [_plain(a) for a in args])

    def drain(self):
        with self._lock:
            out = self._log[:]
            del self._log[:]
        return out

    def has(self, name):
        with self._lock:
            return any(entry[0] == name for entry in self._log)

    # getters
    def get_playback_state(self):
        self._rec("get_playback_state")
        return (12.0, 1.0, 250.0)

    def get_playback_fps(self):
        self._rec("get_playback_fps")
        return BARRIER_FPS

    def get_scene_cameras(self):
        self._rec("get_scene_cameras")
        return list(CAMERAS)

    def get_camera_exists(self, name):
        self._rec("get_camera_exists", name)
        return name in EXISTING

    def get_camera_has_keys(self, name):
        self._rec("get_camera_has_keys", name)
        return HAS_KEYS.get(name, (False, False))

    def get_camera_focal_length(self, name):
        self._rec("get_camera_focal_length", name)
        return FOCAL.get(name, 18.0)

    def get_camera_transform(self, name):
        self._rec("get_camera_transform", name)
        return [float(i) + 0.25 * len(name) for i in range(16)]

    def create_new_camera(self):
        self._rec("create_new_camera")
        return "Camera.New"

    def get_script_labels(self):
        self._rec("get_script_labels")
        return list(SCRIPT_LABELS)

    def execute_script(self, index, camera):
        self._rec("execute_script", index, camera)
        return index == 0  # index 1 returns False

    # setters
    def set_frame(self, frame):
        self._rec("set_frame", frame)

    def set_playback_range(self, start, end):
        self._rec("set_playback_range", start, end)

    def start_playback(self, forward):
        self._rec("start_playback", forward)

    def stop_playback(self):
        self._rec("stop_playback")

    def set_camera_focal_length(self, name, focal):
        self._rec("set_camera_focal_length", name, focal)

    def set_camera_transform(self, name, matrix):
        self._rec("set_camera_transform", name, matrix)

    def set_camera_flen_keys(self, name, keys, values):
        self._rec("set_camera_flen_keys", name, keys, values)

    def set_camera_transform_keys(self, name, keys, matrices):
        self._rec("set_camera_transform_keys", name, keys, matrices)

    def remove_camera_keys(self, name):
        self._rec("remove_camera_keys", name)

    def capture_will_start(self):
        self._rec("capture_will_start")

    def capture_did_end(self):
        self._rec("capture_did_end")

    def look_through_camera(self, name):
        self._rec("look_through_camera", name)

    def client_connected(self, ip, port):
        self._rec("client_connected")  # ip and port are per-run, so they are not logged

    def client_disconnected(self):
        self._rec("client_disconnected")

    def current_camera_changed(self, name):
        self._rec("current_camera_changed", name)

    def server_did_stop(self):
        self._rec("server_did_stop")


def _plain(value):
    """Makes a logged argument JSON-friendly: tuples become lists."""
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def req(tok, fmt="", *vals):
    """One request: the token byte, then the struct-packed payload."""
    return bytes([tok]) + (struct.pack("<" + fmt, *vals) if fmt else b"")


START = req(150, "ffH??", 30.0, 8.0, 0, False, True)
STOP = req(151)


def keys_req(tok, rec_fmt, records):
    """Tokens 59 and 60 (spec 7.3): u16 n, then n packed records."""
    body = b"".join(struct.pack("<" + rec_fmt, *rec) for rec in records)
    return bytes([tok]) + struct.pack("<H", len(records)) + body


def _step(label, data, kind="send", after=None):
    return {"label": label, "kind": kind, "send": data, "after": after}


# Request script. Labels are the fixture keys, so keep them stable.
SCRIPT = [
    _step("ASK SERVER_INFO", req(0)),
    _step("ASK PLUGIN_VERSION", req(8)),
    _step("ASK SCENE_STATUS, no camera", req(1)),
    _step("ASK CAMERA_MATRIX, no camera", req(3)),
    _step("ASK CAMERA_HAS_KEYS, no camera", req(6)),
    _step("ASK VIEWPORT_IMG, not streaming", req(4)),
    _step("ASK SCRIPT_INFO", req(7)),
    _step("SET CAMERA_MATRIX, no camera", req(53, "16d", *M)),
    _step("SET FOCAL_LENGTH, no camera", req(55, "d", 50.0)),
    _step("SET CAMERA_MATRIX_KEYS, no camera", keys_req(59, "17d", [M + [1.0]])),
    _step("SET CAMERA_FLEN_KEYS, no camera", keys_req(60, "2d", [(35.0, 1.0)])),
    _step("DO REMOVE_CAMERA_KEYS, no camera", req(155)),
    _step("DO EXECUTE_SCRIPT, no camera", req(157, "B", 0)),
    _step("DO START_STREAMING, no camera", req(150, "ffH??", 30.0, 8.0, 0, False, True)),
    _step("ASK SCENE_CAMERAS", req(2)),
    _step("SET CURRENT_CAMERA, Ghost (missing)", req(52, "H", 2)),
    _step("ASK SCENE_STATUS, after Ghost", req(1)),
    _step("SET CURRENT_CAMERA, Camera", req(52, "H", 0)),
    _step("ASK SCENE_CAMERAS, Camera selected", req(2)),
    _step("ASK SCENE_STATUS", req(1)),
    _step("ASK CAMERA_MATRIX", req(3)),
    _step("ASK CAMERA_HAS_KEYS, Camera", req(6)),
    _step("SET CURRENT_CAMERA, Cam.001", req(52, "H", 1)),
    _step("ASK CAMERA_HAS_KEYS, Cam.001", req(6)),
    _step("SET CURRENT_CAMERA, Cam.002", req(52, "H", 3)),
    _step("ASK CAMERA_HAS_KEYS, Cam.002", req(6)),
    _step("SET CURRENT_CAMERA, Cam.003", req(52, "H", 4)),
    _step("ASK CAMERA_HAS_KEYS, Cam.003", req(6)),
    _step("ASK SCENE_CAMERAS, Cam.003 selected", req(2)),
    _step("SET PLAYBACK_RANGE", req(50, "2d", 1.0, 100.0)),
    _step("SET CURRENT_TIME", req(51, "d", 42.5)),
    _step("SET CAMERA_MATRIX", req(53, "16d", *M)),
    _step("SET CAMERA_MATRIX_AT_TIME", req(54, "17d", *(M + [43.0]))),
    _step("SET FOCAL_LENGTH", req(55, "d", 50.0)),
    _step("SET FOCAL_LENGTH_AT_TIME", req(56, "2d", 35.0, 44.0)),
    _step("SET CAMERA_ALL", req(57, "17d", *(M + [28.0]))),
    _step("SET CAMERA_ALL_AT_TIME", req(58, "18d", *(M + [28.0, 45.0]))),
    _step("DO CREATE_NEW_CAMERA (new camera becomes current)", req(156)),
    _step("DO START_PLAYBACK", req(152, "?", True)),
    _step("DO STOP_PLAYBACK", req(153)),
    _step("DO EXECUTE_SCRIPT 0", req(157, "B", 0)),
    _step("DO EXECUTE_SCRIPT 1 (returns False)", req(157, "B", 1)),
    _step("DO STOP_STREAMING, not streaming", req(151)),
    _step("DO REMOVE_CAMERA_KEYS, new camera (missing)", req(155)),
    _step("SET CURRENT_CAMERA, Cam.003 again", req(52, "H", 4)),
    _step("DO REMOVE_CAMERA_KEYS, Cam.003", req(155)),
    _step("SET CAMERA_MATRIX_KEYS, 2 keys", keys_req(59, "17d", [M + [1.0], [1.5 * i for i in range(16)] + [2.0]])),
    _step("SET CAMERA_FLEN_KEYS, 3 keys", keys_req(60, "2d", [(35.0, 1.0), (50.0, 2.0), (85.0, 3.0)])),
    _step("ASK SCRIPT_INFO", req(7)),
    _step("DO token 154 (no-op, no payload)", req(154)),
    _step("DO START_STREAMING + STOP (video stripped)", START, kind="stream", after=STOP),
    _step("ASK VIEWPORT_IMG, after STOP", req(4)),
    _step("reconnect", b"", kind="reconnect"),
    _step("ASK SCENE_STATUS, new connection (camera reset)", req(1)),
    _step("SET CURRENT_CAMERA, index 0, no SCENE_CAMERAS on this connection", req(52, "H", 0)),
    _step("ASK SCENE_STATUS, list kept across connections", req(1)),
    _step("ASK SCENE_CAMERAS", req(2)),
    _step("stop_serving while connected", b"", kind="stop"),
]


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class fixed_hostname(object):
    """Pins the computer name both servers report, so the fixture carries no real machine name."""

    def __enter__(self):
        self._env = os.environ.get("COMPUTERNAME")
        self._gethostname = socket.gethostname
        os.environ["COMPUTERNAME"] = HOST_NAME
        socket.gethostname = lambda: HOST_NAME
        return self

    def __exit__(self, *exc):
        socket.gethostname = self._gethostname
        if self._env is None:
            os.environ.pop("COMPUTERNAME", None)
        else:
            os.environ["COMPUTERNAME"] = self._env
        return False


def pump(server, stop):
    """Runs host calls on this thread, as the Blender main thread would."""
    while not stop.is_set():
        server.execute_pending_events()
        time.sleep(0.002)


def _read_until_barrier(sock):
    """Reads until the barrier reply has arrived and gone quiet. Returns (bytes_without_barrier, closed)."""
    buf = b""
    closed = False
    deadline = time.time() + 15.0
    while True:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            if buf.endswith(BARRIER_REPLY) or closed:
                break
            if time.time() > deadline:
                raise RuntimeError("no barrier reply, tail=%r" % buf[-16:])
            continue
        except OSError:
            closed = True
            break
        if not chunk:
            closed = True
            break
        buf += chunk
    if buf.endswith(BARRIER_REPLY):
        buf = buf[:-len(BARRIER_REPLY)]
    return buf, closed


def _strip_video(buf):
    """Drops 04 <u32> <vp9> frame packets. Their count depends on timing."""
    out = bytearray()
    i = 0
    while i < len(buf):
        if buf[i] == 0x04 and i + 5 <= len(buf):
            (n,) = struct.unpack_from("<I", buf, i + 1)
            i += 5 + n
        else:
            out.append(buf[i])
            i += 1
    return bytes(out)


def _wait_for(host, name, timeout=5.0):
    end = time.time() + timeout
    while not host.has(name):
        if time.time() > end:
            raise RuntimeError("callback %s did not arrive" % name)
        time.sleep(0.01)


def _record(label, request, reply, host, closed=False):
    entry = {"label": label, "request": request.hex(), "reply": reply.hex(), "callbacks": host.drain()}
    if closed:
        entry["closed"] = True
    return entry


def run_script(port, host, server, script=SCRIPT):
    """Runs the script against a started server with its host calls pumped elsewhere.

    Returns the transcript: one dict per step with the request, reply hex and host callbacks.
    """
    out = []
    sock = _connect(port)
    for step in script:
        kind = step["kind"]
        if kind == "stop":
            server.stop_serving()
            _wait_for(host, "server_did_stop")
            if sock is not None:
                sock.close()
                sock = None
            out.append(_record(step["label"], b"", b"", host))
            continue
        if kind == "reconnect":
            if sock is not None:
                sock.close()
            sock = _connect(port)
            reply, closed = _read_after(sock, b"")
            out.append(_record(step["label"], b"", reply, host, closed))
            continue
        if sock is None:
            raise RuntimeError("step %r needs a connection" % step["label"])
        if kind == "stream":
            sock.sendall(step["send"])
            time.sleep(0.5)  # let frames flow before STOP
            sock.sendall(step["after"] + BARRIER_REQ)
            buf, closed = _read_until_barrier(sock)
            out.append(_record(step["label"], step["send"] + step["after"], _strip_video(buf), host, closed))
            continue
        reply, closed = _read_after(sock, step["send"])
        if closed:
            _wait_for(host, "client_disconnected")
            sock.close()
            sock = None
        out.append(_record(step["label"], step["send"], reply, host, closed))
    if sock is not None:
        sock.close()
    return out


def _connect(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.settimeout(0.05)
    return sock


def _read_after(sock, data):
    """Sends data (may be empty) followed by the barrier, then reads the reply."""
    try:
        if data:
            sock.sendall(data)
        sock.sendall(BARRIER_REQ)
    except OSError:
        return b"", True
    return _read_until_barrier(sock)
