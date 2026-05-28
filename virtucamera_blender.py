import os
import sys
import math
import traceback

import bpy
import bpy.utils.previews
import gpu
import mathutils
from multiprocessing.shared_memory import SharedMemory

from .virtucamera import VCBase, VCServer

plugin_version = (1, 1, 0)


class VirtuCameraBlender(VCBase):
    TRANSFORM_CHANNELS = (
        "location", "rotation_euler", "rotation_quaternion",
        "rotation_axis_angle",
    )
    B_TO_V_ROTATION_MAT = mathutils.Matrix((
        (1, 0, 0, 0),
        (0, 0, -1, 0),
        (0, 1, 0, 0),
        (0, 0, 0, 1),
    ))
    V_TO_B_ROTATION_MAT = mathutils.Matrix((
        (1, 0, 0, 0),
        (0, 0, 1, 0),
        (0, -1, 0, 0),
        (0, 0, 0, 1),
    ))

    last_rect_data = None
    _shm = None
    _shm_name = None

    # Utility

    def camera_rect_changed(
        self, offset_value_x, offset_value_y, zoom_value,
        region_rect, camera_aspect_ratio,
    ):
        rect_data = (
            offset_value_x, offset_value_y, zoom_value,
            region_rect, camera_aspect_ratio,
        )
        if self.last_rect_data != rect_data:
            self.last_rect_data = rect_data
            return True
        return False

    def view_zoom_factor(self, zoom_value):
        return ((zoom_value / 50 + math.sqrt(2)) / 2) ** 2

    def view_region_width_zoom_factor(
        self, zoom_factor, region_aspect_ratio, camera_aspect_ratio,
    ):
        return zoom_factor * min(camera_aspect_ratio, 1) / min(
            region_aspect_ratio, 1
        )

    def view_offset_factor(self, offset_value, zoom_factor):
        return offset_value * zoom_factor * -2

    def _find_3d_region(self):
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        return area, region
        return None, None

    def get_view_camera_rect(self):
        scene = bpy.context.scene
        render = scene.render
        area, region = self._find_3d_region()
        if region is None:
            return getattr(self, "last_rect", (0, 0, 640, 480))

        r3d = area.spaces.active.region_3d

        zoom_value = r3d.view_camera_zoom
        offset_value_x = r3d.view_camera_offset[0]
        offset_value_y = r3d.view_camera_offset[1]
        region_rect = (region.x, region.y, region.width, region.height)
        camera_aspect_ratio = (
            (render.resolution_x * render.pixel_aspect_x)
            / (render.resolution_y * render.pixel_aspect_y)
        )

        if self.camera_rect_changed(
            offset_value_x, offset_value_y, zoom_value,
            region_rect, camera_aspect_ratio,
        ):
            region_aspect_ratio = region.width / region.height
            zoom_factor = self.view_zoom_factor(zoom_value)
            width_factor = self.view_region_width_zoom_factor(
                zoom_factor, region_aspect_ratio, camera_aspect_ratio,
            )
            width = int(region.width * width_factor)
            height = int(width / camera_aspect_ratio)
            offset_factor_x = self.view_offset_factor(
                offset_value_x, zoom_factor,
            )
            offset_factor_y = self.view_offset_factor(
                offset_value_y, zoom_factor,
            )
            x = int(
                (region.width - width) * 0.5
                + region.width * offset_factor_x
            )
            y = int(
                (region.height - height) * 0.5
                + region.height * offset_factor_y
            )
            width = min(width, region.width)
            height = min(height, region.height)
            x = min(max(x, 0), region.width - width)
            y = min(max(y, 0), region.height - height)
            self.last_rect = (x, y, width, height)

        return self.last_rect

    def get_script_files(self):
        scripts_dir = bpy.context.scene.virtucamera.custom_scripts_dir
        if not os.path.isdir(scripts_dir):
            return []
        dir_files = os.listdir(scripts_dir)
        dir_files.sort()
        valid_files = []
        for file in dir_files:
            if file.endswith(".py"):
                filepath = os.path.join(scripts_dir, file)
                if os.path.isdir(filepath):
                    continue
                valid_files.append(filepath)
        return valid_files

    # Scene state

    def get_playback_state(self, vcserver):
        current_frame = bpy.context.scene.frame_current
        range_start = bpy.context.scene.frame_start
        range_end = bpy.context.scene.frame_end
        return (current_frame, range_start, range_end)

    def get_playback_fps(self, vcserver):
        return bpy.context.scene.render.fps

    def set_frame(self, vcserver, frame):
        bpy.context.scene.frame_current = frame

    def set_playback_range(self, vcserver, start, end):
        bpy.context.scene.frame_start = start
        bpy.context.scene.frame_end = end

    def start_playback(self, vcserver, forward):
        if not bpy.context.screen.is_animation_playing:
            bpy.ops.screen.animation_play(reverse=(not forward), sync=True)

    def stop_playback(self, vcserver):
        bpy.ops.screen.animation_cancel(restore_frame=False)

    # Camera

    def get_scene_cameras(self, vcserver):
        scene_cameras = []
        for obj in bpy.data.objects:
            if obj.type == "CAMERA" and obj.visible_get():
                scene_cameras.append(obj)
        return [camera.name for camera in scene_cameras]

    def get_camera_exists(self, vcserver, camera_name):
        if camera_name in bpy.data.objects:
            return bpy.data.objects[camera_name].visible_get()
        return False

    def get_camera_has_keys(self, vcserver, camera_name):
        camera = bpy.data.objects[camera_name]
        transform_has_keys = False
        if camera.animation_data and camera.animation_data.action:
            for fcu in camera.animation_data.action.fcurves:
                if fcu.data_path in self.TRANSFORM_CHANNELS:
                    transform_has_keys = True
                    break
        focal_length_has_keys = False
        if camera.data.animation_data and camera.data.animation_data.action:
            for fcu in camera.data.animation_data.action.fcurves:
                if fcu.data_path == "lens":
                    focal_length_has_keys = True
                    break
        return (transform_has_keys, focal_length_has_keys)

    def get_camera_focal_length(self, vcserver, camera_name):
        return bpy.data.objects[camera_name].data.lens

    def get_camera_transform(self, vcserver, camera_name):
        camera_matrix = bpy.data.objects[camera_name].matrix_local.transposed()
        camera_matrix @= self.B_TO_V_ROTATION_MAT
        return (
            *camera_matrix[0],
            *camera_matrix[1],
            *camera_matrix[2],
            *camera_matrix[3],
        )

    def set_camera_focal_length(self, vcserver, camera_name, focal_length):
        bpy.data.objects[camera_name].data.lens = focal_length

    def set_camera_transform(self, vcserver, camera_name, transform_matrix):
        camera = bpy.data.objects[camera_name]
        matrix = mathutils.Matrix((
            transform_matrix[0:4],
            transform_matrix[4:8],
            transform_matrix[8:12],
            transform_matrix[12:16],
        ))
        matrix @= self.V_TO_B_ROTATION_MAT
        matrix.transpose()
        camera.matrix_local = matrix
        # Force redraw to prevent timer throttling
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    def set_camera_flen_keys(
        self, vcserver, camera_name, keyframes, focal_length_values,
    ):
        camera_data = bpy.data.objects[camera_name].data
        for keyframe, focal_length in zip(keyframes, focal_length_values):
            camera_data.lens = focal_length
            camera_data.keyframe_insert('lens', frame=keyframe)

    def set_camera_transform_keys(
        self, vcserver, camera_name, keyframes, transform_matrix_values,
    ):
        camera = bpy.data.objects[camera_name]
        for keyframe, matrix in zip(keyframes, transform_matrix_values):
            self.set_camera_transform(vcserver, camera_name, matrix)
            camera.keyframe_insert('location', frame=keyframe)
            camera.keyframe_insert('rotation_euler', frame=keyframe)
        bpy.ops.graph.virtucamera_euler_filter(object_name=camera_name)

    def remove_camera_keys(self, vcserver, camera_name):
        camera = bpy.data.objects[camera_name]
        if camera.animation_data and camera.animation_data.action:
            for fcu in list(camera.animation_data.action.fcurves):
                if fcu.data_path in self.TRANSFORM_CHANNELS:
                    camera.animation_data.action.fcurves.remove(fcu)
        if camera.data.animation_data and camera.data.animation_data.action:
            for fcu in list(camera.data.animation_data.action.fcurves):
                if fcu.data_path == "lens":
                    camera.data.animation_data.action.fcurves.remove(fcu)
                    break

    def create_new_camera(self, vcserver):
        bpy.ops.object.camera_add(enter_editmode=False)
        return bpy.context.scene.objects[-1].name

    # Viewport capture

    def capture_will_start(self, vcserver):
        (x, y, width, height) = self.get_view_camera_rect()
        shm_size = width * height * 4
        if not hasattr(self, '_shm_counter'):
            self._shm_counter = 0
        self._shm_counter += 1
        shm_name = f"vc_blender_{os.getpid()}_{self._shm_counter}"
        try:
            _old = SharedMemory(name=shm_name)
            _old.close()
            _old.unlink()
        except Exception:
            pass
            
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass
        self._shm = SharedMemory(name=shm_name, create=True, size=shm_size)
        self._shm_name = shm_name
        vcserver.set_shm_name(shm_name)
        vcserver.set_capture_resolution(width, height)
        vcserver.set_capture_mode(
            vcserver.CAPMODE_BUFFER, vcserver.CAPFORMAT_UBYTE_BGRA,
        )
        vcserver.set_vertical_flip(True)

        # Create offscreen buffer for rendering
        self._offscreen = gpu.types.GPUOffScreen(width, height)
        self._capture_width = width
        self._capture_height = height

        # Start the capture timer
        if not hasattr(self, '_capture_timer_running') or not self._capture_timer_running:
            self._capture_timer_running = True
            bpy.app.timers.register(self._capture_timer, first_interval=0.0)

    def capture_did_end(self, vcserver):
        self._capture_timer_running = False
        if hasattr(self, '_offscreen') and self._offscreen is not None:
            self._offscreen.free()
            self._offscreen = None
        if self._shm is not None:
            try:
                self._shm.close()
                self._shm.unlink()
            except Exception:
                pass
            self._shm = None
            self._shm_name = None

    def _capture_timer(self):
        vcserver = _server
        if vcserver is None or self._shm is None or not self._capture_timer_running:
            self._capture_timer_running = False
            return None  # Stop timer

        try:
            (x, y, width, height) = self.get_view_camera_rect()

            # Handle resolution changes
            if width != self._capture_width or height != self._capture_height:
                vcserver.set_capture_resolution(width, height)
                shm_size = width * height * 4
                if not hasattr(self, '_shm_counter'):
                    self._shm_counter = 0
                self._shm_counter += 1
                shm_name = f"vc_blender_{os.getpid()}_{self._shm_counter}"
                try:
                    _old = SharedMemory(name=shm_name)
                    _old.close()
                    _old.unlink()
                except Exception:
                    pass
                try:
                    if self._shm is not None:
                        self._shm.close()
                except Exception:
                    pass
                try:
                    self._shm = SharedMemory(
                        name=shm_name, create=True, size=shm_size,
                    )
                    self._shm_name = shm_name
                    vcserver.set_shm_name(shm_name)
                except Exception:
                    self._shm = None
                    return None

                # Recreate offscreen buffer
                if self._offscreen is not None:
                    self._offscreen.free()
                self._offscreen = gpu.types.GPUOffScreen(width, height)
                self._capture_width = width
                self._capture_height = height

            # Find the 3D viewport
            area, region = self._find_3d_region()
            if area is None or region is None:
                return 1.0 / 30.0

            space = area.spaces.active
            r3d = space.region_3d

            # Render viewport into offscreen buffer
            import numpy as np
            with self._offscreen.bind():
                self._offscreen.draw_view3d(
                    bpy.context.scene,
                    bpy.context.view_layer,
                    space,
                    region,
                    r3d.view_matrix,
                    r3d.window_matrix,
                )
                fb = gpu.state.active_framebuffer_get()
                buffer = fb.read_color(0, 0, width, height, 4, 0, 'UBYTE')
                raw = bytes(buffer)

            arr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 4)
            bgra = arr.copy()
            bgra[:, 0] = arr[:, 2]  # B = old R
            bgra[:, 2] = arr[:, 0]  # R = old B
            bgra[:, 3] = 255        # Force opaque
            self._shm.buf[:] = bgra.tobytes()

        except Exception:
            pass

        # Tag viewport for redraw to keep it alive
        try:
            area, _ = self._find_3d_region()
            if area:
                area.tag_redraw()
        except Exception:
            pass

        return 1.0 / 30.0  # ~30 FPS capture rate

    def get_capture_buffer(self, vcserver, camera_name):
        return None

    def look_through_camera(self, vcserver, camera_name):
        camera = bpy.data.objects[camera_name]
        bpy.context.scene.camera = camera
        area, _ = self._find_3d_region()
        if area:
            area.spaces.active.region_3d.view_perspective = 'CAMERA'

    # Feedback

    def client_connected(self, vcserver, client_ip, client_port):
        bpy.ops.view3d.virtucamera_redraw()

    def client_disconnected(self, vcserver):
        bpy.ops.view3d.virtucamera_redraw()

    def current_camera_changed(self, vcserver, current_camera):
        bpy.ops.view3d.virtucamera_redraw()

    def server_did_stop(self, vcserver):
        bpy.ops.view3d.virtucamera_redraw()

    # Custom scripts

    def get_script_labels(self, vcserver):
        script_files = self.get_script_files()
        labels = []
        for filepath in script_files:
            filename = os.path.split(filepath)[1]
            tokens = filename.split("_")
            if len(tokens) > 1 and tokens[0].isdigit():
                prefix_len = len(tokens[0])
                label = filename[prefix_len + 1:-3]
                labels.append(label)
            else:
                labels.append(filename[:-3])
        return labels

    def execute_script(self, vcserver, script_index, current_camera):
        script_files = self.get_script_files()
        if script_index >= len(script_files):
            print(
                "Can't execute script " + str(script_index + 1)
                + ". Reason: Script doesn't exist"
            )
            return False
        try:
            with open(script_files[script_index], "r") as script_file:
                script_code = script_file.read()
        except Exception:
            traceback.print_exc()
            print(
                "Can't execute script " + str(script_index + 1)
                + ". Reason: Unable to open file '"
                + script_files[script_index] + "'"
            )
            return False
        if script_code == '':
            print(
                "Can't execute script " + str(script_index + 1)
                + ". Reason: Empty script"
            )
            return False
        selcam_var_def = 'vc_selcam = "' + current_camera + '"\n'
        script_code = selcam_var_def + script_code
        try:
            exec(script_code)
            return True
        except Exception:
            traceback.print_exc()
            return False


def timer_function():
    if _server is None:
        return None
    _server.execute_pending_events()
    if _server.is_serving:
        return 0.0
    return None


def update_script_labels(self, context):
    if _server is not None:
        _server.update_script_labels()


class VirtuCameraState(bpy.types.PropertyGroup):
    tcp_port: bpy.props.IntProperty(
        name="Server TCP Port",
        description=(
            "TCP port to listen for VirtuCamera App connections"
        ),
        default=23354,
        min=0,
        max=65535,
    )
    custom_scripts_dir: bpy.props.StringProperty(
        name="Scripts",
        description=(
            "Path to directory containing custom Python scripts "
            "to be shown as buttons in the app.\n"
            "If you prefix file names with a number, it will be "
            "used to order the buttons (e.g.: 1_myscript.py)"
        ),
        default="",
        subtype="DIR_PATH",
        update=update_script_labels,
    )


class VIEW3D_OT_virtucamera_start(bpy.types.Operator):
    bl_idname = "view3d.virtucamera_start"
    bl_label = "Start Serving"
    bl_description = (
        "Start listening for incoming connections, "
        "then you can scan the QR Code from the App"
    )

    @classmethod
    def poll(cls, context):
        return _server is not None and not _server.is_serving

    def execute(self, context):
        if _server is None:
            return {'CANCELLED'}
        state = context.scene.virtucamera
        prefs = context.preferences.addons[__package__].preferences
        _server._python_executable = prefs.python_39_path or None
        _server.start_serving(state.tcp_port)
        if not _server.is_serving:
            return {'FINISHED'}
        bpy.app.timers.register(timer_function)
        file_path = os.path.join(
            os.path.dirname(__file__), 'virtucamera_qr_img.png',
        )
        _server.write_qr_image_png(file_path, 3)
        global _custom_icons
        if _custom_icons is None:
            _custom_icons = bpy.utils.previews.new()
        _custom_icons.clear()
        _custom_icons.load('qr_image', file_path, 'IMAGE')
        return {'FINISHED'}


class VIEW3D_OT_virtucamera_stop(bpy.types.Operator):
    bl_idname = "view3d.virtucamera_stop"
    bl_label = "Stop Serving"
    bl_description = (
        "Stop listening for incoming connections from VirtuCamera App"
    )

    @classmethod
    def poll(cls, context):
        return _server is not None and _server.is_serving

    def execute(self, context):
        if _server is None:
            return {'CANCELLED'}
        _server.stop_serving()
        return {'FINISHED'}


class VIEW3D_OT_virtucamera_redraw(bpy.types.Operator):
    bl_idname = "view3d.virtucamera_redraw"
    bl_label = "Redraw UI"

    def execute(self, context):
        for window in context.window_manager.windows:
            screen = window.screen
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'FINISHED'}


class VIEW3D_OT_virtucamera_reinit(bpy.types.Operator):
    bl_idname = "view3d.virtucamera_reinit"
    bl_label = "Retry Initialization"

    def execute(self, context):
        global _init_attempts
        _init_attempts = 0
        _ensure_init_timer()
        return {'FINISHED'}


class GRAPH_OT_virtucamera_euler_filter(bpy.types.Operator):
    bl_idname = "graph.virtucamera_euler_filter"
    bl_label = "Euler Filter"

    object_name: bpy.props.StringProperty(name="Object Name")

    def execute(self, context):
        camera = bpy.data.objects[self.object_name]
        prev_cam_select = camera.select_get()
        camera.select_set(True)

        for area in context.screen.areas:
            if area.type == 'GRAPH_EDITOR':
                fcurves = [
                    fcu for fcu in camera.animation_data.action.fcurves
                    if fcu.data_path == 'rotation_euler'
                ]
                with context.temp_override(
                    area=area,
                    selected_visible_fcurves=fcurves,
                ):
                    bpy.ops.graph.euler_filter()
                break

        camera.select_set(prev_cam_select)
        return {'FINISHED'}


class VIEW3D_PT_virtucamera_main(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_virtucamera_main"
    bl_label = 'VirtuCamera'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'VirtuCamera'

    def draw(self, context):
        if _server is None:
            layout = self.layout
            layout.label(text="Initializing...")
            layout.operator(
                "view3d.virtucamera_reinit", text="Retry Initialization",
            )
            return
        state = context.scene.virtucamera
        layout = self.layout
        column = layout.column()
        column.label(
            text='v%d.%d.%d (server v%d.%d.%d)'
            % (plugin_version + _server.SERVER_VERSION),
        )
        row = layout.row()
        if _server.is_serving:
            row.enabled = False
        row.prop(state, "tcp_port")
        layout.operator('view3d.virtucamera_start')
        layout.operator('view3d.virtucamera_stop')
        if (
            _server.is_serving
            and not _server.is_connected
            and _custom_icons
            and 'qr_image' in _custom_icons
        ):
            column = layout.column()
            column.label(text='Server Ready')
            column.label(text='Connect through the App')
            layout.template_icon(
                icon_value=_custom_icons['qr_image'].icon_id, scale=6,
            )
        elif _server.is_connected:
            column = layout.column()
            column.label(
                text='Connected: ' + _server.client_ip, icon='CHECKMARK',
            )
            if _server.current_camera:
                column.label(
                    text=_server.current_camera, icon='VIEW_CAMERA',
                )
        column = layout.column()
        column.separator()
        column.prop(state, "custom_scripts_dir")


class VIRTUCAMERA_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    python_39_path: bpy.props.StringProperty(
        name="Python 3.9 Executable",
        description=(
            "Path to the Python 3.9 executable required by the "
            "VirtuCamera bridge server"
        ),
        default="",
        subtype="FILE_PATH",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "python_39_path")


_classes = (
    VirtuCameraState,
    VIEW3D_OT_virtucamera_start,
    VIEW3D_OT_virtucamera_stop,
    VIEW3D_OT_virtucamera_redraw,
    VIEW3D_OT_virtucamera_reinit,
    GRAPH_OT_virtucamera_euler_filter,
    VIEW3D_PT_virtucamera_main,
    VIRTUCAMERA_AddonPreferences,
)


_init_attempts = 0
_server = None
_custom_icons = None


def _ensure_init_timer():
    """Register the init timer if not already running."""
    if not bpy.app.timers.is_registered(_deferred_init):
        bpy.app.timers.register(_deferred_init, first_interval=0.1)


def _deferred_init():
    """Initialize VCServer after Blender's context is fully available."""
    global _init_attempts, _server, _custom_icons
    if _server is not None:
        return None  # already initialized
    _init_attempts += 1
    try:
        _server = VCServer(
            platform="Blender",
            plugin_version=plugin_version,
            event_mode=VCServer.EVENTMODE_PULL,
            vcbase=VirtuCameraBlender(),
        )
    except Exception as e:
        _server = None
        print(f"[VirtuCamera] Init attempt {_init_attempts} failed: {e}")
        if _init_attempts < 30:
            return 2.0  # retry in 2 seconds
        print("[VirtuCamera] Giving up after 30 attempts. "
              "Check Python 3.9 path in addon preferences.")
        return None
    _custom_icons = bpy.utils.previews.new()
    print("[VirtuCamera] Initialized successfully.")
    return None


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.virtucamera = bpy.props.PointerProperty(
        type=VirtuCameraState,
    )
    _ensure_init_timer()


def unregister():
    global _server, _custom_icons
    if _server is not None and _server.is_serving:
        _server.stop_serving()
    _server = None
    if _custom_icons is not None:
        bpy.utils.previews.remove(_custom_icons)
    _custom_icons = None
    try:
        del bpy.types.Scene.virtucamera
    except Exception:
        pass
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
