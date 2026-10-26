"""Where the app lives on disk (source folder or the EXE's folder) and its
bundled resources."""

import os
import sys


def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # lat/paths.py -> the folder holding LeagueArenaTools.py.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource_path(filename):
    resource_dir = getattr(sys, "_MEIPASS", get_base_dir())
    return os.path.join(resource_dir, filename)
