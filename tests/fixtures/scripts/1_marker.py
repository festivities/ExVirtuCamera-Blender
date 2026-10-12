"""Custom script fixture for tests\\e2e_blender.py (run by tests\\run_e2e.ps1).

The addon runs it on the Blender main thread when the client sends EXECUTE_SCRIPT (157, index 0).
It writes marker.txt into the run's work dir (E2E_OUT) so the client can confirm the run.
"""
import os

_p = os.path.join(os.environ["E2E_OUT"], "marker.txt")
with open(_p, "w") as _f:
    _f.write("ran, vc_selcam=%r\n" % (vc_selcam,))
