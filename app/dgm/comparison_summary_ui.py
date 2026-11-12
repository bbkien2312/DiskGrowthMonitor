"""Compact, read-only highlights for the full snapshot pair."""
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHeaderView, QLabel, QLayout, QPushButton, QScrollArea, QSizePolicy,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .disk_measurements import display_size, exact


GROUPS = {
    'grown': ('↑  Tăng nhiều nhất', '#e8f2fc', '#195987'),
    'shrunk': ('↓  Giảm nhiều nhất', '#faece9', '#9f423a'),
    'new': ('+  Thêm / chỉ có ở Mới', '#e8f4eb', '#286342'),
    'missing': ('−  Mất / chỉ có ở Cũ', '#edf0f3', '#4b5966'),
}


class ComparisonSummaryPanel(QScrollArea):
    retry = Signal()

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.overview = QLabel()
        self.overview.setTextFormat(Qt.TextFormat.PlainText)
        self.overview.setWordWrap(True)
        self.overview.setStyleSheet('background:#eaf3fa;color:#254e68;border-radius:5px;padding:7px;')
        layout.addWidget(self.overview)
        self.overview.hide()
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        for label in (self.overview, self.message):
            policy = label.sizePolicy()
            policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
            label.setSizePolicy(policy)
        self.retry_button = QPushButton('Tải lại tóm tắt')
        self.retry_button.clicked.connect(self.retry.emit)
        layout.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.groups = {}
        for key, (title, background, color) in GROUPS.items():
            heading = QLabel(title)
            heading.setStyleSheet(f'background:{background};color:{color};font-weight:600;border-radius:4px;padding:4px 6px;')
            table = QTreeWidget()
            table.setHeaderLabels(['Thư mục', '±', 'Ghi chú'])
            table.setRootIsDecorated(False)
            table.setUniformRowHeights(True)
            table.setAlternatingRowColors(True)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            table.header().setStretchLastSection(False)
            table.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for column in (1, 2):
                table.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            layout.addWidget(heading)
            layout.addWidget(table)
            self.groups[key] = (heading, table)
        layout.addStretch()
        self.setWidget(body)
        self.set_state('Bấm So sánh để xem tóm tắt.')

    def set_state(self, message, *, retry=False):
        self.message.setText(message)
        self.retry_button.setVisible(retry)
        for heading, table in self.groups.values():
            heading.hide()
            table.hide()
            table.clear()

    def render(self, highlights, unit, delta_text):
        self.message.setText('Top 10 mỗi nhóm theo độ lớn chênh lệch trên toàn cặp mốc, không theo bộ lọc Chi tiết. '
                             'Thư mục cha và con có thể trùng phần dung lượng; không cộng các dòng top lại.')
        self.retry_button.hide()
        for key, rows in highlights.items():
            heading, table = self.groups[key]
            title, _, color = GROUPS[key]
            heading.setText(f'{title}  ·  {len(rows)} mục' if rows else f'{title}  ·  Không có')
            heading.show()
            table.clear()
            row_height = max(24, table.fontMetrics().height() + 7)
            for number, row in enumerate(rows, 1):
                if key in ('new', 'missing'):
                    status = ('Đã xác minh' if row['change'] == key else
                              'Chỉ Mới' if key == 'new' else 'Chỉ Cũ')
                else:
                    status = ('Chưa quy rõ' if row['reason'] or row['unknown_files'] else
                              'Thay thế' if row['change'] == 'replaced' else '')
                before, after = row['before'], row['after']
                def recorded(value, side):
                    if row['presence'] not in (side, 'both'):
                        return 'Không có bản ghi'
                    if value is None:
                        return 'Thiếu số đo'
                    return f'{display_size(value, unit, precise=True)} ({exact(value)})'
                note = ('Chưa đủ bằng chứng khẳng định đã thêm/xóa.' if status in ('Chỉ Mới', 'Chỉ Cũ')
                        else 'Chênh theo dữ liệu ghi nhận; chưa quy rõ mọi thay đổi.' if status == 'Chưa quy rõ' else '')
                tip = '\n'.join(filter(None, (row['path'], f"Cũ: {recorded(before, 'old')}",
                    f"Mới: {recorded(after, 'new')}", row['reason_text'], note)))
                item = QTreeWidgetItem([f"{number}. {row['path']}", delta_text(row['delta']), status])
                item.setSizeHint(0, QSize(0, row_height))
                item.setForeground(1, QColor(color))
                item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                for column in range(3):
                    item.setToolTip(column, tip)
                table.addTopLevelItem(item)
            table.setVisible(bool(rows))
            if rows:
                table.ensurePolished()
                table.setFixedHeight(table.header().sizeHint().height() + len(rows) * row_height + 6)
