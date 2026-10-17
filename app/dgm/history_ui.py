import json
from PySide6.QtCore import Qt,QTimer,QItemSelectionModel,QSignalBlocker
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QTabWidget,QTableWidget,QTableWidgetItem,QLabel,QMessageBox,QInputDialog,QPlainTextEdit
from .store import Store
from .comparison import Comparisons
from .presentation import scan_label,scan_name,local_time,size,change_text,STATUS,storage_delta,storage_tooltip,whole


class HistoryPanel(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.scan_rows=[];self.pair_rows=[]
        self.loaded=False;self.dirty=True;self.refresh_worker=None;self.revision=0
        layout=QVBoxLayout(self);actions=QHBoxLayout()
        owner.button("Làm mới lịch sử",self.refresh,actions);owner.button("Xem lượt / so sánh",self.open_selected,actions)
        owner.button("Phục hồi lượt bị gián đoạn",self.recover_selected,actions)
        self.load_state=QLabel('Đang nạp lịch sử…');actions.addWidget(self.load_state,1);layout.addLayout(actions)
        maintenance=QHBoxLayout()
        owner.button("Xóa tóm tắt đã chọn",self.remove_pair,maintenance)
        owner.button("Xóa cache so sánh",self.clear_cache,maintenance)
        owner.button('Dọn CSV nguồn nén',self.clear_sources,maintenance)
        owner.button("Xóa toàn bộ lịch sử",self.clear_history,maintenance);maintenance.addStretch();layout.addLayout(maintenance)
        scan_actions=QHBoxLayout()
        owner.button("Chọn tất cả lượt",self.select_all_scans,scan_actions);owner.button("Bỏ chọn",self.clear_scan_selection,scan_actions)
        owner.button("Xóa lượt quét đã chọn",self.delete_selected_scans,scan_actions)
        owner.button('Kiểm tra & chuẩn hóa…',self.normalize_selected,scan_actions)
        owner.button('Chọn lượt chưa chuẩn hóa',self.select_legacy,scan_actions)
        scan_actions.addWidget(QLabel("Có thể giữ Ctrl/Shift để chọn nhiều lượt."));scan_actions.addStretch();layout.addLayout(scan_actions)
        self.tabs=QTabWidget();layout.addWidget(self.tabs)
        self.scans=QTableWidget(0,11);self.scans.setHorizontalHeaderLabels(["Lần quét","Thời điểm • UTC+07","Phạm vi","Công cụ","Kết quả lưu","File / thư mục","Logic / trên đĩa","Thời gian","Δ data / log","Độ đầy đủ","Chuẩn hóa"])
        self.pairs=QTableWidget(0,10);self.pairs.setHorizontalHeaderLabels(["Mốc cũ → mới","Dung lượng cũ","Dung lượng mới","Mức thay đổi","Tăng (file/folder)","Giảm (file/folder)","Thêm mới (file/folder)","Mất đi (file/folder)","Khác / chưa rõ","Δ data / log"])
        self.tabs.addTab(self.scans,"Các lần quét");self.tabs.addTab(self.pairs,"Các lần so sánh")
        for table in (self.scans,self.pairs):
            table.setAlternatingRowColors(True);table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.scans.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        owner.columns(self.scans,"history-scans-v3",[175,195,210,90,190,130,175,90,135,165,210])
        owner.columns(self.pairs,"history-pairs-v2",[290,120,120,150,130,130,140,150,180,140])
        for table in (self.scans,self.pairs):
            table.verticalHeader().setDefaultSectionSize(32)
            table.itemSelectionChanged.connect(self.selection_details)
        self.scans.doubleClicked.connect(lambda _:self.open_selected());self.pairs.doubleClicked.connect(lambda _:self.open_selected())
        self.info=QLabel("Tóm tắt tính toàn phạm vi, không chỉ 20 dòng. Thư mục cha gồm con, không cộng trùng dung lượng. Chọn một/nhiều dòng rồi xóa để bỏ metadata snapshot cùng cache so sánh liên quan; Chọn tất cả lượt để xóa toàn bộ. Không xóa file thật, cấu hình hay log.");self.info.setWordWrap(True);layout.addWidget(self.info)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumHeight(90)
        self.details.setPlaceholderText('Chọn một dòng để xem phạm vi, các bước đã hoàn thành, giới hạn dữ liệu và dung lượng data/log.');layout.addWidget(self.details)
        self.tabs.currentChanged.connect(lambda _:self.selection_details())

    def invalidate(self):
        self.revision+=1;self.dirty=True
        if self.isVisible():QTimer.singleShot(0,self.ensure_loaded)

    def ensure_loaded(self):
        if self.dirty or not self.loaded:self.refresh()

    def selection_details(self):
        table=self.scans if self.tabs.currentIndex()==0 else self.pairs
        index=table.currentRow()
        if index<0:return
        cells=[table.item(index,column) for column in range(table.columnCount())]
        self.details.setPlainText('\n'.join(cell.toolTip() if i in (4,8,9) else cell.text()
            for i,cell in enumerate(cells) if cell is not None))

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

    def clear_sources(self):
        if not self.require_idle():return
        if QMessageBox.question(self,'Dọn nguồn CSV','Xóa các CSV nguồn nén của app? Lịch sử và lớp chuẩn hóa giữ nguyên, nhưng không thể kiểm chứng lại nguồn đã xóa.')!=QMessageBox.StandardButton.Yes:return
        from .source_archive import clear
        self.owner.job(lambda _:clear(),lambda result:self.owner.show_text(result,'Đã dọn CSV nguồn'))

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

    def select_legacy(self):
        from .normalization import VERSION
        self.tabs.setCurrentIndex(0);self.scans.clearSelection()
        for index,row in enumerate(self.scan_rows):
            summary=json.loads(row['summary'] or '{}')
            if row['status'] in ('complete','partial') and summary.get('normalization',{}).get('version')!=VERSION:
                self.scans.selectionModel().select(self.scans.model().index(index,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)

    def normalize_selected(self):
        if not self.require_idle():return
        ids=[self.scans.item(i.row(),0).data(Qt.ItemDataRole.UserRole) for i in self.scans.selectionModel().selectedRows()]
        if not ids:QMessageBox.information(self,'Chọn lịch sử','Chọn một/nhiều lượt, hoặc bấm Chọn lượt chưa chuẩn hóa.');return
        valid={r['id'] for r in self.scan_rows if r['status'] in ('complete','partial')}
        if not set(ids)<=valid:QMessageBox.warning(self,'Lượt chưa nhập đủ','Bỏ chọn lượt đang chạy/lỗi/đã dừng; dùng Phục hồi khi cần.');return
        from .normalization_ui import NormalizationDialog
        self.normalization_dialog=NormalizationDialog(self,ids);self.normalization_dialog.show()

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
        if self.refresh_worker is not None:
            self.dirty=True;return
        revision=self.revision;self.dirty=False
        self.load_state.setText('Đang đọc lịch sử đã lưu…')
        selected_scans={self.scans.item(index.row(),0).data(Qt.ItemDataRole.UserRole)
            for index in self.scans.selectionModel().selectedRows() if self.scans.item(index.row(),0)}
        selected_pair=self.pairs.item(self.pairs.currentRow(),0)
        selected_pair=selected_pair.data(Qt.ItemDataRole.UserRole) if selected_pair else None
        scrolls=[table.verticalScrollBar().value() for table in (self.scans,self.pairs)]
        def read(worker):
            store=Store(readonly=True)
            try:scans=store.scans()
            finally:store.close()
            cache=Comparisons(worker.stop,worker.event.emit)
            try:pairs=cache.history()
            finally:cache.close()
            return scans,pairs
        def done(data):
            blockers=[QSignalBlocker(self.scans),QSignalBlocker(self.pairs)]
            self.scans.clearSelection();self.pairs.clearSelection()
            self.scan_rows,self.pair_rows=data;self.scans.setRowCount(len(self.scan_rows));self.pairs.setRowCount(len(self.pair_rows))
            lookup={r["id"]:r for r in self.scan_rows}
            for i,row in enumerate(self.scan_rows):
                summary=json.loads(row["summary"] or "{}");roots="; ".join(json.loads(row["config"])["roots"])
                note=summary.get("coverage_note") or summary.get("note","") or ("Phục hồi, không quét mới" if summary.get("recovered") else "")
                if summary.get('completed_steps'):note=' • '.join(summary['completed_steps'])+(' • '+note if note else '')
                status_text=STATUS.get(row["status"],row["status"])+(" • "+summary["phase"] if row["status"]=="running" and summary.get("phase") else "")
                if row['status'] in ('complete','partial'):
                    status_text=f"Đã lưu {summary.get('files',0):,} file / {summary.get('folders',0):,} thư mục"
                    if summary.get('errors'):status_text+=f" • {summary['errors']:,} lỗi đọc"
                coverage='Có vùng chưa xác minh' if summary.get('coverage_unverified') else f"{summary['errors']:,} lỗi đọc" if summary.get('errors') else 'Không ghi nhận lỗi đọc'
                from .normalization import STATES
                norm=summary.get('normalization',{})
                norm_text=STATES.get(norm.get('state'),'Chuẩn cũ / chưa chuẩn hóa')
                values=[scan_name(row),local_time(row["started"]),roots,row["engine"],status_text,
                        f"{summary.get('files',0):,} / {summary.get('folders',0):,}",size(summary.get("logical"))+" / "+size(summary.get("allocated")),f"{whole(summary.get('seconds',0)):,} s",storage_delta(summary.get("storage")),coverage,norm_text]
                for j,value in enumerate(values):
                    cell=QTableWidgetItem(value);cell.setToolTip(value+f"\nID nội bộ: {row['id']}\n"+storage_tooltip(summary.get("storage")))
                    if j==4 and (summary.get("coverage_note") or summary.get("note")):cell.setToolTip(status_text+"\n"+(summary.get("coverage_note") or summary.get("note")))
                    if j==0:cell.setData(Qt.ItemDataRole.UserRole,row['id'])
                    if j==9:cell.setToolTip(coverage+'\n'+note)
                    if j==10:cell.setToolTip(norm_text+'\n'+'\n'.join(norm.get('warnings',[])))
                    if j in (4,9):cell.setForeground(QColor('#b45309' if row['status']=='partial' else '#b42318' if row['status']=='failed' else '#315975'))
                    self.scans.setItem(i,j,cell)
                if row['id'] in selected_scans:self.scans.selectionModel().select(
                    self.scans.model().index(i,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
            for i,row in enumerate(self.pair_rows):
                summary=json.loads(row["summary"] or "{}");a=summary.get("older");b=summary.get("newer")
                old=summary.get('full_old_totals',summary.get('old_totals')) or {};new=summary.get('full_new_totals',summary.get('new_totals')) or {};counts=summary.get("counts",[])
                tally=lambda status:f"{sum(x['count'] for x in counts if x['kind']=='file' and x['change']==status):,} / {sum(x['count'] for x in counts if x['kind']=='folder' and x['change']==status):,}"
                other=sum(x["count"] for x in counts if x["change"] not in ("same","first_scan","grown","shrunk","new","missing","moved_from"))
                delta=new['logical']-old['logical'] if a and new.get('logical') is not None and old.get('logical') is not None else None
                if 'verified_delta' in summary:delta=summary['verified_delta']
                scope_label=' [CHUNG / RIÊNG]' if 'private_counts' in summary else ' [CHỈ PHẦN CHUNG]' if summary.get('common_scope') else ''
                values=[(scan_name(a) if a else "Mốc ban đầu")+" → "+(scan_name(b) if b else "Đang chuẩn bị")+scope_label,size(old.get("logical")),size(new.get("logical")),change_text(delta),tally("grown"),tally("shrunk"),tally("new"),tally("missing"),f"{other:,} • "+("chi tiết đọc lại khi mở" if row["status"]=="archived" else f"Đã lưu {summary.get('rows',0):,} mục so sánh"),storage_delta(summary.get("storage"))]
                for j,value in enumerate(values):
                    cell=QTableWidgetItem(value);cell.setToolTip(value+"\n"+storage_tooltip(summary.get("storage")))
                    if 'private_counts' in summary:
                        po=summary.get('private_old_totals') or {};pn=summary.get('private_new_totals') or {}
                        cell.setToolTip(cell.toolTip()+'\nCũ/Mới là toàn bộ số đo; chênh lệch chỉ tính phần đủ bằng chứng của Chung và Riêng. Riêng: cũ '+size(po.get('logical'))+' / mới '+size(pn.get('logical'))+'.')
                    if j==0:cell.setData(Qt.ItemDataRole.UserRole,row['id'])
                    if j==3:cell.setForeground(QColor('#667085' if delta is None else '#138454' if delta>0 else '#d34242'))
                    self.pairs.setItem(i,j,cell)
                if row['id']==selected_pair:self.pairs.setCurrentCell(i,0)
            for table,value in zip((self.scans,self.pairs),scrolls):table.verticalScrollBar().setValue(value)
            self.loaded=True
            self.load_state.setText(f'{len(self.scan_rows):,} lượt quét • {len(self.pair_rows):,} cặp so sánh • mới nhất ở trên')
            if self.revision!=revision:self.dirty=True
            for blocker in blockers:blocker.unblock()
            self.selection_details()
        self.refresh_worker=self.owner.job(read,done)
        read_failed=[False]
        def finished():
            self.refresh_worker=None
            if self.dirty and self.isVisible() and not read_failed[0]:QTimer.singleShot(0,self.ensure_loaded)
        def failed(_):
            read_failed[0]=True;self.dirty=True;self.load_state.setText('Không đọc được lịch sử; xem nhật ký rồi thử Làm mới')
        self.refresh_worker.error.connect(failed)
        self.refresh_worker.finished.connect(finished)

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
            self.owner.browser.common_scope.setChecked(bool(json.loads(row['summary'] or '{}').get('common_scope')))
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
