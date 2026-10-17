"""One vector master for Qt, Windows taskbar and packaged EXE/shortcut icons."""
from functools import lru_cache
from pathlib import Path
import ctypes
import logging
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

APP_USER_MODEL_ID = 'bbkien2312.DiskGrowthMonitor'
ICON_PATH = Path(__file__).with_name('assets') / 'app-icon.svg'
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def set_taskbar_identity():
    """A stable, version-independent ID, shared with installer shortcuts."""
    if os.name != 'nt':
        return False
    try:
        setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        return setter(APP_USER_MODEL_ID) >= 0
    except (AttributeError, OSError):
        logging.getLogger('dgm').debug('Could not set taskbar identity', exc_info=True)
        return False


def render_icon(size):
    renderer = QSvgRenderer(str(ICON_PATH))
    if not renderer.isValid():
        raise RuntimeError(f'Invalid application icon: {ICON_PATH}')
    canvas = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
    finally:
        painter.end()
    return canvas


@lru_cache(maxsize=1)
def application_icon():
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(render_icon(size)))
    return icon
