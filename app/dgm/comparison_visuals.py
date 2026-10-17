"""GUI-only comparison vocabulary; persisted/CLI metadata stays unchanged."""
from .presentation import change_text, row_status, snapshot_cell

# symbol, caption, ink, paper. One palette applies to the entire row.
VISUALS = {
    'grown': ('↑','Tăng dung lượng','#195c3d','#edf7f0'),
    'shrunk': ('↓','Giảm dung lượng','#854717','#fff1e4'),
    'same': ('=','Không đổi','#4d5967','#fcfcfb'),
    'new': ('+','Thêm mới','#146357','#e3f5ee'),
    'missing': ('−','Không còn ở mốc mới','#68717d','#eff0f2'),
    'modified': ('✎','Sửa metadata','#285a86','#eaf2fb'),
    'moved': ('↔','Đổi tên / di chuyển','#65508e','#f0ecf8'),
    'moved_from': ('↔','Đã chuyển đi','#65508e','#f0ecf8'),
    'replaced': ('↺','Thay thế','#814c82','#f7edf6'),
    'not_scanned': ('∅','Không quét ở một mốc','#506276','#edf1f5'),
    'unknown': ('?','Chưa xác định','#7f5c16','#fff5da'),
    'partial': ('≈','Có phần chưa rõ','#7f5c16','#fff5da'),
    'first_scan': ('●','Mốc ban đầu','#365d82','#edf3fa'),
    'private': ('?','Cần mở lại so sánh','#7f5c16','#fff5da'),
}
SEMANTIC_HINT_ROLE = 273  # Qt.UserRole + 17: icon-only meaning, not duplicate text.


def visual(change):return VISUALS.get(change,VISUALS['unknown'])


def status_symbol(row):return visual(row.get('change','unknown'))[0]


def status_icon(change):
    from PySide6.QtCore import Qt,QRect
    from PySide6.QtGui import QPixmap,QPainter,QFont,QColor,QIcon
    glyph,_,ink,_=visual(change);pixmap=QPixmap(20,20);pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);font=QFont('Segoe UI Symbol');font.setPixelSize(18);font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font);painter.setPen(QColor(ink));painter.drawText(QRect(0,0,20,20),Qt.AlignmentFlag.AlignCenter,glyph);painter.end()
    return QIcon(pixmap)


def status_hint(row):
    description=row_status(row)
    return description+'\n'+row['reason_text'] if row.get('reason_text') else description


def gui_snapshot_cell(row,side):
    text=snapshot_cell(row,side)
    return {'Không quét':'∅','Chưa rõ':'?','Không có':'×'}.get(text,text)


def gui_change_text(value):return '= 0 B' if value==0 else change_text(value)


def gui_change_color(value):
    return '#1264b0' if value is not None and value>0 else '#c62828' if value is not None and value<0 else visual('same')[2]


def row_palette(row, private=False):
    _,_,ink,paper=visual(row.get('change','unknown'))
    if private:
        presence=row.get('presence') or ('old' if row.get('old_revision') and not row.get('new_revision') else 'new')
        paper='#eff0f2' if presence=='old' else '#e9f7e9' if presence=='new' else paper
    return ink,paper


def gui_scan_label(row,profile=None):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from .snapshot_scope import option_label
    moment=datetime.fromisoformat(row['started']).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%d/%m %H:%M')
    engine={'wiztree':'WizTree','native':'Native','gdu':'gdu','windirstat':'WinDirStat'}.get(row['engine'],row['engine'])
    options=option_label(row).replace(': ',' ').replace('Chưa ghi nhận','?').replace(' • ',' · ')
    prefix=(row['profile']+' · ') if profile and row['profile']!=profile else ''
    import json
    app_version=json.loads(row.get('summary') or '{}').get('provenance',{}).get('app_version') or '?'
    return f"{prefix}Lần {row.get('ordinal',row['id'])} · {moment} · {engine} · app {app_version} · {options}"+(' · !' if row['status']=='partial' else '')


def make_legend(parent):
    from PySide6.QtWidgets import QFrame,QVBoxLayout,QGridLayout,QLabel
    from PySide6.QtCore import Qt
    panel=QFrame(parent);panel.setObjectName('comparisonLegend')
    outer=QVBoxLayout(panel);outer.setContentsMargins(8,6,8,6);outer.setSpacing(5)
    grid=QGridLayout();grid.setHorizontalSpacing(7);grid.setVerticalSpacing(5)
    keys=('grown','shrunk','same','new','missing','modified','moved','replaced','not_scanned','unknown','partial','first_scan')
    for number,key in enumerate(keys):
        symbol,caption,ink,paper=visual(key)
        chip=QLabel(f'<span style="font-family:Segoe UI Symbol;font-size:16px;font-weight:600">{symbol}</span> &nbsp;{caption}')
        chip.setTextFormat(Qt.TextFormat.RichText)
        chip.setStyleSheet(f'background:{paper};color:{ink};border:1px solid #d6dfe5;border-radius:5px;padding:5px 7px;')
        grid.addWidget(chip,number//6,number%6)
    outer.addLayout(grid)
    note=QLabel('Ô Cũ / Mới:  × không có  ·  ∅ không quét  ·  ? chưa rõ  ·  — chưa có mốc đối chiếu. '
        'Không quét / chưa rõ không được tính thành 0 B.')
    note.setWordWrap(True);note.setObjectName('legendNote');outer.addWidget(note)
    color_note=QLabel('Riêng: chỉ Cũ nền xám · chỉ Mới nền xanh lá nhạt. Chung: ± và ± Đĩa tăng chữ xanh nước biển, giảm chữ đỏ; mỗi ô theo giá trị riêng. ± luôn là kích thước file, ± Đĩa là cấp phát.')
    color_note.setWordWrap(True);color_note.setObjectName('legendNote');outer.addWidget(color_note)
    keys=QLabel('Ctrl+F: lọc đường dẫn   ·   Ctrl+Shift+C: sao chép đường dẫn   ·   Alt+Enter: Properties   ·   F2: đổi tên   ·   Delete: xác nhận xóa')
    keys.setWordWrap(True);keys.setObjectName('legendNote');outer.addWidget(keys)
    panel.hide();return panel
