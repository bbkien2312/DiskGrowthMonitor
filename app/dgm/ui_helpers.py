"""UI-only preferences remain inside app/data; no registry/global settings."""
import json
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolButton, QLabel, QHeaderView, QSizePolicy, QMenu, QTreeWidget, QStyle
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
        self.toggle.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)); self.toggle.setIconSize(QSize(18,18))
        self.toggle.setAccessibleName("Hiện/ẩn hướng dẫn")
        self.toggle.setObjectName("helpToggle")

    def expanded(self, expanded):
        self.body.setVisible(expanded)
        self.preferences.set("help/" + self.key, expanded)

    def show_transient(self, expanded=True):
        """Result-driven expansion must not overwrite the user's saved default."""
        self.toggle.blockSignals(True)
        self.toggle.setChecked(expanded)
        self.toggle.blockSignals(False)
        self.body.setVisible(expanded)


class StatusMenu(QMenu):
    def mouseReleaseEvent(self,event):
        action=self.actionAt(event.pos())
        if action and action.isCheckable():action.trigger();return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self,event):
        action=self.activeAction()
        if event.key()==Qt.Key.Key_Space and action and action.isCheckable():action.trigger();return
        super().keyPressEvent(event)


class StatusFilter(QToolButton):
    """Checkable status menu. Empty selection deliberately means zero matches."""
    def __init__(self, statuses):
        super().__init__(); self.statuses = statuses; self.actions = {}
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = StatusMenu(self); self.setMenu(menu)
        menu.addAction("Chọn tất cả", lambda: self.select(statuses))
        menu.addAction("Bỏ chọn", lambda: self.select([]))
        menu.addAction("Chỉ có thay đổi", lambda: self.select([s for s in statuses if s not in ('same','first_scan')]))
        menu.addSeparator()
        for key, label in statuses.items():
            action = QAction(label, self); action.setCheckable(True); action.setChecked(True)
            action.toggled.connect(self.refresh_text); menu.addAction(action); self.actions[key] = action
        self.refresh_text()

    def select(self, keys):
        for key, action in self.actions.items(): action.setChecked(key in keys)
        self.refresh_text()

    def currentData(self):
        keys = [key for key, action in self.actions.items() if action.isChecked()]
        return 'all' if len(keys) == len(self.actions) else keys

    def refresh_text(self, *_):
        keys = [key for key, action in self.actions.items() if action.isChecked()]
        self.setText('Mọi trạng thái' if len(keys) == len(self.actions) else f'Trạng thái ({len(keys)})')
        self.setToolTip('Lọc toàn bộ dữ liệu trước khi sắp xếp/tải trang. Bấm Lọc / về đầu để áp dụng.\n' +
                        (', '.join(self.statuses[k] for k in keys) or 'Không chọn trạng thái: không có kết quả'))


class MetricCard(QWidget):
    def __init__(self, tone):
        super().__init__(); self.setObjectName('metricCard'); self.setProperty('tone', tone)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.setMinimumHeight(76)
        layout = QVBoxLayout(self); layout.setContentsMargins(10, 6, 10, 6); layout.setSpacing(2)
        self.caption = QLabel('—'); self.caption.setObjectName('metricCaption'); self.caption.setWordWrap(True)
        self.value = QLabel('—'); self.value.setObjectName('metricValue'); self.value.setWordWrap(True)
        self.note = QLabel(); self.note.setObjectName('metricNote'); self.note.setWordWrap(True)
        layout.addWidget(self.caption); layout.addWidget(self.value); layout.addWidget(self.note)
        self._text = '—'

    def setText(self, text):
        self._text = text; parts = text.split('\n', 2)
        self.caption.setText(parts[0]); self.value.setText(parts[1] if len(parts) > 1 else '—')
        self.note.setText(parts[2] if len(parts) > 2 else ''); self.note.setVisible(len(parts) > 2)

    def text(self): return self._text


class FittedTree(QTreeWidget):
    """Keep all three columns inside the viewport, never auto-measure the dataset."""
    def __init__(self):
        super().__init__(); self._fitting = False
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.header().sectionResized.connect(self.fit_columns)

    def default_widths(self):
        metrics = self.fontMetrics()
        return [metrics.horizontalAdvance('System Volume Information') + self.indentation() * 2 + 26,
                max(105, metrics.horizontalAdvance('999.99 GiB') + 24),
                max(125, metrics.horizontalAdvance('↓ −999.99 GiB') + 24)]

    def fit_columns(self, index=None, old=None, new=None):
        if self._fitting or self.columnCount() != 3 or not self.isVisible(): return
        self._fitting = True
        try:
            widths = [self.columnWidth(i) for i in range(3)]
            available = self.viewport().width()
            if available < 170: return
            second=max(90,widths[1]);third=max(105,widths[2]);name=available-second-third
            if index==0:
                name=min(max(80,new),available-195)
                third=max(105,available-name-second)
                second=available-name-third
            if second+third>available-80:
                if index==2:third=min(third,available-170);second=available-80-third
                else:second=min(second,available-185);third=available-80-second
            for i, width in enumerate((available-second-third,second,third)):
                self.setColumnWidth(i, width)
        finally: self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event); self.fit_columns()


def interactive_columns(table, widths, preferences, key):
    header = table.header() if hasattr(table, "header") else table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(False); header.setMinimumSectionSize(55)
    header.setResizeContentsPrecision(200)
    stored = preferences.values.get("columns/" + key, widths)
    fitted=hasattr(table,'_fitting'); previous=getattr(table,'_fitting',False)
    if fitted:table._fitting=True
    try:
        for index, width in enumerate(stored if len(stored) == len(widths) else widths):
            header.resizeSection(index, max(55, int(width)))
    finally:
        if fitted:table._fitting=previous;table.fit_columns()
    return header
