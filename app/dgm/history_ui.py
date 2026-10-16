import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QTabWidget,QTableWidget,QTableWidgetItem,QLabel,QMessageBox,QInputDialog
from .store import Store
from .comparison import Comparisons
from .presentation import scan_label,scan_name,local_time,size,change_text,STATUS,storage_delta,storage_tooltip


class HistoryPanel(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.scan_rows=[];self.pair_rows=[]
        layout=QVBoxLayout(self);actions=QHBoxLayout()
        owner.button("Làm mới lịch sử",self.refresh,actions);owner.button("Xem lượt / so sánh",self.open_selected,actions)
        owner.button("Phục hồi metadata đã nhập",self.recover_selected,actions)
        owner.button("Xóa tóm tắt so sánh",self.remove_pair,actions)
        owner.button("Xóa cache so sánh",self.clear_cache,actions)
        owner.button("Xóa toàn bộ lịch sử quét",self.clear_history,actions);layout.addLayout(actions)
        scan_actions=QHBoxLayout()
        owner.button("Chọn tất cả lượt",self.select_all_scans,scan_actions);owner.button("Bỏ chọn",self.clear_scan_selection,scan_actions)
        owner.button("Xóa lượt quét đã chọn",self.delete_selected_scans,scan_actions)
        scan_actions.addWidget(QLabel("Có thể giữ Ctrl/Shift để chọn nhiều lượt."));scan_actions.addStretch();layout.addLayout(scan_actions)
        self.tabs=QTabWidget();layout.addWidget(self.tabs)
        self.scans=QTableWidget(0,10);self.scans.setHorizontalHeaderLabels(["Lần quét","Thời điểm dữ liệu","Phạm vi","Công cụ","Trạng thái","File / thư mục","Tổng file / trên đĩa","Thời gian","Δ data / log","Ghi chú"])
        self.pairs=QTableWidget(0,10);self.pairs.setHorizontalHeaderLabels(["Mốc cũ → mới","Dung lượng cũ","Dung lượng mới","Mức thay đổi","Tăng (file/folder)","Giảm (file/folder)","Thêm mới (file/folder)","Mất đi (file/folder)","Khác / chưa rõ","Δ data / log"])
        self.tabs.addTab(self.scans,"Các lần quét");self.tabs.addTab(self.pairs,"Các lần so sánh")
        for table in (self.scans,self.pairs):
            table.setAlternatingRowColors(True);table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.scans.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        owner.columns(self.scans,"history-scans",[180,220,250,100,170,160,200,110,160,300])
        owner.columns(self.pairs,"history-pairs",[300,120,120,160,140,140,140,150,190,160])
        self.scans.doubleClicked.connect(lambda _:self.open_selected());self.pairs.doubleClicked.connect(lambda _:self.open_selected())
        self.info=QLabel("Tóm tắt tính toàn phạm vi, không chỉ 20 dòng. Thư mục cha gồm con, không cộng trùng dung lượng. Chọn một/nhiều dòng rồi xóa để bỏ metadata snapshot cùng cache so sánh liên quan; Chọn tất cả lượt để xóa toàn bộ. Không xóa file thật, cấu hình hay log.");self.info.setWordWrap(True);layout.addWidget(self.info)

    def require_idle(self):
        active=[worker for worker in self.owner.workers if worker.isRunning()]
        if self.owner.scan_worker is not None or active:
            QMessageBox.warning(self,"Đang có tác vụ","Chờ quét, so sánh hoặc tác vụ nền đang chạy kết thúc rồi mới dọn dữ liệu lịch sử.")
            return False
        return True

    def clear_cache(self):
        if not self.require_idle():return
        if QMessageBox.question(self,"Xóa cache so sánh","Xóa toàn bộ chỉ mục/tóm tắt so sánh đã lưu? Snapshot lịch sử quét vẫn giữ nguyên và so sánh sẽ được tạo lại khi cần.")!=QMessageBox.StandardButton.Yes:return
        def clear(_):
            cache=Comparisons()
            try:cache.clear_all()
            finally:cache.close()
            return True
        self.owner.job(clear,lambda _:(self.owner.browser.cache_cleared(),self.refresh()))

    def clear_history(self):
        if not self.require_idle():return
        if QMessageBox.warning(self,"Xóa toàn bộ lịch sử quét","Thao tác này xóa metadata của tất cả snapshot, các lỗi quét và cache so sánh. Không xóa file trong các thư mục đã quét, cấu hình, profile, log hay tác vụ lịch. Không thể hoàn tác.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        phrase,ok=QInputDialog.getText(self,"Xác nhận xóa lịch sử","Nhập XOA LICH SU để tiếp tục")
        if not ok or phrase.strip()!="XOA LICH SU":return
        def clear(_):
            cache=Comparisons()
            try:cache.clear_all()
            finally:cache.close()
            store=Store()
            try:store.clear_scans()
            finally:store.close()
            return True
        def done(_):
            self.owner.refresh_history();self.owner.browser.compare();self.refresh()
            self.info.setText("Đã xóa lịch sử quét và cache so sánh. File thật, cấu hình, profile và log vẫn được giữ.")
        self.owner.job(clear,done)

    def select_all_scans(self):
        self.tabs.setCurrentIndex(0);self.scans.selectAll()

    def clear_scan_selection(self):
        self.scans.clearSelection()

    def delete_selected_scans(self):
        if not self.require_idle():return
        if self.tabs.currentIndex()!=0:
            self.info.setText("Chuyển sang tab Các lần quét rồi chọn một hay nhiều dòng cần xóa.");return
        selected=sorted({index.row() for index in self.scans.selectionModel().selectedRows()})
        if not selected:
            self.info.setText("Chọn một hay nhiều lượt quét trước; nút Chọn tất cả lượt dùng để chọn toàn bộ.");return
        rows=[self.scan_rows[index] for index in selected]
        running=[row for row in rows if row["status"]=="running"]
        if running:
            QMessageBox.warning(self,"Lượt quét đang chạy","Không xóa được khi lựa chọn có lượt quét đang chạy.");return
        ids=[row["id"] for row in rows]
        all_selected=len(ids)==len(self.scan_rows) and {row["id"] for row in rows}=={row["id"] for row in self.scan_rows}
        labels=[scan_name(row) for row in rows[:5]]
        if len(rows)>5:labels.append(f"… và {len(rows)-5} lượt khác")
        title="Xóa toàn bộ lịch sử đã chọn" if all_selected else "Xóa lượt quét đã chọn"
        message=(f"Xóa metadata của {len(ids)} lượt quét và các cache so sánh phụ thuộc?\n\n"+
                 "\n".join(labels)+"\n\nKhông xóa file/thư mục thật, cấu hình, profile, lịch chạy hay log. Không thể hoàn tác.")
        if QMessageBox.warning(self,title,message,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,
                               QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        if all_selected:
            phrase,ok=QInputDialog.getText(self,"Xác nhận xóa toàn bộ","Nhập XOA LICH SU để tiếp tục")
            if not ok or phrase.strip()!="XOA LICH SU":return
        def remove(worker):
            cache=Comparisons(worker.stop,worker.event.emit)
            try:cache.delete_for_scans(ids)
            finally:cache.close()
            store=Store()
            try:return store.delete_scans(ids)
            finally:store.close()
        def done(count):
            self.owner.refresh_history();self.owner.browser.compare();self.refresh()
            self.info.setText(f"Đã xóa metadata của {count} lượt quét và cache so sánh phụ thuộc. File thật, cấu hình, lịch chạy và log vẫn được giữ.")
        self.owner.job(remove,done)

    def refresh(self):
        def read(_):
            store=Store(readonly=True)
            try:scans=store.scans()
            finally:store.close()
            cache=Comparisons()
            try:pairs=cache.history()
            finally:cache.close()
            return scans,pairs
        def done(data):
            self.scan_rows,self.pair_rows=data;self.scans.setRowCount(len(self.scan_rows));self.pairs.setRowCount(len(self.pair_rows))
            lookup={r["id"]:r for r in self.scan_rows}
            for i,row in enumerate(self.scan_rows):
                summary=json.loads(row["summary"] or "{}");roots="; ".join(json.loads(row["config"])["roots"])
                note=summary.get("coverage_note") or summary.get("note","") or ("Phục hồi, không quét mới" if summary.get("recovered") else "")
                status_text=STATUS.get(row["status"],row["status"])+(" • "+summary["phase"] if row["status"]=="running" and summary.get("phase") else "")
                values=[scan_name(row),local_time(row["started"]),roots,row["engine"],status_text,
                        f"{summary.get('files',0):,} / {summary.get('folders',0):,}",size(summary.get("logical"))+" / "+size(summary.get("allocated")),f"{summary.get('seconds',0):,.1f} s",storage_delta(summary.get("storage")),note]
                for j,value in enumerate(values):
                    cell=QTableWidgetItem(value);cell.setToolTip(value+f"\nID nội bộ: {row['id']}\n"+storage_tooltip(summary.get("storage")))
                    if j==4 and (summary.get("coverage_note") or summary.get("note")):cell.setToolTip(status_text+"\n"+(summary.get("coverage_note") or summary.get("note")))
                    self.scans.setItem(i,j,cell)
            for i,row in enumerate(self.pair_rows):
                summary=json.loads(row["summary"] or "{}");a=summary.get("older");b=summary.get("newer")
                old=summary.get("old_totals") or {};new=summary.get("new_totals") or {};counts=summary.get("counts",[])
                tally=lambda status:f"{sum(x['count'] for x in counts if x['kind']=='file' and x['change']==status):,} / {sum(x['count'] for x in counts if x['kind']=='folder' and x['change']==status):,}"
                other=sum(x["count"] for x in counts if x["change"] not in ("same","first_scan","grown","shrunk","new","missing","moved_from"))
                values=[(scan_name(a) if a else "Mốc ban đầu")+" → "+(scan_name(b) if b else "Đang chuẩn bị"),size(old.get("logical")),size(new.get("logical")),change_text(new.get("logical",0)-old.get("logical",0) if a else None),tally("grown"),tally("shrunk"),tally("new"),tally("missing"),f"{other:,} • "+("chi tiết đọc lại khi mở" if row["status"]=="archived" else row["status"]),storage_delta(summary.get("storage"))]
                for j,value in enumerate(values):cell=QTableWidgetItem(value);cell.setToolTip(value+"\n"+storage_tooltip(summary.get("storage")));self.pairs.setItem(i,j,cell)
        self.owner.job(read,done)

    def open_selected(self):
        if self.tabs.currentIndex()==0:
            index=self.scans.currentRow()
            if index<0:return
            row=self.scan_rows[index]
            if row["status"] not in ("complete","partial"):
                self.info.setText("Lượt này chưa hợp lệ để kết luận thay đổi; có thể xem nhật ký hoặc phục hồi nếu nhập đã hoàn tất.");return
            profile,newer,older=row["profile"],row["id"],None
        else:
            index=self.pairs.currentRow()
            if index<0:return
            row=self.pair_rows[index];older=row["older"];newer=row["newer"]
            store=Store(readonly=True)
            try:profile=store.scan(newer)["profile"]
            except ValueError:self.info.setText("Snapshot chi tiết đã được dọn; chỉ còn tóm tắt lịch sử.");return
            finally:store.close()
        self.owner.profile_combo.setCurrentText(profile);self.owner.refresh_history()
        self.owner.newer.setCurrentIndex(self.owner.newer.findData(newer));self.owner.older.setCurrentIndex(max(0,self.owner.older.findData(older)))
        self.owner.tabs.setCurrentIndex(0);self.owner.compare()

    def recover_selected(self):
        index=self.scans.currentRow()
        if self.tabs.currentIndex()!=0 or index<0:return
        row=self.scan_rows[index]
        if QMessageBox.question(self,"Phục hồi metadata","Chỉ dùng khi lượt này đã nhập ĐẦY ĐỦ và dừng ở bước tính tổng. Không quét lại file. Tạo lượt phục hồi riêng, giữ nguyên lượt cũ. Bạn xác nhận dữ liệu đã nhập đủ?")!=QMessageBox.StandardButton.Yes:return
        from .recovery import rebuild
        self.owner.job(lambda worker:rebuild(row["id"],confirmed=True,stop=worker.stop,notify=worker.event.emit),lambda result:(self.refresh(),self.owner.scan_done(result)),scan_job=True)

    def remove_pair(self):
        index=self.pairs.currentRow()
        if self.tabs.currentIndex()!=1 or index<0:return
        pair=self.pair_rows[index]["id"]
        if QMessageBox.question(self,"Xóa tóm tắt","Chỉ xóa tóm tắt/cache cặp so sánh này, không xóa snapshot hay file thật. Tiếp tục?")!=QMessageBox.StandardButton.Yes:return
        def remove(_):
            cache=Comparisons()
            try:
                cache.delete(pair)
            finally:cache.close()
            return True
        self.owner.job(remove,lambda _:self.refresh())
