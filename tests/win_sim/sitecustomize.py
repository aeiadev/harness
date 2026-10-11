"""Linux stand-in for native Windows Python: PYTHONPATH=tests/win_sim python3 tests/test_compat.py

Hides the POSIX-only names Windows Python lacks, so hooks must reach them through compat.py.
Child processes inherit PYTHONPATH and get the same view.
"""

import os
import shutil
import sys

HIDDEN = ("geteuid", "getuid", "mkfifo", "fchmod", "O_NOFOLLOW", "O_NONBLOCK")
for name in HIDDEN:
    if hasattr(os, name):
        delattr(os, name)
sys.modules["pwd"] = None  # import pwd now raises ImportError, as on Windows
# shutil.rmtree's descriptor walk asks for os.O_NONBLOCK; Windows Python takes the plain walk.
shutil._use_fd_functions = False
if hasattr(shutil, "_rmtree_unsafe") and hasattr(shutil, "_rmtree_impl"):
    shutil._rmtree_impl = shutil._rmtree_unsafe
os.environ["HARNESS_WIN_SIM"] = "1"
