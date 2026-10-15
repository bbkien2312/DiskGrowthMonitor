import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QTabWidget,QTableWidget,QTableWidgetItem,QLabel,QMessageBox
from .store import Store
from .comparison import Comparisons
from .presentation import scan_label,scan_name,local_time,size,change_text,STATUS


class HistoryPanel(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.scan_rows=[];self.pair_rows=[]
        layout=QVBoxLayout(self);actions=QHBoxLayout()
        owner.button("Làm mới lịch sử",self.refresh,actions);owner.button("Xem lượt / so sánh",self.open_selected,actions)
        owner.button("Phục hồi metadata đã nhập",self.recover_selected,actions)
        owner.button("Xóa tóm tắt so sánh",self.remove_pair,actions);layout.addLayout(actions)
        self.tabs=QTabWidget();layout.addWidget(self.tabs)
        self.scans=QTableWidget(0,9);self.scans.setHorizontalHeaderLabels(["Lần quét","Thời điểm dữ liệu","Phạm vi","Công cụ","Trạng thái","File / thư mục","Tổng file / trên đĩa","Thời gian","Ghi chú"])
        self.pairs=QTableWidget(0,9);self.pairs.setHorizontalHeaderLabels(["Mốc cũ → mới","Dung lượng cũ","Dung lượng mới","Mức thay đổi","Tăng (file/folder)","Giảm (file/folder)","Thêm mới (file/folder)","Mất đi (file/folder)","Khác / chưa rõ"])
        self.tabs.addTab(self.scans,"Các lần quét");self.tabs.addTab(self.pairs,"Các lần so sánh")
        for table in (self.scans,self.pairs):
            table.setAlternatingRowColors(True);table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        owner.columns(self.scans,"history-scans",[180,220,250,100,200,160,200,110,330])
        owner.columns(self.pairs,"history-pairs",[300,120,120,160,140,140,140,150,230])
        self.scans.doubleClicked.connect(lambda _:self.open_selected());self.pairs.doubleClicked.connect(lambda _:self.open_selected())
        self.info=QLabel("Tóm tắt tính toàn phạm vi, không chỉ 20 dòng. Thư mục cha gồm con, không cộng trùng dung lượng.");self.info.setWordWrap(True);layout.addWidget(self.info)

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
                note=summary.get("note","") or ("Phục hồi, không quét mới" if summary.get("recovered") else "")
                values=[scan_name(row),local_time(row["started"]),roots,row["engine"],STATUS.get(row["status"],row["status"])+(" • "+summary["phase"] if row["status"]=="running" and summary.get("phase") else ""),
                        f"{summary.get('files',0):,} / {summary.get('folders',0):,}",size(summary.get("logical"))+" / "+size(summary.get("allocated")),f"{summary.get('seconds',0):,.1f} s",note]
                for j,value in enumerate(values):
                    cell=QTableWidgetItem(value);cell.setToolTip(value+f"\nID nội bộ: {row['id']}");self.scans.setItem(i,j,cell)
            for i,row in enumerate(self.pair_rows):
                summary=json.loads(row["summary"] or "{}");a=summary.get("older");b=summary.get("newer")
                old=summary.get("old_totals") or {};new=summary.get("new_totals") or {};counts=summary.get("counts",[])
                tally=lambda status:f"{sum(x['count'] for x in counts if x['kind']=='file' and x['change']==status):,} / {sum(x['count'] for x in counts if x['kind']=='folder' and x['change']==status):,}"
                other=sum(x["count"] for x in counts if x["change"] not in ("same","first_scan","grown","shrunk","new","missing","moved_from"))
                values=[(scan_name(a) if a else "Mốc ban đầu")+" → "+(scan_name(b) if b else "Đang chuẩn bị"),size(old.get("logical")),size(new.get("logical")),change_text(new.get("logical",0)-old.get("logical",0) if a else None),tally("grown"),tally("shrunk"),tally("new"),tally("missing"),f"{other:,} • "+("chi tiết đọc lại khi mở" if row["status"]=="archived" else row["status"])]
                for j,value in enumerate(values):cell=QTableWidgetItem(value);cell.setToolTip(value);self.pairs.setItem(i,j,cell)
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
