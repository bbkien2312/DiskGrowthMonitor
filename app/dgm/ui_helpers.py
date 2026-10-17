"""UI-only preferences remain inside app/data; no registry/global settings."""
import json
from PySide6.QtCore import Qt, QSize, QEvent
from PySide6.QtGui import QAction, QPainter, QPalette, QFontMetrics, QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame, QToolButton, QLabel, QHeaderView, QSizePolicy, QMenu, QTreeWidget, QStyle, QScrollArea
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


class ElidingLabel(QLabel):
    """A single line never changes the layout when a long path/status arrives."""
    def __init__(self,text=''):
        super().__init__(text);self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)

    def sizeHint(self):return QSize(140,self.fontMetrics().height()+2)
    def minimumSizeHint(self):return QSize(0,self.sizeHint().height())
    def setText(self,text):
        super().setText(text);self.setToolTip(text)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(self.contentsRect(),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,
                         self.fontMetrics().elidedText(self.text().replace('\n',' • '),Qt.TextElideMode.ElideRight,self.contentsRect().width()))


class CompactNote(QLabel):
    """Use the available column width, one line first and at most two lines."""
    def __init__(self):
        super().__init__();self.setObjectName('metricNote');self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)

    def sizeHint(self):return QSize(0,2*self.fontMetrics().height())
    def minimumSizeHint(self):return self.sizeHint()
    def setText(self,text):super().setText(text);self.setToolTip(text)

    def display_lines(self):
        fm=self.fontMetrics();width=self.contentsRect().width();text=' '.join(self.text().split())
        if not text:return []
        if fm.horizontalAdvance(text)<=width:return [text]
        explicit=self.text().splitlines()
        if len(explicit)==2 and all(fm.horizontalAdvance(line)<=width for line in explicit):return explicit
        words=text.split();choices=[]
        for i in range(1,len(words)):
            left,right=' '.join(words[:i]),' '.join(words[i:])
            a,b=fm.horizontalAdvance(left),fm.horizontalAdvance(right)
            if max(a,b)<=width:choices.append((abs(a-b),left,right))
        if choices:
            _,left,right=min(choices);return [left,right]
        first=[]
        while words and fm.horizontalAdvance(' '.join(first+[words[0]]))<=width:first.append(words.pop(0))
        if not first:
            return [fm.elidedText(text,Qt.TextElideMode.ElideRight,width)]
        return [' '.join(first),fm.elidedText(' '.join(words),Qt.TextElideMode.ElideRight,width)]

    def paintEvent(self,event):
        painter=QPainter(self);painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        line_height=self.fontMetrics().height()
        for i,line in enumerate(self.display_lines()):
            painter.drawText(0,i*line_height,self.width(),line_height,Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignVCenter,line)


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
        self.body.setStyleSheet("background:#eef4f9;padding:3px 6px;color:#3b5369;")
        self.body.setMinimumWidth(0);self.body.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setWidget(self.body)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(42 if key=='compare' else 48)
        self.scroll.setStyleSheet('QScrollArea{background:#eef4f9;border:1px solid #c8d8e6;border-radius:4px;}')
        layout.addWidget(self.scroll)
        self.toggle.toggled.connect(self.expanded)
        self.toggle.setChecked(bool(preferences.values.get("help/" + key, False)))
        self.body.setVisible(self.toggle.isChecked())
        self.scroll.setVisible(self.toggle.isChecked())
        self.toggle.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)); self.toggle.setIconSize(QSize(18,18))
        self.toggle.setAccessibleName("Hiện/ẩn hướng dẫn")
        self.toggle.setObjectName("helpToggle")

    def expanded(self, expanded):
        self.body.setVisible(expanded)
        self.scroll.setVisible(expanded)
        self.preferences.set("help/" + self.key, expanded)

    def show_transient(self, expanded=True):
        """Result-driven expansion must not overwrite the user's saved default."""
        self.toggle.blockSignals(True)
        self.toggle.setChecked(expanded)
        self.toggle.blockSignals(False)
        self.body.setVisible(expanded)
        self.scroll.setVisible(expanded)


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
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self); layout.setContentsMargins(8,3,8,3); layout.setSpacing(1)
        self.caption = QLabel('—'); self.caption.setObjectName('metricCaption');self.caption.setWordWrap(False)
        self.caption.setMinimumWidth(0);self.caption.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value = QLabel('—'); self.value.setObjectName('metricValue');self.value.setWordWrap(False)
        self.note = CompactNote()
        layout.addWidget(self.caption)
        self.parts=QWidget();columns=QHBoxLayout(self.parts);columns.setContentsMargins(0,0,0,0);columns.setSpacing(7)
        self.common_heading=QLabel('CHUNG');self.common_heading.setObjectName('metricNote')
        self.private_heading=QLabel('RIÊNG');self.private_heading.setObjectName('metricNote')
        self.private_value=QLabel('—');self.private_value.setObjectName('metricValue');self.private_value.setWordWrap(False)
        self.private_note=CompactNote();self.value_boxes=[]
        for label in (self.value,self.private_value,self.common_heading,self.private_heading):label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for heading,value,note in ((self.common_heading,self.value,self.note),(self.private_heading,self.private_value,self.private_note)):
            box=QWidget();column=QVBoxLayout(box);column.setContentsMargins(0,0,0,0);column.setSpacing(1)
            column.addWidget(heading);column.addWidget(value);column.addWidget(note)
            self.value_boxes.append(box)
            columns.addWidget(box,1)
            if heading is self.common_heading:
                line=QFrame();line.setFrameShape(QFrame.Shape.VLine);line.setStyleSheet('color:#c8d6e3;');columns.addWidget(line)
        layout.addWidget(self.parts)
        self.setMinimumWidth(210 if tone!='summary' else 265)
        for label in (self.value,self.private_value):
            label.setMinimumWidth(0);label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self._text = '—'
        self.refresh_dimensions()

    def refresh_dimensions(self):
        # One numeric row, even for file/folder counts. Keep every card aligned
        # without reserving an unused second row above the actual results.
        for label in (self.caption,self.common_heading,self.value,self.private_value,self.note,self.private_note):label.ensurePolished()
        capacity_font=QFont(self.value.font());capacity_font.setPixelSize(20)
        value_height=max(QFontMetrics(capacity_font).height(),self.value.fontMetrics().height(),self.private_value.fontMetrics().height())
        for value in (self.value,self.private_value):value.setFixedHeight(value_height)
        for note in (self.note,self.private_note):note.setFixedHeight(2*note.fontMetrics().height())
        margins=self.layout().contentsMargins()
        padding=margins.top()+margins.bottom()+self.layout().spacing()+2*self.value_boxes[0].layout().spacing()
        self.setFixedHeight(self.caption.fontMetrics().height()+self.common_heading.fontMetrics().height()+
                            value_height+2*self.note.fontMetrics().height()+padding)

    def event(self,event):
        result=super().event(event)
        if event.type() in (QEvent.Type.Polish,QEvent.Type.FontChange) and hasattr(self,'private_note'):self.refresh_dimensions()
        return result

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.refresh_notes()

    def refresh_notes(self):
        numeric_width=max(label.fontMetrics().horizontalAdvance(line) for label in (self.value,self.private_value)
                          for line in (label.text().splitlines() or ['—']))
        self.setMinimumWidth(max(265 if self.property('tone')=='summary' else 210,2*numeric_width+34))
        for value,note in ((self.value,self.note),(self.private_value,self.private_note)):
            # Fill the already allocated column, but never impose a fixed
            # minimum width: a long note cannot make the card/window wider.
            note.setMinimumWidth(0);note.setMaximumWidth(16777215)
            self.parts.layout().itemAt(0 if value is self.value else 2).widget().layout().setAlignment(note,Qt.AlignmentFlag(0))

    def setText(self, text):
        self._text = text; parts = text.split('\n', 2)
        self.caption.setText(parts[0]); self.value.setText(parts[1] if len(parts) > 1 else '—')
        self.note.setText(parts[2] if len(parts) > 2 else ''); self.note.setVisible(len(parts) > 2)
        self.private_value.setText('—');self.private_note.clear()
        self.refresh_notes()

    def set_parts(self, caption, common, private, common_note='', private_note=''):
        self.caption.setText(caption);self.value.setText(common);self.private_value.setText(private)
        self.note.setText(common_note);self.note.setVisible(bool(common_note));self.private_note.setText(private_note)
        self.refresh_notes()
        self._text='\n'.join((caption,'Chung: '+common,common_note,'Riêng: '+private,private_note))

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
