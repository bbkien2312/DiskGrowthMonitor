"""Variable-size cursor pages, bounded row LRU and an independent latest tree."""
from bisect import bisect_right
from collections import OrderedDict
import json
from pathlib import Path
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal, QEvent
from PySide6.QtGui import QColor, QFont, QPen, QShortcut, QKeySequence
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton, QCheckBox, QSplitter, QTreeWidget, QTreeWidgetItem, QTableView, QStyledItemDelegate, QTabBar, QStyle, QDialog, QPlainTextEdit, QDialogButtonBox
from .store import Store
from .config import comparison_path_key
from .comparison import Comparisons
from .presentation import size, local_time, scan_label, scan_name, change_text, change_color, CHANGES, storage_delta, row_status, status_colors, snapshot_cell
from .ui_helpers import StatusFilter, MetricCard, FittedTree, ElidingLabel, SectionLabel
from .comparison_visuals import (visual,status_symbol,status_hint,gui_snapshot_cell,gui_scan_label,make_legend,
    SEMANTIC_HINT_ROLE,row_palette,gui_change_text as change_text,gui_change_color as change_color)

HEADERS = ["TT", "Đường dẫn", "Cũ", "Mới", "±", "Loại", "± Đĩa", "Tạo", "Sửa"]
SORT_COLUMNS = ["change", "path", "old", "new", "delta", "kind", "allocated", "created", "modified"]
PAGE_SIZES = (20,50,100,200,500,1000)


class ComparisonRowDelegate(QStyledItemDelegate):
    def initStyleOption(self,option,index):
        super().initStyleOption(option,index)
        if index.column()==1:option.textElideMode=Qt.TextElideMode.ElideMiddle

    def paint(self,painter,option,index):
        from PySide6.QtWidgets import QStyleOptionViewItem
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        normal=QStyleOptionViewItem(option)
        # Keep the semantic row color when selected; selection has blue edges.
        normal.state &= ~(QStyle.StateFlag.State_Selected|QStyle.StateFlag.State_HasFocus|QStyle.StateFlag.State_MouseOver)
        super().paint(painter,normal,index)
        if selected:
            painter.save();painter.setPen(QPen(QColor('#327bbb'),1))
            r=option.rect.adjusted(0,0,-1,-1)
            painter.drawLine(r.topLeft(),r.topRight());painter.drawLine(r.bottomLeft(),r.bottomRight())
            if index.column()==0:painter.fillRect(r.left(),r.top(),3,r.height()+1,QColor('#327bbb'))
            painter.restore()


class PathDelegate(ComparisonRowDelegate):pass


class DetailModel(QAbstractTableModel):
    need_page = Signal(int)
    def __init__(self):
        super().__init__();self.batch_size=20;self.cache_limit=400;self.private=False;self.measurement='allocated'
        self.path_icons=None
        self.chunks=OrderedDict();self.anchors={0:None};self.pending=set();self.starts=[];self.lengths=[]
        self.loaded=0;self.next_cursor=None;self.total=0;self.paused=False

    def reset(self):
        self.beginResetModel();self.chunks.clear();self.anchors={0:None};self.pending.clear();self.starts.clear();self.lengths.clear()
        self.loaded=0;self.next_cursor=None;self.total=0;self.paused=False;self.cache_limit=max(400,2*self.batch_size);self.endResetModel()

    def set_batch_size(self,value):
        if value not in PAGE_SIZES:raise ValueError('Số mục mỗi lượt không hợp lệ')
        self.batch_size=value
        self.cache_limit=max(400,2*value,2*max(self.lengths,default=0))
        self.trim()

    def trim(self):
        while sum(map(len,self.chunks.values()))>self.cache_limit:
            self.chunks.popitem(last=False)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.loaded

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section] if orientation == Qt.Orientation.Horizontal else str(section + 1)
        if orientation==Qt.Orientation.Horizontal and role==Qt.ItemDataRole.ToolTipRole:
            if section in (2,3):return HEADERS[section]+' · '+('Chiếm trên đĩa' if self.measurement=='allocated' else 'Kích thước file')
            return {0:'Trạng thái • xem Ký hiệu & màu',4:'Chênh lệch dung lượng logic',6:'Chênh lệch dung lượng cấp phát trên đĩa'}.get(section,HEADERS[section])
        if orientation==Qt.Orientation.Horizontal and role==SEMANTIC_HINT_ROLE:
            return {0:'Trạng thái • xem Ký hiệu & màu',6:'Chênh lệch dung lượng cấp phát trên đĩa'}.get(section)
        if orientation==Qt.Orientation.Horizontal and section in (2,3):
            if role==Qt.ItemDataRole.ForegroundRole:return QColor('#325c8a' if section==2 else '#256d55')
            if role==Qt.ItemDataRole.BackgroundRole:return QColor('#edf4fc' if section==2 else '#eaf7ef')

    def row_at(self, row):
        if not 0<=row<self.loaded:return None
        chunk=bisect_right(self.starts,row)-1;offset=row-self.starts[chunk]
        if chunk not in self.chunks:
            if not self.paused and chunk in self.anchors and chunk not in self.pending:
                self.pending.add(chunk); self.need_page.emit(chunk)
            return None
        self.chunks.move_to_end(chunk)
        return self.chunks[chunk][offset] if offset < len(self.chunks[chunk]) else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.row_at(index.row())
        if row is None:
            return ('Đã dừng tải' if self.paused else 'Đang tải…') if role == Qt.ItemDataRole.DisplayRole and index.column() == 0 else None
        change=row.get('change','unknown');foreground,background=row_palette(row,self.private);column=index.column()
        measured=dict(row,before=row.get('allocated_before'),after=row.get('allocated_after')) if self.measurement=='allocated' else row
        if role==Qt.ItemDataRole.DecorationRole and column==1:
            if self.path_icons is None:
                from PySide6.QtWidgets import QApplication
                style=QApplication.style()
                self.path_icons={
                    'folder':style.standardIcon(QStyle.StandardPixmap.SP_DirIcon),
                    'file':style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)}
            # Generic icons only: never ask Explorer/stat the real saved path.
            return self.path_icons['folder' if row.get('kind')=='folder' else 'file']
        if role==SEMANTIC_HINT_ROLE:
            if column==0:return status_hint(row)
            if column in (2,3) and gui_snapshot_cell(measured,'old' if column==2 else 'new') in ('∅','?','×','—'):
                return ('Mốc cũ: ' if column==2 else 'Mốc mới: ')+snapshot_cell(measured,'old' if column==2 else 'new')
        if role==Qt.ItemDataRole.AccessibleTextRole and column==0:return status_hint(row)
        if role==Qt.ItemDataRole.FontRole:
            if column in (0,4):
                font=QFont('Segoe UI Symbol') if column==0 else QFont();font.setWeight(QFont.Weight.DemiBold)
                if column==0:font.setPixelSize(18)
                return font
            if column in (2,3) and gui_snapshot_cell(measured,'old' if column==2 else 'new') in ('∅','?','×','—'):
                return QFont('Segoe UI Symbol')
            if column==1 and change=='missing':
                font=QFont();font.setStrikeOut(True);return font
        if role==Qt.ItemDataRole.ForegroundRole:
            if not self.private and column in (4,6):
                delta=row.get('delta' if column==4 else 'allocated_delta')
                if delta is not None and delta!=0:return QColor(change_color(delta))
            return QColor(foreground)
        if role==Qt.ItemDataRole.BackgroundRole:
            return QColor(background)
        if role==Qt.ItemDataRole.TextAlignmentRole and column==0:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (2, 3, 4, 6):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            from datetime import datetime
            from zoneinfo import ZoneInfo
            stamp = lambda v: datetime.fromtimestamp(v, ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y %H:%M:%S") if v else "—"
            values = [status_symbol(row) if role==Qt.ItemDataRole.DisplayRole else status_hint(row),row['path'],gui_snapshot_cell(measured,'old'),gui_snapshot_cell(measured,'new'),
                      ('≈ ' if row.get('unverified_count') and row.get('delta') is not None else '')+change_text(row.get('delta')),
                      {"file":"File","folder":"Thư mục","link":"Liên kết"}.get(row["kind"],row["kind"]),change_text(row.get('allocated_delta')),
                      stamp(row.get('created')),stamp(row.get('modified'))]
            return values[column]

    def accept(self, group, page):
        rows=page['rows'];append=group==len(self.starts)
        if not 0<=group<=len(self.starts):raise ValueError('Trang tải không liên tục')
        start=self.loaded if append else self.starts[group];end=start+len(rows)
        if not append and len(rows)!=self.lengths[group]:raise ValueError('Trang đã thay đổi; cần tải lại kết quả')
        if append and rows:
            self.beginInsertRows(QModelIndex(),self.loaded,end-1)
            self.starts.append(start);self.lengths.append(len(rows));self.loaded=end
        if rows:self.chunks[group]=rows;self.chunks.move_to_end(group)
        self.pending.discard(group);self.total=page['total']
        if append:
            self.next_cursor=page['cursor']
            if page['cursor']:self.anchors[group+1]=page['cursor']
        self.cache_limit=max(400,2*self.batch_size,2*max(self.lengths,default=0));self.trim()
        if append and rows:
            self.endInsertRows()
        elif rows:
            self.dataChanged.emit(self.index(start,0),self.index(end-1,8))


class DiskBrowser(QWidget):
    def __init__(self, owner):
        super().__init__(); self.owner = owner; self.generation = 0; self.tree_generation = 0; self.detail_worker = None; self.loading_workers=set()
        self.detail_jobs=set();self.restore_target=0;self.query_options={};self.result_help_pending=False
        self.auto_baseline=False;self.fitting_columns=False
        self.pair_id = None; self.tree_pair = None; self.tree_ids = None; self.latest_tree_ids=None; self.root = None; self.profile = None; self.sort = "delta"; self.direction = "desc";self.pending_open=None
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(3)
        comparison_controls=QWidget();comparison_controls.setObjectName('comparisonControls');comparison_controls.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        controls = QHBoxLayout(comparison_controls);controls.setContentsMargins(7,4,5,4);controls.setSpacing(6)
        self.older = QComboBox(); self.newer = QComboBox()
        for field in (self.older,self.newer):
            field.setMinimumWidth(150);field.setMinimumContentsLength(10)
            field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        controls.addWidget(QLabel("Mốc cũ")); controls.addWidget(self.older,1); controls.addWidget(QLabel("Mốc mới")); controls.addWidget(self.newer,1)
        self.compare_button=owner.button("So sánh", self.compare, controls);self.compare_button.setProperty('actionRole','compare')
        owner.button("Xuất snapshot",owner.export,controls); owner.button("Xóa lượt quét",owner.delete_snapshot,controls)
        layout.addWidget(comparison_controls)
        self.common_scope=QCheckBox('Chỉ phần chung')
        self.common_scope.setChecked(True);self.common_scope.hide()
        controls.insertWidget(4,self.common_scope)
        self.older.setToolTip("Mốc cũ phải trước mốc mới và cùng phạm vi. Cây mặc định dùng đúng cặp này.")
        self.newer.setToolTip("Cũ/Mới áp dụng cho tổng quan, cây và bảng chi tiết khi cây chọn ‘Cặp đang so sánh’.")
        cards = QHBoxLayout(); self.metrics = []
        for tone in ('old','new','delta','summary'):
            label=MetricCard(tone); cards.addWidget(label,4 if tone=='summary' else 3); self.metrics.append(label)
        layout.addLayout(cards)
        filter_host=QWidget();filter_host.setObjectName('resultFilters')
        filters=QHBoxLayout(filter_host);filters.setContentsMargins(7,4,5,4);filters.setSpacing(6)
        filter_label=QLabel('LỌC');filter_label.setObjectName('zoneTag');filters.addWidget(filter_label)
        self.kind=QComboBox(); self.change=StatusFilter(CHANGES); self.search=QLineEdit(); self.search.setPlaceholderText("Tìm trong đường dẫn…")
        for label,value in [("Mọi loại","all"),("Thư mục","folder"),("File","file")]: self.kind.addItem(label,value)
        self.recursive=QCheckBox("Gồm thư mục con"); self.recursive.setChecked(True)
        for field in (self.kind,self.change,self.search): filters.addWidget(field,1 if field is self.search else 0)
        self.units=QComboBox();self.units.addItem('GB (10⁹ B)',False);self.units.addItem('GiB (2³⁰ B)',True)
        self.units.setCurrentIndex(int(bool(owner.preferences.values.get('units/binary',False))))
        self.units.setToolTip('Chỉ đổi cách hiển thị; dữ liệu luôn lưu byte. Windows thường ghi GB nhưng tính theo GiB.')
        filters.addWidget(self.units);self.units.currentIndexChanged.connect(self.units_changed)
        owner.button('Đối soát',self.show_reconciliation,filters)
        self.measurement=QComboBox();self.measurement.addItem('Chiếm trên đĩa','allocated');self.measurement.addItem('Kích thước file','logical')
        self.measurement.setCurrentIndex(max(0,self.measurement.findData(owner.preferences.values.get('measurement/mode','allocated'))))
        self.measurement.setToolTip('Đổi Cũ/Mới, thống kê và cây. ± luôn là kích thước file; ± Đĩa là cấp phát. Không so sánh Size với Windows đã dùng.')
        filters.insertWidget(filters.indexOf(self.units),self.measurement)
        owner.button("Lọc",self.reload,filters); owner.button("Toàn phạm vi",lambda:self.select_scope(None),filters)
        self.legend_toggle=owner.button('Ký hiệu & màu',lambda:self.legend.setVisible(self.legend_toggle.isChecked()),filters)
        self.legend_toggle.setCheckable(True);self.legend_toggle.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        layout.addWidget(filter_host)
        self.legend=make_legend(self);layout.addWidget(self.legend)
        self.search.returnPressed.connect(self.reload)
        compare_help=owner.help_box(layout,"compare","Chọn mốc cũ/mới rồi So sánh. Tổng quan, cây và chi tiết mặc định cùng một cặp mốc. Chọn thư mục để giới hạn chi tiết; sắp xếp áp dụng toàn bộ kết quả thỏa bộ lọc, không chỉ các dòng đã tải. Dung lượng logic khác dung lượng cấp phát trên đĩa.")
        self.compare_help=compare_help;self.compare_help.show_transient(False)
        filters.addWidget(compare_help.toggle)
        self.info=ElidingLabel("Chưa có kết quả")
        self.info.setStyleSheet('color:#526579;font-size:11px;');layout.addWidget(self.info)
        self.reconciliation_label=self.info
        self.splitter=QSplitter();self.splitter.setMinimumHeight(188)
        self.splitter.setCollapsible(0,False);self.splitter.setCollapsible(1,False)
        left=QWidget(); ll=QVBoxLayout(left); ll.setContentsMargins(0,0,0,0);ll.setSpacing(3)
        self.tree_header=QWidget();self.tree_header.setObjectName('paneBar');self.tree_header.setProperty('zone','tree')
        tree_header=QHBoxLayout(self.tree_header);tree_header.setContentsMargins(8,3,6,3);tree_header.setSpacing(7)
        self.tree_label=SectionLabel('Cây thư mục');self.tree_label.setObjectName('paneTitle');tree_header.addWidget(self.tree_label,1)
        self.tree_label.setProperty('zone','tree')
        self.tree_mode=QComboBox();self.tree_mode.addItem("Cặp đang so sánh","selected");self.tree_mode.addItem("Hai lượt mới nhất","latest")
        self.tree_mode.setMinimumHeight(self.tree_mode.fontMetrics().height()+14)
        self.tree_mode.view().setStyleSheet('QListView::item{min-height:30px;padding:2px 5px;}')
        self.tree_mode.setMinimumContentsLength(13)
        self.tree_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.tree_mode.setCurrentIndex(max(0,self.tree_mode.findData(owner.preferences.values.get('tree/mode','selected'))))
        self.tree_mode.setToolTip("Chọn cặp so sánh để cây khớp bảng chi tiết, hoặc theo dõi hai lượt hợp lệ mới nhất.");tree_header.addWidget(self.tree_mode);ll.addWidget(self.tree_header)
        self.tree=FittedTree(); self.tree.setHeaderLabels(["Thư mục","Mới","±"]); self.tree.setAlternatingRowColors(False)
        self.tree.headerItem().setToolTip(1,'Dung lượng logic tại mốc mới của cặp đang dùng cho cây')
        self.tree.itemExpanded.connect(self.expand); self.tree.itemClicked.connect(self.clicked);self.tree.itemDoubleClicked.connect(self.open_tree_item)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tree.customContextMenuRequested.connect(self.tree_menu)
        ll.addWidget(self.tree); self.splitter.addWidget(left)
        right=QWidget(); rl=QVBoxLayout(right); rl.setContentsMargins(0,0,0,0);rl.setSpacing(3)
        self.detail_header=QWidget();self.detail_header.setObjectName('paneBar');self.detail_header.setProperty('zone','details')
        detail_header=QHBoxLayout(self.detail_header);detail_header.setContentsMargins(8,3,6,3);detail_header.setSpacing(7)
        self.scope_label=SectionLabel('Chi tiết: Toàn phạm vi');self.scope_label.setObjectName('paneTitle');detail_header.addWidget(self.scope_label,1)
        self.scope_label.setProperty('zone','details')
        detail_header.addWidget(self.recursive);rl.addWidget(self.detail_header)
        self.tree_mode.ensurePolished()
        header_height=max(38,self.tree_mode.minimumHeight()+6,self.tree_mode.sizeHint().height()+6,self.fontMetrics().height()+18)
        for bar in (self.tree_header,self.detail_header):bar.setFixedHeight(header_height)
        sectionrow=QHBoxLayout();self.sections=QTabBar();self.sections.addTab('Chung • hai mốc');self.sections.addTab('Riêng • một mốc')
        self.sections.setObjectName('comparisonSections')
        self.sections.setTabToolTip(0,'Có bản ghi ở cả Cũ và Mới; dữ liệu chưa đủ vẫn mang trạng thái Chưa rõ')
        self.sections.setTabToolTip(1,'Chỉ một mốc: phân biệt thêm/mất thật với không quét/chưa rõ')
        self.sections.setStyleSheet('QTabBar::tab{padding:5px 9px;}')
        sectionrow.addWidget(self.sections,1)
        self.presence=QComboBox();self.presence.addItem('Mọi mốc','all');self.presence.addItem('Cả hai','both');self.presence.addItem('Chỉ Cũ','old');self.presence.addItem('Chỉ Mới','new')
        self.presence.setMaximumWidth(105);sectionrow.addWidget(self.presence)
        self.reason=QComboBox();self.reason.addItem('Mọi lý do',None)
        from .snapshot_scope import REASONS
        for key,label in REASONS.items():self.reason.addItem(label,key)
        self.reason.setMaximumWidth(145);self.reason.setMinimumWidth(90);sectionrow.addWidget(self.reason)
        self.reason.setMinimumContentsLength(8);self.reason.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        rl.addLayout(sectionrow)
        self.model=DetailModel(); self.model.need_page.connect(self.fetch_group)
        self.model.measurement=self.measurement.currentData();self.measurement.currentIndexChanged.connect(self.measurement_changed)
        self.table=QTableView();self.table.setObjectName('comparisonTable'); self.table.setModel(self.model); self.table.setAlternatingRowColors(False); self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(132)
        self.table.setWordWrap(False); self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setItemDelegate(ComparisonRowDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(max(32,self.table.fontMetrics().height()+14))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.table.customContextMenuRequested.connect(self.table_menu)
        self.table.doubleClicked.connect(self.open_row)
        self.table.horizontalHeader().setSortIndicatorShown(True); self.table.horizontalHeader().setSortIndicator(4,Qt.SortOrder.DescendingOrder)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_clicked)
        self.sections.currentChanged.connect(self.section_changed);self.reason.currentIndexChanged.connect(lambda *_:self.reload())
        self.presence.currentIndexChanged.connect(lambda *_:self.reload())
        self.tree_mode.currentIndexChanged.connect(self.tree_mode_changed)
        rl.addWidget(self.table,1); self.splitter.addWidget(right)
        layout.addWidget(self.splitter,1)
        widths=self.tree.default_widths();left.setMinimumWidth(sum(widths)+8);right.setMinimumWidth(430)
        self.splitter.setSizes(owner.preferences.values.get("splitter/scan-v3",[sum(widths)+8,850]))
        owner.columns(self.tree,"tree-v3",widths); owner.columns(self.table,"details-v5",[60,270,80,80,100,64,96,148,148])
        self.detail_columns_fitted=bool(owner.preferences.values.get('columns/details-v5'))
        self.path_user_sized=self.detail_columns_fitted
        self.table.viewport().installEventFilter(self)
        self.table.horizontalHeader().sectionResized.connect(self.detail_column_resized)
        footer=QHBoxLayout(); self.page_label=ElidingLabel("0 mục");footer.addWidget(self.page_label,1)
        footer.addWidget(QLabel('Mỗi lượt:'));self.page_size=QComboBox()
        for count in PAGE_SIZES:self.page_size.addItem(str(count),count)
        selected=owner.preferences.values.get('details/page-size',20)
        self.page_size.setCurrentIndex(max(0,self.page_size.findData(selected)))
        self.model.set_batch_size(self.page_size.currentData());footer.addWidget(self.page_size)
        self.page_size.setToolTip('Số mục lấy trong lần tải tiếp theo, theo thứ tự của toàn bộ phạm vi đang xem. Không xóa các dòng đã mở.')
        self.load_button=owner.button("Tải thêm 20 mục",self.load_more,footer); self.load_button.setEnabled(False)
        self.load_button.setProperty('actionRole','compare')
        self.page_size.currentIndexChanged.connect(self.batch_changed);self.update_page_text()
        self.cancel_button=owner.button("Dừng tải",self.cancel_loading,footer);self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("Dừng chuẩn bị/tải chi tiết và cây; không xóa snapshot, không thay đổi file trên ổ.")
        footer.addStretch(); owner.button("Hỏi AI",owner.ai_snapshot,footer); layout.addLayout(footer)
        self.install_shortcuts()

    def refresh(self, profile, *, load_tree=True):
        tree_ids=None
        store=Store(readonly=True)
        try:
            all_rows=sorted(store.scans(),key=lambda r:(r["started"],r["id"]),reverse=True)
            rows=[r for r in all_rows if r['profile']==profile]
            prior=self.newer.currentData(); old=self.older.currentData(); changed=profile!=self.profile
            self.profile=profile; self.newer.clear(); self.older.clear(); self.older.addItem("Tự động: lượt hợp lệ trước mốc mới",None)
            self.older.addItem("Mốc ban đầu: không so với lượt cũ",0)
            valid=[r for r in rows if r["status"] in ("complete","partial")]
            for row in valid:
                self.newer.addItem(gui_scan_label(row,profile),row['id']);self.newer.setItemData(self.newer.count()-1,scan_label(row)+'\n'+row['config'],Qt.ItemDataRole.ToolTipRole)
            for row in all_rows:
                if row['status'] in ('complete','partial'):
                    self.older.addItem(gui_scan_label(row,profile),row['id']);self.older.setItemData(self.older.count()-1,scan_label(row)+'\n'+row['config'],Qt.ItemDataRole.ToolTipRole)
            if prior and not changed: self.newer.setCurrentIndex(max(0,self.newer.findData(prior)))
            if old is not None and not changed: self.older.setCurrentIndex(max(0,self.older.findData(old)))
            if changed: self.root=None
            if valid:
                latest=valid[0]; previous=store.previous(latest["id"])
                self.latest_tree_ids=(previous,latest["id"])
                tree_ids=self.latest_tree_ids if self.tree_mode.currentData()=='latest' else self.selected_tree_ids(store)
            else:
                self.latest_tree_ids=None
        finally: store.close()

        if not load_tree:
            self.tree_generation+=1;self.tree.clear();self.tree_ids=None;self.tree_pair=None
            self.tree.addTopLevelItem(QTreeWidgetItem(['Bấm So sánh để nạp cây và chi tiết','','']))
        elif tree_ids and self.tree_ids!=tree_ids and self.tree_mode.currentData()=='latest':self.refresh_tree(*tree_ids)
        elif not tree_ids:
            self.tree_generation+=1;self.tree.clear();self.tree_ids=None;self.tree_pair=None
            self.tree_label.setText('Cây thư mục')
            self.tree.addTopLevelItem(QTreeWidgetItem(['Chưa có lượt quét hợp lệ','','']))

    def show_saved_overview(self):
        """Startup preview from small summaries, never from the JSONL payloads."""
        from .history_backend import comparison_history
        from .comparison import VERSION
        selected=self.selected_tree_ids()
        if not selected:
            self.info.setText('Chưa có lượt hợp lệ. Xem các lượt đã lưu tại Lịch sử.');return
        older,newer=selected
        store=Store(readonly=True)
        try:
            current=[store.scan(older) if older else None,store.scan(newer)]
        finally:store.close()
        fields=('id','started','finished','status','config','summary')
        def same(saved,actual):
            return saved is actual if saved is None or actual is None else all(saved.get(key)==actual.get(key) for key in fields)
        for row in comparison_history():
            if (row.get('older') or None,row['newer'])!=(older,newer) or row.get('status') not in ('ready','archived'):continue
            summary=json.loads(row.get('summary') or '{}')
            if summary.get('comparison_version')!=VERSION or 'counts' not in summary:continue
            if not all(same(summary.get(key),actual) for key,actual in zip(('older','newer'),current)):continue
            self.render_metrics(summary)
            self.info.setText('Tóm tắt đã lưu • '+self.result_caption+' • Bấm So sánh để nạp cây và chi tiết.')
            return
        self.info.setText('Đã nạp các mốc lịch sử • Bấm So sánh để đọc log và nạp cây/chi tiết khi cần.')

    def units_changed(self):
        from .presentation import set_units
        binary=self.units.currentData();set_units(binary);self.owner.preferences.set('units/binary',binary)
        if getattr(self,'current_summary',None):self.render_metrics(self.current_summary)
        if self.pair_id:self.reload(preserve_count=True)
        self.tree_ids=None;self.owner.refresh_history(load_tree=bool(self.pair_id or self.tree_pair))
        if hasattr(self.owner,'history_panel'):self.owner.history_panel.invalidate()

    def selected_tree_ids(self,store=None):
        newer=self.newer.currentData()
        if not newer:return None
        owned=store is None
        store=store or Store(readonly=True)
        try:
            selected=self.older.currentData()
            older=store.previous(newer) if selected is None else selected or None
            return older,newer
        finally:
            if owned:store.close()

    def tree_mode_changed(self,*_):
        mode=self.tree_mode.currentData()
        self.owner.preferences.set('tree/mode',mode)
        if mode=='latest':
            if self.latest_tree_ids:self.refresh_tree(*self.latest_tree_ids)
        else:
            selected=self.selected_tree_ids()
            if selected and getattr(self,'ids',None)!=selected:self.compare()
            elif selected:self.refresh_tree(*selected)

    def refresh_tree(self, previous, newer_id, pair_id=None):
        self.tree_generation+=1; generation=self.tree_generation; self.tree_ids=(previous,newer_id); self.tree.clear(); self.tree_pair=None
        store=Store(readonly=True)
        try:
            prior=store.scan(previous) if previous else None
            latest=store.scan(newer_id)
        finally: store.close()
        self.tree_label.setText('Cây thư mục')
        self.tree_label.setToolTip((scan_name(prior)+" → " if prior else "Mốc ban đầu → ")+scan_name(latest)+"\n"+local_time(latest["started"]))
        roots=list(dict.fromkeys((json.loads(prior['config'])['roots'] if prior else [])+json.loads(latest['config'])['roots']))
        common=self.common_scope.isChecked() and self.tree_mode.currentData()=='selected'
        def prepare(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:
                pair=pair_id or cache.prepare(previous,newer_id)
                rows=[]
                for root in roots:
                    records=cache.db.execute("SELECT c.*,p.path,pr.common_before,pr.common_after,pr.private_before,pr.private_after,pr.unverified_count FROM source.paths p INDEXED BY paths_key JOIN diff_rows c ON c.path_id=p.id LEFT JOIN folder_parts pr ON pr.pair_id=c.pair_id AND pr.path_id=c.path_id WHERE c.pair_id=? AND p.path_key=?",(pair,comparison_path_key(root))).fetchmany(2)
                    if len(records)==1: rows.append(dict(records[0]))
                return pair,rows
            finally: cache.close()
        def done(value):
            if generation!=self.tree_generation:return
            self.tree_pair,rows=value
            for row in rows:self.add_node(row)
        self.track(self.owner.job(prepare,done))

    def add_node(self,row,parent=None):
        disk=self.measurement.currentData()=='allocated'
        measured=dict(row,before=row.get('allocated_before'),after=row.get('allocated_after')) if disk else row
        delta=row.get('allocated_delta' if disk else 'delta')
        item=QTreeWidgetItem([row["path"] if parent is None else Path(row["path"]).name,gui_snapshot_cell(measured,'new'),
            ('≈ ' if row.get('unverified_count') and delta is not None else '')+change_text(delta)])
        item.setToolTip(0,row['path'])
        item.setIcon(0,self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        item.setToolTip(1,'Chung: '+size(row['after'])+'\nRiêng: '+size(row.get('private_after')))
        item.setData(0,Qt.ItemDataRole.UserRole,{"path":row["path"],"pair":self.tree_pair,"cursor":None,'row':row})
        _,_,foreground,background=visual(row['change'])
        for column in range(3):
            item.setForeground(column,QColor(foreground));item.setBackground(column,QColor(background))
        if row['change']=='missing':
            font=item.font(0);font.setStrikeOut(True);item.setFont(0,font)
        basis='theo hai lượt mới nhất' if self.tree_mode.currentData()=='latest' else 'theo cặp Cũ/Mới đang chọn'
        item.setForeground(2,QColor(change_color(delta))); item.setToolTip(2,CHANGES.get(row["change"],row["change"])+" • "+basis)
        (parent.addChild(item) if parent else self.tree.addTopLevelItem(item)); item.addChild(QTreeWidgetItem(["Mở để tải thư mục con…"]))

    def expand(self,item):
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if not data or data.get("more") or item.childCount()!=1 or item.child(0).data(0,Qt.ItemDataRole.UserRole):return
        self.children(item,None,replace=True)

    def children(self,parent,cursor,replace=False):
        data=parent.data(0,Qt.ItemDataRole.UserRole); generation=self.tree_generation
        sort='disk_new' if self.measurement.currentData()=='allocated' else 'new'
        def fetch(worker):
            cache=Comparisons(worker.stop)
            try:
                pair=data['pair']
                return cache.page(pair,root=data["path"],recursive=False,folders_only=True,limit=20,cursor=cursor,sort=sort)
            finally:cache.close()
        def done(page):
            if generation!=self.tree_generation:return
            if replace:parent.takeChildren()
            for row in page["rows"]:self.add_node(row,parent)
            if page["cursor"]:
                more=QTreeWidgetItem(["Tải thêm 20 thư mục…"]);more.setData(0,Qt.ItemDataRole.UserRole,{"more":True,"cursor":page["cursor"]});parent.addChild(more)
        self.track(self.owner.job(fetch,done))

    def clicked(self,item,column):
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if not data:return
        if data.get("more"):
            parent=item.parent();cursor=data["cursor"];parent.removeChild(item);self.children(parent,cursor)
        else:self.select_scope(data["path"])

    def select_scope(self,path):
        self.root=path;self.reload()

    def section_changed(self,*_):
        self.auto_baseline=False
        self.model.private=self.sections.currentIndex()==1
        self.model.headerDataChanged.emit(Qt.Orientation.Horizontal,0,len(HEADERS)-1)
        self.presence.blockSignals(True);self.presence.setCurrentIndex(0);self.presence.blockSignals(False)
        self.reload()

    def options(self):
        sort={'old':'disk_old','new':'disk_new'}.get(self.sort,self.sort) if self.measurement.currentData()=='allocated' else self.sort
        return dict(root=self.root,recursive=self.recursive.isChecked(),minimum=self.owner.threshold.value()*1_000_000,
                    kind=self.kind.currentData(),changes=self.change.currentData(),search=self.search.text(),sort=sort,direction=self.direction,
                    section='private' if self.sections.currentIndex()==1 else 'common',reason=self.reason.currentData(),presence=self.presence.currentData())

    def batch_changed(self,*_):
        count=self.page_size.currentData();self.model.set_batch_size(count)
        self.owner.preferences.set('details/page-size',count);self.update_page_text()

    def update_page_text(self):
        count=self.page_size.currentData();self.load_button.setText(f'Tải thêm {count} mục')
        sort=self.query_options.get('sort',self.sort);direction=self.query_options.get('direction',self.direction)
        arrow='↑' if direction=='asc' else '↓'
        label=HEADERS[SORT_COLUMNS.index({'disk_old':'old','disk_new':'new'}.get(sort,sort))]
        if sort=='delta' and getattr(self,'ids',(1,))[0] is None:label='Mới (mốc đầu)'
        self.page_label.setText(f'Đã mở {self.model.loaded:,}/{self.model.total:,} mục • {label} {arrow} • cache RAM ≤ {self.model.cache_limit:,} dòng')
        root=self.query_options.get('root',self.root)
        scope=str(root) if root else 'Toàn phạm vi'
        self.scope_label.setText('Chi tiết: '+scope)

    def stop_details(self):
        for worker in list(self.detail_jobs):worker.stop.set()
        self.detail_worker=None;self.restore_target=0

    def track_detail(self,worker):
        self.detail_jobs.add(worker);self.track(worker)
        worker.finished.connect(lambda:self.detail_jobs.discard(worker))

    def compare(self):
        if self.tree_ids is None and self.profile:self.refresh(self.profile)
        newer=self.newer.currentData()
        self.generation+=1;generation=self.generation
        self.stop_details();self.query_options=self.options()
        self.model.reset();self.pair_id=None;self.load_button.setEnabled(False)
        self.update_page_text()
        self.owner.rows=[]
        if not newer:
            for metric in self.metrics: metric.setText("—")
            self.info.setText("Chưa có lượt hợp lệ. Các lượt lỗi/đã dừng xem tại Lịch sử.");return
        store=Store(readonly=True)
        try:
            selected=self.older.currentData()
            older=store.previous(newer) if selected is None else selected or None
            a=store.scan(older) if older else None;b=store.scan(newer)
        finally:store.close()
        self.ids=(older,newer)
        if older is None:
            self.sections.blockSignals(True);self.sections.setCurrentIndex(1);self.sections.blockSignals(False)
            self.auto_baseline=True;self.model.private=True;self.query_options=self.options()
        elif self.auto_baseline:
            self.sections.blockSignals(True);self.sections.setCurrentIndex(0);self.sections.blockSignals(False)
            self.auto_baseline=False;self.model.private=False;self.query_options=self.options()
        self.result_help_pending=True
        if self.tree_mode.currentData()=='selected':
            self.tree_generation+=1;self.tree.clear();self.tree_pair=None;self.tree_ids=self.ids
        old_totals=json.loads(a["summary"] or "{}") if a else None;new_totals=json.loads(b["summary"] or "{}")
        self.metrics[0].setText("DUNG LƯỢNG CŨ\nĐang chia…")
        self.metrics[1].setText("DUNG LƯỢNG MỚI\nĐang chia…")
        delta=None
        uncertain=b['status']=='partial' or (a and a['status']=='partial')
        self.metrics[2].setText("CHÊNH LỆCH\nĐang tính…")
        self.metrics[2].value.setStyleSheet('color:'+change_color(delta)+';');self.metrics[3].setText("TOÀN PHẠM VI\nĐang tính…")
        warnings=" • Có vùng chưa xác minh: thêm/mất có thể hiện ‘chưa rõ’." if new_totals.get("coverage_unverified") or (old_totals or {}).get("coverage_unverified") else ""
        volumes=new_totals.get("volumes",{})
        disk=" • Ổ tại mốc mới: "+"; ".join(f"{v} đã dùng {size(x['used'])}, trống {size(x['free'])}" for v,x in volumes.items()) if volumes else " • Dung lượng trống tại mốc này chưa được lưu."
        self.info.setText(("Cũ: "+scan_name(a)+" → Mới: " if a else "Mốc ban đầu → Mới: ")+scan_name(b)+' • '+b['engine']+(' • Có giới hạn dữ liệu' if uncertain else ''))
        self.info.setToolTip(("Cũ: "+scan_label(a)+" → " if a else "Mốc ban đầu • ")+"Mới: "+scan_label(b)+disk+warnings)
        def prepare(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:return cache.prepare(older,newer)
            finally:cache.close()
        def done(pair):
            if generation!=self.generation:return
            self.pair_id=pair;self.detail_worker=None
            if self.tree_mode.currentData()=='selected':self.refresh_tree(*self.ids,pair_id=pair)
            self.fetch_group(0)
            if hasattr(self.owner,'history_panel'):self.owner.history_panel.invalidate()
        common=self.common_scope.isChecked()
        self.detail_worker=self.owner.job(prepare,done)
        self.track_detail(self.detail_worker)
        current_worker=self.detail_worker
        current_worker.finished.connect(lambda:setattr(self,'detail_worker',None) if self.detail_worker is current_worker else None)

    def reload(self,preserve_count=False):
        if self.pair_id:
            wanted=self.model.loaded if preserve_count else 0
            self.generation+=1
            self.stop_details();self.query_options=self.options();self.restore_target=wanted
            self.model.reset();self.update_page_text();self.fetch_group(0)
        else:self.compare()

    def cache_cleared(self):
        self.generation+=1;self.stop_details();self.pair_id=None;self.model.reset();self.owner.rows=[]
        self.load_button.setEnabled(False);self.update_page_text()
        self.info.setText("Cache so sánh đã xóa; snapshot vẫn được giữ. Bấm So sánh để tạo lại kết quả khi cần.")

    def render_metrics(self,summary):
        self.current_summary=summary
        from .measurements import amount,reconciliation,provenance
        mode=self.measurement.currentData()
        def measured(t):
            t=t or {};unknown=t.get('allocated_unknown_files',0)
            return ('Đã đo ≈ '+size(t.get('allocated_known'))+'\n'+str(unknown)+' chưa đo') if unknown else 'Cấp phát\n'+size(t.get('allocated'))
        for index,key,title in ((0,'old','DUNG LƯỢNG CŨ'),(1,'new','DUNG LƯỢNG MỚI')):
            common=summary.get(key+'_totals') or {};private=summary.get('private_'+key+'_totals') or {}
            def main(t):
                value,unknown=amount(t,mode)
                if mode=='allocated' and unknown:
                    return 'Chưa rõ' if unknown>=t.get('files',0) else '≥ '+size(t.get('allocated_known'))
                return size(value)
            def note(t):
                if mode=='logical':return measured(t)
                unknown=t.get('allocated_unknown_files',0)
                return (f'{unknown:,} file chưa rõ' if unknown else 'Chiếm trên đĩa')+'\nSize '+size(t.get('logical'))
            self.metrics[index].set_parts(title,main(common),main(private),note(common),note(private))
            self.metrics[index].setToolTip('Size theo đường dẫn có thể lớn hơn sức chứa do hardlink.\n'+summary.get('partition_note','')+'\n'+
                '\n'.join((summary.get('full_'+key+'_totals') or {}).get('normalization',{}).get('warnings',[])))
            raw=lambda t:'—' if t.get('logical') is None else f"{t['logical']:,} B"
            self.metrics[index].setToolTip(self.metrics[index].toolTip()+'\nByte gốc — Chung: '+raw(common)+'; Riêng: '+raw(private))
            full=summary.get('full_'+key+'_totals') or {}
            if full.get('identity_unique_logical_estimate') is not None:
                self.metrics[index].setToolTip(self.metrics[index].toolTip()+'\nToàn phạm vi — Size bỏ đường dẫn hardlink lặp theo ID đã biết (ước tính): '+size(full['identity_unique_logical_estimate'])+'; không phải Allocated hay Windows đã dùng.')
        key='_allocated' if mode=='allocated' else ''
        delta=summary.get('verified_common'+key+'_delta');private_delta=summary.get('verified_private'+key+'_delta')
        def verified_text(group,value):
            unknown=summary.get('unverified_'+group+key+'_files',0)
            counts=summary.get(group+'_counts',[]);files=sum(r['count'] for r in counts if r['kind']=='file')
            if unknown and unknown>=files:return 'Chưa rõ'
            return ('≈ ' if unknown else '')+change_text(value)
        def verified_note(group):
            unknown=summary.get('unverified_'+group+key+'_files',0)
            return f'Đã xác định\n{unknown:,} chưa rõ' if unknown else 'Đã xác định' if summary.get('older') else 'Mốc ban đầu'
        self.metrics[2].set_parts('CHÊNH LỆCH',verified_text('common',delta),verified_text('private',private_delta),
            verified_note('common'),verified_note('private'))
        self.metrics[2].setToolTip('Chỉ cộng thay đổi đủ bằng chứng của cả Chung và Riêng; không lấy hiệu các số đã làm tròn. Không quét/chưa rõ không tính bằng 0.')
        self.metrics[2].value.setStyleSheet('color:'+change_color(delta)+';')
        self.metrics[2].private_value.setStyleSheet('color:'+change_color(private_delta)+';')
        def count_text(counts,private=False):
            files=sum(x['count'] for x in counts if x['kind']=='file');folders=sum(x['count'] for x in counts if x['kind']=='folder')
            changed=sum(x['count'] for x in counts if x['change'] not in ('same','first_scan','moved_from'))
            return f'{files:,} file · {folders:,} thư mục',f'{changed:,} mục đổi/chưa rõ'
        common,cnote=count_text(summary.get('common_counts',summary['counts']));private,pnote=count_text(summary.get('private_counts',[]),True)
        self.metrics[3].set_parts('TOÀN PHẠM VI',common,private,cnote,pnote)
        self.metrics[3].setToolTip('Mỗi đường dẫn thuộc một nhóm hiện diện; tổng dung lượng chỉ cộng file, không cộng chồng thư mục cha/con.\n'+summary.get('partition_note',''))
        full=summary.get('full_new_totals',summary.get('new_totals',{})) or {}
        checks=summary.get('measurement_checks',{})
        self.metrics[3].setToolTip(self.metrics[3].toolTip()+'\nKiểm tra Chung + Riêng: '+json.dumps(checks,ensure_ascii=False))
        roots=json.loads(summary['newer']['config'])['roots'] if summary.get('newer') else []
        lines=[]
        for r in reconciliation(full,roots):
            lines.append(f"{r['volume']} · Sức chứa {size(r['total'])} · Windows đã dùng {size(r['used'])} · File đã đo {size(r['measured'])} · Chưa quy vào file {size(r['residual'])}"+(' · CẢNH BÁO: '+r['warning'] if r['warning'] else ''))
        small=full.get('small_files')
        lines.append((f"≤10 MB: {small:,} file, Size {size(full.get('small_logical'))}, đã tính trong tổng." if small is not None else 'Mốc cũ chưa lưu thống kê ≤10 MB; không suy từ phần chênh với Windows.'))
        lines.append(provenance(full)+' · Phần chưa quy vào file không xác định tăng/giảm theo đường dẫn.')
        self.reconciliation_text='\n'.join(lines)
        old,new=summary.get('older'),summary.get('newer')
        self.result_caption=('Cũ: '+scan_name(old)+' → Mới: ' if old else 'Mốc ban đầu → Mới: ')+(scan_name(new) if new else '')
        self.reconciliation_label.setText(self.result_caption+' • '+lines[0])
        self.reconciliation_label.setToolTip(self.reconciliation_text)
        self.reconciliation_label.show()
        for card in self.metrics:
            card.setToolTip(card.toolTip()+'\nSố hiển thị đã làm tròn; phép tính dùng byte gốc. Tổng các số làm tròn có thể lệch một đơn vị hiển thị.')
        width=max(card.minimumWidth() for card in self.metrics[:3])
        for card in self.metrics[:3]:card.setMinimumWidth(width)

    def measurement_changed(self,*_):
        mode=self.measurement.currentData();self.model.measurement=mode
        self.owner.preferences.set('measurement/mode',mode)
        self.model.headerDataChanged.emit(Qt.Orientation.Horizontal,0,len(HEADERS)-1)
        self.tree.headerItem().setToolTip(1,'Chiếm trên đĩa' if mode=='allocated' else 'Kích thước file theo đường dẫn')
        if getattr(self,'current_summary',None):self.render_metrics(self.current_summary)
        if self.tree_ids:self.refresh_tree(*self.tree_ids)
        if self.pair_id:self.reload(preserve_count=True)

    def fetch_group(self,group):
        if not self.pair_id:return
        append=group==len(self.model.starts)
        if append and self.detail_worker is not None:return
        if group not in self.model.anchors:return
        generation=self.generation;pair=self.pair_id;cursor=self.model.anchors[group]
        count=self.page_size.currentData() if append else self.model.lengths[group]
        if append and self.restore_target>self.model.loaded:count=min(count,self.restore_target-self.model.loaded)
        options=dict(self.query_options,limit=count,cursor=cursor)
        if append:self.load_button.setEnabled(False)
        accepted=[False]
        def fetch(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:return cache.page(pair,**options),cache.summary(pair)
            finally:cache.close()
        def done(result):
            if generation!=self.generation:return
            page,summary=result;self.model.accept(group,page);self.owner.rows=page["rows"]
            self.render_metrics(summary)
            if not self.detail_columns_fitted:
                self.fit_detail_columns()
            volumes=summary.get('full_new_totals',summary.get('new_totals',{})).get('volumes',{})
            disk='; '.join(f"{volume} sức chứa {size(values.get('total'))}, Windows đã dùng {size(values.get('used'))}, trống {size(values.get('free'))}" for volume,values in volumes.items())
            base=getattr(self,'result_caption',self.info.text().split(' • Tổng đã xác định')[0].split('\n')[0])
            unknown=summary.get('unverified_all_files',0)
            if getattr(self,'reconciliation_text',''):
                self.info.setText(base+' • '+self.reconciliation_text.splitlines()[0])
                self.info.setToolTip(base+'\n'+self.reconciliation_text)
            else:
                self.info.setText(base+' • Tổng đã xác định '+change_text(summary.get('verified_delta'))+
                    (f' • {unknown:,} file chưa rõ' if unknown else '')+('\n'+disk if disk else ''))
            accepted[0]=True;self.update_page_text()
            if self.result_help_pending:
                self.result_help_pending=False
                compatibility=summary.get('compatibility',{})
                old=summary.get('older');new=summary.get('newer')
                if new:
                    pair_text=(scan_name(old)+' → ' if old else 'Mốc ban đầu → ')+scan_name(new)
                    engines=' → '.join(x for x in compatibility.get('engines',[]) if x)
                    self.compare_help.body.setText(pair_text+' • '+engines+'\n'+
                        'Chung = hai mốc; Riêng = một mốc. Tổng chỉ cộng file; folder/cây là toàn bộ nhánh, có thể chứa cả Chung và Riêng. '+
                        '+ thêm, − không còn, » không quét, ? chưa rõ, ≈ phần đã xác định. Cũ xanh lam, Mới xanh ngọc. '+
                        'Tổng đã xác định tính cả hai nhóm; không coi không quét/chưa rõ là đã xóa. '+
                        (' So sánh khác công cụ dùng độ chính xác thời gian chung một giây.' if compatibility.get('cross_engine') else '')+
                        (' Mốc cũ chưa ghi chuẩn đo mới; giữ dữ liệu gốc, chỉ chuẩn hóa phần có thể đối chiếu.' if compatibility.get('legacy_metadata') else '')+
                        (' Vùng chưa đủ bằng chứng có nhãn Chưa xác định, không kết luận file đã xóa.' if compatibility.get('coverage_unverified') else ''))
                self.compare_help.show_transient()
            counts=summary["counts"];changed=sum(r["count"] for r in counts if r["change"] not in ("same","first_scan","moved_from"))
            files=sum(r["count"] for r in counts if r["kind"]=="file");folders=sum(r["count"] for r in counts if r["kind"]=="folder")
            self.owner.displayed_scan_id=self.ids[1]
            if self.owner.scan_worker is None:
                restoring=self.restore_target>self.model.loaded and bool(self.model.next_cursor)
                count_change=lambda key:sum(r['count'] for r in counts if r['change']==key)
                brief=f"{count_change('grown'):,} tăng • {count_change('shrunk'):,} giảm • {count_change('new'):,} mới • {count_change('missing'):,} mất • {count_change('not_scanned'):,} không quét • {count_change('unknown')+count_change('partial'):,} chưa rõ"
                self.owner.status.setText((f'Đang lấy lại {self.model.loaded:,}/{self.restore_target:,} mục theo thứ tự mới…' if restoring else f"Đã tải {self.model.loaded:,}/{page['total']:,} dòng • Toàn phạm vi: {brief} • Δ data/log {storage_delta(summary.get('storage'))}"))
            if self.pending_open and self.pending_open[0]==generation:
                pending_row=self.pending_open[1];start=self.model.starts[group]
                if start<=pending_row<start+len(page["rows"]):
                    self.pending_open=None;self._dispatch_open(self.model.row_at(pending_row))
        worker=self.owner.job(fetch,done)
        if append:self.detail_worker=worker
        self.track_detail(worker)
        def finished():
            if generation!=self.generation:return
            self.model.pending.discard(group)
            if not accepted[0] and self.pending_open and self.pending_open[0]==generation:
                pending_row=self.pending_open[1]
                if group<len(self.model.starts) and self.model.starts[group]<=pending_row<self.model.starts[group]+self.model.lengths[group]:self.pending_open=None
            if self.detail_worker is worker:self.detail_worker=None
            if append:
                if accepted[0] and self.restore_target>self.model.loaded and self.model.next_cursor:
                    self.fetch_group(len(self.model.starts));return
                self.restore_target=0
            self.load_button.setEnabled(bool(self.model.next_cursor) and self.detail_worker is None)
        worker.finished.connect(finished)

    def load_more(self):
        if self.model.next_cursor and self.detail_worker is None:self.fetch_group(len(self.model.starts))

    def show_reconciliation(self):
        summary=getattr(self,'current_summary',None)
        if not summary:return
        from .measurements import reconciliation,partition_checks
        full=summary.get('full_new_totals',summary.get('new_totals',{})) or {}
        roots=json.loads(summary['newer']['config'])['roots']
        self.owner.show_text({'Tóm tắt':getattr(self,'reconciliation_text',''),
            'Đối soát byte gốc':reconciliation(full,roots),'Chung + Riêng = Tổng':partition_checks(summary),
            'Giải thích':'± là kích thước file, ± Đĩa là cấp phát. Các ô Cũ/Mới và cây theo chế độ chọn. Không dùng hiệu Size và Windows để suy ra file nhỏ. Mốc cũ thiếu số đo vẫn để chưa rõ.'},'Đối soát dung lượng — không quét lại ổ')

    def track(self,worker):
        self.loading_workers.add(worker);self.cancel_button.setEnabled(True)
        def finished():
            self.loading_workers.discard(worker);self.cancel_button.setEnabled(bool(self.loading_workers))
        worker.finished.connect(finished)

    def cancel_loading(self):
        for worker in list(self.loading_workers):worker.stop.set()
        self.generation+=1;self.tree_generation+=1;self.tree_ids=None;self.model.pending.clear();self.model.paused=True;self.detail_worker=None;self.restore_target=0
        self.load_button.setEnabled(False);self.cancel_button.setEnabled(False)
        self.owner.status.setText("Đã yêu cầu dừng tải; giữ metadata và các dòng đã tải. Bấm So sánh để tải lại.")

    def sort_clicked(self,column):
        key=SORT_COLUMNS[column]
        self.direction=('asc' if self.direction=='desc' else 'desc') if self.sort==key else ('asc' if key in ('path','kind','change') else 'desc')
        self.sort=key
        self.table.horizontalHeader().setSortIndicator(column,Qt.SortOrder.AscendingOrder if self.direction=="asc" else Qt.SortOrder.DescendingOrder);self.reload(preserve_count=True)

    def table_menu(self,point):
        index=self.table.indexAt(point);row=self.model.row_at(index.row()) if index.isValid() else None
        if row:self.owner.path_menu(row["path"],self.table.viewport().mapToGlobal(point),extra=lambda:self.snapshot_details(row))

    def fit_detail_columns(self):
        # Measure at most the bounded in-memory page, never all saved paths.
        widths=[60,270,76,76,94,64,96,148,148];fm=self.table.fontMetrics()
        self.fitting_columns=True
        try:
            self.table.setColumnWidth(0,60)
            for column in (2,3,4,5,6,7,8):
                measured=max((fm.horizontalAdvance(str(self.model.data(self.model.index(i,column))))+20 for i in range(min(20,self.model.loaded))),default=0)
                widths[column]=max(fm.horizontalAdvance(HEADERS[column])+28,min(measured,220 if column==0 else 160 if column in (7,8) else 120),widths[column])
                self.table.setColumnWidth(column,widths[column])
            self.detail_columns_fitted=True
        finally:self.fitting_columns=False

    def detail_column_resized(self,*_):
        if not self.fitting_columns:self.detail_columns_fitted=True
        if not self.fitting_columns and _ and _[0]==1:self.path_user_sized=True

    def eventFilter(self,watched,event):
        if watched is self.table.viewport() and event.type()==QEvent.Type.Resize and not self.path_user_sized:
            self.fitting_columns=True
            try:
                fixed=sum(self.table.columnWidth(i) for i in (0,2,3,4,5,6))
                self.table.setColumnWidth(1,max(180,min(340,self.table.viewport().width()-fixed-5)))
            finally:self.fitting_columns=False
        return super().eventFilter(watched,event)

    def install_shortcuts(self):
        self.shortcuts=[]
        def bind(keys,widget,action):
            shortcut=QShortcut(QKeySequence(keys),widget)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action);self.shortcuts.append(shortcut)
        def focus_search():self.search.setFocus();self.search.selectAll()
        bind('Ctrl+F',self,focus_search)
        for widget in (self.table,self.tree):
            bind('Ctrl+Shift+C',widget,lambda:self.selected_action('copy'))
            bind('Alt+Return',widget,lambda:self.selected_action('properties'))
            bind('F2',widget,lambda:self.selected_action('rename'))
            bind('Delete',widget,lambda:self.selected_action('trash'))
        bind('Escape',self.table,self.escape_details)

    def escape_details(self):
        if self.detail_jobs:self.cancel_loading()
        elif self.legend.isVisible():self.legend_toggle.setChecked(False);self.legend.hide()

    def selected_action(self,action):
        from PySide6.QtWidgets import QApplication
        focus=QApplication.focusWidget();row=None
        if focus is not None and (focus is self.tree or self.tree.isAncestorOf(focus)):
            item=self.tree.currentItem();data=item.data(0,Qt.ItemDataRole.UserRole) if item else None
            row=data.get('row') if data and not data.get('more') else None
        else:
            index=self.table.currentIndex()
            if index.isValid():row=self.model.row_at(index.row())
        if not row:return
        if action=='copy':QApplication.clipboard().setText(row['path']);return
        if not Path(row['path']).exists():
            self.owner.status.setText('Mục chỉ có trong lịch sử hoặc không còn truy cập được; có thể sao chép đường dẫn / xem dữ liệu hai mốc.');return
        # Existing handlers retain rename/trash confirmations. No new delete path.
        self.owner.file_action(row['path'],action)

    def snapshot_details(self,row):
        dialog=QDialog(self.owner);dialog.setWindowTitle('Dữ liệu hai mốc — không đọc file hiện tại');dialog.resize(720,460)
        layout=QVBoxLayout(dialog);text=QPlainTextEdit();text.setReadOnly(True)
        from .snapshot_scope import REASONS
        issue_text=lambda side:' • '.join(REASONS.get(tag,tag) for tag in set((row.get(side+'_issue') or '').split('|')) if tag) or 'Không ghi nhận vấn đề'
        summary=getattr(self,'current_summary',{});older=summary.get('older');newer=summary.get('newer')
        facts={'Trạng thái':row_status(row),'Đường dẫn':row['path'],
               'Lần quét Cũ':scan_name(older) if older else 'Mốc ban đầu','Lần quét Mới':scan_name(newer) if newer else 'Theo cặp đang chọn',
               'Số đo Cũ':snapshot_cell(row,'old'),'Số đo Mới':snapshot_cell(row,'new'),
               'Cũ (byte gốc)':row.get('before'),'Mới (byte gốc)':row.get('after'),'Chênh lệch xác định (byte)':row.get('delta'),
               'Cấp phát cũ (byte)':row.get('allocated_before'),'Cấp phát mới (byte)':row.get('allocated_after'),
               'Vấn đề mốc cũ':issue_text('old'),'Vấn đề mốc mới':issue_text('new'),
               'Giải thích':row.get('reason_text',''),'Đường dẫn mốc kia':row.get('other_path'),
               'Chung Cũ/Mới (byte)': [row.get('common_before'),row.get('common_after')],
               'Riêng Cũ/Mới (byte)': [row.get('private_before'),row.get('private_after')]}
        text.setPlainText(json.dumps(facts,ensure_ascii=False,indent=2));layout.addWidget(text)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons);dialog.exec()

    def open_row(self,index):
        if not index.isValid():return
        row_index=index.row();self.pending_open=(self.generation,row_index)
        row=self.model.row_at(row_index)
        if not row:
            self.owner.status.setText("Đang tải lại dòng đã rời cache để mở mục…")
            return
        self.pending_open=None;self._dispatch_open(row)

    def _dispatch_open(self,row):
        if not row:return
        if row['kind']=='folder':
            if self.root!=row['path']:self.select_scope(row['path'])
            self.table.setFocus()
        else:
            self.owner.file_action(row['path'],'open')

    def open_tree_item(self,item,column):
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if data and not data.get('more') and self.root!=data['path']:
            self.select_scope(data['path'])

    def tree_menu(self,point):
        item=self.tree.itemAt(point);data=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if data and not data.get("more"):self.owner.path_menu(data["path"],self.tree.viewport().mapToGlobal(point),
            extra=(lambda:self.snapshot_details(data['row'])) if data.get('row') else None)
