"""GUI host for tests\\run_e2e.ps1 (runs inside Blender via --python, not on its own).

Installs the built zip into the run's scratch user dirs (redirect verified first), enables it,
builds a known scene, starts serving on loopback from a real VIEW_3D, writes ready.json, then
answers req_NNN.json files from e2e_client.py with resp_NNN.json. Quits on the 'quit' request.
Run it through:  pwsh tests\\run_e2e.ps1 -Exe C:\\Apps\\blender\\blender.exe
Env (set by run_e2e.ps1): E2E_OUT (work dir), E2E_PORT, E2E_ZIP (extension zip), E2E_SCRIPTS.
"""
import hashlib
import importlib
import json
import math
import os
import sys
import traceback
import zipfile

import bpy
from bpy_extras import anim_utils

OUT = os.environ["E2E_OUT"]
PORT = int(os.environ["E2E_PORT"])
ZIP = os.environ["E2E_ZIP"]
SCRIPTS = os.environ["E2E_SCRIPTS"]
MOD = "bl_ext.user_default.exvirtucamera_blender"

vb = None  # the addon's virtucamera_blender module, set after enable


def log(*args):
    print("E2E:", *args, flush=True)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def under(path, root):
    a = os.path.normcase(os.path.abspath(path))
    b = os.path.normcase(os.path.abspath(root))
    return a == b or a.startswith(b + os.sep)


def die(msg, code=2):
    log("FATAL", msg)
    write_json(os.path.join(OUT, "ready.json"), {"ok": False, "error": msg})
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def view_override():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is not None:
                    return dict(window=win, area=area, region=region)
    raise RuntimeError("no VIEW_3D area in any window")


# -- Install and enable -----------------------------------------------------

def install_zip():
    zf = zipfile.ZipFile(ZIP)
    zip_members = {n: zf.read(n) for n in ("__init__.py", "vc_server.py", "virtucamera_blender.py",
                                           "blender_manifest.toml")}
    try:
        cls = getattr(bpy.types, "EXTENSIONS_OT_package_install_files")
        names = set(cls.bl_rna.properties.keys())
    except Exception as e:  # noqa: BLE001
        names = set()
        log("could not introspect install operator:", repr(e))
    log("install operator props:", sorted(names))
    log("repos:", [(r.module, r.directory) for r in bpy.context.preferences.extensions.repos])
    kw = {"filepath": ZIP, "repo": "user_default", "enable_on_install": True}
    if "overwrite" in names:
        kw["overwrite"] = True
    method = "package_install_files"
    try:
        with bpy.context.temp_override(**view_override()):
            res = bpy.ops.extensions.package_install_files(**kw)
        log("package_install_files", kw, "->", res)
        if "FINISHED" not in res:
            raise RuntimeError("operator returned %r" % (res,))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        method = "EXTRACT_FALLBACK (operator failed: %r)" % (e,)
        log("operator install failed, extracting zip into scratch repo instead")
        ext_dir = os.path.join(bpy.utils.user_resource("EXTENSIONS"), "user_default", "exvirtucamera_blender")
        os.makedirs(ext_dir, exist_ok=True)
        zf.extractall(ext_dir)
    inst_dir = os.path.join(bpy.utils.user_resource("EXTENSIONS"), "user_default", "exvirtucamera_blender")
    hashes = {}
    for name, data in zip_members.items():
        p = os.path.join(inst_dir, name)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                hashes[name] = sha256_bytes(f.read()) == sha256_bytes(data)
        else:
            hashes[name] = False
    if MOD not in bpy.context.preferences.addons:
        bpy.ops.preferences.addon_enable(module=MOD)
    return {"method": method, "installed_dir": inst_dir, "hashes_match_zip": hashes,
            "enabled": MOD in bpy.context.preferences.addons}


# -- Scene ------------------------------------------------------------------

def setup_scene():
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        if obj.type not in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(obj, do_unlink=True)
    cam = bpy.data.objects.get("Camera")
    if cam is None or cam.type != "CAMERA":
        raise RuntimeError("factory scene has no 'Camera'")
    cam.location = (0.0, -6.0, 0.0)
    cam.rotation_mode = "XYZ"
    cam.rotation_euler = (math.pi / 2, 0.0, 0.0)  # looks along +Y, up is +Z
    cam.data.lens = 50.0
    scene.camera = cam
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    mesh = bpy.data.meshes.new("E2EMarkerMesh")
    mesh.from_pydata([(-0.8, 0.0, 0.3), (0.8, 0.0, 0.3), (0.8, 0.0, 1.1), (-0.8, 0.0, 1.1)], [], [(0, 1, 2, 3)])
    mesh.update()
    mat = bpy.data.materials.new("E2EMarkerRed")
    mat.diffuse_color = (1.0, 0.0, 0.0, 1.0)
    mesh.materials.append(mat)
    marker = bpy.data.objects.new("E2EMarker", mesh)
    scene.collection.objects.link(marker)
    scene.vc_port = PORT
    scene.vc_scripts_dir = SCRIPTS
    return {"camera": cam.name, "resolution": [scene.render.resolution_x, scene.render.resolution_y]}


# -- F-Curve reads (independent of the addon; channelbag for slotted actions) --

def _fcurves(datablock):
    adt = datablock.animation_data
    if adt is None or adt.action is None:
        return []
    act = adt.action
    if getattr(act, "is_action_legacy", True):
        return list(getattr(act, "fcurves", []))  # empty actions on 5.2 report legacy without fcurves
    cb = anim_utils.action_get_channelbag_for_slot(act, adt.action_slot)
    return list(cb.fcurves) if cb is not None else []


def _curve_dump(datablock):
    out = {}
    for fc in _fcurves(datablock):
        key = "%s[%d]" % (fc.data_path, fc.array_index)
        out[key] = {"frames": [kp.co[0] for kp in fc.keyframe_points],
                    "values": [kp.co[1] for kp in fc.keyframe_points]}
    return out


# -- Request handlers -------------------------------------------------------

def op_inspect(_req):
    scene = bpy.context.scene
    cam = bpy.data.objects["Camera"]
    bpy.context.view_layer.update()
    return {
        "scene_camera": scene.camera.name if scene.camera else None,
        "location": list(cam.location),
        "rotation_euler": list(cam.rotation_euler),
        "rotation_mode": cam.rotation_mode,
        "lens": cam.data.lens,
        "matrix_world_translation": list(cam.matrix_world.to_translation()),
        "object_curves": _curve_dump(cam),
        "data_curves": _curve_dump(cam.data),
        "slotted_actions": bool(cam.animation_data and cam.animation_data.action
                                and not getattr(cam.animation_data.action, "is_action_legacy", True)),
    }


def op_state(_req):
    srv = vb._server
    return {"is_serving": srv.is_serving, "is_connected": srv.is_connected,
            "is_capturing": srv.is_capturing, "client_ip": srv.client_ip,
            "qr": srv.qr_string() if srv.is_serving else None}


def op_stop_start(_req):
    with bpy.context.temp_override(**view_override()):
        bpy.ops.virtucamera.stop_serving()
    after_stop = vb._server.is_serving
    with bpy.context.temp_override(**view_override()):
        res = bpy.ops.virtucamera.start_serving()
    return {"after_stop_is_serving": after_stop, "start_result": sorted(res),
            "after_start_is_serving": vb._server.is_serving, "qr": vb._server.qr_string()}


def op_start(_req):
    with bpy.context.temp_override(**view_override()):
        res = bpy.ops.virtucamera.start_serving()
    return {"start_result": sorted(res), "is_serving": vb._server.is_serving}


def op_quit(_req):
    return {"bye": True}


OPS = {"inspect": op_inspect, "state": op_state, "stop_start": op_stop_start,
       "start": op_start, "quit": op_quit}


def poll():
    try:
        for name in sorted(os.listdir(OUT)):
            if not (name.startswith("req_") and name.endswith(".json")):
                continue
            path = os.path.join(OUT, name)
            with open(path) as f:
                req = json.load(f)
            os.remove(path)
            rid = req["id"]
            try:
                resp = {"ok": True, "result": OPS[req["op"]](req)}
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                resp = {"ok": False, "error": repr(e)}
            write_json(os.path.join(OUT, "resp_%s.json" % rid), resp)
            log("request", rid, req["op"], "ok" if resp["ok"] else "ERROR")
            if req["op"] == "quit":
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(0)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    return 0.05


def _watchdog():
    log("self-watchdog fired after 110 s")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(3)


def startup():
    global vb
    try:
        log("version", bpy.app.version_string, "python", sys.version.split()[0])
        for key in ("CONFIG", "SCRIPTS", "EXTENSIONS", "DATAFILES"):
            p = bpy.utils.user_resource(key)
            log("user_resource", key, p)
            if not under(p, OUT):
                die("user_resource %s is outside the run's -Out dir: %s" % (key, p))
        log("redirect verified: all user_resource paths under", OUT)
        info = {"ok": False}
        info["install"] = install_zip()
        log("install:", info["install"])
        if not info["install"]["enabled"]:
            die("addon not enabled after install")
        vb = importlib.import_module(MOD + ".virtucamera_blender")
        # Loopback only: the addon binds 0.0.0.0 by default; the fake client is 127.0.0.1.
        importlib.import_module(MOD + ".vc_server").BIND_HOST = "127.0.0.1"
        prefs = bpy.context.preferences.addons[MOD].preferences
        info["stream_long_edge_pref"] = prefs.stream_long_edge
        info["scene"] = setup_scene()
        with bpy.context.temp_override(**view_override()):
            bpy.ops.virtucamera.start_serving()
        srv = vb._server
        if not srv.is_serving:
            die("start_serving did not start (port %d)" % PORT)
        info.update({"ok": True, "port": PORT, "qr": srv.qr_string(), "version": bpy.app.version_string,
                     "scripts_dir": SCRIPTS, "addon_version": getattr(vb, "PLUGIN_VERSION", None)})
        write_json(os.path.join(OUT, "ready.json"), info)
        log("READY serving on", PORT)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        die("startup failed: %r" % (e,))
    return None


try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001
    pass
bpy.app.timers.register(_watchdog, first_interval=110.0)
bpy.app.timers.register(poll, first_interval=0.05, persistent=True)
bpy.app.timers.register(startup, first_interval=0.5)
