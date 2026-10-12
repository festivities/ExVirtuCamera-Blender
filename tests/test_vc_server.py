"""Tests for vc_server.VCServer (loopback only).

Run from the repo root with a Python that has numpy (and av for the video test):
    py -3.13 tests\\test_vc_server.py
    py -3.11 tests\\test_vc_server.py
Set VC_AV_PATH to a directory holding the av package to use it instead of an installed one.
The conformance fixture is regenerated from the original binary with
    py -3.9 tests\\regen_conformance_fixture.py
"""
import json
import os
import socket
import struct
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
if os.environ.get("VC_AV_PATH"):
    sys.path.insert(0, os.environ["VC_AV_PATH"])

import numpy as np  # noqa: E402

import vc1_conformance as vc  # noqa: E402
import vc_server  # noqa: E402

vc_server.BIND_HOST = "127.0.0.1"  # loopback only: the server binds 0.0.0.0 by default

try:
    import av
    HAVE_AV = True
except ImportError:
    HAVE_AV = False

FIXTURE = os.path.join(HERE, "fixtures", "vc1_original_transcript.json")
M = vc.M
START = vc.START
STOP = vc.STOP
BARRIER = b"\x05"
BARRIER_REPLY = b"\x05" + struct.pack("<d", vc.BARRIER_FPS)
SCENE_NAMES = "$".join(vc.CAMERAS).encode("ascii")


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("closed after %d of %d bytes" % (len(buf), n))
        buf += chunk
    return buf


def ask(sock, payload, n):
    """Sends a request and reads exactly n reply bytes."""
    sock.sendall(payload)
    return recv_exact(sock, n)


def u32(n):
    return struct.pack("<I", n)


def scene_list_reply(index=-1):
    body = struct.pack("<h", index) + SCENE_NAMES
    return b"\x02" + u32(len(body)) + body


class Harness(object):
    """A VCServer on a free loopback port, with host calls pumped on a helper thread."""

    def __init__(self, host=None):
        self.host = host or vc.FakeHost()
        self.server = vc_server.VCServer(self.host, plugin_version=(2, 0, 0), platform="Blender")
        self.port = vc.free_port()
        self._stop_pump = threading.Event()
        self._pump = threading.Thread(target=vc.pump, args=(self.server, self._stop_pump), daemon=True)
        self._socks = []

    def start(self):
        self.assert_started()
        self._pump.start()
        return self

    def assert_started(self):
        if not self.server.start_serving(self.port):
            raise AssertionError("start_serving(%d) failed" % self.port)

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sock.settimeout(5)
        self._socks.append(sock)
        return sock

    def calls(self):
        """Host callbacks since the last call. Drops the barrier's get_playback_fps and connect noise."""
        return [c for c in self.host.drain() if c[0] not in ("get_playback_fps", "client_connected")]

    def close(self):
        for sock in self._socks:
            sock.close()
        if self.server.is_serving:
            self.server.stop_serving()
        self._stop_pump.set()
        if self._pump.is_alive():
            self._pump.join(5)


def barrier(sock):
    """ASK PLAY_FPS. Its reply proves earlier queued host calls have run."""
    return ask(sock, BARRIER, len(BARRIER_REPLY)) == BARRIER_REPLY


def wait_until(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self._hostname = vc.fixed_hostname()
        self._hostname.__enter__()
        self.h = Harness().start()

    def tearDown(self):
        self.h.close()
        self._hostname.__exit__(None, None, None)

    def select_camera(self, sock, index):
        """Fetches the scene list (token 2), then selects by index (token 52)."""
        self.assertEqual(ask(sock, b"\x02", len(scene_list_reply())), scene_list_reply())
        self.assertEqual(ask(sock, vc.req(52, "H", index), 1), b"\x34")
        self.assertTrue(barrier(sock))
        self.h.calls()  # discard the list and selection callbacks


class ProtocolTests(ServerTestCase):
    def test_ask_replies_have_spec_layout(self):
        s = self.h.connect()
        text = b"Blender_TESTHOST - Blender"
        body = struct.pack("<3H", 2, 1, 2) + text
        self.assertEqual(ask(s, b"\x00", 1 + 4 + len(body)), b"\x00" + u32(len(body)) + body)
        self.assertEqual(ask(s, b"\x08", 7), b"\x08" + struct.pack("<3H", 2, 0, 0))
        self.assertEqual(ask(s, BARRIER, len(BARRIER_REPLY)), BARRIER_REPLY)
        self.assertEqual(ask(s, b"\x07", 1 + 4 + 10), b"\x07" + u32(10) + b"Alpha%Beta")
        self.assertEqual(ask(s, b"\x04", 1), b"\xc9", "VIEWPORT_IMG while not streaming is C9")
        s.close()

    def test_camera_scoped_requests_need_a_camera(self):
        s = self.h.connect()
        self.assertEqual(ask(s, b"\x01", 1), b"\xc8")
        self.assertEqual(ask(s, b"\x03", 1), b"\xc8")
        self.assertEqual(ask(s, b"\x06", 1), b"\xc8")
        # SET echoes, then C8. The 128-byte payload must still be consumed.
        self.assertEqual(ask(s, vc.req(53, "16d", *M), 2), b"\x35\xc8")
        self.assertEqual(ask(s, vc.req(150, "ffH??", 30.0, 8.0, 0, False, True), 2), b"\x96\xc8")
        self.assertTrue(barrier(s))
        self.assertEqual(self.h.calls(), [], "no host call without a camera")
        self.assertFalse(self.h.server.is_capturing)
        s.close()

    def test_selection_needs_scene_list_then_checks_existence(self):
        s = self.h.connect()
        self.assertEqual(ask(s, vc.req(52, "H", 0), 1), b"\x34", "no list yet: echo only")
        self.assertTrue(barrier(s))
        self.assertEqual(self.h.calls(), [], "no host call without a list")
        self.assertEqual(ask(s, b"\x02", len(scene_list_reply())), scene_list_reply(-1))
        self.assertEqual(ask(s, vc.req(52, "H", 2), 2), b"\x34\xc8", "Ghost is listed but missing")
        self.assertEqual(self.h.server.current_camera, "")
        s.close()

    def test_selection_and_per_connection_camera_state(self):
        s = self.h.connect()
        ask(s, b"\x02", len(scene_list_reply()))
        self.assertEqual(ask(s, vc.req(52, "H", 2), 2), b"\x34\xc8")
        self.assertEqual(ask(s, b"\x01", 1), b"\xc8")
        self.assertEqual(ask(s, vc.req(52, "H", 9), 1), b"\x34", "index past the list: no change")
        self.assertEqual(ask(s, vc.req(52, "H", 0), 1), b"\x34")
        self.assertTrue(barrier(s))
        self.assertEqual(self.h.calls()[-1], ["current_camera_changed", "Camera"])
        self.assertEqual(self.h.server.current_camera, "Camera")
        self.assertEqual(ask(s, b"\x01", 33), b"\x01" + struct.pack("<4d", 12.0, 1.0, 250.0, 35.5))
        self.assertEqual(ask(s, b"\x06", 2), b"\x06\x03", "Camera: transform keys only")
        self.assertEqual(ask(s, vc.req(52, "H", 1), 1), b"\x34")
        self.assertEqual(ask(s, b"\x06", 2), b"\x06\x05", "Cam.001: focal length keys only")
        s.close()
        self.assertTrue(wait_until(lambda: self.h.host.has("client_disconnected")))
        s2 = self.h.connect()
        self.assertEqual(ask(s2, b"\x01", 1), b"\xc8", "disconnect resets the camera")
        self.assertEqual(ask(s2, vc.req(52, "H", 0), 1), b"\x34", "scene list kept across connections")
        self.assertTrue(barrier(s2))
        self.assertIn(["current_camera_changed", "Camera"], self.h.calls())
        s2.close()

    def test_set_tokens_callbacks_in_order(self):
        s = self.h.connect()
        self.select_camera(s, 1)
        self.assertEqual(ask(s, vc.req(50, "2d", 1.0, 100.0), 1), b"\x32")
        self.assertEqual(ask(s, vc.req(51, "d", 42.5), 1), b"\x33")
        self.assertEqual(ask(s, vc.req(54, "17d", *(M + [43.0])), 1), b"\x36")
        self.assertEqual(ask(s, vc.req(56, "2d", 35.0, 44.0), 1), b"\x38")
        self.assertEqual(ask(s, vc.req(58, "18d", *(M + [28.0, 45.0])), 1), b"\x3a")
        self.assertTrue(barrier(s))
        self.assertEqual(self.h.calls(), [
            ["set_playback_range", 1.0, 100.0],
            ["set_frame", 42.5],
            ["get_camera_exists", "Cam.001"],
            ["set_camera_transform", "Cam.001", M],
            ["set_frame", 43.0],
            ["get_camera_exists", "Cam.001"],
            ["set_camera_focal_length", "Cam.001", 35.0],
            ["set_frame", 44.0],
            ["get_camera_exists", "Cam.001"],
            ["set_camera_transform", "Cam.001", M],
            ["set_camera_focal_length", "Cam.001", 28.0],
            ["set_frame", 45.0],
        ])
        s.close()

    def test_keyed_sets_n_zero_is_a_noop(self):
        s = self.h.connect()
        self.select_camera(s, 0)
        self.assertEqual(ask(s, vc.keys_req(59, "17d", [M + [1.0], M + [2.0]]), 1), b"\x3b")
        self.assertEqual(ask(s, vc.keys_req(60, "2d", [(35.0, 1.0)]), 1), b"\x3c")
        self.assertEqual(ask(s, vc.keys_req(59, "17d", []), 1), b"\x3b", "echo only for n=0")
        self.assertEqual(ask(s, vc.keys_req(60, "2d", []), 1), b"\x3c")
        self.assertTrue(barrier(s))
        self.assertEqual(self.h.calls(), [
            ["get_camera_exists", "Camera"],
            ["set_camera_transform_keys", "Camera", [1.0, 2.0], [M, M]],
            ["get_camera_exists", "Camera"],
            ["set_camera_flen_keys", "Camera", [1.0], [35.0]],
        ], "n=0 makes no host call")
        s.close()

    def test_token_154_is_a_noop_without_payload(self):
        s = self.h.connect()
        self.assertEqual(ask(s, b"\x9a" + BARRIER, len(BARRIER_REPLY)), BARRIER_REPLY,
                         "154 sends nothing and reads no payload")
        s.close()

    def test_execute_script_replies_without_echo(self):
        s = self.h.connect()
        self.assertEqual(ask(s, vc.req(157, "B", 0), 2), b"\x9d\x00")
        self.assertEqual(ask(s, vc.req(157, "B", 1), 2), b"\xcb\x01", "False result: CB then the index")
        self.assertTrue(barrier(s))
        self.assertEqual(self.h.calls(), [["execute_script", 0, ""], ["execute_script", 1, ""]])
        s.close()

    def test_unknown_bytes_are_skipped(self):
        s = self.h.connect()
        s.sendall(b"\x55\xff")  # no reply for unknown bytes
        self.assertEqual(ask(s, b"\x08", 7), b"\x08" + struct.pack("<3H", 2, 0, 0),
                         "the session continues after unknown bytes")
        s.close()

    def test_start_and_stop_streaming(self):
        s = self.h.connect()
        self.select_camera(s, 0)
        self.assertEqual(ask(s, START, 1), b"\x96")
        self.assertTrue(wait_until(lambda: self.h.server.is_capturing))
        self.assertEqual(self.h.server.capture_fps, 30.0)
        self.assertTrue(barrier(s))
        self.assertEqual(self.h.calls(), [
            ["get_camera_exists", "Camera"],
            ["capture_will_start"],
            ["look_through_camera", "Camera"],
        ])
        self.assertEqual(ask(s, STOP, 1), b"\x97")
        self.assertTrue(wait_until(lambda: not self.h.server.is_capturing), "STOP echoes before the encoder joins")
        self.assertTrue(barrier(s))
        self.assertEqual(self.h.calls(), [["capture_did_end"]])
        s.close()

    def test_host_exceptions_fall_back_and_keep_session(self):
        class Flaky(vc.FakeHost):
            def get_camera_transform(self, name):
                raise RuntimeError("host bug")

            def get_camera_focal_length(self, name):
                raise RuntimeError("host bug")

            def set_frame(self, frame):
                raise RuntimeError("host bug")

        self.h.close()
        self.h = Harness(Flaky()).start()
        with mock.patch("traceback.print_exc"):
            s = self.h.connect()
            self.select_camera(s, 0)
            self.assertEqual(ask(s, b"\x03", 129), b"\x03" + struct.pack("<16d", *vc_server.IDENTITY))
            self.assertEqual(ask(s, b"\x01", 33), b"\x01" + struct.pack("<4d", 12.0, 1.0, 250.0, 0.0))
            self.assertEqual(ask(s, vc.req(51, "d", 1.0), 1), b"\x33", "a failing setter keeps the session")
            self.assertTrue(barrier(s))
            self.assertEqual(ask(s, b"\x08", 7), b"\x08" + struct.pack("<3H", 2, 0, 0))
            s.close()

    def test_stop_and_start_cycles_on_same_port(self):
        for _ in range(3):
            s = self.h.connect()
            self.assertEqual(ask(s, b"\x08", 7), b"\x08" + struct.pack("<3H", 2, 0, 0))
            s.close()
            self.h.server.stop_serving()
            self.assertFalse(self.h.server.is_serving)
            self.assertTrue(wait_until(lambda: self.h.host.has("server_did_stop")))
            self.h.host.drain()
            self.h.assert_started()
        self.assertTrue(self.h.server.is_serving)

    def test_stop_serving_never_deadlocks_on_a_blocked_getter(self):
        self.h._stop_pump.set()  # no main thread: the session blocks in a getter
        self.h._pump.join(5)
        s = self.h.connect()
        s.sendall(b"\x02")  # SCENE_CAMERAS waits for the main thread
        time.sleep(0.2)
        done = threading.Event()

        def stop():
            self.h.server.stop_serving()
            done.set()

        threading.Thread(target=stop, daemon=True).start()
        self.assertTrue(done.wait(8), "stop_serving() must not wait on the main thread")
        self.assertFalse(self.h.server.is_serving)
        s.close()

    def test_qr_string_lists_ipv4_without_loopback_or_link_local(self):
        fake = [
            (socket.AF_INET, 0, 0, "", ("127.0.0.1", 0)),
            (socket.AF_INET, 0, 0, "", ("169.254.3.4", 0)),
            (socket.AF_INET, 0, 0, "", ("192.168.1.20", 0)),
            (socket.AF_INET, 0, 0, "", ("192.168.1.20", 0)),
            (socket.AF_INET, 0, 0, "", ("10.0.0.5", 0)),
            (socket.AF_INET, 0, 0, "", ("0.0.0.0", 0)),
        ]
        with mock.patch("socket.getaddrinfo", return_value=fake):
            self.assertEqual(self.h.server.qr_string(), "%d_192.168.1.20_10.0.0.5" % self.h.port)
        self.h.close()
        with self.assertRaises(RuntimeError):
            self.h.server.qr_string()

    def test_update_script_labels_pushes_script_info(self):
        s = self.h.connect()
        ask(s, b"\x08", 7)  # the session is up
        self.h.server.update_script_labels()  # called on the test thread, which plays the main thread
        self.assertEqual(recv_exact(s, 1 + 4 + 10), b"\x07" + u32(10) + b"Alpha%Beta")
        s.close()

    def test_submit_frame_copies_the_buffer(self):
        buf = bytearray(16 * 8 * 4)
        self.h.server.submit_frame(buf, 16, 8)
        buf[:] = b"\x01" * len(buf)  # the caller reuses its buffer
        frame, w, h = self.h.server._latest
        self.assertEqual((w, h, frame.shape), (16, 8, (8, 16, 4)))
        self.assertEqual(int(frame.max()), 0, "the server kept its own copy")


class ConformanceTests(unittest.TestCase):
    """Replays the request script and compares every reply and host callback with the original."""

    def test_transcript_matches_original_fixture(self):
        with open(FIXTURE, encoding="utf-8") as f:
            fixture = json.load(f)
        self.assertEqual(fixture["computer_name"], vc.HOST_NAME)
        with vc.fixed_hostname():
            h = Harness().start()
            try:
                steps = vc.run_script(h.port, h.host, h.server)
            finally:
                h.close()
        self.assertEqual([s["label"] for s in steps], [s["label"] for s in fixture["steps"]])
        for got, want in zip(steps, fixture["steps"]):
            self.assertEqual(got, want, got["label"])


@unittest.skipUnless(HAVE_AV, "PyAV not importable: install av==18.1.0 or set VC_AV_PATH")
class VideoTests(unittest.TestCase):
    def test_keyframes_size_change_and_orientation(self):
        h = Harness().start()
        try:
            s = h.connect()
            ask(s, b"\x02", len(scene_list_reply()))
            ask(s, vc.req(52, "H", 0), 1)
            s.sendall(vc.req(150, "ffH??", 30.0, 8.0, 0, False, True))
            self.assertEqual(recv_exact(s, 1), b"\x96")

            packets, errors = [], []
            done = threading.Event()

            def reader():
                buf = bytearray()
                s.settimeout(0.2)
                while not done.is_set():
                    try:
                        chunk = s.recv(65536)
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                    if not chunk:
                        return
                    buf += chunk
                    while buf:
                        if buf[0] != 0x04:
                            errors.append(bytes(buf[:8]))
                            return
                        if len(buf) < 5:
                            break
                        (n,) = struct.unpack_from("<I", buf, 1)
                        if len(buf) < 5 + n:
                            break
                        packets.append((time.perf_counter(), bytes(buf[5:5 + n])))
                        del buf[:5 + n]

            reader_thread = threading.Thread(target=reader, daemon=True)
            reader_thread.start()
            self._feed(h.server, 640, 360, 1.5)
            self._feed(h.server, 1280, 720, 1.5)
            time.sleep(0.5)
            done.set()
            s.close()
            reader_thread.join(5)
        finally:
            h.close()

        self.assertEqual(errors, [])
        self.assertGreater(len(packets), 60)
        datas = [p[1] for p in packets]
        self.assertTrue(_is_keyframe(datas[0]), "first packet is a keyframe")

        ctx = av.CodecContext.create("vp9", "r")
        frames = []
        for data in datas:
            frames.extend(ctx.decode(_packet(data)))
        self.assertEqual(len(frames), len(datas), "one decoded frame per packet")
        sizes = [(f.width, f.height) for f in frames]
        self.assertEqual(sizes[0], (640, 360))
        switch = sizes.index((1280, 720))
        self.assertGreater(switch, 0)
        self.assertTrue(all(sz == (640, 360) for sz in sizes[:switch]))
        self.assertTrue(_is_keyframe(datas[switch]), "first packet after the size change is a keyframe")

        first = [p[0] for p in packets[:switch]]
        rate = (len(first) - 1) / (first[-1] - first[0])
        self.assertLess(abs(rate - 30.0), 4.0, "frame rate about 30 fps, got %.1f" % rate)

        top, bottom = _quarters(frames[0])
        self.assertTrue(top[0] > 150 and top[2] < 100, "top of the picture is red (upright): %r" % (top,))
        self.assertTrue(bottom[2] > 150 and bottom[0] < 100, "bottom of the picture is blue: %r" % (bottom,))

    def _feed(self, server, w, h, seconds):
        end = time.perf_counter() + seconds
        k = 0
        next_t = time.perf_counter()
        while time.perf_counter() < end:
            server.submit_frame(_make_frame(w, h, k), w, h)
            k += 1
            next_t += 1.0 / 30
            time.sleep(max(0.0, next_t - time.perf_counter()))


def _make_frame(w, h, k):
    """Bottom-up RGBA: memory rows 0..h/2 are the bottom of the picture (blue), the rest the top (red)."""
    img = np.zeros((h, w, 4), np.uint8)
    img[: h // 2] = (0, 0, 255, 255)
    img[h // 2:] = (255, 0, 0, 255)
    x = (k * 7) % (w - 40)
    img[h // 2 - 20: h // 2 + 20, x: x + 40] = (255, 255, 255, 255)
    return img.tobytes()


def _is_keyframe(data):
    # VP9 profile 0 uncompressed header: frame_type is bit 2 of the first byte (0 = key frame).
    return (data[0] & 0x04) == 0


def _packet(data):
    pkt = av.Packet(len(data))
    pkt.update(data)
    return pkt


def _quarters(frame):
    img = frame.reformat(format="rgb24").to_ndarray()
    h = img.shape[0]
    top = img[: h // 4].reshape(-1, 3).mean(axis=0)
    bottom = img[3 * h // 4:].reshape(-1, 3).mean(axis=0)
    return top, bottom


if __name__ == "__main__":
    unittest.main(verbosity=2)
