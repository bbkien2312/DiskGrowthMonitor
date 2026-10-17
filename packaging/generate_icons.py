"""Render the SVG master into a multi-resolution, PNG-backed Windows ICO."""
import os
from pathlib import Path
import struct

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication
from dgm.branding import ICON_SIZES, render_icon


def build_icon(destination):
    app = QGuiApplication.instance() or QGuiApplication([])
    images = []
    for size in ICON_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not render_icon(size).save(buffer, 'PNG'):
            raise RuntimeError(f'Cannot render icon at {size}px')
        images.append(bytes(buffer.data()))
    offset = 6 + 16 * len(images)
    entries = []
    for size, data in zip(ICON_SIZES, images):
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(struct.pack('<HHH', 0, 1, len(images)) + b''.join(entries) + b''.join(images))
    return destination


if __name__ == '__main__':
    print(build_icon(Path(__file__).resolve().parents[1] / 'build/icons/app.ico'))
