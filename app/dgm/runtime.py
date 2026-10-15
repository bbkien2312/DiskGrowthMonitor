"""Command construction shared by source and frozen background workers."""
import subprocess
import sys
from .config import home


def background_command(*arguments):
    root=home()
    if getattr(sys,'frozen',False):
        return root/'DiskGrowthMonitor.exe',['--home',str(root),*arguments]
    return root/'.venv/Scripts/pythonw.exe',['-m','dgm','--home',str(root),*arguments]


def command_line(executable,arguments):
    return subprocess.list2cmdline([str(executable),*arguments])
