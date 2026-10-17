"""Explicit preview/apply/rollback workflow for captured histories."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QPlainTextEdit,QMessageBox
from .normalization import Normalizations,STATES
from .presentation import size,display_json


class NormalizationDialog(QDialog):
    def __init__(self,panel,scan_ids):
        super().__init__(panel);self.panel=panel;self.owner=panel.owner;self.ids=list(scan_ids);self.reports=[];self.worker=None
        self.closing_after_stop=False
        self.setWindowTitle('Kiểm tra & chuẩn hóa lịch sử — giữ nguyên bản gốc');self.resize(1120,650)
        layout=QVBoxLayout(self)
        label=QLabel(f'{len(self.ids)} lượt đã chọn. Kiểm tra trước → xem trước/sau → Áp dụng. Không đọc lại file trên ổ.\n'
            'Kiểm chứng phép tính không chứng minh lần quét đã bao phủ đầy đủ. CSV gốc thiếu có thể khiến Allocated không phục hồi được.');label.setWordWrap(True);layout.addWidget(label)
        row=QHBoxLayout()
        self.preview_button=self.owner.button('1. Kiểm tra trước',self.preview,row)
        self.apply_button=self.owner.button('2. Áp dụng chuẩn hóa',self.apply,row);self.apply_button.setEnabled(False)
        self.restore_button=self.owner.button('Dùng lại bản gốc',self.restore,row)
        self.stop_button=self.owner.button('Dừng',self.stop,row);self.stop_button.setEnabled(False)
        row.addStretch();layout.addLayout(row)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(['Lượt','Trạng thái','Size gốc → chuẩn','Allocated gốc → chuẩn','Mục thay đổi','Giới hạn'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setColumnWidth(0,65);self.table.setColumnWidth(1,205)
        for i in (2,3):self.table.setColumnWidth(i,190)
        self.table.setColumnWidth(5,310);layout.addWidget(self.table,1)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);layout.addWidget(self.details,1)
        self.state=QLabel('Chưa thay đổi dữ liệu.');self.state.setWordWrap(True);layout.addWidget(self.state)
        self.table.itemSelectionChanged.connect(self.selected)

    def selected(self):
        index=self.table.currentRow()
        if 0<=index<len(self.reports):self.details.setPlainText(display_json(self.reports[index]))

    def run(self,apply=False):
        if self.worker:return
        if not self.panel.require_idle():return
        self.preview_button.setEnabled(False);self.apply_button.setEnabled(False);self.restore_button.setEnabled(False);self.stop_button.setEnabled(True)
        self.state.setText('Đang kiểm chứng lịch sử; có thể Dừng. Bản gốc không bị ghi đè.')
        ids=list(self.ids)
        def work(worker):
            norm=Normalizations(worker.stop,worker.event.emit);reports=[]
            try:
                for ident in ids:
                    if worker.stop.is_set():break
                    reports.append(norm.inspect(ident,apply=apply,force=True))
                    worker.event.emit('normalization-report',reports[-1])
                return reports
            finally:norm.close()
        def done(reports):
            self.reports=reports;self.table.setRowCount(len(reports))
            for index,report in enumerate(reports):
                before,after=report['before'],report['after']
                values=[str(report['scan_id']),STATES[report['state']],size(before['logical'])+' → '+size(after['logical']),
                    size(before['allocated'])+' → '+size(after['allocated']),str(report['changed_rows']),' • '.join(report['warnings']) or 'Không phát hiện lỗi phép tính']
                for column,value in enumerate(values):
                    cell=QTableWidgetItem(value);cell.setToolTip(value);self.table.setItem(index,column,cell)
            if reports:self.table.setCurrentCell(0,0)
            self.state.setText(('Đã áp dụng lớp chuẩn hóa. Có thể dùng lại bản gốc.' if apply else 'Kiểm tra xong, chưa áp dụng. Xem giới hạn và số liệu trước khi xác nhận.')+
                               f' Đã xử lý {len(reports)}/{len(ids)} lượt.')
            if apply:self.changed()
        self.worker=self.owner.job(work,done)
        self.worker.finished.connect(self.job_finished)

    def job_finished(self):
        self.worker=None;self.preview_button.setEnabled(True);self.restore_button.setEnabled(True);self.stop_button.setEnabled(False)
        self.apply_button.setEnabled(len(self.reports)==len(self.ids) and bool(self.reports) and not any(r['applied'] for r in self.reports))
        if self.closing_after_stop:self.accept()

    def preview(self):self.run(False)

    def apply(self):
        if QMessageBox.question(self,'Áp dụng lớp chuẩn hóa','Lưu lớp chuẩn hóa cho các lượt đã chọn? Bản gốc vẫn giữ; dữ liệu thiếu không được tự coi là chính xác.')==QMessageBox.StandardButton.Yes:self.run(True)

    def restore(self):
        if self.worker or not self.panel.require_idle():return
        if QMessageBox.question(self,'Dùng lại bản gốc','Tắt lớp chuẩn hóa cho các lượt đã chọn? Không xóa bản gốc hay các lớp đã lưu.')!=QMessageBox.StandardButton.Yes:return
        def work(worker):
            norm=Normalizations(worker.stop,worker.event.emit)
            try:return norm.restore(self.ids)
            finally:norm.close()
        self.owner.job(work,lambda _:(self.changed(),self.state.setText('Đang dùng lại bản gốc; chỉ áp dụng lại khi bạn chọn Chuẩn hóa.')))

    def changed(self):
        self.owner.browser.cache_cleared();self.owner.browser.tree_ids=None
        self.panel.invalidate();self.owner.refresh_history()

    def stop(self):
        if self.worker:self.worker.stop.set();self.state.setText('Đã yêu cầu dừng; các lượt hoàn thành trước đó được giữ, lượt đang xử lý sẽ hoàn tác.')

    def closeEvent(self,event):
        if self.worker:
            event.ignore()
            if self.closing_after_stop:return
            if QMessageBox.question(self,'Đang chuẩn hóa','Dừng an toàn, lưu điểm tiếp tục vào Nhật ký rồi đóng cửa sổ này? App vẫn mở; lượt hoàn thành được giữ, bước đang dở hoàn tác.')==QMessageBox.StandardButton.Yes:
                self.closing_after_stop=True;self.stop()
        else:super().closeEvent(event)
