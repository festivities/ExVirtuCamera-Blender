# ExVirtuCamera for Blender

Use the **VirtuCamera 1** iOS app with modern Blender. The app streams your scene's camera view to the phone, and the phone drives the camera: move it by moving the phone, change the focal length, play and scrub the timeline, record camera takes as keyframes, and run your own scripts from buttons in the app.

VirtuCamera 1 is abandoned upstream, and its original Blender addon only ran on Blender 3.3 and older: it depended on a closed server module locked to Python 3.9. Version 2 of this extension replaces that module with a pure-Python reimplementation of the VirtuCamera 1 server that runs inside Blender. It needs no Python 3.9, no helper process and no screen capture.

## Requirements

- Blender 4.4 or later on Windows x64. Tested on Blender 5.2, 4.5 and Goo Engine 4.4.3, and checked with a phone on Goo Engine 4.4.3.
- The VirtuCamera 1 app on an iPhone or iPad on the same local network as the computer.

## Installation

1. Build the zip (see [Building](#building)) or use a release zip.
2. In Blender, go to **Edit > Preferences > Get Extensions**.
3. Click the dropdown arrow at the top right and choose **Install from Disk...**.
4. Select the zip and enable **ExVirtuCamera**.

If an older ExVirtuCamera (1.x) is installed, the new zip replaces it. Restart Blender afterwards.

## Usage

1. Open the **VirtuCamera** tab in the 3D Viewport sidebar (`N`).
2. Click **Start Serving**. Keep the port (default 23354), or change it while not serving. If Windows Firewall asks, allow Blender on **Private networks**.
3. In VirtuCamera 1, scan the QR code shown in the panel.
4. Pick a camera in the app. The stream shows that camera's view, and moving the phone moves the camera.
5. Record a take in the app to key the camera's location, rotation and focal length.

The stream is rendered offscreen from the selected camera, so you can zoom, pan, cover or minimize the viewport without affecting it. It uses the viewport's shading and overlay settings. The stream size follows the render aspect ratio; its long edge is set in **Preferences > Add-ons > ExVirtuCamera** (default 720 px, range 256 to 1920). Values above 720 were never sent by the original addon and may strain older phones.

### Custom scripts

Set **Scripts** in the panel to a folder of `.py` files. Each file becomes a button in the app. A numeric prefix sets the order and is hidden from the label: `1_reset_camera.py` shows as `reset_camera`. Scripts run in Blender with `bpy`, `mathutils` and `vc_selcam` (the name of the camera selected in the app) available. Tracebacks print to Blender's system console.

## Troubleshooting

- **The app can't connect:** make sure the phone and computer are on the same network and the firewall allows Blender. A VPN or a virtual network adapter can get in the way; try disabling it.
- **"Could not listen on port":** another program uses the port. Pick another one.
- **The camera list is empty:** only visible camera objects are listed.
- When you disable the extension after serving, Windows may print `PermissionError` about `av.libs` DLLs. This is harmless.

## Building

Requirements: Windows x64, the `py` launcher with pip, and Blender 5.2. `build.bat` uses `C:\Apps\blender\blender.exe`; set `BLENDER` to another 5.2 executable if needed.

Run `build.bat`. It downloads the `av` and `qrcode` wheels into `wheels\` and runs `blender --factory-startup --command extension build`. The zip is written to the repository folder.

## Tests

- `tests\test_vc_server.py`: protocol and video tests over loopback, without Blender. Run with Blender's bundled Python, and point `VC_AV_PATH` at an extracted copy of the `av` wheel:
  `C:\Apps\blender\5.2\python\bin\python.exe -s tests\test_vc_server.py`
- `tests\run_e2e.ps1`: installs the built zip into a throwaway Blender user folder, starts a GUI Blender, and drives it with a fake VirtuCamera 1 client over loopback:
  `pwsh tests\run_e2e.ps1 -Exe C:\Apps\blender\blender.exe`

## How it works

- `vc_server.py` implements the VirtuCamera 1 TCP protocol and encodes the stream as VP9 on its own threads. It never touches Blender's API.
- `virtucamera_blender.py` answers the app's requests on Blender's main thread through a timer, renders the camera offscreen with the GPU module, and holds the UI.
- The protocol is documented in `docs/vc_core_protocol.md`.

## Third-party components

The zip bundles unmodified PyPI wheels: [PyAV](https://github.com/PyAV-Org/PyAV) 18.1.0 (BSD, with an FFmpeg build that includes GPL components such as libx264/libx265; the stream uses libvpx-vp9) and [qrcode](https://github.com/lincolnloop/python-qrcode) 8.2 (BSD). Their licences and sources are available from those projects.

## Disclaimer

This is an unofficial community port and isn't affiliated with or endorsed by the original VirtuCamera developers. It's provided "as is" to help users who purchased the original app continue using it in modern workflows.
