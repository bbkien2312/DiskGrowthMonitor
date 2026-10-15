"""UI-only preferences remain inside app/data; no registry/global settings."""
import json
from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolButton, QLabel, QHeaderView, QSizePolicy
from .config import home


class Preferences:
    def __init__(self):
        self.path = home() / "data/ui-settings.json"
        try:
            self.values = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.values = {}

    def set(self, key, value):
        self.values[key] = value
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


class HelpBox(QWidget):
    def __init__(self, key, text, preferences):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.key, self.preferences = key, preferences
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        self.toggle = QToolButton(); self.toggle.setCheckable(True)
        self.toggle.setToolTip("Hiện/ẩn hướng dẫn cho nhóm lựa chọn này")
        layout.addWidget(self.toggle)
        self.body = QLabel(text); self.body.setWordWrap(True)
        self.body.setStyleSheet("background:#eef4f9;border:1px solid #c8d8e6;border-radius:5px;padding:8px;color:#3b5369;")
        layout.addWidget(self.body)
        self.toggle.toggled.connect(self.expanded)
        self.toggle.setChecked(bool(preferences.values.get("help/" + key, False)))
        self.body.setVisible(self.toggle.isChecked())
        self.toggle.setText("v" if self.toggle.isChecked() else ">")

    def expanded(self, expanded):
        self.body.setVisible(expanded); self.toggle.setText("v" if expanded else ">")
        self.preferences.set("help/" + self.key, expanded)


def interactive_columns(table, widths, preferences, key):
    header = table.header() if hasattr(table, "header") else table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(False); header.setMinimumSectionSize(55)
    header.setResizeContentsPrecision(200)
    stored = preferences.values.get("columns/" + key, widths)
    for index, width in enumerate(stored if len(stored) == len(widths) else widths):
        header.resizeSection(index, max(55, int(width)))
    return header
