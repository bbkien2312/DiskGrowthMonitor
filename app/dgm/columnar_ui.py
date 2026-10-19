"""Isolated, opt-in Parquet workbench. No legacy Store/SQLite at startup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import traceback
import re
import html

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor, QIcon, QAction, QFontDatabase, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QTreeWidget, QTreeWidgetItem, QSplitter, QStyle, QPlainTextEdit, QProgressBar,
    QMessageBox, QFileDialog, QInputDialog, QMenu)

from .columnar_store import Repository
from .columnar_compare import Comparison
from .columnar_jobs import Jobs
from .platform_ops import ProcessCancelled, manual_action
from .presentation import size, local_time
from .disk_measurements import (MEASURES, size as disk_size, delta as disk_delta,
                                exact, snapshot_text, read_volume_usage)
from .comparison_visuals import status_symbol, status_hint, row_palette, gui_change_color
from .theme import LIGHT

ROLE = Qt.ItemDataRole.UserRole


def scan_label(row):
    browser = 'không Chrome/Edge' if row['scope'].get('strict_browsers') else 'có Chrome/Edge'
    return f"#{row['id']} · {local_time(row['started'])[:16]} · {row['engine']} · {browser} · {'MFT' if row.get('mft') else 'walk'} · v{row.get('app_version','?')}"


class Worker(QThread):
    event = Signal(str, dict)

    def __init__(self, function, callback, parent):
        super().__init__(parent)
        self.function, self.callback = function, callback
        self.stop = threading.Event()
        self.value = self.error = self.trace = None

    def run(self):
        try:
            self.value = self.function(self.stop, self.event.emit)
        except BaseException as exc:
            self.error, self.trace = exc, traceback.format_exc()


class Window(QMainWindow):
    busy_changed = Signal(bool)
    # Snapshot digests can exceed signed 64-bit integers. QVariantList attempts
    # recursive integer conversion on Windows; carry the Python object intact.
    snapshots_changed = Signal(object)

    def __init__(self, repository=None, *, production=False):
        super().__init__()
        font_dir=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'Fonts'
        for name in ('segoeui.ttf','seguisym.ttf'):
            font=font_dir/name
            if font.is_file():QFontDatabase.addApplicationFont(str(font))
        QApplication.instance().setFont(QFont('Segoe UI',10))
        self.repo = repository or Repository()
        self.jobs = Jobs(self.repo)
        self.query = self.worker = None
        self.cursor = self.folder_scope = None
        self.scope_display = None
        self.loaded_rows = []
        self.live_disks = []
        self.want_close = False
        self.production = production
        self.profile_provider = None
        self.can_begin = None
        self.setWindowTitle('Disk Growth Monitor · Parquet Preview (D: thử nghiệm)')
        self.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets/app-icon.svg')))
        self.resize(1440, 920)
        self.setStyleSheet(LIGHT)
        base = QWidget(); outer = QVBoxLayout(base); outer.setContentsMargins(10, 8, 10, 8)
        self.setCentralWidget(base)
        banner = QLabel('BẢN THỬ LÕI NHANH  ·  Parquet + DuckDB RAM  ·  Không thay bản cài/lịch sử cũ; không tự xóa file trên ổ')
        banner.setObjectName('safeBanner'); banner.setWordWrap(True); outer.addWidget(banner)
        if production:
            banner.setText('LÕI PARQUET · Trên đĩa là chỉ số chính · Không tự xóa lịch sử hoặc file đã quét')
        self.tabs = QTabWidget(); outer.addWidget(self.tabs, 1)
        self.inspection = QWidget(); self.tabs.addTab(self.inspection, 'Kiểm tra')
        self._inspection()
        self._history()
        self._journal()
        system = QWidget(); layout = QVBoxLayout(system)
        import duckdb
        note = QLabel(f'Lõi thử nghiệm: DuckDB {duckdb.__version__} · Parquet nén ZSTD\n'
            'Snapshot chỉ lưu metadata, không lưu nội dung file. Không dựng SQLite khi mở hoặc so sánh.\n'
            f'Kho riêng: {self.repo.root}\n'
            f"Ngân sách DuckDB: {self.repo.limits['memory_mb']} MiB (không phải giới hạn toàn bộ RSS Python); spill tối đa {self.repo.limits['temp_mb']} MiB.\n"
            'Các tab dọn dẹp/lịch chạy/cập nhật của bản ổn định giữ nguyên; bản thử này chưa thay worker lịch chạy.\n'
            'WizTree mặc định. Native/gdu/WinDirStat dùng chung khóa đường dẫn; trường thiếu giữ là chưa rõ, không đi stat bổ sung.\n'
            'Logical là size theo đường dẫn, có thể lớn hơn Used của ổ do hardlink/nén/sparse. Đĩa là cấp phát đã biết, không cộng folder vào tổng file.')
        note.setWordWrap(True); layout.addWidget(note); layout.addStretch(); self.tabs.addTab(system, 'Hệ thống / giới hạn')
        from .config import load
        for engine,path in load()['engines'].items():
            row=QHBoxLayout();row.addWidget(QLabel(engine));edit=QLineEdit(path);row.addWidget(edit,1)
            self.button('📁',lambda checked=False,e=edit:self.choose_executable(e),row)
            self.button('Lưu',lambda checked=False,k=engine,e=edit:self.save_executable(k,e.text()),row)
            layout.insertLayout(layout.count()-1,row)
        bottom = QHBoxLayout(); self.progress = QProgressBar(); self.progress.setRange(0,100); self.progress.setValue(0)
        self.phase = QLabel('Chọn mốc hoặc quét để bắt đầu'); self.phase.setWordWrap(True)
        self.stop_button = QPushButton('Dừng an toàn'); self.stop_button.setProperty('actionRole','stop')
        self.stop_button.setEnabled(False); self.stop_button.clicked.connect(self.stop_work)
        bottom.addWidget(self.progress,1); bottom.addWidget(self.phase,4); bottom.addWidget(self.stop_button); outer.addLayout(bottom)
        self.refresh_history()
        self.refresh_live_usage()

    def button(self, text, callback, layout, role=None):
        button = QPushButton(text); button.clicked.connect(callback)
        if role: button.setProperty('actionRole',role)
        layout.addWidget(button)
        return button

    def choose_executable(self,edit):
        path,_=QFileDialog.getOpenFileName(self,'Chọn engine portable','','Executable (*.exe)')
        if path:edit.setText(path)

    def save_executable(self,engine,path):
        from .config import load,save
        if not Path(path).is_file():QMessageBox.warning(self,'Đường dẫn','Chọn file executable tồn tại');return
        cfg=load();cfg['engines'][engine]=path;save(cfg)
        self.on_progress('info',{'message':'Đã lưu engine trong cấu hình bản thử: '+engine})

    def _inspection(self):
        layout = QVBoxLayout(self.inspection); layout.setContentsMargins(6,6,6,6); layout.setSpacing(6)
        controls = QWidget(); controls.setObjectName('scanConfiguration'); row = QHBoxLayout(controls)
        self.roots = QLineEdit('C:\\'); self.roots.setPlaceholderText('Phạm vi; nhiều mục cách nhau bằng |')
        row.addWidget(QLabel('Phạm vi')); row.addWidget(self.roots,1)
        self.button('📁',self.choose_root,row)
        self.engine = QComboBox(); self.engine.addItems(['wiztree','native','gdu','windirstat']); self.engine.setMaximumWidth(120)
        row.addWidget(self.engine)
        self.mft = QCheckBox('MFT'); self.mft.setChecked(True); row.addWidget(self.mft)
        self.browser = QCheckBox('Không đọc Chrome/Edge'); row.addWidget(self.browser)
        self.scan_button = self.button('Quét ngay', self.scan, row, 'primary')
        layout.addWidget(controls)
        comparison = QWidget(); comparison.setObjectName('comparisonControls'); row = QHBoxLayout(comparison)
        row.addWidget(QLabel('Cũ')); self.older = QComboBox(); row.addWidget(self.older,1)
        row.addWidget(QLabel('Mới')); self.newer = QComboBox(); row.addWidget(self.newer,1)
        for combo in (self.older,self.newer):
            combo.setMinimumContentsLength(18);combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumWidth(150)
        self.compare_button = self.button('So sánh / mở mốc', self.compare, row, 'compare'); layout.addWidget(comparison)
        row = QHBoxLayout()
        row.addWidget(QLabel('Chỉ số file'))
        self.measure = QComboBox(); self.measure.addItem('Trên đĩa · Allocated','allocated'); self.measure.addItem('Logical · xem phụ','logical')
        row.addWidget(self.measure)
        self.units = QComboBox(); self.units.addItem('GiB · 1024 (Windows)','binary'); self.units.addItem('GB · 1000','decimal'); row.addWidget(self.units)
        self.live_label = QLabel(); self.live_label.setWordWrap(True); row.addWidget(self.live_label,1)
        self.button('⟳ Used hiện tại',self.refresh_live_usage,row); layout.addLayout(row)
        self.disk_metrics = QLabel('Chưa mở mốc: Used hiện tại không thay số đo lịch sử.')
        self.disk_metrics.setObjectName('safeBanner'); self.disk_metrics.setWordWrap(True); layout.addWidget(self.disk_metrics)
        self.metrics = QLabel('Trên đĩa: chưa mở mốc. Không dùng Logical làm dung lượng chiếm ổ.')
        self.metrics.setObjectName('metric'); self.metrics.setWordWrap(True); layout.addWidget(self.metrics)
        row = QHBoxLayout()
        self.section = QComboBox(); self.section.addItem('Tất cả','all'); self.section.addItem('Chung','common'); self.section.addItem('Riêng','private')
        self.change = QComboBox()
        for text,key in [('Mọi trạng thái','all'),('↑','grown'),('↓','shrunk'),('+','new'),('−','missing'),('=','same'),('✎','modified'),('∅','not_scanned'),('? / ≈','unknown,partial')]:
            self.change.addItem(text,key)
        self.minimum = QComboBox(); self.minimum.addItem('Mọi size',0); self.minimum.addItem('≥ 10 MB',10_000_000)
        self.search = QLineEdit(); self.search.setPlaceholderText('Lọc đường dẫn (Enter)')
        self.batch = QComboBox(); self.batch.addItems(['20','100','1000']); self.batch.setMaximumWidth(85)
        for widget in (self.section,self.change,self.minimum):
            row.addWidget(widget); widget.currentIndexChanged.connect(self.reload_page)
        row.addWidget(self.search,1); self.search.returnPressed.connect(self.reload_page)
        row.addWidget(QLabel('Mỗi lần')); row.addWidget(self.batch)
        self.button('Toàn phạm vi',self.clear_scope,row)
        self.help_button = self.button('ⓘ',lambda: self.note.setVisible(not self.note.isVisible()),row)
        layout.addLayout(row)
        if self.production:
            extra=QHBoxLayout()
            self.kind=QComboBox()
            for label,value in [('File + thư mục','all'),('File','file'),('Thư mục','folder'),('Liên kết','link')]:self.kind.addItem(label,value)
            self.presence=QComboBox()
            for label,value in [('Mọi mốc','all'),('Cũ ↔ Mới','both'),('Chỉ Cũ','old'),('Chỉ Mới','new')]:self.presence.addItem(label,value)
            self.reason=QComboBox();self.reason.addItem('Mọi lý do',None)
            from .columnar_compare import REASONS
            for value,label in REASONS.items():self.reason.addItem(label,value)
            for widget in (self.kind,self.presence,self.reason):extra.addWidget(widget);widget.currentIndexChanged.connect(self.reload_page)
            extra.addStretch();layout.addLayout(extra)
        self.note = QLabel('↑ tăng · ↓ giảm · = không đổi · + thêm · − không còn · ∅ không quét · ? chưa rõ. '
            'Riêng chỉ Cũ nền xám, chỉ Mới nền xanh nhạt. Không quét/chưa rõ không coi là 0 B. '
            'Sắp xếp toàn bộ dữ liệu trong phạm vi đang chọn, rồi mới lấy trang. '
            '± folder là tổng chênh file xác minh được; khi có phần chưa rõ có thể khác Mới trừ Cũ. '
            'Mặc định Trên đĩa; Logical chỉ là kích thước theo đường dẫn, không phải Used. '
            'GiB = 1024³ byte (cùng cơ số Windows); số nguyên rút gọn như Windows, tooltip giữ byte chính xác. '
            'Chưa quy vào file không đồng nghĩa file nhỏ; Used hiện tại không thay số đo lịch sử.')
        self.note.setWordWrap(True); self.note.setObjectName('warningBanner'); self.note.hide(); layout.addWidget(self.note)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(['Thư mục','Mới · Đĩa','± Đĩa']); self.tree.setMinimumWidth(430)
        self.tree.setColumnWidth(0,255); self.tree.setColumnWidth(1,85); self.tree.setColumnWidth(2,85)
        self.tree.header().setStretchLastSection(False); self.tree.header().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.tree.itemExpanded.connect(self.expand_tree); self.tree.itemClicked.connect(self.select_tree)
        self.table = QTableWidget(0,7); self.table.setHorizontalHeaderLabels(['TT','Đường dẫn','±','Cũ','Mới','± Đĩa','Có ở'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide(); self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setStretchLastSection(False)
        for index,width in enumerate([44,360,90,85,85,90,75]): self.table.setColumnWidth(index,width)
        self.table.horizontalHeader().setSortIndicator(2,Qt.SortOrder.DescendingOrder)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.sort, self.direction = 'abs_delta','desc'
        self.table.horizontalHeader().sectionClicked.connect(self.sort_column)
        self.table.cellDoubleClicked.connect(self.double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.table.customContextMenuRequested.connect(self.context_menu)
        splitter.addWidget(self.tree); splitter.addWidget(self.table); splitter.setSizes([440,960]); layout.addWidget(splitter,1)
        row = QHBoxLayout(); self.loaded = QLabel('Chưa tải chi tiết'); self.loaded.setWordWrap(True); row.addWidget(self.loaded,1)
        self.more_button = self.button('Tải thêm',lambda: self.reload_page(more=True),row); self.more_button.setEnabled(False)
        layout.addLayout(row)
        self.measure.currentIndexChanged.connect(self.change_measure)
        self.units.currentIndexChanged.connect(self.change_units)

    def format_size(self,value):
        return disk_size(value,self.units.currentData())

    def refresh_live_usage(self):
        self.live_disks=read_volume_usage([s.strip() for s in re.split(r'[|;]',self.roots.text()) if s.strip()])
        self.render_live_usage()

    def render_live_usage(self):
        self.live_label.setText('Hiện tại · '+' | '.join(f"{r['root']} Used {self.format_size(r.get('used'))}" for r in self.live_disks))
        self.live_label.setToolTip('\n'.join(f"{r['root']} · Used {exact(r.get('used'))} · Tổng {exact(r.get('capacity'))} · Trống {exact(r.get('free'))}" for r in self.live_disks))

    def update_headers(self):
        physical=self.measure.currentData()=='allocated'
        label='Đĩa' if physical else 'Logical'
        self.tree.setHeaderLabels(['Thư mục','Mới · '+label,'± '+label])
        self.table.setHorizontalHeaderLabels(['TT','Đường dẫn','± '+label,'Cũ · '+label,'Mới · '+label,'± Logical' if physical else '± Đĩa','Có ở'])

    def update_metrics(self):
        if not self.query:return
        summary=self.query.summary(); measure=self.measure.currentData(); lines=[]; totals=[]
        for side,title in [('old','Cũ'),('new','Mới')]:
            common,private=summary['common_'+side],summary['private_'+side]
            if common is not None:
                def measured(data):
                    return self.format_size(data[measure]) if data[measure] is not None else '≥'+self.format_size(data[measure+'_known'])+' ?'
                suffix='disk_delta' if measure=='allocated' else 'delta'
                change=disk_delta(summary['verified_common_'+suffix],self.units.currentData(),partial=bool(summary['unknown_common_'+('disk_files' if measure=='allocated' else 'files')]))
                lines.append(f"{title} · {MEASURES[measure]}: Chung {measured(common)} | Riêng {measured(private)}" + (' · ± Chung '+change if side=='new' else ''))
                total=common[measure]+private[measure] if common[measure] is not None and private[measure] is not None else None
                totals.append(f"{title} · Chung {exact(common[measure])} · Riêng {exact(private[measure])} · Tổng {exact(total)}")
        lines.append(f"{summary['rows']:,} đường dẫn · chuẩn bị {summary['seconds']:.0f}s · chỉ cộng file; không cộng thêm thư mục cha")
        self.metrics.setText('\n'.join(lines))
        self.metrics.setToolTip('\n'.join(totals)+ '\nTổng tính trên byte trước khi rút gọn; cộng các số nguyên GiB đã rút gọn có thể lệch 1 GiB.')
        disk_lines=[]; hints=[]
        for disk in summary.get('disks_new',[]):
            if disk.get('valid'):
                disk_lines.append(f"Mốc Mới #{self.query.new['id']} · {disk['root']} · Tổng {self.format_size(disk.get('capacity'))} | Đã dùng {self.format_size(disk.get('used'))} | Trống {self.format_size(disk.get('free'))}")
            else:
                disk_lines.append(f"Mốc Mới #{self.query.new['id']} · {disk['root']} · Tổng ? | Đã dùng ? | Trống ? · Thiếu hoặc mâu thuẫn số đo Windows")
            known=disk.get('allocated_known'); residual=disk.get('residual')
            file_value=('≥' if disk.get('allocated_unknown_files') else '')+self.format_size(known)
            remainder=self.format_size(residual) if residual is not None else '?'
            disk_lines.append(f"File trên đĩa đã đo {file_value} | Chưa quy vào file {remainder}" + (' · CẢNH BÁO: file vượt Used' if disk['status']=='mismatch' else ' · chỉ phạm vi thư mục' if not disk['whole_volume'] else ''))
            raw=json.dumps({key:disk.get(key) for key in ('capacity','used','free')},ensure_ascii=False) if not disk.get('valid') else f"Tổng {exact(disk.get('capacity'))} · Used {exact(disk.get('used'))} · Trống {exact(disk.get('free'))}"
            hints.append(f"{disk['root']} · {raw}\nFile cấp phát đã biết {exact(known)} · Chưa quy {exact(residual)}\n{disk.get('note','')}\nNguồn: {disk.get('source')} · {disk.get('captured_at')}")
        self.disk_metrics.setText('\n'.join(disk_lines) or 'Mốc này chưa lưu số đo Windows. Không suy ra dung lượng ổ từ Logical hoặc số đo hiện tại.')
        self.disk_metrics.setToolTip('\n\n'.join(hints))

    def change_measure(self):
        if self.worker:return
        self.update_headers();self.update_metrics()
        if not self.query:return
        options=self.filters();query=self.query
        def done(value):
            tree,page=value;self.tree.clear();self.fill_tree(tree['rows']);self.fill_page(page,False)
        self.begin(lambda stop,notify:(query.tree(measure=options['measure'],stop=stop),query.page(stop=stop,**options)),done)

    def change_units(self):
        self.render_live_usage();self.update_metrics();self.paint_rows()
        def walk(item):
            data=item.data(0,ROLE)
            if data and 'row' in data:
                row=data['row'];item.setText(1,self.format_size(row['after']));item.setText(2,disk_delta(row['delta'],self.units.currentData(),partial=row.get('partial_delta',False)))
            for i in range(item.childCount()):walk(item.child(i))
        for i in range(self.tree.topLevelItemCount()):walk(self.tree.topLevelItem(i))

    def _history(self):
        page = QWidget(); layout = QVBoxLayout(page)
        label = QLabel('Lịch sử thử nghiệm riêng · Nhập bản sao các mốc JSONL đã chọn một lần. Giữ nguyên nguồn; không chuyển/xóa hàng loạt.')
        if self.production:label.setText('Lịch sử chính · Snapshot Parquet nén; chỉ metadata. Xóa mốc không xóa file đã quét hoặc nguồn JSONL cũ.')
        label.setWordWrap(True); layout.addWidget(label)
        row = QHBoxLayout(); self.button('Nhập mốc JSONL cũ…',self.import_legacy,row)
        self.button('Nhập CSV WizTree…',self.import_csv,row); self.button('Làm mới',self.refresh_history,row)
        self.button('Xóa mốc Parquet đã chọn…',self.delete_snapshots,row,'danger')
        self.button('Dọn tạm phiên đã dừng',self.clear_temp,row); row.addStretch(); layout.addLayout(row)
        self.history = QTableWidget(0,6); self.history.setHorizontalHeaderLabels(['Mốc / ngày','Engine / phiên bản','File / Folder','Metadata nén','Giới hạn','Nguồn'])
        self.history.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.history,1)
        self.storage = QLabel(); self.storage.setWordWrap(True); layout.addWidget(self.storage)
        self.tabs.addTab(page,'Lịch sử Parquet')

    def _journal(self):
        page = QWidget(); layout = QVBoxLayout(page)
        row = QHBoxLayout(); self.button('Tiếp tục tác vụ đã chọn',self.resume,row)
        self.button('Bỏ điểm tiếp tục…',self.discard,row,'danger'); self.button('Làm mới',self.refresh_jobs,row); row.addStretch(); layout.addLayout(row)
        self.task_table = QTableWidget(0,4); self.task_table.setHorizontalHeaderLabels(['Tác vụ','Loại','Trạng thái','Chi tiết'])
        self.task_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.task_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.task_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.task_table)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.document().setMaximumBlockCount(1500); layout.addWidget(self.log,1)
        layout.addWidget(QLabel('Export đã hoàn tất có checksum sẽ được dùng lại; quét dở/Native dở phải quét lại. Không phục hồi/xóa file người dùng.'))
        self.tabs.addTab(page,'Nhật ký / tiếp tục')

    def refresh_history(self):
        selected_old, selected_new = self.older.currentData(), self.newer.currentData()
        scans = self.repo.list(); self.older.clear(); self.newer.clear(); self.older.addItem('— Mốc ban đầu —',None)
        self.history.setRowCount(len(scans))
        for index,row in enumerate(scans):
            label = scan_label(row); self.older.addItem(label,row['id']); self.newer.addItem(label,row['id'])
            summary = row['summary']; values = [f"#{row['id']} · {local_time(row['started'])}",f"{row['engine']} · v{row.get('app_version','?')}",
                f"{summary['files']:,} / {summary['folders']:,}",size(row['bytes']),row['status'],f"JSONL #{row['source_id']}" if row.get('source_id') else 'Quét / CSV mới']
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value);cell.setData(ROLE,row['id']);self.history.setItem(index,col,cell)
        self.newer.setCurrentIndex(max(0,self.newer.findData(selected_new)))
        old_index = self.older.findData(selected_old) if selected_old is not None else (2 if len(scans)>1 else 0)
        self.older.setCurrentIndex(max(0,old_index))
        footprint = self.repo.footprint()
        self.storage.setText(f"{len(scans)} mốc · snapshot {size(footprint['snapshot_bytes'])} · tạm {size(footprint['work_bytes'])} · nguồn tiếp tục {size(self.jobs.source_bytes())} · SQLite/DuckDB lưu lâu dài: 0 B")
        self.refresh_jobs()

    def refresh_jobs(self):
        tasks = self.jobs.list(); self.task_table.setRowCount(len(tasks))
        for i,row in enumerate(tasks):
            for j,value in enumerate([row['id'][:12],row['operation'],row['state'],row.get('error') or row['updated']]):
                cell = QTableWidgetItem(str(value)); cell.setData(ROLE,row['id']); self.task_table.setItem(i,j,cell)

    def clear_temp(self):
        if self.worker:return
        try:
            result=self.repo.cleanup_abandoned();self.on_progress('info',{'message':f"Đã bỏ {size(result['removed_bytes'])} tạm của phiên đã chết; giữ snapshot"});self.refresh_history()
        except (OSError,ValueError,RuntimeError) as exc:QMessageBox.warning(self,'Không thể dọn tạm',str(exc))

    def begin(self, function, callback):
        if self.worker:
            return
        if self.can_begin and not self.can_begin():
            return
        self.progress.setValue(0); self.more_button.setEnabled(False)
        self.scan_button.setEnabled(False); self.compare_button.setEnabled(False); self.stop_button.setEnabled(True)
        for widget in (self.older,self.newer,self.section,self.change,self.minimum,self.search,self.batch,self.measure,self.units,self.tree,self.table.horizontalHeader()):widget.setEnabled(False)
        if self.production:
            for widget in (self.kind,self.presence,self.reason):widget.setEnabled(False)
        worker = Worker(function,callback,self); self.worker = worker
        worker.event.connect(self.on_progress); worker.finished.connect(self.finished)
        self.busy_changed.emit(True); worker.start()

    def on_progress(self, event, data):
        message = data.get('message') or (f"Đã đọc {data['count']:,} mục" if 'count' in data else '')
        if event == 'progress' and 'percent' in data:
            self.progress.setValue(max(self.progress.value(),min(100,data['percent'])))
        if message:
            self.phase.setText(message)
            label={'progress':'INFO','count':'DEBUG','started':'INFO'}.get(event,event.upper())
            color={'ERROR':'#c62828','WARNING':'#9a6200','DEBUG':'#1684ce'}.get(label,'#325572')
            self.log.appendHtml(f'<span style="color:{color}">[{label}] {html.escape(message)}</span>')
            if self.production:
                import logging
                level = {'error':logging.ERROR,'warning':logging.WARNING,'debug':logging.DEBUG}.get(event,logging.INFO)
                logging.getLogger('dgm').log(level, '[PARQUET] %s', message)

    def finished(self):
        worker = self.worker; self.worker = None
        self.scan_button.setEnabled(True); self.compare_button.setEnabled(True); self.stop_button.setEnabled(False)
        for widget in (self.older,self.newer,self.section,self.change,self.minimum,self.search,self.batch,self.measure,self.units,self.tree,self.table.horizontalHeader()):widget.setEnabled(True)
        if self.production:
            for widget in (self.kind,self.presence,self.reason):widget.setEnabled(True)
        if worker.error:
            level = 'INFO' if isinstance(worker.error,ProcessCancelled) else 'ERROR'
            self.on_progress(level.lower(),{'message':str(worker.error) or 'Đã dừng an toàn; xem điểm tiếp tục'})
            if level == 'ERROR' and not self.want_close:
                self.log.appendPlainText(worker.trace); QMessageBox.warning(self,'Không hoàn tất tác vụ',str(worker.error))
        else:
            try:
                worker.callback(worker.value)
                self.progress.setValue(100)
            except Exception as exc:
                self.on_progress('error',{'message':str(exc)})
                if not self.want_close:QMessageBox.warning(self,'Không hoàn tất hiển thị',str(exc))
        worker.deleteLater(); self.refresh_jobs()
        self.more_button.setEnabled(bool(self.cursor) and self.query is not None)
        self.busy_changed.emit(False)
        if self.want_close:
            self.close()

    def stop_work(self):
        if self.worker:
            self.worker.stop.set(); self.phase.setText('Đang dừng an toàn; chờ giải phóng phiên và lưu journal…'); self.stop_button.setEnabled(False)

    def compare(self):
        newer,older = self.newer.currentData(),self.older.currentData()
        if newer is None or self.worker: return
        options=self.filters();options['root']=None
        self.loaded_rows=[];self.table.setRowCount(0);self.tree.clear();self.cursor=None
        self.metrics.setText('Đang chuẩn bị mốc; chưa có tổng dung lượng cho cặp mới.')
        self.disk_metrics.setText('Đang đọc số đo ổ đã lưu cùng mốc; không lấy số hiện tại thay lịch sử.')
        def work(stop,notify):
            if self.query: self.query.close(); self.query=None
            query = Comparison(newer,older,repository=self.repo,stop=stop,notify=notify)
            try:
                return query,query.tree(measure=options['measure'],stop=stop),query.page(stop=stop,**options)
            except BaseException:
                query.close();raise
        def done(value):
            self.query,tree,page=value;self.folder_scope=None;self.scope_display=None;self.cursor=None
            if self.production:
                from .production_core import remember_comparison
                remember_comparison(self.repo,self.query)
            self.tree.clear();self.update_headers();self.fill_tree(tree['rows']);self.fill_page(page,False)
            self.update_metrics();self.note.show();self.progress.setValue(100)
        self.begin(work,done)

    def filters(self):
        result=dict(limit=int(self.batch.currentText()),sort=self.sort,direction=self.direction,
                    root=self.folder_scope,section=self.section.currentData(),changes=self.change.currentData(),
                    minimum=self.minimum.currentData(),search=self.search.text().strip(),measure=self.measure.currentData())
        if self.production:
            result.update(kind=self.kind.currentData(),presence=self.presence.currentData())
            if self.reason.currentData() is not None:result['reason']=self.reason.currentData()
        return result

    def reload_page(self, *_, more=False):
        if self.worker or not self.query: return
        options=self.filters();cursor=self.cursor if more else None;query=self.query
        self.begin(lambda stop,notify: query.page(cursor=cursor,stop=stop,**options),lambda page:self.fill_page(page,more))

    def fill_page(self,page,more):
        if not more:self.loaded_rows=[]
        self.loaded_rows.extend(page['rows']);self.paint_rows()
        self.cursor=page['cursor'];self.more_button.setEnabled(bool(self.cursor));self.loaded.setText(f"{self.table.rowCount():,} / {page['total']:,} mục · {self.scope_display or 'Toàn phạm vi'} · sắp xếp cả tập theo {MEASURES[self.measure.currentData()]}")

    def paint_rows(self):
        self.table.setRowCount(len(self.loaded_rows))
        for offset,row in enumerate(self.loaded_rows):
            ink,paper=row_palette(row,private=row['presence']!='both')
            auxiliary=row['logical_delta'] if self.measure.currentData()=='allocated' else row['allocated_delta']
            values=[status_symbol(row),row['path'],disk_delta(row['delta'],self.units.currentData(),partial=row.get('partial_delta',False)),snapshot_text(row,'old',self.units.currentData()),snapshot_text(row,'new',self.units.currentData()),disk_delta(auxiliary,self.units.currentData()),{'both':'↔','old':'← Cũ','new':'Mới →'}[row['presence']]]
            for col,value in enumerate(values):
                cell=QTableWidgetItem(value);cell.setData(ROLE,row);cell.setBackground(QColor(paper));cell.setForeground(QColor(gui_change_color(row['delta'] if col==2 else auxiliary)) if col in (2,5) else QColor(ink))
                if col==0:cell.setToolTip(status_hint(row))
                if col in (2,3,4,5):
                    number={2:row['delta'],3:row['before'],4:row['after'],5:auxiliary}[col]
                    cell.setToolTip(exact(number)+(' · chênh lệch một phần đã xác minh' if col==2 and row.get('partial_delta') else ''))
                if col==1:
                    cell.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon if row['kind']=='folder' else QStyle.StandardPixmap.SP_FileIcon))
                    if self.table.fontMetrics().horizontalAdvance(value)>self.table.columnWidth(1)-35:cell.setToolTip(value)
                if row['change']=='missing':font=cell.font();font.setStrikeOut(True);cell.setFont(font)
                self.table.setItem(offset,col,cell)

    def fill_tree(self,rows,parent=None):
        for row in rows:
            name=Path(row['path']).name or row['path'];item=QTreeWidgetItem([name,self.format_size(row['after']),disk_delta(row['delta'],self.units.currentData(),partial=row.get('partial_delta',False))]);item.setData(0,ROLE,{'row':row,'loaded':False})
            item.setIcon(0,self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon));item.setToolTip(0,row['path'])
            item.setForeground(2,QColor(gui_change_color(row['delta'])));item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            item.setToolTip(1,exact(row['after']));item.setToolTip(2,exact(row['delta'])+'\n'+row.get('reason_text',''))
            (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))

    def expand_tree(self,item):
        data=item.data(0,ROLE)
        if not data or data.get('loaded') or self.worker:return
        query=self.query
        measure=self.measure.currentData()
        self.begin(lambda stop,notify:query.tree(data['row']['path_key'],limit=1000,measure=measure,stop=stop),lambda page:self.tree_children(item,page))

    def tree_children(self,item,page):
        data=item.data(0,ROLE);data['loaded']=True;item.setData(0,ROLE,data);self.fill_tree(page['rows'],item)
        if page['cursor']:
            more=QTreeWidgetItem(['Tải thêm thư mục…']);more.setData(0,ROLE,{'tree_cursor':page['cursor'],'root':data['row']['path_key']});item.addChild(more)

    def select_tree(self,item,*_):
        data=item.data(0,ROLE)
        if self.worker or not data:return
        if 'tree_cursor' in data:
            query=self.query;parent=item.parent()
            def done(page):parent.removeChild(item);self.tree_children(parent,page)
            measure=self.measure.currentData()
            self.begin(lambda stop,notify:query.tree(data['root'],limit=1000,cursor=data['tree_cursor'],measure=measure,stop=stop),done)
        else:
            self.folder_scope=data['row']['path_key'];self.scope_display=data['row']['path'];self.reload_page()

    def clear_scope(self):
        self.folder_scope=None;self.scope_display=None;self.reload_page()

    def sort_column(self,index):
        key=['change','path','delta','old','new','logical_delta' if self.measure.currentData()=='allocated' else 'allocated_delta','presence'][index]
        self.direction='asc' if self.sort==key and self.direction=='desc' else 'desc'
        self.sort=key;self.table.horizontalHeader().setSortIndicator(index,Qt.SortOrder.AscendingOrder if self.direction=='asc' else Qt.SortOrder.DescendingOrder);self.reload_page()

    def double_click(self,index,*_):
        row=self.table.item(index,0).data(ROLE)
        if row['kind']=='folder':self.folder_scope=row['path_key'];self.scope_display=row['path'];self.reload_page()
        else:self.action(row,'open')

    def action(self,row,action):
        try:
            name=None
            if action=='rename':
                name,ok=QInputDialog.getText(self,'Đổi tên','Tên mới:',text=Path(row['path']).name)
                if not ok:return
            if action in ('rename','trash') and QMessageBox.question(self,'Xác nhận thao tác',
                f"{'Đưa vào Thùng rác' if action=='trash' else 'Đổi tên'}: {row['path']}\n"
                'Tác động lên file/thư mục hiện tại; snapshot lịch sử giữ nguyên.')!=QMessageBox.StandardButton.Yes:return
            manual_action(row['path'],action,name)
        except (ValueError,OSError,RuntimeError) as exc:QMessageBox.warning(self,'Không mở được',str(exc))

    def context_menu(self,position):
        item=self.table.itemAt(position)
        if not item:return
        row=item.data(ROLE);menu=QMenu(self)
        for label,action in [('Mở','open'),('Hiện trong Explorer','reveal'),('Properties','properties'),('Đổi tên…','rename'),('Đưa vào Thùng rác…','trash')]:
            entry=menu.addAction(label);entry.triggered.connect(lambda checked=False,a=action:self.action(row,a))
        entry=menu.addAction('Sao chép đường dẫn');entry.triggered.connect(lambda:QApplication.clipboard().setText(row['path']))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def choose_root(self):
        path=QFileDialog.getExistingDirectory(self,'Chọn phạm vi')
        if path:self.roots.setText(path)

    def execute_job(self,job):
        if self.worker:return
        self.begin(lambda stop,notify:self.jobs.execute(job['id'],stop=stop,notify=notify),lambda results:self.job_done(results))

    def job_done(self,results):
        self.refresh_history()
        if results:self.newer.setCurrentIndex(self.newer.findData(results[-1]['id']))
        self.progress.setValue(100);self.on_progress('info',{'message':'Đã lưu các mốc: '+', '.join('#'+str(r['id']) for r in results)+'; bấm So sánh / mở mốc để xem'})
        self.snapshots_changed.emit(results)

    def scan(self):
        from .config import load
        if self.worker:return
        roots=[s.strip() for s in re.split(r'[|;]',self.roots.text()) if s.strip()]
        if not roots:QMessageBox.warning(self,'Phạm vi','Cần chọn phạm vi');return
        p={'roots':roots,'engine':self.engine.currentText(),'mft':self.mft.isChecked(),'strict_browsers':self.browser.isChecked(),'excludes':[]}
        name='preview'
        if self.profile_provider:
            try:name,p=self.profile_provider()
            except ValueError as exc:QMessageBox.warning(self,'Cấu hình',str(exc));return
        self.execute_job(self.jobs.create('scan',{'profile':p,'name':name,'engines':load()['engines']}))

    def import_legacy(self):
        if self.worker:return
        source=QFileDialog.getExistingDirectory(self,'Chọn app-home cũ chứa data/history/catalog.json')
        if not source:return
        ids,ok=QInputDialog.getText(self,'Chọn mốc','ID các mốc cần sao chép, ví dụ 36,37 (không xóa nguồn):')
        if not ok:return
        try:selected=[int(i.strip()) for i in ids.split(',') if i.strip()]
        except ValueError:QMessageBox.warning(self,'ID','Chỉ nhập số ID, cách nhau dấu phẩy');return
        if selected:self.execute_job(self.jobs.create('legacy',{'source_home':source,'ids':selected}))

    def import_csv(self):
        if self.worker:return
        source,_=QFileDialog.getOpenFileName(self,'CSV WizTree hoàn tất','','CSV (*.csv)')
        if not source:return
        zone,ok=QInputDialog.getText(self,'Múi giờ nguồn','UTC nếu export có /exportUTCTime=1, nếu không nhập IANA timezone:',text='UTC')
        roots=[s.strip() for s in re.split(r'[|;]',self.roots.text()) if s.strip()]
        if not ok:return
        mft=QMessageBox.question(self,'Nguồn CSV','CSV này thực sự quét MFT với Admin?\nChọn No nếu walk hoặc không chắc; ID walk không phải MFT thật.',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes
        self.execute_job(self.jobs.create('csv',{'source':source,'timezone':zone,'roots':roots,'mft':mft}))

    def selected_task(self):
        rows=self.task_table.selectionModel().selectedRows()
        return self.task_table.item(rows[0].row(),0).data(ROLE) if rows else None

    def resume(self):
        ident=self.selected_task()
        if ident and not self.worker:self.execute_job(self.jobs.get(ident))

    def discard(self):
        ident=self.selected_task()
        if not ident or self.worker:return
        if QMessageBox.question(self,'Bỏ điểm tiếp tục','Chỉ bỏ journal/export tạm của tác vụ, giữ snapshot và nguồn ngoài?')==QMessageBox.StandardButton.Yes:
            try:self.jobs.discard(ident);self.refresh_jobs()
            except (OSError,ValueError,RuntimeError) as exc:QMessageBox.warning(self,'Không thể bỏ',str(exc))

    def delete_snapshots(self):
        if self.worker:return
        ids=[self.history.item(i.row(),0).data(ROLE) for i in self.history.selectionModel().selectedRows()]
        if not ids:return
        if QMessageBox.question(self,'Xóa mốc thử nghiệm',f'Xóa bản Parquet {ids}? Giữ nguyên JSONL gốc, không xóa file đã quét.')!=QMessageBox.StandardButton.Yes:return
        if self.query:
            self.query.close();self.query=None;self.tree.clear();self.table.setRowCount(0);self.cursor=None;self.loaded_rows=[]
            self.metrics.setText('Chưa mở mốc.');self.disk_metrics.setText('Chưa mở số đo ổ lịch sử.')
        try:self.repo.delete(ids);self.refresh_history()
        except (ValueError,RuntimeError,OSError) as exc:QMessageBox.warning(self,'Không thể xóa',str(exc))

    def closeEvent(self,event):
        if self.worker:
            dialog=QMessageBox(self);dialog.setWindowTitle('Tác vụ đang chạy')
            dialog.setText('Export hoàn tất sẽ được giữ để tiếp tục; quét dở phải quét lại.\nApp chỉ đóng sau khi tác vụ dừng an toàn.')
            stay=dialog.addButton('Tiếp tục mở',QMessageBox.ButtonRole.RejectRole)
            stop_close=dialog.addButton('Dừng rồi tắt',QMessageBox.ButtonRole.DestructiveRole)
            dialog.setDefaultButton(stay);dialog.exec()
            if dialog.clickedButton()==stop_close:self.want_close=True;self.stop_work()
            event.ignore();return
        if self.query:self.query.close();self.query=None
        event.accept()


def start():
    app=QApplication.instance() or QApplication([])
    app.setApplicationName('Disk Growth Monitor · Parquet Preview');app.setStyle('Fusion')
    if os.name=='nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('DiskGrowthMonitor.ParquetPreview')
    window=Window();window.show();return app.exec()
