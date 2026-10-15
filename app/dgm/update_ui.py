"""Explicit app-update controls, separate from read-only dependency checks."""
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGroupBox,QVBoxLayout,QHBoxLayout,QLabel,QPlainTextEdit,QMessageBox,QFileDialog
from . import __version__
from .config import home
from . import app_update


class AppUpdatePanel(QGroupBox):
    def __init__(self,owner):
        super().__init__('Cập nhật Disk Growth Monitor'); self.owner=owner; self.release=None; self.installer=None; self.worker=None
        layout=QVBoxLayout(self); row=QHBoxLayout()
        self.check_button=owner.button('Kiểm tra bản mới',self.check,row)
        self.download_button=owner.button('Tải cập nhật',self.download,row)
        self.install_button=owner.button('Cài cập nhật',self.install,row)
        self.cancel_button=owner.button('Hủy tải',self.cancel,row)
        owner.button('Xem Release',lambda:owner.open_source(f'https://github.com/{app_update.REPO}/releases'),row)
        row.addStretch();layout.addLayout(row)
        self.label=QLabel(f'Đang dùng {__version__} • Chỉ kiểm tra/cài khi bạn bấm nút. Không cập nhật Python/pip hệ thống.');self.label.setWordWrap(True);layout.addWidget(self.label)
        self.notes=QPlainTextEdit();self.notes.setReadOnly(True);self.notes.setMaximumHeight(90)
        self.notes.setPlaceholderText('Ghi chú phát hành sẽ hiện sau khi kiểm tra. SHA-256 không thay thế chữ ký nhà phát hành.');layout.addWidget(self.notes)
        self.set_busy(False)

    def set_busy(self,busy):
        self.check_button.setEnabled(not busy)
        available=bool(self.release and self.release['available'])
        self.download_button.setEnabled(not busy and available)
        self.install_button.setEnabled(not busy and available and bool(self.installer))
        self.cancel_button.setEnabled(busy and getattr(self,'phase','')=='download')

    def run(self,phase,function,done):
        if self.worker is not None:return
        self.phase=phase;self.set_busy(True)
        self.worker=self.owner.job(function,done)
        self.worker.error.connect(lambda message:self.label.setText('Không hoàn tất: '+message.splitlines()[0]))
        self.worker.event.connect(lambda event,data:self.label.setText(data.get('message','')) if event=='cancelled' or (phase=='download' and event=='info') else None)
        def finished():self.worker=None;self.set_busy(False)
        self.worker.finished.connect(finished)

    def check(self):
        self.release=None;self.installer=None;self.label.setText('Đang kiểm tra Release chính thức…')
        def done(release):
            self.release=release;self.notes.setPlainText(release['notes'])
            mode='Bản source: bộ cài chỉ được cài vào thư mục khác.' if release['mode']=='source' else 'Bản cài: giữ cấu hình, lịch và lịch sử.'
            self.label.setText(f"Đang dùng {__version__} | Release ổn định {release['version']} • "+('Có bản mới' if release['available'] else 'Không có bản mới hơn; không hạ phiên bản')+f" • {release['size']/1_000_000:.1f} MB\n"+mode)
            self.install_button.setText('Cài vào thư mục khác' if release['mode']=='source' else 'Cài cập nhật')
        self.run('check',lambda _:app_update.check(),done)

    def download(self):
        if not self.release:return
        release=dict(self.release)
        def done(path):self.installer=path;self.label.setText(f'Đã tải và kiểm tra SHA-256 • {release["version"]}\n{path}')
        self.run('download',lambda worker:app_update.download(release,worker.stop,worker.event.emit),done)

    def cancel(self):
        if self.worker is not None and self.phase=='download':self.worker.stop.set();self.cancel_button.setEnabled(False)

    def install(self):
        if not self.release or not self.installer:return
        if self.owner.workers or self.owner.background_action_busy or self.owner.admin_busy:
            self.owner.status.setText('Chờ các tác vụ hiện tại kết thúc trước khi cài cập nhật.');return
        text=f'Cài Disk Growth Monitor {self.release["version"]}?\nBộ cài chưa có chữ ký số; SHA-256 chỉ xác minh file tải.\n'
        if app_update.installation_mode()=='source':
            if QMessageBox.question(self,'Cài bản đóng gói',text+'Không sửa source/.venv. Hãy chọn một thư mục trống khác.')!=QMessageBox.StandardButton.Yes:return
            target=QFileDialog.getExistingDirectory(self,'Chọn thư mục trống để cài app')
            if not target:return
            destination=Path(target).resolve()
            try:app_update.install_separately(self.release,self.installer,destination)
            except Exception as exc:self.owner.fail(str(exc))
            return
        if QMessageBox.question(self,'Cài cập nhật',text+'App sẽ đóng sau khi bạn xác nhận UAC (nếu cần). Bộ chạy nền của bản này được tạm dừng rồi khôi phục, không gỡ lịch. Cấu hình/lịch sử được sao lưu; mở lại app từ shortcut sau khi xong.')!=QMessageBox.StandardButton.Yes:return
        def launch(worker):
            ticket=app_update.prepare_install(self.release,self.installer)
            worker.event.emit('info',{'message':'Đang bàn giao bộ cài; xác nhận UAC nếu có. Hủy UAC sẽ giữ app hiện tại.'})
            return app_update.launch(ticket)
        def done(result):
            self.owner.poll_timer.stop();self.owner.realtime_timer.stop();self.owner.scan_state_timer.stop()
            self.owner.status.setText('Đã bàn giao bộ cài. Đang đóng app an toàn…')
            def close_when_idle():
                if self.owner.workers:QTimer.singleShot(100,close_when_idle)
                else:self.owner.close()
            QTimer.singleShot(100,close_when_idle)
        self.run('install',launch,done)
