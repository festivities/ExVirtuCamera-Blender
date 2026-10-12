"""Regenerates tests/fixtures/vc1_original_transcript.json from the original binary.

Run with Python 3.9, which the original vc_core binary needs:
    py -3.9 tests\\regen_conformance_fixture.py

Loopback only. The original binds 0.0.0.0, so socket bind is patched to 127.0.0.1.
Child processes it starts (the encoder child on START) are its own and exit with it.
"""
import hashlib
import io
import json
import os
import socket
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ORIG = os.path.join(REPO, "data", "virtucamera_blender_original")
PYD = os.path.join(ORIG, "virtucamera", "vc_core.cp39-win_amd64.pyd")
FIXTURE = os.path.join(HERE, "fixtures", "vc1_original_transcript.json")

sys.path.insert(0, HERE)
sys.path.insert(0, ORIG)

import vc1_conformance as vc  # noqa: E402

_real_bind = socket.socket.bind


def _loopback_bind(self, addr, *rest):
    if isinstance(addr, tuple) and addr and addr[0] in ("0.0.0.0", ""):
        addr = ("127.0.0.1", addr[1])
    return _real_bind(self, addr, *rest)


socket.socket.bind = _loopback_bind

from virtucamera.vc_base import VCBase  # noqa: E402
from virtucamera.vc_core import VCServer  # noqa: E402


def make_adapter(host):
    """Original callbacks take vcserver first. Forwards them to the shared FakeHost."""
    ns = {"__init__": lambda self, h: setattr(self, "host", h)}

    def forward(name):
        def method(self, vcserver, *args):
            return getattr(self.host, name)(*args)
        return method

    for name in vc.HOST_CALLBACKS:
        ns[name] = forward(name)

    def capture_will_start(self, vcserver):
        self.host.capture_will_start()
        vcserver.set_capture_resolution(64, 32)
        vcserver.set_capture_mode(vcserver.CAPMODE_BUFFER, vcserver.CAPFORMAT_UBYTE_BGRA)

    def get_capture_buffer(self, vcserver, camera_name):
        return bytearray(64 * 32 * 4)  # black frames, so the autosend thread runs

    ns["capture_will_start"] = capture_will_start
    ns["get_capture_buffer"] = get_capture_buffer
    return type(VCBase)("OriginalAdapter", (VCBase,), ns)(host)


def main():
    import virtucamera.vc_core  # noqa: F401  (loads the original binary)

    host = vc.FakeHost()
    server = VCServer(platform=vc.PLATFORM, plugin_version=vc.PLUGIN_VERSION,
                      vcbase=make_adapter(host), event_mode=VCServer.EVENTMODE_PULL,
                      python_executable=sys.executable)
    port = vc.free_port()
    stop = threading.Event()
    with vc.fixed_hostname():
        if not server.start_serving(port):
            raise SystemExit("start_serving failed on port %d" % port)
        pumper = threading.Thread(target=vc.pump, args=(server, stop), daemon=True)
        pumper.start()
        try:
            steps = vc.run_script(port, host, server)
        finally:
            stop.set()
            pumper.join(5)
            if server.is_serving:
                server.stop_serving()

    with open(PYD, "rb") as f:
        pyd_sha256 = hashlib.sha256(f.read()).hexdigest()
    fixture = {
        "source": "original vc_core.cp39-win_amd64.pyd, py %s" % sys.version.split()[0],
        "pyd_sha256": pyd_sha256,
        "platform": vc.PLATFORM,
        "plugin_version": list(vc.PLUGIN_VERSION),
        "computer_name": vc.HOST_NAME,
        "generated_by": "tests/regen_conformance_fixture.py",
        "steps": steps,
    }
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    with io.open(FIXTURE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(fixture, f, indent=1, sort_keys=True)
        f.write("\n")
    print("wrote %s (%d steps)" % (FIXTURE, len(steps)))


if __name__ == "__main__":
    main()
