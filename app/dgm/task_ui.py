"""Explicit close decisions and a work queue, independent of rotating text logs."""
import json
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QDialogButtonBox,QTableWidget,QTableWidgetItem,QWidget,QMessageBox,QPlainTextEdit,
    QSystemTrayIcon,QInputDialog)
from .task_journal import LABELS,list_tasks,read,resume,discard
from .presentation import local_time
from datetime import datetime,timezone

STATES={'running':'Đang chạy','interrupted':'Dang dở','complete':'Đã xong'}


class CloseTasksDialog(QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.setWindowTitle('Còn tác vụ đang chạy');self.resize(640,340)
        self.choice='keep';layout=QVBoxLayout(self)
        title=QLabel('Bạn muốn tiếp tục chạy hay dừng an toàn rồi thoát?');title.setWordWrap(True);layout.addWidget(title)
        info=QPlainTextEdit();info.setReadOnly(True)
        active=[r for r in list_tasks() if r['state']=='running']
        info.setPlainText('\n\n'.join(f"{LABELS[r['action']]} · {r.get('percent',0)}% · {r.get('stage','')}\n{r.get('detail','')}" for r in active)
            or f'{sum(w.isRunning() for w in owner.workers)} tác vụ tải/xử lý giao diện đang chạy.')
        layout.addWidget(info,1)
        note=QLabel('Dừng rồi thoát: lưu giai đoạn an toàn, không cưỡng bức ngắt DISM/bước cài cập nhật. '
            'Quét dở phải quét lại; nguồn đã chụp đủ được dùng tiếp. Task/Service đã bật vẫn chạy độc lập theo lịch.')
        note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();layout.addLayout(row)
        for caption,choice in [('Tiếp tục chạy','keep'),('Thu nhỏ xuống khay','tray'),('Dừng an toàn rồi thoát','stop')]:
            button=QPushButton(caption);row.addWidget(button)
            if choice=='tray':button.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
            button.clicked.connect(lambda checked=False,c=choice:self.choose(c))

    def choose(self,choice):
        self.choice=choice;self.accept()


class TaskPanel(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.rows=[];self.signature=None;self.worker=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        row=QHBoxLayout();layout.addLayout(row)
        row.addWidget(QLabel('Tác vụ đang chạy / dang dở'),1)
        self.resume_button=owner.button('Tiếp tục',self.continue_selected,row)
        owner.button('Quét mới',self.new_scan,row);owner.button('Xem điểm lưu',self.details,row)
        owner.button('Bỏ điểm tiếp tục',self.remove,row)
        owner.button('Giới hạn lưu',self.set_limit,row)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Công việc','Trạng thái','Giai đoạn / %','Lưu lúc · UTC+07','Phiên bản'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.table.setMaximumHeight(122)
        self.table.setMinimumHeight(75);self.table.horizontalHeader().setStretchLastSection(True)
        for index,width in enumerate((105,95,340,180,90)):self.table.setColumnWidth(index,width)
        layout.addWidget(self.table);self.table.itemSelectionChanged.connect(self.update_buttons)
        self.note=QLabel('Tiếp tục dùng mốc đã chụp, không phải hiện trạng. Quét chưa xong sẽ chạy lại; không tự tiếp tục xóa.')
        self.note.setWordWrap(True);self.note.setObjectName('subTitle');layout.addWidget(self.note)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(1500);self.refresh()

    def refresh(self):
        if not self.isVisible() and self.signature is not None:return
        rows=list_tasks();signature=json.dumps(rows,sort_keys=True)
        if signature==self.signature:return
        selected=self.selected();selected_id=selected['id'] if selected else None
        self.rows=rows;self.signature=signature;self.table.setRowCount(len(rows))
        for index,r in enumerate(rows):
            moment=local_time(datetime.fromtimestamp(r['updated'],timezone.utc).isoformat()).replace(' UTC+07','')
            for column,value in enumerate((LABELS[r['action']],STATES.get(r['state'],r['state']),f"{r.get('percent',0)}% · {r.get('stage','')}",moment,r.get('app_version','?'))):
                item=QTableWidgetItem(value);item.setToolTip(value);self.table.setItem(index,column,item)
            if selected_id==r['id']:self.table.setCurrentCell(index,0)
        if rows and self.table.currentRow()<0:self.table.setCurrentCell(0,0)
        self.update_buttons()

    def selected(self):
        index=self.table.currentRow()
        return self.rows[index] if 0<=index<len(self.rows) else None

    def set_limit(self):
        from .config import load,save
        cfg=load();value,ok=QInputDialog.getInt(self,'Giới hạn điểm tiếp tục','Tổng MB cho tệp trung gian của tác vụ. Không tự xóa điểm đang dở.',int(cfg.get('resume_limit_mb',512)),1,100000)
        if ok:cfg['resume_limit_mb']=value;save(cfg)

    def update_buttons(self):
        row=self.selected();self.resume_button.setEnabled(bool(row and row['state']=='interrupted' and not self.worker))

    def continue_selected(self):
        row=self.selected()
        if not row or row['state']!='interrupted' or self.worker:return
        if any(w.isRunning() for w in self.owner.workers):
            QMessageBox.information(self,'Chờ tác vụ','Chờ các công việc hiện tại kết thúc trước khi tiếp tục.');return
        text='Dùng metadata đã chụp nếu đã hoàn chỉnh; nếu chưa quét đủ sẽ quét lại từ đầu. '
        text+='Bước đang dở có thể cần tính lại. Không trộn file hiện tại vào mốc đã chụp, không tự tiếp tục thao tác xóa.'
        if QMessageBox.question(self,'Tiếp tục tác vụ?',text)!=QMessageBox.StandardButton.Yes:return
        def done(result):
            if row['action']=='scan' and isinstance(result,dict):self.owner.scan_done(result)
            else:self.owner.status.setText('Đã tiếp tục và hoàn tất công việc; xem kết quả tại Kiểm tra / Lịch sử.')
            self.owner.refresh_history();self.signature=None;self.refresh()
        self.worker=self.owner.job(lambda worker:resume(row['id'],worker.stop,worker.event.emit),done,scan_job=row['action']=='scan')
        worker=self.worker
        def finished():self.worker=None;self.signature=None;self.refresh()
        worker.finished.connect(finished);self.update_buttons()

    def new_scan(self):
        row=self.selected()
        if not row or row['action']!='scan':return
        if any(w.isRunning() for w in self.owner.workers):return
        if QMessageBox.question(self,'Quét mới','Quét hiện trạng từ đầu bằng phạm vi của tác vụ đã chọn? Giữ nguyên mốc và điểm lưu cũ.')!=QMessageBox.StandardButton.Yes:return
        from .scanner import scan
        p=row['payload'];self.owner.job(lambda w:scan(p['name'],p['p'],w.stop,w.event.emit),self.owner.scan_done,scan_job=True)

    def details(self):
        row=self.selected()
        if row:self.owner.show_text(read(row['id']),'Điểm tiếp tục — metadata, không phải nội dung file')

    def remove(self):
        row=self.selected()
        if not row:return
        if QMessageBox.question(self,'Bỏ điểm tiếp tục?','Chỉ bỏ JSON và tệp trung gian riêng của tác vụ. Không xóa lịch sử, file đã quét hay Task/Service.')!=QMessageBox.StandardButton.Yes:return
        try:discard(row['id']);self.signature=None;self.refresh()
        except Exception as exc:QMessageBox.warning(self,'Chưa bỏ được',str(exc))
