"""One explicit update action, using the same transaction as the CLI."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget,QGroupBox,QVBoxLayout,QHBoxLayout,QLabel,QPlainTextEdit,QProgressBar
from . import __version__, app_update


class AppUpdatePanel(QGroupBox):
    def __init__(self,owner):
        super().__init__('Cập nhật Disk Growth Monitor')
        self.owner=owner;self.release=None;self.worker=None;self.phase='';self.handed_off=False
        layout=QVBoxLayout(self);row=QHBoxLayout()
        self.check_button=owner.button('Kiểm tra bản mới',self.check,row)
        self.update_button=owner.button('Cập nhật',self.update,row)
        self.update_button.setToolTip('Tự kiểm tra, tải, xác minh và cập nhật đúng bản app hiện có. Giữ nguyên lịch sử; xác nhận Yes khi Windows hiện UAC.')
        self.cancel_button=owner.button('Hủy trước khi cài',self.cancel,row)
        owner.button('Xem Release',lambda:owner.open_source(f'https://github.com/{app_update.REPO}/releases'),row)
        row.addStretch();layout.addLayout(row)
        self.label=QLabel(f'Đang dùng {__version__} • Cập nhật bằng một nút; giữ nguyên lịch sử, cấu hình và log. Installer yêu cầu UAC.')
        self.label.setWordWrap(True);layout.addWidget(self.label)
        self.progress_bar=QProgressBar();self.progress_bar.setRange(0,100);self.progress_bar.setValue(0);layout.addWidget(self.progress_bar)
        self.notes=QPlainTextEdit();self.notes.setReadOnly(True);self.notes.setMaximumHeight(90)
        self.notes.setPlaceholderText('Bộ cài chưa ký số; SHA-256 kiểm tra toàn vẹn, không thay thế chữ ký nhà phát hành.');layout.addWidget(self.notes)
        self.set_busy(False)

    def set_busy(self,busy):
        self.check_button.setEnabled(not busy)
        self.update_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy and self.phase=='update' and not self.handed_off)

    def run(self,phase,function,done):
        if self.worker is not None:return
        self.phase=phase;self.set_busy(True);self.progress_bar.setValue(0)
        self.worker=self.owner.job(function,done)
        self.worker.error.connect(lambda message:self.label.setText('Không hoàn tất: '+message.splitlines()[0]))
        self.worker.event.connect(self.progress)
        def finished():
            self.worker=None;self.set_busy(False)
            if phase=='update' and not self.handed_off:self.pause_other_actions(False)
        self.worker.finished.connect(finished)

    def progress(self,event,data):
        if event=='update-release':
            self.release=data;self.notes.setPlainText(data['notes'])
        elif event=='update-handoff':self.cancel_button.setEnabled(False)
        elif event=='cancelled':self.label.setText(data.get('message','Đã hủy cập nhật'))
        elif event=='task-progress':
            value=max(self.progress_bar.value(),min(100,int(data.get('percent',0))))
            self.progress_bar.setValue(value);self.progress_bar.setFormat('%p% • '+data.get('stage',''))
            self.label.setText(data.get('stage','')+'\nĐã xong: '+'; '.join(data.get('completed',[])))

    def check(self):
        if self.worker is not None:return
        self.label.setText('Đang kiểm tra Release chính thức…')
        def done(release):
            self.release=release;self.notes.setPlainText(release['notes']);self.progress_bar.setValue(100)
            self.label.setText(f'Đang dùng {__version__} • Release {release["version"]} • '+
                ('Có bản mới; bấm Cập nhật để tải và cài tại app hiện có, giữ nguyên lịch sử.' if release['available'] else 'Không có bản mới hơn; không hạ phiên bản.'))
        self.run('check',lambda _:app_update.check(),done)

    def pause_other_actions(self,busy):
        owner=self.owner
        if busy:
            owner.update_in_progress=True
            self.previous_tabs=[owner.tabs.isTabEnabled(i) for i in range(owner.tabs.count())]
            system_tab=self.parentWidget()
            self.paused_widgets=[(child,child.isEnabled()) for child in system_tab.findChildren(QWidget)
                if child.parentWidget() is system_tab and child is not self]
            for widget,_ in self.paused_widgets:widget.setEnabled(False)
            for i in range(owner.tabs.count()):owner.tabs.setTabEnabled(i,i==owner.tabs.currentIndex())
            for timer in (owner.poll_timer,owner.realtime_timer,owner.scan_state_timer):timer.stop()
            owner.scan_button.setEnabled(False);owner.rescan_button.setEnabled(False)
        else:
            owner.update_in_progress=False
            for widget,enabled in getattr(self,'paused_widgets',[]):widget.setEnabled(enabled)
            for i,enabled in enumerate(getattr(self,'previous_tabs',[])):owner.tabs.setTabEnabled(i,enabled)
            owner.poll_timer.start(30000);owner.realtime_timer.start(3000);owner.scan_state_timer.start(500)
            owner.scan_button.setEnabled(not owner.admin_busy);owner.rescan_button.setEnabled(not owner.admin_busy)

    def cancel(self):
        if self.worker is not None and self.phase=='update' and self.cancel_button.isEnabled():
            self.worker.stop.set();self.cancel_button.setEnabled(False)

    def update(self):
        if self.worker is not None:return
        if self.owner.workers or self.owner.scan_worker is not None or self.owner.background_action_busy or self.owner.admin_busy:
            self.owner.status.setText('Chờ tác vụ hiện tại kết thúc trước khi cập nhật.');return
        self.handed_off=False;self.pause_other_actions(True)
        def done(result):
            if not result['launched']:return
            self.handed_off=True
            self.label.setText('Đã bàn giao bộ cài; app sẽ mở lại sau khi kiểm tra phiên bản và lịch sử. Chưa kết luận cài thành công.')
            self.owner.status.setText('Đang đóng app an toàn để cập nhật…')
            def close_when_idle():
                if self.owner.workers:QTimer.singleShot(100,close_when_idle)
                else:self.owner.close()
            QTimer.singleShot(100,close_when_idle)
        self.run('update',lambda worker:app_update.start_update(worker.stop,worker.event.emit,reopen=True),done)
