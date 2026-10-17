"""The GUI shares the same preview/cleanup/compaction API as the CLI."""
from PySide6.QtCore import Slot,QTimer
from PySide6.QtWidgets import QGroupBox,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,QSpinBox,QMessageBox,QTableWidget,QTableWidgetItem
from .config import load,save,home
from .presentation import size
from . import storage_maintenance as storage


class StoragePanel(QGroupBox):
    def __init__(self,owner):
        super().__init__('Dung lượng app');self.owner=owner;self.worker=None;self.report=None
        layout=QVBoxLayout(self);row=QHBoxLayout()
        self.refresh_button=owner.button('Đo dung lượng',self.refresh,row)
        self.cleanup_button=owner.button('Dọn backup / installer',self.preview_cleanup,row)
        self.compact_button=owner.button('Thu gọn cache SQLite',self.compact,row)
        row.addStretch();layout.addLayout(row)
        logrow=QHBoxLayout()
        self.migrate_button=owner.button('Chuyển lịch sử sang JSONL nén',self.migrate,logrow)
        self.clear_index_button=owner.button('Xóa chỉ mục tạm',self.clear_index,logrow)
        self.retire_button=owner.button('Thu hồi SQLite cũ',self.retire_legacy,logrow)
        from .history_backend import log_mode
        self.migrate_button.setEnabled(not log_mode());self.clear_index_button.setEnabled(log_mode())
        self.retire_button.setVisible(log_mode() and any((home()/'data'/name).exists() for name in ('history.sqlite','comparisons.sqlite','normalization.sqlite')))
        self.cancel_migration_button=owner.button('Dừng chuyển đổi',self.cancel_migration,logrow);self.cancel_migration_button.hide()
        logrow.addStretch();layout.addLayout(logrow)
        self.summary=QLabel('Chỉ đo dữ liệu/log của app; không đọc lại file trên các ổ đã quét.')
        self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['Thành phần','Dung lượng','Mục đích / cách quản lý'])
        self.table.setMinimumHeight(220);self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True);layout.addWidget(self.table)
        row=QHBoxLayout();cfg=load()
        self.keep=QSpinBox();self.keep.setRange(1,20);self.keep.setValue(max(1,cfg.get('update_backup_keep',1)))
        self.reserve=QSpinBox();self.reserve.setRange(256,65536);self.reserve.setValue(cfg.get('storage_reserve_mb',1024))
        row.addWidget(QLabel('Giữ backup thành công'));row.addWidget(self.keep)
        row.addWidget(QLabel('Dự phòng MB'));row.addWidget(self.reserve)
        owner.button('Lưu chính sách',self.save_policy,row)
        owner.button('Dọn đề xuất hết hạn',lambda:self.owner.cleanup_panel.prune() if self.idle() else None,row)
        layout.addLayout(row)
        self.all_backups=QCheckBox('Dọn cả backup gần nhất (mất bản dự phòng)');layout.addWidget(self.all_backups)
        self.note=QLabel('Tự giữ backup gần nhất, dọn bản cũ sau cập nhật đã kiểm chứng. Lượt lỗi/chờ khôi phục được bảo vệ. '
            'Thu gọn giữ kết quả so sánh và lịch sử; cần đủ chỗ tạm. Không tự xóa mốc quét.')
        self.note.setWordWrap(True);layout.addWidget(self.note)
        self.refresh_timer=QTimer(self);self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh_when_idle)
        owner.tabs.currentChanged.connect(lambda index:self.refresh_timer.start(200) if owner.tabs.widget(index).isAncestorOf(self) else self.refresh_timer.stop())

    @Slot()
    def refresh_when_idle(self):
        # A read-only tab refresh must not race a user's immediate Update click.
        if not self.owner.workers and not getattr(self.owner,'update_in_progress',False):self.refresh()

    def idle(self):
        w=self.owner
        if w.workers or w.scan_worker is not None or getattr(w,'update_in_progress',False) or w.admin_busy or w.background_action_busy:
            w.status.setText('Chờ tác vụ hiện tại kết thúc trước khi bảo trì dung lượng.');return False
        return True

    def run(self,function,done):
        if self.worker is not None:return
        self.pending_done=done;self.pending_result=None
        self.worker=self.owner.job(lambda worker:function(worker),self.received)
        self.cleanup_button.setEnabled(False);self.compact_button.setEnabled(False)
        self.worker.finished.connect(self.finished)

    @Slot(object)
    def received(self,value):self.pending_result=(self.pending_done,value)

    @Slot()
    def finished(self):
        self.worker=None;self.cleanup_button.setEnabled(True);self.compact_button.setEnabled(True)
        result=self.pending_result;self.pending_result=None
        if result:
            function,value=result
            function(value)

    def refresh(self):self.run(lambda _:storage.storage_report(),self.loaded)

    def loaded(self,report):
        from .history_backend import log_mode
        mode=log_mode();self.migrate_button.setEnabled(not mode);self.clear_index_button.setEnabled(mode)
        from .config import home
        self.retire_button.setVisible(mode and any((home()/'data'/name).exists() for name in ('history.sqlite','comparisons.sqlite','normalization.sqlite')))
        self.report=report;plan=report['update_cleanup'] or {}
        if hasattr(self.owner,'system_cards'):
            cards=self.owner.system_cards
            cards[1].set_value(size(report['total_data_log_bytes']),'Chỉ dữ liệu/log app; không phải file đã quét')
            cards[2].set_value(size(report['groups'].get('Backup cập nhật',0)),'Đã nằm trong tổng data app, không cộng lại')
            cards[3].set_value(size(report['free_bytes']),'Ổ chứa app; không suy từ tổng Size cây thư mục')
        self.summary.setText(f"Dữ liệu/log {size(report['total_data_log_bytes'])} • Trống {size(report['free_bytes'])} • "
            f"Có thể dọn backup/installer {size(plan.get('reclaimable_bytes',0))} • "
            f"Trang trống trong cache {size(report.get('databases',{}).get('comparisons.sqlite',{}).get('reusable_bytes',0))}")
        groups=sorted(report['groups'].items(),key=lambda x:x[1],reverse=True)
        self.table.setRowCount(len(groups))
        for row,(name,value) in enumerate(groups):
            self.table.setItem(row,0,QTableWidgetItem(name));self.table.setItem(row,1,QTableWidgetItem(size(value)))
            note=('Giữ cấu hình và metadata chưa phân loại; không tự xóa' if name=='Cấu hình / dữ liệu khác' else
                  'Xoay vòng log có hạn dung lượng; không tự gửi ra ngoài' if name=='Log' else
                  'Metadata có hạn 1 giờ; không chứa nội dung file' if name=='Metadata đề xuất dọn nén' else
                  'Dọn đề xuất hết hạn; có thể dựng lại từ log nén' if name=='Chỉ mục đề xuất dọn tạm' else
                  'Giữ log metadata, không chứa nội dung file' if 'JSONL' in name else
                  'Có thể bỏ khi rảnh; giữ log lịch sử' if 'tạm' in name else
                  'Giữ mặc định một bản, bỏ bản cuối cần duyệt' if 'Backup' in name else
                  'Chuyển và xác minh trước khi thu hồi' if name in ('SQLite cũ chưa thu hồi','Lịch sử quét') else
                  'Có hạn dung lượng/tuổi; không xóa file đã quét')
            self.table.setItem(row,2,QTableWidgetItem(note))
        self.table.resizeColumnToContents(0)
        if report['warnings']:self.note.setText('Chưa đo/dọn được một số mục: '+'; '.join(report['warnings']))

    def clear_index(self):
        if not self.idle():return
        from .history_backend import clear_indexes
        if QMessageBox.question(self,'Bỏ chỉ mục tạm','Chỉ xóa SQLite tạm, giữ log lịch sử nén và tóm tắt. '
            'Lần so sánh sau cần đọc lại log. Tiếp tục?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        def done(result):
            self.owner.browser.cache_cleared()
            self.owner.status.setText(f"Đã bỏ chỉ mục tạm, thu hồi {size(result['reclaimed_bytes'])}; log lịch sử giữ nguyên.")
            self.refresh()
        self.run(lambda _:clear_indexes(),done)

    def retire_legacy(self):
        if not self.idle():return
        from .history_migration import retire_legacy
        if QMessageBox.question(self,'Thu hồi SQLite cũ','Xác minh lại log và chỉ bỏ SQLite nguồn cũ còn nguyên theo biên bản chuyển đổi? '
            'Nguồn đã đổi hoặc đang được tác vụ khác dùng sẽ được giữ.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        def done(result):
            self.owner.status.setText(f"Đã thu hồi SQLite cũ {size(result['removed_bytes'])}; log giữ nguyên.");self.refresh()
        self.run(lambda _:retire_legacy(),done)

    def cancel_migration(self):
        if self.worker:self.worker.stop.set();self.cancel_migration_button.setEnabled(False)

    def migrate(self):
        if not self.idle():return
        from .history_migration import migrate
        if QMessageBox.question(self,'Chuyển lịch sử metadata sang JSONL',
            'Ghi log JSONL nén chứa đường dẫn/dung lượng/thời gian/trạng thái, không chứa nội dung file. '
            'Đọc lại và xác minh số bản ghi/checksum trước khi bỏ SQLite cũ, CSV nguồn và backup cập nhật đã hoàn tất (lượt lỗi được giữ). '
            'Giữ mốc quét và tóm tắt so sánh, không xóa file đã quét; '
            'có thể mất thời gian với hàng triệu mục. Tiếp tục chuyển và thu hồi SQLite cũ?',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        self.owner.app_update_panel.pause_other_actions(True)
        self.setEnabled(True);self.cancel_migration_button.show();self.cancel_migration_button.setEnabled(True)
        def done(result):
            self.owner.browser.cache_cleared()
            self.owner.status.setText(f"Đã chuyển {result['scans']} mốc sang JSONL, xác minh {result['records']:,} bản ghi; "
                f"log {size(result['log_bytes'])}, thu hồi SQLite {size(result['legacy_removed_bytes'])}.")
            self.owner.history_panel.refresh();self.refresh()
        self.run(lambda worker:migrate(remove_old=True,stop=worker.stop,notify=worker.event.emit),done)
        def restored():
            self.owner.app_update_panel.pause_other_actions(False);self.cancel_migration_button.hide()
        self.worker.finished.connect(restored)

    def save_policy(self):
        if not self.idle():return
        cfg=load();cfg.update(update_backup_keep=self.keep.value(),storage_reserve_mb=self.reserve.value());save(cfg)
        self.owner.status.setText('Đã lưu giới hạn backup và dung lượng dự phòng; không xóa lịch sử.');self.refresh()

    def preview_cleanup(self):
        if not self.idle():return
        keep=0 if self.all_backups.isChecked() else self.keep.value()
        def preview(plan):
            def confirm():
                if not self.idle():return
                if not plan['files']:self.owner.status.setText('Không có backup/installer hoàn tất phù hợp để dọn.');return
                message=f"Xóa {len(plan['files'])} file cache/backup ({size(plan['reclaimable_bytes'])})?\n"
                message+=('Không còn backup cập nhật hoàn tất. ' if keep==0 else f'Giữ {keep} backup gần nhất. ')
                message+='Không xóa lịch sử, chuẩn hóa đang dùng, cấu hình hay file đã quét. Không thể hoàn tác.'
                if QMessageBox.question(self,'Duyệt dọn dữ liệu cập nhật',message,
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
                self.run(lambda _:storage.cleanup_updates(keep=keep,preview=plan),self.cleaned)
            confirm()
        self.run(lambda _:storage.update_plan(keep=keep),preview)

    def cleaned(self,result):
        self.owner.status.setText(f"Đã dọn {size(result['removed_bytes'])}; giữ lịch sử. {len(result['errors'])} lỗi cần kiểm tra.")
        self.refresh()

    def compact(self):
        if not self.idle():return
        if QMessageBox.question(self,'Thu gọn cache SQLite','Trả trang trống của cache về ổ đĩa, giữ lịch sử và kết quả so sánh. '
            'Có thể cần thời gian và dung lượng tạm; app kiểm tra trước khi chạy. Tiếp tục?',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        def done(result):
            self.owner.status.setText(f"Đã thu gọn cache: thu hồi {size(result['reclaimed_bytes'])}, lịch sử/kết quả được giữ.")
            self.refresh()
        self.run(lambda worker:storage.compact_comparison_cache(worker.stop),done)
