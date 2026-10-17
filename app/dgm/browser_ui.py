"""Variable-size cursor pages, bounded row LRU and an independent latest tree."""
from bisect import bisect_right
from collections import OrderedDict
import json
from pathlib import Path
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton, QCheckBox, QSplitter, QTreeWidget, QTreeWidgetItem, QTableView, QStyledItemDelegate
from .store import Store
from .config import comparison_path_key
from .comparison import Comparisons
from .presentation import size, local_time, scan_label, scan_name, change_text, change_color, CHANGES, storage_delta
from .ui_helpers import StatusFilter, MetricCard, FittedTree

HEADERS = ["Đường dẫn", "Mức thay đổi", "Dung lượng cũ", "Dung lượng mới", "Loại", "Chênh lệch trên đĩa", "Ngày tạo", "Ngày sửa", "Trạng thái"]
SORT_COLUMNS = ["path", "delta", "old", "new", "kind", "allocated", "created", "modified", "change"]
PAGE_SIZES = (20,50,100,200,500,1000)


class PathDelegate(QStyledItemDelegate):
    def initStyleOption(self,option,index):
        super().initStyleOption(option,index)
        # Preserve the filename at the end as well as the drive/root prefix.
        option.textElideMode=Qt.TextElideMode.ElideMiddle


class DetailModel(QAbstractTableModel):
    need_page = Signal(int)
    def __init__(self):
        super().__init__();self.batch_size=20;self.cache_limit=400
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
        if role == Qt.ItemDataRole.ForegroundRole and index.column() in (1, 8):
            return QColor(change_color(row["delta"]))
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (1, 2, 3, 5):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            from datetime import datetime
            from zoneinfo import ZoneInfo
            stamp = lambda v: datetime.fromtimestamp(v, ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y %H:%M:%S") if v else "—"
            values = [row["path"], change_text(row["delta"]), size(row["before"]), size(row["after"]),
                      {"file":"File","folder":"Thư mục","link":"Liên kết"}.get(row["kind"],row["kind"]), change_text(row.get("allocated_delta")),
                      stamp(row["created"]), stamp(row["modified"]), CHANGES.get(row["change"],row["change"])]
            text = values[index.column()]
            if index.column()==5 and role==Qt.ItemDataRole.ToolTipRole:
                text += "\nCấp phát cũ → mới: "+size(row.get("allocated_before"))+" → "+size(row.get("allocated_after"))
            if role == Qt.ItemDataRole.ToolTipRole and row.get("other_path"):
                text += "\nĐường dẫn ở mốc kia: " + row["other_path"]
            return text

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
        self.pair_id = None; self.tree_pair = None; self.tree_ids = None; self.latest_tree_ids=None; self.root = None; self.profile = None; self.sort = "delta"; self.direction = "desc";self.pending_open=None
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(6)
        controls = QHBoxLayout(); self.older = QComboBox(); self.newer = QComboBox()
        controls.addWidget(QLabel("Mốc cũ")); controls.addWidget(self.older,1); controls.addWidget(QLabel("Mốc mới")); controls.addWidget(self.newer,1)
        owner.button("So sánh", self.compare, controls); owner.button("Xuất snapshot",owner.export,controls); owner.button("Xóa lượt quét",owner.delete_snapshot,controls)
        layout.addLayout(controls)
        self.common_scope=QCheckBox('Chỉ phần chung')
        self.common_scope.setToolTip('Cho phép khác bộ loại trừ: tính lại tổng trong vùng chung, không phải toàn ổ. Không khôi phục vùng chưa được quét.')
        controls.insertWidget(4,self.common_scope)
        self.older.setToolTip("Mốc cũ phải trước mốc mới và cùng phạm vi. Cây mặc định dùng đúng cặp này.")
        self.newer.setToolTip("Cũ/Mới áp dụng cho tổng quan, cây và bảng chi tiết khi cây chọn ‘Cặp đang so sánh’.")
        cards = QHBoxLayout(); self.metrics = []
        for tone in ('old','new','delta','summary'):
            label=MetricCard(tone); cards.addWidget(label,1); self.metrics.append(label)
        layout.addLayout(cards)
        filters=QHBoxLayout(); self.kind=QComboBox(); self.change=StatusFilter(CHANGES); self.search=QLineEdit(); self.search.setPlaceholderText("Lọc đường dẫn")
        for label,value in [("Mọi loại","all"),("Thư mục","folder"),("File","file")]: self.kind.addItem(label,value)
        self.recursive=QCheckBox("Gồm thư mục con"); self.recursive.setChecked(True)
        for field in (self.kind,self.change,self.search,self.recursive): filters.addWidget(field)
        self.units=QComboBox();self.units.addItem('GB (10⁹ B)',False);self.units.addItem('GiB (2³⁰ B)',True)
        self.units.setCurrentIndex(int(bool(owner.preferences.values.get('units/binary',False))))
        self.units.setToolTip('Chỉ đổi cách hiển thị; dữ liệu luôn lưu byte. Windows thường ghi GB nhưng tính theo GiB.')
        filters.addWidget(self.units);self.units.currentIndexChanged.connect(self.units_changed)
        owner.button("Lọc / về đầu",self.reload,filters); owner.button("Xem toàn bộ",lambda:self.select_scope(None),filters)
        layout.addLayout(filters)
        self.search.returnPressed.connect(self.reload)
        compare_help=owner.help_box(layout,"compare","Chọn mốc cũ/mới rồi So sánh. Tổng quan, cây và chi tiết mặc định cùng một cặp mốc. Chọn thư mục để giới hạn chi tiết; sắp xếp áp dụng toàn bộ kết quả thỏa bộ lọc, không chỉ các dòng đã tải. Dung lượng logic khác dung lượng cấp phát trên đĩa.")
        self.compare_help=compare_help;self.compare_help.show_transient(False)
        filters.addWidget(compare_help.toggle)
        self.info=QLabel("Chưa có kết quả"); self.info.setWordWrap(True)
        self.info.setStyleSheet('color:#526579;font-size:11px;');layout.addWidget(self.info)
        self.splitter=QSplitter();self.splitter.setMinimumHeight(225)
        left=QWidget(); ll=QVBoxLayout(left); ll.setContentsMargins(0,0,0,0)
        tree_header=QHBoxLayout();self.tree_label=QLabel("Cây thư mục • cặp so sánh đang chọn");self.tree_label.setObjectName('sectionTitle');self.tree_label.setWordWrap(True);tree_header.addWidget(self.tree_label,1)
        self.tree_mode=QComboBox();self.tree_mode.addItem("Cặp đang so sánh","selected");self.tree_mode.addItem("Theo dõi lượt mới nhất","latest")
        self.tree_mode.setCurrentIndex(max(0,self.tree_mode.findData(owner.preferences.values.get('tree/mode','selected'))))
        self.tree_mode.setToolTip("Chọn cặp so sánh để cây khớp bảng chi tiết, hoặc theo dõi hai lượt hợp lệ mới nhất.");tree_header.addWidget(self.tree_mode);ll.addLayout(tree_header)
        self.tree=FittedTree(); self.tree.setHeaderLabels(["Thư mục","Mốc mới","Chênh lệch"]); self.tree.setAlternatingRowColors(True)
        self.tree.headerItem().setToolTip(1,'Dung lượng logic tại mốc mới của cặp đang dùng cho cây')
        self.tree.itemExpanded.connect(self.expand); self.tree.itemClicked.connect(self.clicked);self.tree.itemDoubleClicked.connect(self.open_tree_item)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tree.customContextMenuRequested.connect(self.tree_menu)
        ll.addWidget(self.tree); self.splitter.addWidget(left)
        right=QWidget(); rl=QVBoxLayout(right); rl.setContentsMargins(0,0,0,0)
        self.scope_label=QLabel("Chi tiết: toàn bộ phạm vi so sánh"); self.scope_label.setObjectName('sectionTitle'); self.scope_label.setWordWrap(True); rl.addWidget(self.scope_label)
        self.model=DetailModel(); self.model.need_page.connect(self.fetch_group)
        self.table=QTableView(); self.table.setModel(self.model); self.table.setAlternatingRowColors(True); self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(180)
        self.table.setWordWrap(False); self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setItemDelegateForColumn(0,PathDelegate(self.table))
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.table.customContextMenuRequested.connect(self.table_menu)
        self.table.doubleClicked.connect(self.open_row)
        self.table.horizontalHeader().setSortIndicatorShown(True); self.table.horizontalHeader().setSortIndicator(1,Qt.SortOrder.DescendingOrder)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_clicked)
        self.tree_mode.currentIndexChanged.connect(self.tree_mode_changed)
        rl.addWidget(self.table,1); self.splitter.addWidget(right)
        layout.addWidget(self.splitter,1)
        widths=self.tree.default_widths();left.setMinimumWidth(330);right.setMinimumWidth(430)
        self.splitter.setSizes(owner.preferences.values.get("splitter/scan-v3",[sum(widths)+8,850]))
        owner.columns(self.tree,"tree-v3",widths); owner.columns(self.table,"details-v3",[300,145,120,120,85,155,170,170,185])
        footer=QHBoxLayout(); self.page_label=QLabel("0 mục");self.page_label.setWordWrap(True);footer.addWidget(self.page_label,1)
        footer.addWidget(QLabel('Mỗi lượt:'));self.page_size=QComboBox()
        for count in PAGE_SIZES:self.page_size.addItem(str(count),count)
        selected=owner.preferences.values.get('details/page-size',20)
        self.page_size.setCurrentIndex(max(0,self.page_size.findData(selected)))
        self.model.set_batch_size(self.page_size.currentData());footer.addWidget(self.page_size)
        self.page_size.setToolTip('Số mục lấy trong lần tải tiếp theo, theo thứ tự của toàn bộ phạm vi đang xem. Không xóa các dòng đã mở.')
        self.load_button=owner.button("Tải thêm 20 mục",self.load_more,footer); self.load_button.setEnabled(False)
        self.page_size.currentIndexChanged.connect(self.batch_changed);self.update_page_text()
        self.cancel_button=owner.button("Dừng tải",self.cancel_loading,footer);self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("Dừng chuẩn bị/tải chi tiết và cây; không xóa snapshot, không thay đổi file trên ổ.")
        footer.addStretch(); owner.button("Hỏi AI",owner.ai_snapshot,footer); layout.addLayout(footer)

    def refresh(self, profile):
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
                self.newer.addItem(scan_label(row),row['id']);self.newer.setItemData(self.newer.count()-1,scan_label(row)+'\n'+row['config'],Qt.ItemDataRole.ToolTipRole)
            for row in all_rows:
                if row['status'] in ('complete','partial'):
                    self.older.addItem(scan_label(row),row['id']);self.older.setItemData(self.older.count()-1,scan_label(row)+'\n'+row['config'],Qt.ItemDataRole.ToolTipRole)
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

        if tree_ids and self.tree_ids!=tree_ids:self.refresh_tree(*tree_ids)
        elif not tree_ids:
            self.tree_generation+=1;self.tree.clear();self.tree_ids=None;self.tree_pair=None
            self.tree_label.setText("Chưa có lượt hợp lệ để hiển thị.")

    def units_changed(self):
        from .presentation import set_units
        binary=self.units.currentData();set_units(binary);self.owner.preferences.set('units/binary',binary)
        if self.pair_id:self.reload(preserve_count=True)
        self.tree_ids=None;self.owner.refresh_history()
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

    def refresh_tree(self, previous, newer_id):
        self.tree_generation+=1; generation=self.tree_generation; self.tree_ids=(previous,newer_id); self.tree.clear(); self.tree_pair=None
        store=Store(readonly=True)
        try:
            prior=store.scan(previous) if previous else None
            latest=store.scan(newer_id)
        finally: store.close()
        title="Theo dõi lượt mới nhất (độc lập)" if self.tree_mode.currentData()=='latest' else "Cây theo cặp so sánh"
        self.tree_label.setText(title)
        self.tree_label.setToolTip((scan_name(prior)+" → " if prior else "Mốc ban đầu → ")+scan_name(latest)+"\n"+local_time(latest["started"]))
        roots=json.loads(latest["config"])["roots"]
        common=self.common_scope.isChecked() and self.tree_mode.currentData()=='selected'
        def prepare(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:
                pair=cache.prepare(previous,newer_id,**({'common':True} if common else {}))
                rows=[]
                for root in roots:
                    records=cache.db.execute("SELECT c.*,p.path FROM source.paths p INDEXED BY paths_key JOIN diff_rows c ON c.path_id=p.id WHERE c.pair_id=? AND p.path_key=?",(pair,comparison_path_key(root))).fetchmany(2)
                    if len(records)==1: rows.append(dict(records[0]))
                return pair,rows
            finally: cache.close()
        def done(value):
            if generation!=self.tree_generation:return
            self.tree_pair,rows=value
            for row in rows:self.add_node(row)
        self.track(self.owner.job(prepare,done))

    def add_node(self,row,parent=None):
        item=QTreeWidgetItem([row["path"] if parent is None else Path(row["path"]).name,size(row["after"]),change_text(row["delta"])])
        item.setToolTip(0,row['path'])
        item.setData(0,Qt.ItemDataRole.UserRole,{"path":row["path"],"pair":self.tree_pair,"cursor":None})
        basis='theo hai lượt mới nhất' if self.tree_mode.currentData()=='latest' else 'theo cặp Cũ/Mới đang chọn'
        item.setForeground(2,QColor(change_color(row["delta"]))); item.setToolTip(2,CHANGES.get(row["change"],row["change"])+" • "+basis)
        (parent.addChild(item) if parent else self.tree.addTopLevelItem(item)); item.addChild(QTreeWidgetItem(["Mở để tải thư mục con…"]))

    def expand(self,item):
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if not data or data.get("more") or item.childCount()!=1 or item.child(0).data(0,Qt.ItemDataRole.UserRole):return
        self.children(item,None,replace=True)

    def children(self,parent,cursor,replace=False):
        data=parent.data(0,Qt.ItemDataRole.UserRole); generation=self.tree_generation
        def fetch(worker):
            cache=Comparisons(worker.stop)
            try:
                pair=data['pair']
                return cache.page(pair,root=data["path"],recursive=False,folders_only=True,limit=20,cursor=cursor,sort="new")
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

    def options(self):
        return dict(root=self.root,recursive=self.recursive.isChecked(),minimum=self.owner.threshold.value()*1_000_000,
                    kind=self.kind.currentData(),changes=self.change.currentData(),search=self.search.text(),sort=self.sort,direction=self.direction)

    def batch_changed(self,*_):
        count=self.page_size.currentData();self.model.set_batch_size(count)
        self.owner.preferences.set('details/page-size',count);self.update_page_text()

    def update_page_text(self):
        count=self.page_size.currentData();self.load_button.setText(f'Tải thêm {count} mục')
        sort=self.query_options.get('sort',self.sort);direction=self.query_options.get('direction',self.direction)
        arrow='↑' if direction=='asc' else '↓'
        label=HEADERS[SORT_COLUMNS.index(sort)]
        if sort=='delta' and getattr(self,'ids',(1,))[0] is None:label='Dung lượng mới (mốc đầu)'
        self.page_label.setText(f'Đã mở {self.model.loaded:,}/{self.model.total:,} mục • {label} {arrow} • cache RAM ≤ {self.model.cache_limit:,} dòng')
        root=self.query_options.get('root',self.root)
        scope=(str(root)+(' • gồm nhánh con' if self.query_options.get('recursive',True) else ' • chỉ mục trực tiếp')) if root else 'toàn bộ phạm vi so sánh'
        self.scope_label.setText('Chi tiết: '+scope+' • Sắp xếp toàn bộ kết quả thỏa bộ lọc, không chỉ các dòng đã mở')

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
        self.result_help_pending=True
        if self.tree_mode.currentData()=='selected':self.refresh_tree(*self.ids)
        old_totals=json.loads(a["summary"] or "{}") if a else None;new_totals=json.loads(b["summary"] or "{}")
        self.metrics[0].setText("DUNG LƯỢNG CŨ\n"+size(old_totals.get("logical") if old_totals else None))
        self.metrics[1].setText("DUNG LƯỢNG MỚI\n"+size(new_totals.get("logical")))
        delta=new_totals["logical"]-old_totals["logical"] if old_totals and old_totals.get('logical') is not None and new_totals.get('logical') is not None else None
        uncertain=b['status']=='partial' or (a and a['status']=='partial')
        self.metrics[2].setText("CHÊNH LỆCH DUNG LƯỢNG QUÉT\n"+change_text(delta)+("\nQuét chưa đầy đủ phạm vi" if uncertain else ""))
        self.metrics[2].value.setStyleSheet('color:'+change_color(delta)+';');self.metrics[3].setText("TOÀN PHẠM VI\nĐang tính…")
        warnings=" • Có vùng chưa xác minh: thêm/mất có thể hiện ‘chưa rõ’." if new_totals.get("coverage_unverified") or (old_totals or {}).get("coverage_unverified") else ""
        volumes=new_totals.get("volumes",{})
        disk=" • Ổ tại mốc mới: "+"; ".join(f"{v} đã dùng {size(x['used'])}, trống {size(x['free'])}" for v,x in volumes.items()) if volumes else " • Dung lượng trống tại mốc này chưa được lưu."
        self.info.setText(("Cũ: "+scan_name(a)+" → Mới: " if a else "Mốc ban đầu → Mới: ")+scan_name(b)+' • '+b['engine']+(' • Có giới hạn dữ liệu' if uncertain else ''))
        self.info.setToolTip(("Cũ: "+scan_label(a)+" → " if a else "Mốc ban đầu • ")+"Mới: "+scan_label(b)+disk+warnings)
        def prepare(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:return cache.prepare(older,newer,**({'common':True} if common else {}))
            finally:cache.close()
        def done(pair):
            if generation!=self.generation:return
            self.pair_id=pair;self.detail_worker=None;self.fetch_group(0)
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
            old=summary.get('old_totals');new=summary.get('new_totals',{})
            self.metrics[0].setText('DUNG LƯỢNG CŨ\n'+size(old.get('logical') if old else None)+'\nAllocated: '+size((old or {}).get('allocated')))
            self.metrics[1].setText('DUNG LƯỢNG MỚI\n'+size(new.get('logical'))+'\nAllocated: '+size(new.get('allocated')))
            a,b=(old or {}).get('logical'),new.get('logical')
            delta=b-a if a is not None and b is not None else None
            common_note='\nCHỈ PHẦN CHUNG — không phải toàn ổ' if summary.get('common_scope') else ''
            self.metrics[2].setText('CHÊNH LỆCH DUNG LƯỢNG QUÉT\n'+change_text(delta)+common_note)
            self.metrics[2].value.setStyleSheet('color:'+change_color(delta)+';')
            self.metrics[1].setToolTip('Size: '+size(new.get('logical'))+'\nAllocated (hardlink một lần): '+size(new.get('allocated'))+
                '\nSize theo đường dẫn có thể vượt sức chứa ổ. GB=10⁹ B; GiB=2³⁰ B.\n'+
                '\n'.join(new.get('normalization',{}).get('warnings',[])))
            if summary.get('common_scope'):self.info.setText('CHỈ PHẦN CHUNG — không phải toàn ổ; '+str(summary['common_scope']))
            volumes=new.get('volumes',{})
            disk='; '.join(f"{volume} sức chứa {size(values.get('total'))}, Windows đã dùng {size(values.get('used'))}, trống {size(values.get('free'))}" for volume,values in volumes.items())
            if disk:self.info.setText(self.info.text().split('\n')[0]+'\n'+disk+' • Thẻ Cũ/Mới là Size theo đường dẫn, không phải Windows đã dùng.')
            accepted[0]=True;self.update_page_text()
            if self.result_help_pending:
                self.result_help_pending=False
                compatibility=summary.get('compatibility',{})
                old=summary.get('older');new=summary.get('newer')
                if new:
                    pair_text=(scan_name(old)+' → ' if old else 'Mốc ban đầu → ')+scan_name(new)
                    engines=' → '.join(x for x in compatibility.get('engines',[]) if x)
                    self.compare_help.body.setText(pair_text+' • '+engines+'\n'+
                        'Tổng quan và cây mặc định cùng cặp mốc; chọn thư mục để lọc chi tiết. Sắp xếp trên toàn bộ kết quả thỏa bộ lọc. '+
                        '↑ tăng, ↓ giảm; logic khác byte cấp phát, không cộng trùng tổng các thư mục cha/con.'+
                        (' So sánh khác công cụ dùng độ chính xác thời gian chung một giây.' if compatibility.get('cross_engine') else '')+
                        (' Mốc cũ chưa ghi chuẩn đo mới; giữ dữ liệu gốc, chỉ chuẩn hóa phần có thể đối chiếu.' if compatibility.get('legacy_metadata') else '')+
                        (' Vùng chưa đủ bằng chứng có nhãn Chưa xác định, không kết luận file đã xóa.' if compatibility.get('coverage_unverified') else ''))
                self.compare_help.show_transient()
            counts=summary["counts"];changed=sum(r["count"] for r in counts if r["change"] not in ("same","first_scan","moved_from"))
            files=sum(r["count"] for r in counts if r["kind"]=="file");folders=sum(r["count"] for r in counts if r["kind"]=="folder")
            self.metrics[3].setText(f"TOÀN PHẠM VI\n{files:,} file • {folders:,} thư mục\n{changed:,} mục đổi/chưa rõ (không chỉ dòng đã mở)")
            self.owner.displayed_scan_id=self.ids[1]
            if self.owner.scan_worker is None:
                restoring=self.restore_target>self.model.loaded and bool(self.model.next_cursor)
                count_change=lambda key:sum(r['count'] for r in counts if r['change']==key)
                brief=f"{count_change('grown'):,} tăng • {count_change('shrunk'):,} giảm • {count_change('new'):,} mới • {count_change('missing'):,} mất • {count_change('unknown'):,} chưa rõ"
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
        if row:self.owner.path_menu(row["path"],self.table.viewport().mapToGlobal(point))

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
        if data and not data.get("more"):self.owner.path_menu(data["path"],self.tree.viewport().mapToGlobal(point))
