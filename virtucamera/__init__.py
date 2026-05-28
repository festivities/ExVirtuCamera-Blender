import os, sys

parent_dir = os.path.abspath(os.path.dirname(__file__))
third_party_dir = os.path.join(parent_dir, 'third_party')

# Add 'parent_dir' to sys.path, needed for the video process to work
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Add 'third_party' to sys.path to import third party modules
if third_party_dir not in sys.path:
    sys.path.insert(0, third_party_dir)

# On Windows only
if os.name == 'nt':
    # Add 'crt' to PATH env var, for Windows to access C Runtime DLLs
    crt_dir = os.path.join(third_party_dir, 'crt')
    os.environ["PATH"] += os.pathsep + crt_dir

from .vc_core import VCServer
from .vc_base import VCBase

__all__ = ("VCServer", "VCBase")

# Note: vc_core.py is a pure-Python bridge wrapper for Blender 5.1 (Python 3.13).
# It spawns a Python 3.9 subprocess to host the original vc_core.cp39-win_amd64.pyd.