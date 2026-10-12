"""Fake VirtuCamera 1 client for tests\\run_e2e.ps1 (loopback only). Not meant to run on its own.

run_e2e.ps1 starts it with Blender's bundled Python and E2E_AV_PATH set to the extracted av wheel:
    python.exe -s tests\\e2e_client.py <out_dir> <port>
It speaks the wire protocol in docs/vc_core_protocol.md to 127.0.0.1:<port>, and asks the Blender
host (e2e_blender.py) for scene facts through req_NNN.json / resp_NNN.json files in out_dir.
Exit code 0 only if every check passed.
"""
import json
import math
import os
import socket
import struct
import sys
import time
import traceback
import zlib

AV_PATH = os.environ.get("E2E_AV_PATH", "")
if AV_PATH:
    sys.path.insert(0, AV_PATH)

import av  # noqa: E402
import numpy as np  # noqa: E402

OUT = os.path.abspath(sys.argv[1])
PORT = int(sys.argv[2])
RESULTS = []
DETAIL = {}
T_BEGIN = time.time()


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail if isinstance(detail, (str, int, float, type(None))) else json.dumps(detail, default=str)})
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + json.dumps(detail, default=str)) if detail != "" else ""), flush=True)


def wait_file(path, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except ValueError:
                pass
        time.sleep(0.1)
    raise TimeoutError("no " + os.path.basename(path))


_seq = [0]


def bl(op, timeout=40.0, **kw):
    _seq[0] += 1
    rid = "%03d" % _seq[0]
    body = dict(kw, id=rid, op=op)
    tmp = os.path.join(OUT, "req_%s.json.tmp" % rid)
    with open(tmp, "w") as f:
        json.dump(body, f)
    os.replace(tmp, os.path.join(OUT, "req_%s.json" % rid))
    resp = wait_file(os.path.join(OUT, "resp_%s.json" % rid), timeout)
    if not resp.get("ok"):
        raise RuntimeError("blender op %s failed: %s" % (op, resp.get("error")))
    return resp["result"]


class ProtoError(Exception):
    pass


class Link(object):
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.frames = []  # (perf_counter at arrival, packet bytes)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass

    def _exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise ProtoError("connection closed")
            buf += chunk
        return bytes(buf)

    def _frame_body(self):
        (n,) = struct.unpack("<I", self._exact(4))
        self.frames.append((time.perf_counter(), self._exact(n)))

    def send(self, data):
        self.s.sendall(data)

    def expect(self, want, fixed=0, var=False):
        """Reads until a reply starting with a byte in want. Frame packets (0x04) are recorded on the way."""
        while True:
            b = self._exact(1)[0]
            if b == 0x04:
                self._frame_body()
                continue
            if b in want:
                break
            raise ProtoError("got 0x%02x, wanted one of %s" % (b, sorted("0x%02x" % w for w in want)))
        if var:
            (n,) = struct.unpack("<I", self._exact(4))
            return b, self._exact(n)
        return b, (self._exact(fixed) if fixed else b"")

    def pump(self, seconds):
        """Reads only frame packets for the given time. Any other byte is a protocol error."""
        end = time.perf_counter() + seconds
        self.s.settimeout(0.25)
        try:
            while time.perf_counter() < end:
                try:
                    b = self.s.recv(1)
                except socket.timeout:
                    continue
                if not b:
                    raise ProtoError("connection closed during pump")
                if b[0] != 0x04:
                    raise ProtoError("non-frame byte 0x%02x during pump" % b[0])
                self.s.settimeout(10)
                self._frame_body()
                self.s.settimeout(0.25)
        finally:
            self.s.settimeout(10)


def wire(A):
    """4x4 affine (list of rows) -> 16 doubles, column-major (translation at 12..14), as the app sends."""
    return [A[r][c] for c in range(4) for r in range(4)]


def affine(R3, t):
    return [[R3[0][0], R3[0][1], R3[0][2], t[0]],
            [R3[1][0], R3[1][1], R3[1][2], t[1]],
            [R3[2][0], R3[2][1], R3[2][2], t[2]],
            [0.0, 0.0, 0.0, 1.0]]


def rot_y(theta):
    c, s = math.cos(theta), math.sin(theta)
    return [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]


def start_stream(link, fps, mbits, u16=0, opaque=False, autosend=True):
    link.send(bytes([150]) + struct.pack("<ffH??", fps, mbits, u16, opaque, autosend))
    link.expect({0x96})


def stop_stream(link):
    link.send(b"\x97")
    link.expect({0x97})


def fps_of(frames):
    if len(frames) < 2:
        return 0.0
    return (len(frames) - 1) / (frames[-1][0] - frames[0][0])


def is_keyframe(pkt):
    # VP9 uncompressed header, profile 0: bits 0-1 marker, 2 profile_low, 3 profile_high,
    # 4 show_existing, 5 frame_type (0 = key), 6 show_frame, 7 error_res.
    return len(pkt) > 0 and ((pkt[0] >> 2) & 1) == 0


def connect_retry(port, timeout):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            return Link(port)
        except OSError as e:
            last = e
            time.sleep(0.3)
    raise TimeoutError("reconnect failed: %r" % (last,))


def ask_info(link):
    link.send(b"\x00")
    _, p = link.expect({0x00}, var=True)
    return p


def select_camera(link, name="Camera"):
    link.send(b"\x02")
    _, p = link.expect({0x02}, var=True)
    (idx,) = struct.unpack_from("<h", p, 0)
    names = p[2:].decode("ascii").split("$") if len(p) > 2 else []
    if name not in names:
        raise ProtoError("camera %s not in %r" % (name, names))
    link.send(bytes([52]) + struct.pack("<H", names.index(name)))
    link.expect({0x34})
    return names, idx


# -- decode and images ------------------------------------------------------

def decode_packets(packets):
    codec = None
    used = None
    for name in ("libvpx-vp9", "vp9"):
        try:
            codec = av.CodecContext.create(name, "r")
            used = name
            break
        except Exception:  # noqa: BLE001
            continue
    frames = []
    for p in packets:
        for f in codec.decode(av.Packet(p)):
            frames.append(f)
    try:
        for f in codec.decode(None):
            frames.append(f)
    except Exception:  # noqa: BLE001  (flush not supported: frames above are enough)
        pass
    return used, frames


def write_png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def red_stats(rgb):
    r = rgb[..., 0].astype(np.int32)
    g = rgb[..., 1].astype(np.int32)
    b = rgb[..., 2].astype(np.int32)
    mask = (r > 90) & (r > 2 * g) & (r > 2 * b)
    h = mask.shape[0]
    top = int(mask[: h // 2].sum())
    bottom = int(mask[h // 2:].sum())
    # Marker rows: rows with a real run of red (the viewport 3D cursor adds a few red pixels at the centre).
    big = np.nonzero(mask.sum(axis=1) >= 20)[0]
    return {"red_top": top, "red_bottom": bottom,
            "marker_row_min": int(big.min()) if big.size else None,
            "marker_row_max": int(big.max()) if big.size else None, "h": int(h),
            "mean_rgb": [round(float(x), 1) for x in rgb.reshape(-1, 3).mean(axis=0)]}


# -- stages -----------------------------------------------------------------

def main():
    ready = wait_file(os.path.join(OUT, "ready.json"), 90)
    check("blender ready: zip installed, enabled, serving", bool(ready.get("ok")),
          ready.get("error") or {"install": ready.get("install"), "port": ready.get("port")})
    if not ready.get("ok"):
        return
    inst = ready["install"]
    check("installed files match the repo zip (sha256)", all(inst["hashes_match_zip"].values()),
          inst["hashes_match_zip"])
    check("zip installed via %s" % inst["method"], "EXTRACT" not in inst["method"], inst["installed_dir"])
    check("stream_long_edge preference default 720", ready.get("stream_long_edge_pref") == 720,
          ready.get("stream_long_edge_pref"))
    DETAIL["blender_version"] = ready.get("version")

    link = Link(PORT)
    check("TCP connect 127.0.0.1:%d" % PORT, True)

    # Step 1: handshake, select camera
    p = ask_info(link)
    check("ASK 0 SERVER_INFO", len(p) > 6 and p[6:].startswith(b"Blender_"),
          {"server_version": list(struct.unpack_from("<3H", p, 0)), "name": p[6:].decode("utf-8", "replace")})
    link.send(b"\x08")
    _, p = link.expect({0x08}, fixed=6)
    pv = struct.unpack("<3H", p)
    check("ASK 8 PLUGIN_VERSION == 2.0.0", pv == (2, 0, 0), list(pv))
    names, idx = select_camera(link, "Camera")
    check("ASK 2 SCENE_CAMERAS lists Camera; SET 52 selected it", "Camera" in names,
          {"names": names, "index_before": idx})

    # Step 2: START 30 fps, 5 Mbit, autosend, receive 5 s
    start_stream(link, 30.0, 5.0)
    t_start = time.perf_counter()
    link.pump(5.0)
    s2 = [(t, d) for t, d in link.frames if t >= t_start]
    fps2 = fps_of(s2)
    DETAIL["stream1"] = {"packets": len(s2), "fps_span": round(fps2, 2), "fps_overall": round(len(s2) / 5.0, 2),
                         "bytes": sum(len(d) for _, d in s2)}
    # The first offscreen draw in a fresh profile can compile shaders for ~2 s (seen once on Goo 4.4.3).
    first_delay = (s2[0][0] - t_start) if s2 else None
    check("START 30 fps / 5 Mbit: first packet within 3 s, packets arrive", len(s2) >= 50 and first_delay < 3.0,
          {"packets": len(s2), "first_packet_s": round(first_delay, 2) if s2 else None,
           "fps_span": round(fps2, 2), "fps_overall": round(len(s2) / 5.0, 2)})
    check("measured stream fps within 24..36 (target 30)", 24.0 <= fps2 <= 36.0, round(fps2, 2))
    s2_packets = [d for _, d in s2]
    check("first stream packet is a VP9 keyframe", is_keyframe(s2_packets[0]),
          {"first_byte": "0x%02x" % s2_packets[0][0]})
    keyframes_in_s2 = sum(1 for d in s2_packets if is_keyframe(d))
    DETAIL["s2_keyframes"] = keyframes_in_s2

    # Step 3: while streaming, SET 53 x3 then ASK 3; SET 55 then ASK 1
    M = [affine(rot_y(0.3), (1.0, 2.0, 3.0)), affine(rot_y(-0.4), (-0.5, 0.25, 4.0)),
         affine(rot_y(0.9), (2.0, -1.0, 2.5))]
    for A in M:
        link.send(bytes([53]) + struct.pack("<16d", *wire(A)))
        link.expect({0x35})
        link.pump(0.2)
    link.send(b"\x03")
    _, p = link.expect({0x03}, fixed=128)
    got = struct.unpack("<16d", p)
    err = max(abs(a - b) for a, b in zip(got, wire(M[2])))
    check("while streaming: SET 53 x3, ASK 3 matrix round-trip <= 1e-4", err <= 1e-4, {"max_abs_err": err})
    link.send(bytes([55]) + struct.pack("<d", 42.0))
    link.expect({0x37})
    link.send(b"\x01")
    _, p = link.expect({0x01}, fixed=32)
    frame_now, rs, re_, focal = struct.unpack("<4d", p)
    check("while streaming: SET 55 focal 42 -> ASK 1 reports 42", abs(focal - 42.0) <= 1e-4, {"focal": focal})
    DETAIL["blender_after_matrix"] = bl("inspect")["location"]
    loc = DETAIL["blender_after_matrix"]
    exp = [M[2][0][3], -M[2][2][3], M[2][1][3]]
    check("Blender camera moved to the matrix (location = V_TO_B translation)",
          all(abs(a - b) <= 1e-3 for a, b in zip(loc, exp)), {"location": loc, "expected": exp})

    # Step 4: keyed transforms (10) and focal lengths (10), then ASK 6
    n = 10
    recs = []
    for i in range(n):
        A = affine(rot_y(0.1 * (i + 1)), (0.1 * (i + 1), 0.2 * (i + 1), 1.0 + 0.05 * (i + 1)))
        recs.append(wire(A) + [float(i + 1)])
    link.send(bytes([59]) + struct.pack("<H", n) + b"".join(struct.pack("<17d", *r) for r in recs))
    link.expect({0x3B})
    flen = [(35.0 + i, float(i + 1)) for i in range(n)]
    link.send(bytes([60]) + struct.pack("<H", n) + b"".join(struct.pack("<2d", f, fr) for f, fr in flen))
    link.expect({0x3C})
    link.pump(0.3)
    link.send(b"\x06")
    _, p = link.expect({0x06}, fixed=1)
    flags = p[0]
    check("ASK 6 CAMERA_HAS_KEYS = 7 (any + transform + focal)", flags == 7, {"flags": flags})
    ins = bl("inspect")
    oc, dc = ins["object_curves"], ins["data_curves"]
    counts = {k: len(v["frames"]) for k, v in list(oc.items()) + list(dc.items())}
    want = ["location[0]", "location[1]", "location[2]", "rotation_euler[0]", "rotation_euler[1]",
            "rotation_euler[2]", "lens[0]"]
    got_counts = {k: counts.get(k) for k in want}
    check("Blender: 10 keys on location x/y/z, rotation_euler x/y/z, lens", all(got_counts[k] == n for k in want),
          got_counts)
    frames_ok = all(sorted(oc[k]["frames"]) == [float(i + 1) for i in range(n)] for k in want[:6]) and \
        sorted(dc["lens[0]"]["frames"]) == [float(i + 1) for i in range(n)]
    check("Blender: key frames are 1..10", frames_ok)
    # location key values: loc = (tx, -tz, ty), the translation as Blender sees it
    vals_ok = True
    for i in range(n):
        want_loc = (0.1 * (i + 1), -(1.0 + 0.05 * (i + 1)), 0.2 * (i + 1))
        for axis in range(3):
            fr = oc["location[%d]" % axis]["frames"]
            vv = oc["location[%d]" % axis]["values"]
            v = vv[fr.index(float(i + 1))]
            if abs(v - want_loc[axis]) > 1e-4:
                vals_ok = False
    check("Blender: location key values match the sent translations", vals_ok)
    euler_ok = True
    max_step = 0.0
    for axis in range(3):
        vv = [v for _, v in sorted(zip(oc["rotation_euler[%d]" % axis]["frames"],
                                       oc["rotation_euler[%d]" % axis]["values"]))]
        for a, b in zip(vv, vv[1:]):
            max_step = max(max_step, abs(b - a))
    euler_ok = max_step < math.pi
    check("Blender: no Euler flips across keys (max step < pi)", euler_ok, {"max_step_rad": round(max_step, 4)})
    DETAIL["blender_keys"] = {"counts": got_counts, "lens_values": dc["lens[0]"]["values"][:3]}

    # Step 5: STOP, then START at fps 0.0 + NaN bitrate (fallback to 30 fps)
    link.send(b"\x97")
    link.expect({0x97})
    link.pump(0.5)
    before = len(link.frames)
    link.pump(1.5)
    after_stop = len(link.frames) - before
    check("STOP (151): echo 0x97 and packets stop", after_stop == 0, {"packets_after_0.5s_grace": after_stop})

    nan = float("nan")
    link.send(bytes([150]) + struct.pack("<ffH??", 0.0, nan, 0, False, True))
    link.expect({0x96})
    t_fb = time.perf_counter()
    link.pump(4.0)
    fb = [(t, d) for t, d in link.frames if t >= t_fb]
    fps_fb = fps_of(fb)
    fb_packets = [d for _, d in fb]
    DETAIL["fallback"] = {"packets": len(fb), "fps_span": round(fps_fb, 2)}
    check("START fps 0.0 + NaN bitrate: streams (no error, no stop)", len(fb) > 50, {"packets": len(fb)})
    check("fallback fps ~30 (24..36)", 24.0 <= fps_fb <= 36.0, round(fps_fb, 2))
    check("fallback stream first packet is a keyframe", bool(fb_packets) and is_keyframe(fb_packets[0]))
    stop_stream(link)

    # Step 6: script labels, EXECUTE_SCRIPT in range (writes marker) and out of range (CB)
    link.send(b"\x07")
    _, p = link.expect({0x07}, var=True)
    labels = p.decode("utf-8").split("%") if p else []
    check("ASK 7 SCRIPT_INFO lists the marker script", labels == ["marker"], labels)
    link.send(bytes([157, 0]))
    b0, i0 = link.expect({0x9D, 0xCB}, fixed=1)
    marker_path = os.path.join(OUT, "marker.txt")
    marker = open(marker_path).read().strip() if os.path.exists(marker_path) else None
    check("DO 157 index 0: reply 9D 00 and script ran", b0 == 0x9D and i0[0] == 0 and marker is not None,
          {"reply": "%02x %02x" % (b0, i0[0]), "marker": marker})
    link.send(bytes([157, 99]))
    b1, i1 = link.expect({0x9D, 0xCB}, fixed=1)
    check("DO 157 index 99 (out of range): reply CB 63", b1 == 0xCB and i1[0] == 99,
          "%02x %02x" % (b1, i1[0]))

    # Step 7: disconnect, reconnect, START again
    link.close()
    link = connect_retry(PORT, 10)
    link.send(b"\x01")
    link.expect({0xC8})
    check("reconnect: camera reset (ASK 1 without camera -> C8)", True)
    # Probe: CAMERA_HAS_KEYS with no camera. The original transcript (repo fixture) replies C8.
    link.send(bytes([6]))  # CAMERA_HAS_KEYS, no camera selected
    link.s.settimeout(3)
    try:
        b6 = link.s.recv(1)
    except socket.timeout:
        b6 = None
    link.s.settimeout(10)
    check("ASK 6 with no camera answers C8 (fixture: original replies c8)", b6 == bytes([0xC8]),
          {"reply": "no reply within 3 s" if b6 is None else (b6.hex() or "eof")})
    names, _ = select_camera(link, "Camera")
    start_stream(link, 30.0, 5.0)
    t7 = time.perf_counter()
    link.pump(3.0)
    s7 = [(t, d) for t, d in link.frames if t >= t7]
    check("reconnect: START streams again (~3 s)", len(s7) > 40, {"packets": len(s7), "fps": round(fps_of(s7), 2)})
    check("reconnect: first packet is a keyframe", bool(s7) and is_keyframe(s7[0][1]))
    s7_first = s7[0][1] if s7 else b""
    stop_stream(link)

    # Stop/Start Serving in Blender, while connected: the client must see the connection closed
    st = bl("stop_start", timeout=60)
    check("Blender Stop then Start Serving ran", st["after_stop_is_serving"] is False and
          st["after_start_is_serving"] is True, st)
    eof = False
    link.s.settimeout(0.5)
    end = time.perf_counter() + 8.0
    while time.perf_counter() < end:
        try:
            b = link.s.recv(1)
        except socket.timeout:
            continue
        except OSError:
            eof = True
            break
        if not b:
            eof = True
            break
        if b[0] == 0x04:  # frame still in flight from before STOP: skip it
            link.s.settimeout(10)
            link._frame_body()
            link.s.settimeout(0.5)
            continue
        break  # any other byte: not EOF
    check("Stop Serving closed the connected client (EOF)", eof)
    link.close()
    link = connect_retry(PORT, 15)
    p = ask_info(link)
    check("reconnect after Stop/Start Serving: ASK 0 answers", p[6:].startswith(b"Blender_"))
    link.send(b"\x08")
    _, p = link.expect({0x08}, fixed=6)
    check("reconnect after Stop/Start Serving: ASK 8 == 2.0.0", struct.unpack("<3H", p) == (2, 0, 0))
    select_camera(link, "Camera")
    start_stream(link, 30.0, 5.0)
    t8 = time.perf_counter()
    link.pump(2.0)
    s8 = [(t, d) for t, d in link.frames if t >= t8]
    check("after Stop/Start Serving: stream resumes", len(s8) > 20, {"packets": len(s8)})
    stop_stream(link)
    link.close()

    state = bl("state")
    DETAIL["blender_state_end"] = state
    bl("quit")
    check("Blender quit on request", True)

    # Decode and image checks (after Blender is done, so decode time does not touch the stream)
    used, frames = decode_packets(s2_packets)
    DETAIL["decoder"] = used
    check("decode stream 1 with libvpx VP9 (%s)" % used, len(frames) > 0, {"decoded": len(frames),
                                                                          "packets": len(s2_packets)})
    if frames:
        sizes = set((f.width, f.height) for f in frames)
        w, h = frames[0].width, frames[0].height
        check("stream size: long edge 720, height 404 or 406 (16:9 render, even), all frames same",
              len(sizes) == 1 and w == 720 and h in (404, 406), {"sizes": sorted(sizes)})
        mid = frames[len(frames) // 2]
        rgb = mid.to_ndarray(format="rgb24")
        stats = red_stats(rgb)
        png_path = os.path.join(OUT, "decoded_frame.png")
        write_png(png_path, rgb)
        DETAIL["png"] = png_path
        check("orientation: marker rows all in top half (upright)",
              stats["marker_row_min"] is not None and stats["marker_row_max"] < h // 2 and stats["red_top"] > 200,
              stats)
        check("decoded frame not black", stats["mean_rgb"][0] + stats["mean_rgb"][1] + stats["mean_rgb"][2] > 15,
              stats["mean_rgb"])
        DETAIL["decoded_frames"] = len(frames)
        DETAIL["decoded_size"] = [w, h]
        DETAIL["decoded_frame_stats"] = stats
    used_fb, frames_fb = decode_packets(fb_packets[:200])
    check("fallback stream decodes (%d frames)" % len(frames_fb), len(frames_fb) > 0 and
          (frames_fb[0].width, frames_fb[0].height) == (720, 404) if frames_fb else False,
          [(f.width, f.height) for f in frames_fb[:1]])
    _, frames_s7 = decode_packets([s7_first])
    check("reconnect stream first packet decodes to 720x404", len(frames_s7) == 1 and
          (frames_s7[0].width, frames_s7[0].height) == (720, 404),
          [(f.width, f.height) for f in frames_s7])


def finish():
    ok = sum(1 for r in RESULTS if r["ok"])
    summary = {"passed": ok, "total": len(RESULTS), "results": RESULTS,
               "detail": DETAIL, "seconds": round(time.time() - T_BEGIN, 1)}
    with open(os.path.join(OUT, "client_results.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    print("SUMMARY: %d/%d passed" % (ok, len(RESULTS)), flush=True)
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        check("client aborted: %r" % (e,), False, traceback.format_exc().strip().splitlines()[-1])
        try:
            bl("quit", timeout=5)
        except Exception:  # noqa: BLE001
            pass
    sys.exit(finish())
