# PyVirtuCamera
# Copyright (c) 2021 Pablo Javier Garcia Gonzalez.
# 
# Redistribution and use of the software module "PyVirtuCamera" (the “Software”)
# is permitted, free of charge, provided that the following conditions are met:
#     * Redistributions must reproduce the above copyright notice,
#       this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * You may not decompile, disassemble, reverse engineer or modify
#       any portion of the Software.
# 
# THE SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDERS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
# OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THE SOFTWARE,
# EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

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