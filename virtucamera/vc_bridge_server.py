import os
import sys
import json
import struct
import socket
import threading
import traceback

from multiprocessing.shared_memory import SharedMemory

# When run as __main__ via `python -m virtucamera.vc_bridge_server`,
# relative imports fail. Fix the package context first.
if __name__ == "__main__" and not __package__:
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _parent_dir = os.path.dirname(_this_dir)
    sys.path.insert(0, _parent_dir)
    __package__ = "virtucamera"

# Redirect all stderr/stdout to catch multiprocessing crashes
_log_file = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_sys.log"), "a", buffering=1)
sys.stdout = _log_file
sys.stderr = _log_file

try:
    import _sre
    import sre_compile
    print(f"Bridge _sre.MAGIC: {_sre.MAGIC}", flush=True)
    print(f"Bridge sre_compile.MAGIC: {sre_compile.MAGIC}", flush=True)
    import re
except Exception as e:
    print(f"Bridge RE ERROR: {e}", flush=True)

from .vc_base import VCBase

try:
    from .vc_core import VCServer as _RealVCServer
except ImportError:
    _RealVCServer = None

__all__ = ()

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
            raise ConnectionError("Connection closed")
        header += chunk
    (length,) = struct.unpack(_HEADER_FMT, header)
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data += chunk
    return json.loads(data.decode("utf-8"))


class BridgeVCBase(VCBase):
    """VCBase implementation that delegates all callbacks to the wrapper."""

    def __init__(self, cb_sock, cb_lock, bridge_server):
        self._cb_sock = cb_sock
        self._cb_lock = cb_lock
        self._server = bridge_server
        self._cb_counter = 0
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._pending_events = []

    def _invoke_callback(self, cb_name, *args, **kwargs):
        self._cb_counter += 1
        cb_id = self._cb_counter
        event = threading.Event()
        result_holder = [None, None]

        with self._pending_lock:
            self._pending[cb_id] = (event, result_holder)

        with self._cb_lock:
            _send_msg(self._cb_sock, {
                "type": "callback",
                "cb": cb_name,
                "id": cb_id,
                "args": list(args),
                "kwargs": kwargs,
            })

        event.wait(timeout=30.0)

        with self._pending_lock:
            self._pending.pop(cb_id, None)

        if result_holder[1] is not None:
            raise RuntimeError(result_holder[1])
        return result_holder[0]

    def handle_callback_response(self, msg):
        cb_id = msg.get("id")
        with self._pending_lock:
            entry = self._pending.get(cb_id)
        if entry:
            event, holder = entry
            holder[0] = msg.get("result")
            holder[1] = msg.get("error")
            event.set()

    # Playback

    def get_playback_state(self, vcserver):
        return self._invoke_callback("get_playback_state")

    def get_playback_fps(self, vcserver):
        return self._invoke_callback("get_playback_fps")

    def set_frame(self, vcserver, frame):
        self._invoke_callback("set_frame", frame)

    def set_playback_range(self, vcserver, start, end):
        self._invoke_callback("set_playback_range", start, end)

    def start_playback(self, vcserver, forward):
        self._invoke_callback("start_playback", forward)

    def stop_playback(self, vcserver):
        self._invoke_callback("stop_playback")

    # Camera

    def get_scene_cameras(self, vcserver):
        return self._invoke_callback("get_scene_cameras")

    def get_camera_exists(self, vcserver, camera_name):
        return self._invoke_callback("get_camera_exists", camera_name)

    def get_camera_has_keys(self, vcserver, camera_name):
        return self._invoke_callback("get_camera_has_keys", camera_name)

    def get_camera_focal_length(self, vcserver, camera_name):
        return self._invoke_callback("get_camera_focal_length", camera_name)

    def get_camera_transform(self, vcserver, camera_name):
        return self._invoke_callback("get_camera_transform", camera_name)

    def set_camera_focal_length(self, vcserver, camera_name, focal_length):
        self._invoke_callback(
            "set_camera_focal_length", camera_name, focal_length
        )

    def set_camera_transform(self, vcserver, camera_name, transform_matrix):
        self._invoke_callback(
            "set_camera_transform", camera_name, list(transform_matrix)
        )

    def set_camera_flen_keys(
        self, vcserver, camera_name, keyframes, focal_length_values
    ):
        self._invoke_callback(
            "set_camera_flen_keys",
            camera_name,
            list(keyframes),
            list(focal_length_values),
        )

    def set_camera_transform_keys(
        self, vcserver, camera_name, keyframes, transform_matrix_values
    ):
        self._invoke_callback(
            "set_camera_transform_keys",
            camera_name,
            list(keyframes),
            [list(m) for m in transform_matrix_values],
        )

    def remove_camera_keys(self, vcserver, camera_name):
        self._invoke_callback("remove_camera_keys", camera_name)

    def create_new_camera(self, vcserver):
        return self._invoke_callback("create_new_camera")

    # Capture

    def capture_will_start(self, vcserver):
        self._invoke_callback("capture_will_start")
        self._log("capture_will_start callback completed, shm=%s" % (
            self._server._shm.name if self._server._shm else "None"))

    def capture_did_end(self, vcserver):
        self._log("capture_did_end called")
        self._invoke_callback("capture_did_end")

    def _log(self, msg):
        import datetime
        try:
            log_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "bridge_debug.log",
            )
            with open(log_path, "a") as f:
                ts = datetime.datetime.now().strftime("%H:%M:%S.%f")
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    def __getattribute__(self, name):
        if not name.startswith('_'):
            try:
                object.__getattribute__(self, '_log')(f"Attr accessed: {name}")
            except Exception:
                pass
        return object.__getattribute__(self, name)

    def get_capture_buffer(self, vcserver, camera_name):
        if self._server._shm is not None:
            buf = self._server._shm.buf
            
            import ctypes
            # Get the memory address of the shared memory buffer
            buf_address = ctypes.addressof(ctypes.c_char.from_buffer(buf))
            
            # Wrap the memoryview in a ctypes array without copying
            # ctypes arrays implement the C buffer protocol (bytes-like object)
            # and allow us to dynamically add the .ptr attribute the C++ extension needs
            arr = (ctypes.c_char * len(buf)).from_buffer(buf)
            arr.ptr = buf_address
            
            return arr
            
        self._log("get_capture_buffer: shm is None!")
        return None

    def get_capture_pointer(self, vcserver, camera_name):
        if self._server._shm is not None:
            import ctypes
            buf = self._server._shm.buf
            # Cache the ctypes array so it isn't garbage collected
            # while the C++ extension reads from the pointer
            if not hasattr(self, '_cached_c_array') or self._cached_c_array is None:
                self._cached_c_array = (ctypes.c_char * len(buf)).from_buffer(buf)
            ptr = ctypes.addressof(self._cached_c_array)
            # Log occasionally (every 100 calls)
            if not hasattr(self, '_ptr_call_count'):
                self._ptr_call_count = 0
            self._ptr_call_count += 1
            if self._ptr_call_count <= 5 or self._ptr_call_count % 100 == 0:
                nz = sum(1 for b in bytes(buf[:100]) if b != 0)
                self._log(
                    f"get_capture_pointer #{self._ptr_call_count}: "
                    f"ptr=0x{ptr:016X}, buf_size={len(buf)}, "
                    f"first4={list(bytes(buf[:4]))}, first100_nz={nz}"
                )
            return ptr
        self._log("get_capture_pointer: shm is None!")
        return 0

    def look_through_camera(self, vcserver, camera_name):
        self._invoke_callback("look_through_camera", camera_name)

    # Feedback

    def client_connected(self, vcserver, client_ip, client_port):
        self._invoke_callback("client_connected", client_ip, client_port)

    def client_disconnected(self, vcserver):
        self._invoke_callback("client_disconnected")

    def current_camera_changed(self, vcserver, current_camera):
        self._invoke_callback("current_camera_changed", current_camera)

    def server_did_stop(self, vcserver):
        self._invoke_callback("server_did_stop")

    # Scripts

    def get_script_labels(self, vcserver):
        return self._invoke_callback("get_script_labels")

    def execute_script(self, vcserver, script_index, current_camera):
        return self._invoke_callback(
            "execute_script", script_index, current_camera
        )


class BridgeServer:
    def __init__(self, cb_port):
        self._shm = None
        self._vcserver = None
        self._bridge_vcbase = None

        self._cb_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._cb_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._cb_sock.connect(("127.0.0.1", cb_port))
        self._cb_lock = threading.Lock()

        self._cmd_serv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._cmd_serv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._cmd_serv.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._cmd_serv.bind(("127.0.0.1", 0))
        self._cmd_port = self._cmd_serv.getsockname()[1]
        self._cmd_serv.listen(1)
        self._cmd_serv.settimeout(15.0)

        _send_msg(self._cb_sock, {
            "type": "ready",
            "cmd_port": self._cmd_port,
        })
        ack = _recv_msg(self._cb_sock)
        if ack.get("type") != "ack":
            raise RuntimeError("Handshake failed")

        self._cmd_sock, _ = self._cmd_serv.accept()
        self._cmd_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._cmd_sock.settimeout(None)
        self._cmd_serv.close()

        self._cb_reader_thread = threading.Thread(
            target=self._cb_reader_loop, daemon=True
        )
        self._cb_reader_thread.start()

        self._bridge_vcbase = BridgeVCBase(self._cb_sock, self._cb_lock, self)

        if _RealVCServer is not None:
            self._vcserver = _RealVCServer(
                platform="Blender",
                plugin_version=(1, 1, 0),
                python_executable=sys.executable,
                event_mode=0,
                vcbase=self._bridge_vcbase
            )
            print("Bridge: VCServer initialized", flush=True)
        else:
            print("Bridge WARNING: vc_core.pyd not found", flush=True)

    def _cb_reader_loop(self):
        try:
            while True:
                msg = _recv_msg(self._cb_sock)
                if msg.get("type") == "callback_response":
                    self._bridge_vcbase.handle_callback_response(msg)
        except (ConnectionError, OSError):
            pass

    def _send_props(self):
        if self._vcserver is None:
            return
        vs = self._vcserver
        try:
            _send_msg(self._cmd_sock, {
                "type": "properties",
                "is_serving": getattr(vs, "is_serving", False),
                "is_connected": getattr(vs, "is_connected", False),
                "is_event_loop_running": getattr(
                    vs, "is_event_loop_running", False
                ),
                "is_capturing": getattr(vs, "is_capturing", False),
                "is_stopping": getattr(vs, "is_stopping", False),
                "client_ip": getattr(vs, "client_ip", ""),
                "client_port": getattr(vs, "client_port", 0),
                "current_camera": getattr(vs, "current_camera", ""),
                "capture_width": getattr(vs, "capture_width", 0),
                "capture_height": getattr(vs, "capture_height", 0),
                "capture_mode": getattr(vs, "capture_mode", 0),
                "capture_format": getattr(vs, "capture_format", 0),
                "use_vflip": getattr(vs, "use_vflip", False),
                "server_port": getattr(vs, "server_port", 0),
            })
        except (ConnectionError, OSError):
            pass

    def run(self):
        try:
            self._main_loop()
        finally:
            self._cleanup()

    def _main_loop(self):
        while True:
            try:
                msg = _recv_msg(self._cmd_sock)
            except (ConnectionError, OSError):
                break
            if msg is None:
                break
            self._dispatch(msg)

    def _dispatch(self, msg):
        cmd = msg.get("cmd")
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler:
            try:
                handler(msg)
            except Exception as e:
                self._respond(cmd, error=str(e))
        else:
            self._respond(cmd, error=f"Unknown command: {cmd}")

    def _respond(self, cmd, **kwargs):
        kwargs["cmd"] = cmd
        try:
            _send_msg(self._cmd_sock, kwargs)
        except (ConnectionError, OSError):
            pass

    # Command handlers

    def _cmd_start_serving(self, msg):
        port = msg.get("port", 23354)
        try:
            self._vcserver.start_serving(port)
            self._respond("start_serving", ok=True)
        except Exception as e:
            self._respond("start_serving", error=str(e))

    def _cmd_stop_serving(self, msg):
        self._vcserver.stop_serving()
        self._respond("stop_serving", ok=True)

    def _cmd_set_capture_resolution(self, msg):
        self._bridge_vcbase._log(
            f"set_capture_resolution: {msg['width']}x{msg['height']}")
        self._vcserver.set_capture_resolution(msg["width"], msg["height"])
        self._respond("set_capture_resolution", ok=True)

    def _cmd_set_capture_mode(self, msg):
        self._bridge_vcbase._log(
            f"set_capture_mode: mode={msg['mode']}, pixel_format={msg['pixel_format']}")
        self._vcserver.set_capture_mode(msg["mode"], msg["pixel_format"])
        # Log what the C++ extension actually has after setting
        vs = self._vcserver
        self._bridge_vcbase._log(
            f"  -> C++ capture_mode={vs.capture_mode}, capture_format={vs.capture_format}")
        self._respond("set_capture_mode", ok=True)

    def _cmd_set_vertical_flip(self, msg):
        self._vcserver.set_vertical_flip(msg["flip"])
        self._respond("set_vertical_flip", ok=True)

    def _cmd_update_script_labels(self, msg):
        self._vcserver.update_script_labels()
        self._respond("update_script_labels", ok=True)

    def _cmd_write_qr_image_png(self, msg):
        self._vcserver.write_qr_image_png(msg["filepath"], msg["box_size"])
        self._respond("write_qr_image_png", ok=True)

    def _cmd_set_shm_name(self, msg):
        shm_name = msg.get("shm_name")
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass
            self._shm = None
        # Invalidate cached ctypes pointer
        if self._bridge_vcbase is not None:
            self._bridge_vcbase._cached_c_array = None
        if shm_name:
            self._shm = SharedMemory(name=shm_name)
        self._respond("set_shm_name", ok=True)

    def _cmd_exec_events(self, msg):
        vs = self._vcserver
        self._respond(
            "exec_events",
            ok=True,
            is_serving=getattr(vs, "is_serving", False),
            is_connected=getattr(vs, "is_connected", False),
            is_event_loop_running=getattr(vs, "is_event_loop_running", False),
            is_capturing=getattr(vs, "is_capturing", False),
            is_stopping=getattr(vs, "is_stopping", False),
            client_ip=getattr(vs, "client_ip", ""),
            client_port=getattr(vs, "client_port", 0),
            current_camera=getattr(vs, "current_camera", ""),
            capture_width=getattr(vs, "capture_width", 0),
            capture_height=getattr(vs, "capture_height", 0),
            capture_mode=getattr(vs, "capture_mode", 0),
            capture_format=getattr(vs, "capture_format", 0),
            use_vflip=getattr(vs, "use_vflip", False),
            server_port=getattr(vs, "server_port", 0),
        )

    def _cleanup(self):
        if self._shm:
            try:
                self._shm.close()
            except Exception:
                pass
        if self._vcserver:
            try:
                if self._vcserver.is_serving:
                    self._vcserver.stop_serving()
            except Exception:
                pass
        try:
            self._cmd_sock.close()
        except Exception:
            pass
        try:
            self._cb_sock.close()
        except Exception:
            pass


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m virtucamera.vc_bridge_server <cb_port>",
              file=sys.stderr)
        sys.exit(1)

    cb_port = int(sys.argv[1])
    parent_dir = os.path.abspath(os.path.dirname(__file__))
    third_party_dir = os.path.join(parent_dir, "third_party")

    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    if third_party_dir not in sys.path:
        sys.path.insert(0, third_party_dir)

    if os.name == "nt":
        crt_dir = os.path.join(third_party_dir, "crt")
        if os.path.isdir(crt_dir):
            os.environ["PATH"] += os.pathsep + crt_dir

    server = BridgeServer(cb_port)
    server.run()


if __name__ == "__main__":
    main()
