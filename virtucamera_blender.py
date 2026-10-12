"""VirtuCamera 1 host for Blender: callbacks, viewport stream, UI, registration.

Every bpy call runs on the main thread: host callbacks via the server's event
queue, capture via a timer, and the UI via Blender itself.
"""

import os
import time
import traceback

import bpy
import bpy.utils.previews
import gpu
import mathutils
import numpy as np
import qrcode
from bpy_extras import anim_utils

PLUGIN_VERSION = (2, 0, 0)
TRANSFORM_PATHS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle")
EULER_ORDERS = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")

# Blender (Z-up) -> VirtuCamera (Y-up) on column vectors: y' = z, z' = -y.
# The wire format is the transpose of (V @ M), so the camera's own axes are not
# converted, only the world. This is the VC1 convention; checked numerically.
V_FROM_B = mathutils.Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
V_TO_B = V_FROM_B.inverted()


# -- Transform conversion ---------------------------------------------------

def _matrix_to_wire(matrix):
    return tuple(v for row in (V_FROM_B @ matrix).transposed() for v in row)


def _wire_to_matrix(values):
    rows = mathutils.Matrix(tuple(tuple(values[i:i + 4]) for i in (0, 4, 8, 12)))
    return V_TO_B @ rows.transposed()


# -- F-Curves: slotted actions (4.4+, 5.x) with the legacy fallback ----------

def _assigned_channelbag(adt):
    if adt.action_slot is None:
        return None
    return anim_utils.action_get_channelbag_for_slot(adt.action, adt.action_slot)


def _fcurve_owner(datablock):
    """The object holding datablock's assigned F-Curves (.fcurves), or None."""
    adt = datablock.animation_data
    if adt is None or adt.action is None:
        return None
    action = adt.action
    if action.is_action_legacy:
        return action if hasattr(action, "fcurves") else None
    return _assigned_channelbag(adt)


def _iter_action_fcurves(datablock):
    owner = _fcurve_owner(datablock)
    return list(owner.fcurves) if owner is not None else []


def _remove_action_fcurves_by_path(datablock, match):
    owner = _fcurve_owner(datablock)
    if owner is None:
        return
    for fc in [fc for fc in owner.fcurves if match(fc.data_path)]:
        owner.fcurves.remove(fc)


def _fcurve(datablock, data_path, index=0):
    """Existing or new F-Curve; creates animation_data and an action if missing."""
    if datablock.animation_data is None:
        datablock.animation_data_create()
    adt = datablock.animation_data
    if adt.action is None:
        adt.action = bpy.data.actions.new(f"{datablock.name}Action")
    return adt.action.fcurve_ensure_for_datablock(datablock, data_path, index=index)


def _set_key(fc, frame, value):
    fc.keyframe_points.insert(float(frame), float(value), options={'FAST'})


def _euler_order(obj):
    return obj.rotation_mode if obj.rotation_mode in EULER_ORDERS else "XYZ"


# -- Scene helpers ----------------------------------------------------------

def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def _is_visible_camera(obj):
    return obj.type == 'CAMERA' and obj.visible_get()


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _find_view3d():
    """(space, WINDOW region) of a VIEW_3D area, preferring one in camera view."""
    fallback = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            space = area.spaces.active
            if space.region_3d.view_perspective == 'CAMERA':
                return space, region
            if fallback is None:
                fallback = (space, region)
    return fallback


def _script_number(name):
    head, sep, _ = name.partition("_")
    return int(head) if sep and head.isdigit() else None


def _script_files():
    """Full paths of the .py files in the scripts folder, numbered prefixes first."""
    folder = bpy.path.abspath(bpy.context.scene.vc_scripts_dir)
    if not os.path.isdir(folder):
        return []
    paths = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".py")]
    paths = [p for p in paths if os.path.isfile(p)]

    def order(path):
        name = os.path.basename(path)
        number = _script_number(name)
        return (number is None, number or 0, name)

    return sorted(paths, key=order)


def _script_label(path):
    stem = os.path.basename(path)[:-3]
    if _script_number(stem) is None:
        return stem
    return stem.partition("_")[2]


# -- Host callbacks (VC1 names, no vcserver argument) -----------------------

class VirtuCameraHost:
    # Scene and playback

    def get_playback_state(self):
        scene = bpy.context.scene
        return (float(scene.frame_current), float(scene.frame_start), float(scene.frame_end))

    def get_playback_fps(self):
        render = bpy.context.scene.render
        return render.fps / render.fps_base

    def set_frame(self, frame):
        bpy.context.scene.frame_set(int(round(frame)))

    def set_playback_range(self, start, end):
        scene = bpy.context.scene
        scene.frame_start = int(round(start))
        scene.frame_end = int(round(end))

    def start_playback(self, forward):
        screen = bpy.context.screen
        if not screen.is_animation_playing:
            bpy.ops.screen.animation_play(reverse=not forward, sync=True)

    def stop_playback(self):
        if bpy.context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)

    # Cameras

    def get_scene_cameras(self):
        return [obj.name for obj in bpy.data.objects if _is_visible_camera(obj)]

    def get_camera_exists(self, camera_name):
        obj = bpy.data.objects.get(camera_name)
        return obj is not None and _is_visible_camera(obj)

    def get_camera_has_keys(self, camera_name):
        obj = bpy.data.objects[camera_name]
        transform = any(fc.data_path in TRANSFORM_PATHS for fc in _iter_action_fcurves(obj))
        flen = any(fc.data_path == "lens" for fc in _iter_action_fcurves(obj.data))
        return (transform, flen)

    def get_camera_focal_length(self, camera_name):
        return bpy.data.objects[camera_name].data.lens

    def get_camera_transform(self, camera_name):
        bpy.context.view_layer.update()  # matrix_local is cached until the depsgraph updates
        return _matrix_to_wire(bpy.data.objects[camera_name].matrix_local)

    def set_camera_focal_length(self, camera_name, focal_length):
        bpy.data.objects[camera_name].data.lens = float(focal_length)

    def set_camera_transform(self, camera_name, transform_matrix):
        bpy.data.objects[camera_name].matrix_local = _wire_to_matrix(transform_matrix)

    def set_camera_flen_keys(self, camera_name, keyframes, focal_length_values):
        cam = bpy.data.objects[camera_name].data
        fc = _fcurve(cam, "lens")
        for frame, lens in zip(keyframes, focal_length_values):
            _set_key(fc, frame, lens)
        fc.update()
        if focal_length_values:
            cam.lens = float(focal_length_values[-1])

    def set_camera_transform_keys(self, camera_name, keyframes, transform_matrix_values):
        obj = bpy.data.objects[camera_name]
        order = _euler_order(obj)
        loc_curves = [_fcurve(obj, "location", i) for i in range(3)]
        rot_curves = [_fcurve(obj, "rotation_euler", i) for i in range(3)]
        prev = obj.rotation_euler.copy()
        pairs = sorted(zip(keyframes, transform_matrix_values), key=lambda pair: pair[0])
        for frame, values in pairs:
            matrix = _wire_to_matrix(values)
            loc = matrix.to_translation()
            # Compatible with the previous key, so no Graph Editor pass is needed.
            prev = matrix.to_3x3().normalized().to_euler(order, prev)
            for i in range(3):
                _set_key(loc_curves[i], frame, loc[i])
                _set_key(rot_curves[i], frame, prev[i])
        for fc in loc_curves + rot_curves:
            fc.update()
        if pairs:
            obj.location = loc
            obj.rotation_euler = prev

    def remove_camera_keys(self, camera_name):
        obj = bpy.data.objects[camera_name]
        _remove_action_fcurves_by_path(obj, lambda path: path in TRANSFORM_PATHS)
        _remove_action_fcurves_by_path(obj.data, lambda path: path == "lens")

    def create_new_camera(self):
        scene = bpy.context.scene
        cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        cam.location = scene.cursor.location
        scene.collection.objects.link(cam)
        return cam.name

    def look_through_camera(self, camera_name):
        bpy.context.scene.camera = bpy.data.objects[camera_name]
        view = _find_view3d()
        if view is not None:
            view[0].region_3d.view_perspective = 'CAMERA'

    # Viewport capture

    def capture_will_start(self):
        global _stream
        _stop_capture()
        w, h = _stream_size(bpy.context.scene)
        _stream = _Stream(w, h)
        bpy.app.timers.register(_capture_tick, first_interval=_capture_period(), persistent=True)

    def capture_did_end(self):
        _stop_capture()

    # Feedback: only redraws the UI

    def client_connected(self, client_ip, client_port):
        _tag_redraw()

    def client_disconnected(self):
        _tag_redraw()

    def current_camera_changed(self, current_camera):
        _tag_redraw()

    def server_did_stop(self):
        _tag_redraw()

    # Custom scripts

    def get_script_labels(self):
        return [_script_label(path) for path in _script_files()]

    def execute_script(self, script_index, current_camera):
        files = _script_files()
        if not 0 <= script_index < len(files):
            print(f"VirtuCamera: can't run script {script_index + 1}: no such script")
            return False
        path = files[script_index]
        try:
            with open(path, encoding="utf-8") as f:
                source = f.read()
            if not source.strip():
                print(f"VirtuCamera: can't run script {script_index + 1}: empty script")
                return False
            code = compile(source, path, "exec")
            # VC1 ran scripts with its module globals; expose the common ones.
            exec(code, {"__name__": "vc_script", "bpy": bpy, "mathutils": mathutils,
                        "vc_selcam": current_camera})
            return True
        except Exception:
            traceback.print_exc()
            return False


# -- Viewport stream --------------------------------------------------------

class _Stream:
    """One offscreen target and one flat readback buffer for a stream size."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.off = gpu.types.GPUOffScreen(w, h)
        self.buf = gpu.types.Buffer('UBYTE', (w * h * 4,))

    def free(self):
        self.off.free()


_stream = None


def _stream_size(scene):
    render = scene.render
    aspect = (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)
    long_edge = _prefs().stream_long_edge
    w, h = (long_edge, long_edge / aspect) if aspect >= 1 else (long_edge * aspect, long_edge)
    return max(2, 2 * round(w / 2)), max(2, 2 * round(h / 2))


def _capture_camera(scene):
    cam = bpy.data.objects.get(_server.current_camera or "")
    if cam is None or cam.type != 'CAMERA':
        cam = scene.camera
    return cam if cam is not None and cam.type == 'CAMERA' else None


def _render_stream():
    global _stream
    scene = bpy.context.scene
    w, h = _stream_size(scene)
    if (w, h) != (_stream.w, _stream.h):
        _stream.free()
        _stream = _Stream(w, h)
    view = _find_view3d()
    cam = _capture_camera(scene)
    if view is None or cam is None:
        return
    space, region = view
    stream = _stream
    depsgraph = bpy.context.evaluated_depsgraph_get()
    with stream.off.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=(0.0, 0.0, 0.0, 1.0))
        stream.off.draw_view3d(
            scene, bpy.context.view_layer, space, region,
            cam.matrix_world.inverted(), cam.calc_matrix_camera(depsgraph, x=w, y=h),
            do_color_management=True)
        fb.read_color(0, 0, w, h, 4, 0, 'UBYTE', data=stream.buf)
    _server.submit_frame(np.frombuffer(stream.buf, np.uint8), w, h)


def _capture_period():
    return 1.0 / max(1.0, _server.capture_fps)


def _capture_tick():
    if _stream is None or _server is None or not _server.is_serving:
        return None
    start = time.perf_counter()
    try:
        _render_stream()
    except Exception:
        traceback.print_exc()
    # Subtract the render time so the timer keeps the app's fps.
    return max(0.0, _capture_period() - (time.perf_counter() - start))


def _stop_capture():
    global _stream
    if bpy.app.timers.is_registered(_capture_tick):
        bpy.app.timers.unregister(_capture_tick)
    if _stream is not None:
        _stream.free()
        _stream = None


# -- Server lifecycle and event pump ----------------------------------------

_server = None
_previews = None
_qr_icon = 0
_registered = False


def _pump_events():
    if _server is None:
        return None
    try:
        _server.execute_pending_events()
    except Exception:
        traceback.print_exc()
    if _server.is_serving:
        return 0.0
    _stop_capture()
    return None


def _set_qr(text):
    global _qr_icon
    qr = qrcode.QRCode(border=4)
    qr.add_data(text)
    qr.make(fit=True)
    dark = np.array(qr.get_matrix(), dtype=bool)
    scale = 4
    gray = np.repeat(np.repeat(np.where(dark, 0.0, 1.0).astype(np.float32), scale, axis=0), scale, axis=1)
    rgba = np.dstack([gray, gray, gray, np.ones_like(gray)])[::-1]  # Blender rows are bottom-up
    _previews.clear()
    preview = _previews.new("qr")
    preview.image_size = (rgba.shape[1], rgba.shape[0])
    preview.image_pixels_float = rgba.ravel().tolist()
    _qr_icon = preview.icon_id


def _shutdown():
    global _server, _previews, _qr_icon
    if _server is not None and _server.is_serving:
        _server.stop_serving()
    _server = None
    if bpy.app.timers.is_registered(_pump_events):
        bpy.app.timers.unregister(_pump_events)
    _stop_capture()
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
    _qr_icon = 0


def _scripts_changed(self, context):
    if _server is not None:
        _server.update_script_labels()


class VC_OT_start_serving(bpy.types.Operator):
    bl_idname = "virtucamera.start_serving"
    bl_label = "Start Serving"
    bl_description = "Listen for VirtuCamera 1 connections, then scan the QR code in the app"

    @classmethod
    def poll(cls, context):
        return _server is None or not _server.is_serving

    def execute(self, context):
        global _server
        if _server is None:
            from .vc_server import VCServer
            _server = VCServer(VirtuCameraHost(), plugin_version=PLUGIN_VERSION, platform="Blender")
        port = context.scene.vc_port
        if not _server.start_serving(port):
            self.report({'ERROR'}, f"VirtuCamera: could not listen on port {port}")
            return {'CANCELLED'}
        _set_qr(_server.qr_string())
        if not bpy.app.timers.is_registered(_pump_events):
            bpy.app.timers.register(_pump_events, persistent=True)
        _tag_redraw()
        return {'FINISHED'}


class VC_OT_stop_serving(bpy.types.Operator):
    bl_idname = "virtucamera.stop_serving"
    bl_label = "Stop Serving"
    bl_description = "Stop listening for VirtuCamera 1 connections"

    @classmethod
    def poll(cls, context):
        return _server is not None and _server.is_serving

    def execute(self, context):
        _server.stop_serving()
        _stop_capture()
        _tag_redraw()
        return {'FINISHED'}


class VIEW3D_PT_virtucamera(bpy.types.Panel):
    bl_label = "VirtuCamera"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "VirtuCamera"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        serving = _server is not None and _server.is_serving
        connected = serving and _server.is_connected
        layout.label(text="v" + ".".join(map(str, PLUGIN_VERSION)))
        row = layout.row()
        row.enabled = not serving
        row.prop(scene, "vc_port")
        row = layout.row(align=True)
        row.operator(VC_OT_start_serving.bl_idname, icon='PLAY')
        row.operator(VC_OT_stop_serving.bl_idname, icon='PAUSE')
        if serving and not connected and _qr_icon:
            col = layout.column(align=True)
            col.label(text="Server Ready")
            col.label(text="Connect through the App")
            layout.template_icon(icon_value=_qr_icon, scale=6.0)
        elif connected:
            layout.label(text=f"Connected: {_server.client_ip}", icon='CHECKMARK')
            if _server.current_camera:
                layout.label(text=_server.current_camera, icon='VIEW_CAMERA')
        layout.separator()
        layout.prop(scene, "vc_scripts_dir")


class VirtuCameraPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    stream_long_edge: bpy.props.IntProperty(
        name="Stream long edge",
        description="Long edge of the streamed camera view in pixels (the short edge follows the render aspect). "
                    "Values above 720 were never sent by the original VirtuCamera pipeline and may strain older phones",
        default=720, min=256, max=1920,
    )

    def draw(self, context):
        self.layout.prop(self, "stream_long_edge")


_classes = (VirtuCameraPreferences, VC_OT_start_serving, VC_OT_stop_serving, VIEW3D_PT_virtucamera)


def register():
    global _registered, _previews
    if _registered:
        return
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.vc_port = bpy.props.IntProperty(
        name="Port", description="TCP port to listen on for the VirtuCamera 1 app",
        default=23354, min=0, max=65535,
    )
    bpy.types.Scene.vc_scripts_dir = bpy.props.StringProperty(
        name="Scripts",
        description="Folder of .py scripts shown as buttons in the app. Prefix a file name with a number to set its order (e.g. 1_myscript.py)",
        subtype='DIR_PATH', default="", update=_scripts_changed,
    )
    _previews = bpy.utils.previews.new()
    _registered = True


def unregister():
    global _registered
    if not _registered:
        return
    _shutdown()
    del bpy.types.Scene.vc_scripts_dir
    del bpy.types.Scene.vc_port
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    _registered = False
