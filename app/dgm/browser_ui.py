"""Virtual comparison view: 20-row fetches, bounded 400-row LRU, independent tree."""
from collections import OrderedDict
import json
from pathlib import Path
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton, QCheckBox, QSplitter, QTreeWidget, QTreeWidgetItem, QTableView
from .store import Store
from .comparison import Comparisons
from .presentation import size, local_time, scan_label, scan_name, change_text, change_color, CHANGES

HEADERS = ["Đường dẫn", "Loại", "Dung lượng cũ", "Dung lượng mới", "Mức thay đổi", "Trên đĩa (mới)", "Ngày tạo", "Ngày sửa", "Trạng thái"]
SORT_COLUMNS = ["path", "kind", "old", "new", "delta", "allocated", "created", "modified", "change"]


class DetailModel(QAbstractTableModel):
    need_page = Signal(int)
    def __init__(self):
        super().__init__(); self.chunks = OrderedDict(); self.anchors = {0: None}; self.pending = set(); self.loaded = 0; self.next_cursor = None; self.total = 0

    def reset(self):
        self.beginResetModel(); self.chunks.clear(); self.anchors = {0: None}; self.pending.clear(); self.loaded = 0; self.next_cursor = None; self.total = 0; self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.loaded

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section] if orientation == Qt.Orientation.Horizontal else str(section + 1)

    def row_at(self, row):
        chunk, offset = divmod(row, 20)
        if chunk not in self.chunks:
            if chunk in self.anchors and chunk not in self.pending:
                self.pending.add(chunk); self.need_page.emit(chunk)
            return None
        self.chunks.move_to_end(chunk)
        return self.chunks[chunk][offset] if offset < len(self.chunks[chunk]) else None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.row_at(index.row())
        if row is None:
            return "Đang tải…" if role == Qt.ItemDataRole.DisplayRole and index.column() == 0 else None
        if role == Qt.ItemDataRole.ForegroundRole and index.column() in (4, 8):
            return QColor(change_color(row["delta"]))
        if role == Qt.ItemDataRole.TextAlignmentRole and 2 <= index.column() <= 5:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            from datetime import datetime
            from zoneinfo import ZoneInfo
            stamp = lambda v: datetime.fromtimestamp(v, ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y %H:%M:%S") if v else "—"
            values = [row["path"], {"file":"File","folder":"Thư mục","link":"Liên kết"}.get(row["kind"],row["kind"]),
                      size(row["before"]), size(row["after"]), change_text(row["delta"]), size(row["allocated"]),
                      stamp(row["created"]), stamp(row["modified"]), CHANGES.get(row["change"],row["change"])]
            text = values[index.column()]
            if role == Qt.ItemDataRole.ToolTipRole and row.get("other_path"):
                text += "\nĐường dẫn ở mốc kia: " + row["other_path"]
            return text

    def accept(self, group, page):
        rows = page["rows"]; old = self.loaded; end = group * 20 + len(rows)
        if end > old:
            self.beginInsertRows(QModelIndex(), old, end - 1)
            self.loaded = end
        self.chunks[group] = rows; self.chunks.move_to_end(group); self.pending.discard(group); self.total = page["total"]
        if end >= old:
            self.next_cursor = page["cursor"]
            if page["cursor"]:
                self.anchors[group + 1] = page["cursor"]
        while len(self.chunks) > 20:
            self.chunks.popitem(last=False)
        if end > old:
            self.endInsertRows()
        elif rows:
            self.dataChanged.emit(self.index(group * 20, 0), self.index(end - 1, 8))


class DiskBrowser(QWidget):
    def __init__(self, owner):
        super().__init__(); self.owner = owner; self.generation = 0; self.tree_generation = 0; self.detail_worker = None; self.loading_workers=set()
        self.pair_id = None; self.tree_pair = None; self.tree_ids = None; self.root = None; self.profile = None; self.sort = "delta"; self.direction = "desc"
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(6)
        controls = QHBoxLayout(); self.older = QComboBox(); self.newer = QComboBox()
        controls.addWidget(QLabel("Mốc cũ")); controls.addWidget(self.older,1); controls.addWidget(QLabel("Mốc mới")); controls.addWidget(self.newer,1)
        owner.button("So sánh", self.compare, controls); owner.button("Xuất snapshot",owner.export,controls); owner.button("Xóa metadata",owner.delete_snapshot,controls)
        layout.addLayout(controls)
        self.older.setToolTip("Mốc cũ phải trước mốc mới và cùng phạm vi. Không điều khiển mốc theo dõi của cây bên trái.")
        self.newer.setToolTip("Cũ/Mới áp dụng cho tổng quan và bảng chi tiết. Cây luôn theo hai lượt hợp lệ gần nhất.")
        cards = QHBoxLayout(); self.metrics = []
        for _ in range(4):
            label=QLabel("—"); label.setObjectName("metric"); label.setWordWrap(True); cards.addWidget(label,1); self.metrics.append(label)
        layout.addLayout(cards)
        filters=QHBoxLayout(); self.kind=QComboBox(); self.change=QComboBox(); self.search=QLineEdit(); self.search.setPlaceholderText("Lọc đường dẫn")
        for label,value in [("Mọi loại","all"),("Thư mục","folder"),("File","file")]: self.kind.addItem(label,value)
        self.change.addItem("Mọi trạng thái","all")
        for key,label in CHANGES.items(): self.change.addItem(label,key)
        self.recursive=QCheckBox("Gồm thư mục con"); self.recursive.setChecked(True)
        for field in (self.kind,self.change,self.search,self.recursive): filters.addWidget(field)
        owner.button("Lọc / về 20 mục đầu",self.reload,filters); owner.button("Xem toàn bộ",lambda:self.select_scope(None),filters)
        layout.addLayout(filters)
        self.search.returnPressed.connect(self.reload)
        compare_help=owner.help_box(layout,"compare","Tổng quan áp dụng cho hai mốc Cũ/Mới. Cây bên trái chỉ theo dõi hai lượt hợp lệ gần nhất cùng phạm vi, độc lập với mốc so sánh. Bấm cây để lọc chi tiết bên phải, không đổi tổng quan hay mốc. Bảng tải 20 mục/lượt và chỉ giữ tối đa 400 dòng trong RAM; cuộn lại sẽ đọc cache. Bấm tiêu đề để sắp xếp toàn bộ kết quả. Màu xanh ↑ là tăng dung lượng, không có nghĩa đó là thay đổi tốt.")
        filters.addWidget(compare_help.toggle)
        self.info=QLabel("Chưa có kết quả"); self.info.setWordWrap(True); layout.addWidget(self.info)
        self.splitter=QSplitter(); left=QWidget(); ll=QVBoxLayout(left); ll.setContentsMargins(0,0,0,0)
        self.tree_label=QLabel("Theo dõi gần nhất"); self.tree_label.setWordWrap(True); ll.addWidget(self.tree_label)
        self.tree=QTreeWidget(); self.tree.setHeaderLabels(["Thư mục","Mới nhất","Thay đổi"]); self.tree.setAlternatingRowColors(True)
        self.tree.itemExpanded.connect(self.expand); self.tree.itemClicked.connect(self.clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tree.customContextMenuRequested.connect(self.tree_menu)
        ll.addWidget(self.tree); self.splitter.addWidget(left)
        right=QWidget(); rl=QVBoxLayout(right); rl.setContentsMargins(0,0,0,0)
        self.scope_label=QLabel("Chi tiết: toàn bộ phạm vi so sánh"); self.scope_label.setWordWrap(True); rl.addWidget(self.scope_label)
        self.model=DetailModel(); self.model.need_page.connect(self.fetch_group)
        self.table=QTableView(); self.table.setModel(self.model); self.table.setAlternatingRowColors(True); self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.table.customContextMenuRequested.connect(self.table_menu)
        self.table.horizontalHeader().setSortIndicatorShown(True); self.table.horizontalHeader().setSortIndicator(4,Qt.SortOrder.DescendingOrder)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_clicked)
        rl.addWidget(self.table,1); self.splitter.addWidget(right)
        self.splitter.setSizes(owner.preferences.values.get("splitter/scan-v2",[340,1080])); layout.addWidget(self.splitter,1)
        owner.columns(self.tree,"tree-v2",[185,110,125]); owner.columns(self.table,"details-v2",[300,80,110,110,145,135,170,170,185])
        footer=QHBoxLayout(); self.page_label=QLabel("0 mục"); footer.addWidget(self.page_label)
        self.load_button=owner.button("Tải thêm 20 mục",self.load_more,footer); self.load_button.setEnabled(False)
        self.cancel_button=owner.button("Dừng tải",self.cancel_loading,footer);self.cancel_button.setEnabled(False)
        self.cancel_button.setToolTip("Dừng chuẩn bị/tải chi tiết và cây; không xóa snapshot, không thay đổi file trên ổ.")
        footer.addStretch(); owner.button("Hỏi AI",owner.ai_snapshot,footer); layout.addLayout(footer)

    def refresh(self, profile):
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
            compatible_scopes={r['scope'] for r in valid}
            for row in all_rows:
                if row['status'] in ('complete','partial') and row['scope'] in compatible_scopes:
                    self.older.addItem(scan_label(row),row['id']);self.older.setItemData(self.older.count()-1,scan_label(row)+'\n'+row['config'],Qt.ItemDataRole.ToolTipRole)
            if prior and not changed: self.newer.setCurrentIndex(max(0,self.newer.findData(prior)))
            if old is not None and not changed: self.older.setCurrentIndex(max(0,self.older.findData(old)))
            if changed: self.root=None
            if valid:
                latest=valid[0]; previous=store.previous(latest["id"])
                if self.tree_ids!=(previous,latest["id"]): self.refresh_tree(previous,latest)
            else:
                self.tree_generation+=1; self.tree.clear(); self.tree_ids=None; self.tree_label.setText("Chưa có lượt hợp lệ để theo dõi.")
        finally: store.close()

    def refresh_tree(self, previous, latest):
        self.tree_generation+=1; generation=self.tree_generation; self.tree_ids=(previous,latest["id"]); self.tree.clear(); self.tree_pair=None
        store=Store(readonly=True)
        try: prior=store.scan(previous) if previous else None
        finally: store.close()
        self.tree_label.setText("Theo dõi gần nhất (độc lập)\n"+(scan_name(prior)+" → " if prior else "Mốc ban đầu → ")+scan_name(latest)+"\n"+local_time(latest["started"]))
        roots=json.loads(latest["config"])["roots"]
        def prepare(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:
                pair=cache.prepare(previous,latest["id"])
                rows=[]
                for root in roots:
                    record=cache.db.execute("SELECT c.*,p.path FROM diff_rows c JOIN source.paths p ON p.id=c.path_id WHERE c.pair_id=? AND p.path=?",(pair,str(Path(root)))).fetchone()
                    if record: rows.append(dict(record))
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
        item.setForeground(2,QColor(change_color(row["delta"]))); item.setToolTip(2,CHANGES.get(row["change"],row["change"])+" • theo hai lượt gần nhất, không theo mốc Cũ/Mới bên phải")
        (parent.addChild(item) if parent else self.tree.addTopLevelItem(item)); item.addChild(QTreeWidgetItem(["Mở để tải thư mục con…"]))

    def expand(self,item):
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if not data or data.get("more") or item.childCount()!=1 or item.child(0).data(0,Qt.ItemDataRole.UserRole):return
        self.children(item,None,replace=True)

    def children(self,parent,cursor,replace=False):
        data=parent.data(0,Qt.ItemDataRole.UserRole); generation=self.tree_generation
        def fetch(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:
                pair=cache.prepare(*self.tree_ids)
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
        self.root=path;self.scope_label.setText("Chi tiết: "+(path or "toàn bộ phạm vi so sánh"));self.reload()

    def compare(self):
        if self.tree_ids is None and self.profile:self.refresh(self.profile)
        newer=self.newer.currentData()
        self.generation+=1;generation=self.generation
        if self.detail_worker:self.detail_worker.stop.set()
        self.model.reset();self.pair_id=None;self.load_button.setEnabled(False)
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
        old_totals=json.loads(a["summary"] or "{}") if a else None;new_totals=json.loads(b["summary"] or "{}")
        self.metrics[0].setText("DUNG LƯỢNG CŨ\n"+size(old_totals.get("logical") if old_totals else None))
        self.metrics[1].setText("DUNG LƯỢNG MỚI\n"+size(new_totals.get("logical")))
        delta=new_totals["logical"]-old_totals["logical"] if old_totals and "logical" in old_totals and "logical" in new_totals else None
        uncertain=b['status']=='partial' or (a and a['status']=='partial')
        self.metrics[2].setText(("CHÊNH SỐ LIỆU QUÉT\n" if uncertain else "THAY ĐỔI TOÀN PHẠM VI\n")+change_text(delta));self.metrics[3].setText("Đang chuẩn bị số mục thay đổi…")
        warnings=" • Có vùng chưa xác minh: thêm/mất có thể hiện ‘chưa rõ’." if new_totals.get("coverage_unverified") or (old_totals or {}).get("coverage_unverified") else ""
        volumes=new_totals.get("volumes",{})
        disk=" • Ổ tại mốc mới: "+"; ".join(f"{v} đã dùng {size(x['used'])}, trống {size(x['free'])}" for v,x in volumes.items()) if volumes else " • Dung lượng trống tại mốc này chưa được lưu."
        self.info.setText(("Cũ: "+scan_label(a)+" → " if a else "Mốc ban đầu • ")+"Mới: "+scan_label(b)+"\nTổng file quét ≠ dung lượng ổ đã dùng; thư mục cha gồm con, không cộng trùng."+disk+warnings)
        self.ids=(older,newer)
        def prepare(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:return cache.prepare(older,newer)
            finally:cache.close()
        def done(pair):
            if generation!=self.generation:return
            self.pair_id=pair;self.detail_worker=None;self.fetch_group(0)
        self.detail_worker=self.owner.job(prepare,done)
        self.track(self.detail_worker)
        current_worker=self.detail_worker
        current_worker.finished.connect(lambda:setattr(self,'detail_worker',None) if self.detail_worker is current_worker else None)

    def reload(self):
        if self.pair_id:
            self.generation+=1
            if self.detail_worker:self.detail_worker.stop.set()
            self.model.reset();self.fetch_group(0)
        else:self.compare()

    def fetch_group(self,group):
        if not self.pair_id:return
        generation=self.generation;pair=self.pair_id;cursor=self.model.anchors.get(group)
        options=dict(root=self.root,recursive=self.recursive.isChecked(),minimum=self.owner.threshold.value()*1_000_000,kind=self.kind.currentData(),changes=self.change.currentData(),search=self.search.text(),sort=self.sort,direction=self.direction,limit=20,cursor=cursor)
        self.load_button.setEnabled(False)
        def fetch(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:return cache.page(pair,**options),cache.summary(pair)
            finally:cache.close()
        def done(result):
            if generation!=self.generation:return
            page,summary=result;self.model.accept(group,page);self.owner.rows=page["rows"]
            self.page_label.setText(f"Đã tải {self.model.loaded:,}/{page['total']:,} mục • 20/lượt • cache tối đa 400 dòng")
            self.load_button.setEnabled(bool(self.model.next_cursor))
            counts=summary["counts"];changed=sum(r["count"] for r in counts if r["change"] not in ("same","first_scan","moved_from"))
            files=sum(r["count"] for r in counts if r["kind"]=="file");folders=sum(r["count"] for r in counts if r["kind"]=="folder")
            self.metrics[3].setText(f"TOÀN PHẠM VI\n{files:,} file • {folders:,} thư mục\n{changed:,} mục đổi/chưa rõ (không chỉ 20 dòng)")
            self.owner.displayed_scan_id=self.ids[1]
            if self.owner.scan_worker is None:
                self.owner.status.setText(f"Đã sẵn sàng • hiển thị {self.model.loaded:,}/{page['total']:,} mục chi tiết; tổng quan tính toàn phạm vi.")
        worker=self.owner.job(fetch,done);self.detail_worker=worker
        self.track(worker)
        worker.finished.connect(lambda:self.model.pending.discard(group) if generation==self.generation else None)
        worker.finished.connect(lambda:setattr(self,"detail_worker",None) if self.detail_worker is worker else None)
        worker.finished.connect(lambda:self.load_button.setEnabled(bool(self.model.next_cursor)) if generation==self.generation else None)

    def load_more(self):
        if self.model.next_cursor:self.fetch_group(self.model.loaded//20)

    def track(self,worker):
        self.loading_workers.add(worker);self.cancel_button.setEnabled(True)
        def finished():
            self.loading_workers.discard(worker);self.cancel_button.setEnabled(bool(self.loading_workers))
        worker.finished.connect(finished)

    def cancel_loading(self):
        for worker in list(self.loading_workers):worker.stop.set()
        self.generation+=1;self.tree_generation+=1;self.tree_ids=None;self.model.pending.clear()
        self.load_button.setEnabled(False);self.cancel_button.setEnabled(False)
        self.owner.status.setText("Đã yêu cầu dừng tải; giữ metadata và các dòng đã tải. Bấm So sánh để tải lại.")

    def sort_clicked(self,column):
        key=SORT_COLUMNS[column];self.direction="asc" if self.sort==key and self.direction=="desc" else "desc";self.sort=key
        self.table.horizontalHeader().setSortIndicator(column,Qt.SortOrder.AscendingOrder if self.direction=="asc" else Qt.SortOrder.DescendingOrder);self.reload()

    def table_menu(self,point):
        index=self.table.indexAt(point);row=self.model.row_at(index.row()) if index.isValid() else None
        if row:self.owner.path_menu(row["path"],self.table.viewport().mapToGlobal(point))

    def tree_menu(self,point):
        item=self.tree.itemAt(point);data=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if data and not data.get("more"):self.owner.path_menu(data["path"],self.tree.viewport().mapToGlobal(point))
