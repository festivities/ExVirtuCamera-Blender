# PyVirtuCamera - Python 3.13 Bridge Wrapper
# Wraps the Python 3.9 vc_core.pyd via a local bridge subprocess.
#
# Based on PyVirtuCamera
# Bridge wrapper implementation for Blender 5.1 compatibility.

import os
import sys
import json
import struct
import socket
import subprocess
import threading
import time
import tempfile
import traceback
import select

__all__ = ("VCServer",)

_HEADER_FMT = "!I"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)


def _send_msg(sock, payload):
    data = json.dumps(payload).encode("utf-8")
    sock.sendall(struct.pack(_HEADER_FMT, len(data)) + data)


def _recv_msg(sock):
    header = b""
    while len(header) < _HEADER_SIZE:
        chunk = sock.recv(_HEADER_SIZE - len(header))
        if not chunk:
            raise ConnectionError("Bridge connection closed")
        header += chunk
    (length,) = struct.unpack(_HEADER_FMT, header)
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError("Bridge connection closed")
        data += chunk
    return json.loads(data.decode("utf-8"))


class VCServer:

    SERVER_VERSION = (1, 1, 0)

    EVENTMODE_PUSH = 0
    EVENTMODE_PULL = 1

    CAPMODE_SCREENSHOT = 0
    CAPMODE_BUFFER = 1
    CAPMODE_BUFFER_POINTER = 2

    CAPFORMAT_UBYTE_RGB = 0
    CAPFORMAT_UBYTE_BGR = 1
    CAPFORMAT_UBYTE_RGBA = 2
    CAPFORMAT_UBYTE_BGRA = 3

    def __init__(self, platform, plugin_version, vcbase, event_mode=1,
                 main_thread_func=None, python_executable=None):
        self._vcbase = vcbase
        self._event_mode = event_mode
        self._platform = platform
        self._plugin_version = plugin_version

        self._is_serving = False
        self._is_connected = False
        self._is_event_loop_running = False
        self._is_capturing = False
        self._is_stopping = False
        self._client_ip = ""
        self._client_port = 0
        self._current_camera = ""
        self._capture_width = 0
        self._capture_height = 0
        self._capture_mode = 0
        self._capture_format = 0
        self._use_vflip = False
        self._server_port = 0
        self._shm_name = None

        self._cmd_sock = None
        self._cb_sock = None
        self._proc = None
        self._reader_thread = None
        self._callback_queue = []
        self._callback_lock = threading.Lock()
        self._cmd_lock = threading.Lock()
        self._python_executable = python_executable

        self._start_bridge(platform, plugin_version, main_thread_func)

    def _start_bridge(self, platform, plugin_version, main_thread_func):
        cb_serv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cb_serv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        cb_serv.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        cb_serv.bind(("127.0.0.1", 0))
        cb_port = cb_serv.getsockname()[1]
        cb_serv.listen(1)
        cb_serv.settimeout(15.0)

        py_exe = self._python_executable
        if py_exe:
            py_args = [py_exe]
        else:
            py_args = self._find_python39()
        if not py_args:
            raise RuntimeError(
                "Python 3.9 executable not found. "
                "Set python_executable in VCServer constructor or "
                "configure it in the addon preferences."
            )

        addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        
        # Scrub Blender's python paths to prevent SRE module mismatches
        if "PYTHONHOME" in env:
            del env["PYTHONHOME"]
        
        # Only keep our addon dir in PYTHONPATH
        env["PYTHONPATH"] = addon_dir

        self._proc = subprocess.Popen(
            py_args + ["-m", "virtucamera.vc_bridge_server",
                       str(cb_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        try:
            self._cb_sock, _ = cb_serv.accept()
            self._cb_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except socket.timeout:
            cb_serv.close()
            stderr = b""
            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read()
            raise RuntimeError(
                "Bridge server did not connect in time"
                + (": " + stderr.decode().strip().splitlines()[-1]
                   if stderr else "")
            )

        try:
            ready = _recv_msg(self._cb_sock)
            if ready.get("type") != "ready":
                raise RuntimeError("Unexpected handshake from bridge")
            _send_msg(self._cb_sock, {"type": "ack"})
        except Exception:
            cb_serv.close()
            raise

        cb_serv.close()

        self._cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._cmd_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._cmd_sock.connect(("127.0.0.1", ready["cmd_port"]))
        self._cmd_sock.settimeout(30.0)

        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True
        )
        self._reader_thread.start()

    def _find_python39(self):
        import shutil
        import subprocess

        candidates = [
            [sys.executable.replace("python313", "python39")],
            [sys.executable.replace("python3.13", "python3.9")],
        ]
        if os.name == "nt":
            base = os.path.dirname(sys.executable)
            candidates.extend([
                [os.path.join(base, "python3.9.exe")],
                [os.path.join(base, "..", "python39", "python.exe")],
                [r"C:\Python39\python.exe"],
                [os.path.expandvars(
                    r"%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
                )],
                [os.path.expandvars(
                    r"%LOCALAPPDATA%\Python\bin\python3.9.EXE"
                )],
            ])
        py_launcher = shutil.which("py")
        if py_launcher:
            candidates.insert(0, [py_launcher, "-3.9"])

        for args in candidates:
            path = args[0]
            if not path or not os.path.isfile(path):
                continue
            try:
                proc = subprocess.run(
                    args + ["--version"],
                    capture_output=True, text=True, timeout=5,
                )
                if "3.9" in proc.stdout:
                    return args
            except Exception:
                continue
        return None

    def _reader_loop(self):
        try:
            while True:
                msg = _recv_msg(self._cb_sock)
                mtype = msg.get("type")
                if mtype == "callback":
                    with self._callback_lock:
                        self._callback_queue.append(msg)
                elif mtype == "properties":
                    self._is_serving = msg.get("is_serving", self._is_serving)
                    self._is_connected = msg.get(
                        "is_connected", self._is_connected
                    )
                    self._is_event_loop_running = msg.get(
                        "is_event_loop_running", self._is_event_loop_running
                    )
                    self._is_capturing = msg.get(
                        "is_capturing", self._is_capturing
                    )
                    self._is_stopping = msg.get(
                        "is_stopping", self._is_stopping
                    )
                    self._client_ip = msg.get("client_ip", self._client_ip)
                    self._client_port = msg.get(
                        "client_port", self._client_port
                    )
                    self._current_camera = msg.get(
                        "current_camera", self._current_camera
                    )
                    self._capture_width = msg.get(
                        "capture_width", self._capture_width
                    )
                    self._capture_height = msg.get(
                        "capture_height", self._capture_height
                    )
                    self._capture_mode = msg.get(
                        "capture_mode", self._capture_mode
                    )
                    self._capture_format = msg.get(
                        "capture_format", self._capture_format
                    )
                    self._use_vflip = msg.get(
                        "use_vflip", self._use_vflip
                    )
                    self._server_port = msg.get(
                        "server_port", self._server_port
                    )
        except (ConnectionError, OSError):
            self._is_event_loop_running = False

    def _send_cmd(self, cmd, **kwargs):
        with self._cmd_lock:
            payload = {"cmd": cmd}
            payload.update(kwargs)
            try:
                _send_msg(self._cmd_sock, payload)
                resp = _recv_msg(self._cmd_sock)
                if "error" in resp:
                    raise RuntimeError(resp["error"])
                return resp
            except (ConnectionError, OSError):
                self._is_event_loop_running = False
                raise

    def _process_callback(self, msg):
        cb_name = msg.get("cb")
        cb_id = msg.get("id")
        args = msg.get("args", [])
        kwargs = msg.get("kwargs", {})
        result = None
        error = None
        try:
            if cb_name in ("get_capture_buffer", "get_capture_pointer"):
                result = None
            else:
                method = getattr(self._vcbase, cb_name)
                result = method(self, *args, **kwargs)
        except Exception:
            error = traceback.format_exc()
        try:
            _send_msg(self._cb_sock, {
                "type": "callback_response",
                "id": cb_id,
                "result": result,
                "error": error,
            })
        except (ConnectionError, OSError):
            pass

    # Public API

    def _apply_props(self, msg):
        self._is_serving = msg.get("is_serving", self._is_serving)
        self._is_connected = msg.get("is_connected", self._is_connected)
        self._is_event_loop_running = msg.get(
            "is_event_loop_running", self._is_event_loop_running
        )
        self._is_capturing = msg.get("is_capturing", self._is_capturing)
        self._is_stopping = msg.get("is_stopping", self._is_stopping)
        self._client_ip = msg.get("client_ip", self._client_ip)
        self._client_port = msg.get("client_port", self._client_port)
        self._current_camera = msg.get(
            "current_camera", self._current_camera
        )
        self._capture_width = msg.get("capture_width", self._capture_width)
        self._capture_height = msg.get(
            "capture_height", self._capture_height
        )
        self._capture_mode = msg.get("capture_mode", self._capture_mode)
        self._capture_format = msg.get(
            "capture_format", self._capture_format
        )
        self._use_vflip = msg.get("use_vflip", self._use_vflip)
        self._server_port = msg.get("server_port", self._server_port)

    def execute_pending_events(self):
        try:
            resp = self._send_cmd("exec_events")
            self._apply_props(resp)
        except (ConnectionError, OSError, RuntimeError):
            self._is_event_loop_running = False

        with self._callback_lock:
            queued = list(self._callback_queue)
            self._callback_queue.clear()

        for msg in queued:
            self._process_callback(msg)

    def start_serving(self, port):
        try:
            resp = self._send_cmd("start_serving", port=port)
            self._is_serving = resp.get("ok", False)
        except (ConnectionError, OSError, RuntimeError):
            self._is_serving = False

    def stop_serving(self):
        try:
            self._send_cmd("stop_serving")
        except (ConnectionError, OSError, RuntimeError):
            pass
        self._is_serving = False

    @property
    def bridge_alive(self):
        """True if the bridge subprocess and reader thread are both running."""
        return (
            self._proc is not None
            and self._proc.poll() is None
            and self._reader_thread is not None
            and self._reader_thread.is_alive()
        )

    def restart_bridge(self):
        """Kill the crashed bridge and start a fresh one, resetting all state."""
        # Close sockets
        for sock in (self._cb_sock, self._cmd_sock):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self._cb_sock = None
        self._cmd_sock = None
        # Kill old process
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except Exception:
                pass
        self._proc = None
        self._reader_thread = None
        # Reset all state flags
        self._is_serving = False
        self._is_connected = False
        self._is_event_loop_running = False
        self._is_capturing = False
        self._is_stopping = False
        self._client_ip = ""
        self._client_port = 0
        self._current_camera = ""
        with self._callback_lock:
            self._callback_queue.clear()
        # Start a fresh bridge subprocess
        self._start_bridge(self._platform, self._plugin_version, None)

    def set_capture_resolution(self, width, height):
        self._capture_width = width
        self._capture_height = height
        try:
            self._send_cmd(
                "set_capture_resolution", width=width, height=height
            )
        except (ConnectionError, OSError, RuntimeError):
            pass

    def set_capture_mode(self, mode, pixel_format):
        self._capture_mode = mode
        self._capture_format = pixel_format
        try:
            self._send_cmd(
                "set_capture_mode", mode=mode, pixel_format=pixel_format
            )
        except (ConnectionError, OSError, RuntimeError):
            pass

    def set_vertical_flip(self, flip):
        self._use_vflip = flip
        try:
            self._send_cmd("set_vertical_flip", flip=flip)
        except (ConnectionError, OSError, RuntimeError):
            pass

    def update_script_labels(self):
        try:
            self._send_cmd("update_script_labels")
        except (ConnectionError, OSError, RuntimeError):
            pass

    def write_qr_image_png(self, filepath, box_size=3):
        abs_path = os.path.abspath(filepath)
        try:
            self._send_cmd(
                "write_qr_image_png",
                filepath=abs_path,
                box_size=box_size,
            )
        except (ConnectionError, OSError, RuntimeError):
            pass

    def get_shm_name(self):
        return self._shm_name

    def set_shm_name(self, name):
        self._shm_name = name
        try:
            self._send_cmd("set_shm_name", shm_name=name)
        except (ConnectionError, OSError, RuntimeError):
            pass

    # Properties

    @property
    def is_serving(self):
        return self._is_serving

    @property
    def is_connected(self):
        return self._is_connected

    @property
    def is_event_loop_running(self):
        return self._is_event_loop_running

    @property
    def is_capturing(self):
        return self._is_capturing

    @property
    def is_stopping(self):
        return self._is_stopping

    @property
    def client_ip(self):
        return self._client_ip

    @property
    def client_port(self):
        return self._client_port

    @property
    def current_camera(self):
        return self._current_camera

    @property
    def capture_width(self):
        return self._capture_width

    @property
    def capture_height(self):
        return self._capture_height

    @property
    def capture_mode(self):
        return self._capture_mode

    @property
    def capture_format(self):
        return self._capture_format

    @property
    def use_vflip(self):
        return self._use_vflip

    @property
    def server_port(self):
        return self._server_port
