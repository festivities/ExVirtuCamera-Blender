# ExVirtuCamera for Blender

This is a community revival project for the original **VirtuCamera 1** app integration for Blender. 

The original VirtuCamera 1 app has been abandoned in favor of VirtuCamera 2. As Blender's API evolved (particularly with the transition from Python 3.9 to 3.11+ and the deprecation of the `bgl` module in Blender 4.0+), the original addon stopped working, leaving legacy app users without a working pipeline.

This project revives the addon by wrapping the original `vc_core.pyd` (which was hardcoded to Python 3.9) in a multi-process bridge server, and modernizing the viewport capture logic to use the new `gpu` module. It is fully compatible with modern Blender versions (4.0+ and 5.1+).

The revival works by spawning a hidden Python 3.9 background process that loads the original `vc_core.pyd`. Blender communicates with this background process using ultra-low-latency TCP sockets over localhost. For viewport streaming, instead of serializing millions of pixels over JSON, Blender writes the `gpu` framebuffer directly into a `multiprocessing.SharedMemory` block, which the background process reads and forwards to the phone instantly.

## Installation

1. Download or clone this repository.
2. In Blender, navigate to **Edit > Preferences > Get Extensions**.
3. Click the dropdown arrow in the top right and select **Install from Disk...**
4. Select the `.zip` archive or the addon folder.
5. Enable the **VirtuCamera** extension in the list.

*(Note: The bridge requires a standalone Python 3.9 environment bundled or accessible to run the legacy Cython module).*

## Usage

1. Open the VirtuCamera panel in the 3D Viewport sidebar (`N` panel).
2. Click **Start Serving** to initialize the bridge and begin broadcasting.
3. Open the VirtuCamera 1 app on your phone and connect to the displayed IP/QR code.
4. Voila!

## Disclaimer

This is an unofficial community port and isn't affiliated with or endorsed by the original VirtuCamera developers. It's provided "as is" to help users who purchased the original app continue using it in modern workflows.
