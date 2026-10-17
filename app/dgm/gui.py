from __future__ import annotations
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QPushButton, QComboBox, QLineEdit, QCheckBox, QDoubleSpinBox, QSpinBox, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QFileDialog, QInputDialog, QMenu, QSplitter,
    QTreeWidget, QTreeWidgetItem, QSystemTrayIcon, QStyle, QGroupBox, QToolButton, QScrollArea, QProgressBar, QSizePolicy, QDialog, QDialogButtonBox)
from .config import ROOT, load, save, profile
from .store import Store
from .comparison import Comparisons
from .ui_helpers import Preferences, HelpBox, interactive_columns, ElidingLabel
from .presentation import scan_label, scan_name, whole, display_json
from .platform_ops import ProcessCancelled


def size(value):
    from .presentation import size as format_size
    return format_size(value)


def date(value):
    return datetime.fromtimestamp(value, ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y %H:%M:%S") if value else "Không có"


def local_time(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%d/%m/%Y %H:%M:%S UTC+07") if value else "Chưa có"


def difference(value):
    return ("+" if value > 0 else "") + size(value) if value is not None else "—"


def snapshot_label(row):
    return scan_label(row)


class Worker(QThread):
    event = Signal(str, dict)
    result = Signal(object)
    error = Signal(str)

    def __init__(self, function):
        super().__init__()
        self.function = function
        self.stop = threading.Event()

    def run(self):
        try:
            self.result.emit(self.function(self))
        except ProcessCancelled as exc:
            self.event.emit('cancelled',{'message':str(exc) or 'Đã dừng tác vụ theo yêu cầu.'})
        except Exception as exc:
            import traceback
            if self.stop.is_set():
                return
            import logging
            logging.getLogger("dgm").exception("GUI worker failed")
            self.error.emit(f"{exc}\n{traceback.format_exc()}")


class Window(QMainWindow):
    def __init__(self):
        from .branding import application_icon, set_taskbar_identity
        set_taskbar_identity()
        super().__init__()
        icon=application_icon();QApplication.instance().setWindowIcon(icon);self.setWindowIcon(icon)
        # Large legacy path indexes are upgraded by cancellable background jobs,
        # not before the first window can be painted.
        s = Store(migrate_keys=False); s.close()
        font_path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts/segoeui.ttf"
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
            QApplication.instance().setFont(QFont("Segoe UI", 10))
        from . import __version__
        self.setWindowTitle(f"Disk Growth Monitor {__version__} — Theo dõi ổ đĩa")
        screen=QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(min(640,max(320,screen.width()-24)),min(480,max(240,screen.height()-48)))
        self.resize(min(1440,screen.width()-24),min(940,screen.height()-48))
        self.workers = []
        self.scan_worker = None
        self.progress_worker=None;self.task_progress_id=None;self._scan_ui_busy=False;self.last_scan_brief=''
        self.admin_busy=False;self.admin_worker=None;self.system_check_buttons=[];self.cleanup_action_buttons=[]
        self.preferences = Preferences()
        from .presentation import set_units
        set_units(self.preferences.values.get('units/binary',False))
        self.column_specs = {}
        self.help_boxes = {}
        self.loaded_profile = None
        self.loaded_values = None
        self.compare_generation = 0
        self.displayed_scan_id = None
        self.background_worker = None
        self.background_panels = []
        self.background_state = {}
        self.background_action_busy = False
        self.realtime_boxes = []
        self.current_plan = None
        self.rows = []
        self.offset = 0
        self.status = ElidingLabel("Sẵn sàng • CLI và GUI dùng chung dữ liệu")
        self.scan_progress = QProgressBar(); self.scan_progress.setRange(0, 100); self.scan_progress.setValue(0)
        self.scan_progress.setFormat("Sẵn sàng"); self.scan_progress.setTextVisible(True)
        self.scan_progress.setAccessibleName("Tiến độ công việc"); self.scan_progress.setMinimumHeight(22)
        from .theme import LIGHT
        self.setStyleSheet(LIGHT)
        outer = QWidget()
        layout = QVBoxLayout(outer);layout.setContentsMargins(6,4,6,4);layout.setSpacing(3)
        self.tabs = QTabWidget()
        title = QLabel("DISK GROWTH MONITOR");title.setStyleSheet('font-size:12px;font-weight:700;color:#325572;padding:0 8px;')
        self.tabs.setCornerWidget(title,Qt.Corner.TopRightCorner)
        self.workspace_splitter=QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.addWidget(self.tabs);layout.addWidget(self.workspace_splitter,1)
        status_host=QWidget();status_layout=QVBoxLayout(status_host);status_layout.setContentsMargins(0,0,0,0);status_layout.setSpacing(2)
        status_host.setMinimumHeight(42)
        status_row=QHBoxLayout();status_row.addWidget(self.status,1)
        self.admin_stop_button=self.button('Dừng tác vụ Admin',self.cancel_admin,status_row)
        self.admin_stop_button.setToolTip('Gửi yêu cầu dừng cho đúng tác vụ này. DISM được chờ kết thúc an toàn; không cưỡng bức tắt.')
        self.admin_stop_button.hide();status_layout.addLayout(status_row)
        status_layout.addWidget(self.scan_progress)
        self.completed_steps=QLabel();self.completed_steps.setWordWrap(True)
        self.completed_steps.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.completed_steps.setStyleSheet('color:#325572;background:#edf3f6;padding:6px;border-radius:4px;')
        self.completed_steps.hide()
        self.status_scroll=QScrollArea();self.status_scroll.setWidgetResizable(True);self.status_scroll.setMinimumHeight(24)
        self.status_scroll.setWidget(self.completed_steps);status_layout.addWidget(self.status_scroll,1);self.status_scroll.hide()
        self.workspace_splitter.addWidget(status_host)
        self.workspace_splitter.setCollapsible(0,False);self.workspace_splitter.setCollapsible(1,False)
        saved=self.preferences.values.get('splitter/status-v2',[self.height()-80,68])
        self.workspace_splitter.setSizes([max(200,self.height()-min(saved[1],110)),min(saved[1],110)])
        self.workspace_splitter.setStretchFactor(0,1);self.workspace_splitter.setStretchFactor(1,0)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setCentralWidget(outer)
        self.make_scan()
        self.make_system()
        self.make_cleanup()
        self.make_schedule()
        self.make_log()
        self.make_history()
        self.tabs.currentChanged.connect(lambda index: self.history_panel.ensure_loaded()
                                         if self.tabs.widget(index).isAncestorOf(self.history_panel) else None)
        QTimer.singleShot(0, self.history_panel.ensure_loaded)
        self.refresh_profiles()
        self.refresh_history()
        self.refresh_plans()
        self.refresh_schedules()
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        menu.addAction("Mở app", self.bring_to_front)
        menu.addAction("Quét profile đang chọn", self.begin_scan)
        menu.addAction("Thoát", self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.bring_to_front() if reason in
            (QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.messageClicked.connect(self.bring_to_front)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_background)
        self.poll_timer.start(30000)
        self.realtime_timer = QTimer(self); self.realtime_timer.timeout.connect(self.poll_realtime)
        self.realtime_timer.start(3000)
        self.scan_state_timer = QTimer(self); self.scan_state_timer.timeout.connect(self.poll_scan_state); self.scan_state_timer.start(500)
        self.last_scan_id = self.latest_id()
        self.last_scan_signature = self.latest_signature()
        self.last_plan_count = 0
        self.refresh_background()
        from .conditional_tooltips import install_tooltips
        self.conditional_tooltips=install_tooltips(self)

    def bring_to_front(self):
        # Hidden/maximized windows retain their previous state. One handler is
        # shared by the tray menu, icon, and desktop notification click.
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show();self.raise_();self.activateWindow()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'browser'):
            # Explanations scroll inside their own box. Essential controls and
            # the table footer never disappear just because help auto-opens.
            self.browser.compare_help.scroll.setFixedHeight(24 if self.height()<710 else 42)

    def add_tab(self, title, *, scroll=True):
        tab = QWidget()
        layout = QVBoxLayout(tab);layout.setContentsMargins(5,4,5,4);layout.setSpacing(3)
        if scroll:
            viewport=QScrollArea();viewport.setWidgetResizable(True)
            content=QWidget();content_layout=QVBoxLayout(content)
            viewport.setWidget(content);layout.addWidget(viewport);layout=content_layout
        self.tabs.addTab(tab, title)
        return layout

    def button(self, label, fn, row):
        button = QPushButton(label)
        if label.startswith(('Xóa','Gỡ','Dừng')):button.setProperty('actionRole','danger')
        button.clicked.connect(lambda checked=False: fn())
        row.addWidget(button)
        return button

    def job(self, fn, done=None, scan_job=False):
        if scan_job and self.scan_worker is not None:
            self.status.setText("Đang có lượt quét; Dừng và chờ hoàn tất trước khi quét lại.")
            return None
        worker = Worker(fn)
        self.workers.append(worker)
        def event_message(event,data):
            if event=='task-progress':
                if data.get('task')=='tree':return
                if self.scan_worker is not None and not scan_job:return
                if self.progress_worker is not None and self.progress_worker is not worker and self.progress_worker.isRunning() and not scan_job:return
                self.progress_worker=worker;self.progress(event,data);return
            if event=='uac-state':
                if data['phase']=='prompt':self.admin_worker=worker
                elif data['phase']=='done' and self.admin_worker is worker:self.admin_worker=None
            if event=='uac-state' or scan_job or self.scan_worker is None:self.progress(event,data)
        worker.event.connect(event_message)
        worker.error.connect(lambda msg: self.fail(msg))
        def result(value):
            try:
                (done or (lambda v: self.show_text(v)))(value)
            except Exception as exc:
                self.fail(str(exc))
        worker.result.connect(result)
        def finished():
            if self.progress_worker is worker:self.progress_worker=None
            if worker in self.workers:
                self.workers.remove(worker)
            if self.scan_worker is worker:
                self.scan_worker = None
                self.set_scan_busy(False)
            worker.deleteLater()
        worker.finished.connect(finished)
        if scan_job:
            self.scan_worker = worker
            self.set_scan_busy(True)
        worker.start()
        return worker

    def fail(self, message):
        import logging
        logging.getLogger("dgm").error("GUI %s", message)
        self.status.setText("Lỗi: " + message.splitlines()[0])
        if self.scan_worker is not None:
            self.scan_progress.setRange(0, 100); self.scan_progress.setFormat('%p% • Lỗi ở bước hiện tại')
        QMessageBox.warning(self, "Không hoàn tất", message)

    def set_scan_busy(self, busy):
        blocked=busy or self.admin_busy
        for control in (self.scan_button, self.rescan_button, self.profile_combo, self.engine, self.roots,
                        self.excludes, self.strict, self.threshold, self.new_profile_button, self.save_profile_button,
                        self.reload_profile_button, self.run_schedule_button, self.benchmark_button):
            control.setEnabled(not blocked)
        self.mft.setEnabled(not blocked and not self.strict.isChecked() and self.engine.currentText()=='wiztree')
        self.stop_button.setEnabled(busy)
        if busy and not self._scan_ui_busy:
            self.task_progress_id=None;self.scan_progress.setRange(0,100);self.scan_progress.setValue(0)
            self.scan_progress.setFormat('0% • Chuẩn bị lượt quét');self.completed_steps.clear();self.completed_steps.hide();self.status_scroll.hide();self.last_scan_brief=''
        self._scan_ui_busy=busy

    def set_admin_busy(self,busy):
        self.admin_busy=busy;self.set_scan_busy(self.scan_worker is not None)
        self.admin_stop_button.setVisible(busy);self.admin_stop_button.setEnabled(busy)
        for button in self.system_check_buttons+self.cleanup_action_buttons:button.setEnabled(not busy)
        self.render_background()

    def cancel_admin(self):
        if self.admin_worker is not None:
            self.admin_worker.stop.set();self.admin_stop_button.setEnabled(False)
            self.status.setText('Đã yêu cầu dừng tác vụ Admin; đang chờ kết thúc an toàn. DISM không bị cưỡng bức tắt.')

    def help_box(self, layout, key, text):
        box = HelpBox(key, text, self.preferences)
        layout.addWidget(box); self.help_boxes[key] = box
        return box

    def columns(self, table, key, widths):
        self.column_specs[key] = (table, widths)
        interactive_columns(table, widths, self.preferences, key)

    def save_layout(self):
        for key, (table, widths) in self.column_specs.items():
            header = table.header() if isinstance(table, QTreeWidget) else table.horizontalHeader()
            self.preferences.set("columns/" + key, [header.sectionSize(i) for i in range(len(widths))])
        self.preferences.set("splitter/scan-v3", self.scan_splitter.sizes())
        self.preferences.set('splitter/status-v1',self.workspace_splitter.sizes())
        self.preferences.set('splitter/status-v2',self.workspace_splitter.sizes())

    def reset_layout(self):
        for key, (table, widths) in self.column_specs.items():
            self.preferences.set("columns/" + key, widths)
            interactive_columns(table, widths, self.preferences, key)
        self.scan_splitter.setSizes([sum(self.tree.default_widths())+8, 850]); self.tree.fit_columns()
        self.workspace_splitter.setSizes([max(200,self.height()-80),68])
        self.status.setText("Đã khôi phục chiều rộng cột mặc định.")

    def show_text(self, value, title="Kết quả"):
        text = value if isinstance(value, str) else display_json(value)
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text[:2000])
        box.setDetailedText(text)
        box.exec()

    def progress(self, event, data):
        if event=='task-progress':
            ident=data.get('id',data.get('task'))
            if ident!=self.task_progress_id:self.task_progress_id=ident;self.scan_progress.setValue(0)
            value=max(self.scan_progress.value(),min(100,int(data.get('percent',0))))
            self.scan_progress.setRange(0,100);self.scan_progress.setValue(value)
            self.scan_progress.setFormat('%p% • '+data.get('stage',''))
            self.scan_progress.setToolTip(data.get('basis','')+'\n'+data.get('detail',''))
            self.status.setText(data.get('stage','')+(' • '+data['detail'] if data.get('detail') else ''))
            items=data.get('completed',[])
            prefix=self.last_scan_brief+'\n' if self.last_scan_brief and data.get('task') in ('comparison','details') else ''
            self.completed_steps.setText(prefix+'Đã xong: '+' • '.join(items));self.completed_steps.setToolTip(self.completed_steps.text());self.completed_steps.setVisible(bool(prefix or items))
            self.status_scroll.setVisible(bool(prefix or items))
            return
        if event=='uac-state':
            self.set_admin_busy(data['phase']!='done')
            if self.scan_worker is not None and data['phase'] != 'done':
                self.scan_progress.setFormat('%p% • Chờ xác nhận quyền Admin')
            return
        if event=='cancelled':
            self.status.setText(data['message'])
            if self.scan_worker is not None:
                self.scan_progress.setFormat('%p% • Đã yêu cầu dừng')
            return
        if event == "progress":
            if not (self.scan_worker and self.scan_worker.stop.is_set()):
                self.status.setText(f"{data['count']:,}" + (f"/{data['total']:,}" if data.get('total') else "") + f" mục • {data['path']}")
                if self.scan_worker is not None and self.task_progress_id is None:
                    total = data.get("total")
                    if total:
                        self.scan_progress.setRange(0,100); self.scan_progress.setValue(min(99,data['count']*100//total))
                        self.scan_progress.setFormat(f"%p% • {data['count']:,}/{total:,} mục")
                    else:
                        self.scan_progress.setFormat(f"%p% • Đã đọc {data['count']:,} mục")
        elif event == "info":
            if not (self.scan_worker and self.scan_worker.stop.is_set()):
                self.status.setText(data["message"])
                if self.scan_worker is not None:
                    self.scan_progress.setFormat('%p% • '+data["message"])
        elif event == "started":
            if self.scan_worker is not None:
                self.scan_progress.setFormat('%p% • Đã tạo mốc quét')
            s=Store(readonly=True)
            try:label=scan_name(s.scan(data['scan_id']))
            finally:s.close()
            self.scope_note.setText(f"Công cụ thực tế: {data['engine']} • {label}")
        elif event == "overview" and data.get("trusted"):
            self.browser.metrics[1].setText("WIZTREE — TỔNG QUAN ĐÃ CÓ\n"+size(data["logical"])+"\nChi tiết đang nhập nền")

    def make_scan(self):
        host_layout = self.add_tab("Kiểm tra",scroll=False)
        self.scan_page_scroll=QScrollArea();self.scan_page_scroll.setWidgetResizable(True)
        self.scan_page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        canvas=QWidget();outer_layout=QVBoxLayout(canvas);outer_layout.setContentsMargins(0,0,0,0);outer_layout.setSpacing(2)
        self.scan_page_scroll.setWidget(canvas);host_layout.addWidget(self.scan_page_scroll)
        canvas.setObjectName('scanCanvas');canvas.setMinimumWidth(1120)
        self.scan_canvas=canvas
        configuration = QWidget();configuration.setObjectName('scanConfiguration');configuration.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        layout = QVBoxLayout(configuration); layout.setContentsMargins(5, 1, 3, 1);layout.setSpacing(3)
        configuration.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        row = QHBoxLayout();row.setSpacing(4)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(100);self.profile_combo.setMaximumWidth(135)
        self.profile_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.currentTextChanged.connect(self.load_profile)
        row.addWidget(QLabel("Cấu hình")); row.addWidget(self.profile_combo)
        self.profile_combo.setToolTip("Một bộ lựa chọn gồm phạm vi, công cụ, loại trừ và ngưỡng hiển thị.")
        self.new_profile_button = self.button("Tạo", self.new_profile, row)
        self.new_profile_button.setToolTip("Tạo cấu hình có tên mới từ phạm vi đang chọn; công cụ mặc định là WizTree.")
        self.save_profile_button = self.button("Lưu", self.save_profile, row)
        self.save_profile_button.setToolTip("Lưu lựa chọn để dùng lại và cho lịch chạy. Quét ngay không tự ghi đè cấu hình.")
        self.reload_profile_button = self.button("Nạp", self.refresh_profiles, row)
        self.reload_profile_button.setToolTip("Đọc lại cấu hình đã lưu; hỏi Lưu/Bỏ thay đổi/Hủy nếu bạn đang sửa chưa lưu.")
        layout.addLayout(row)
        profile_help=self.help_box(layout, "profile", "Cấu hình quét là bộ lựa chọn để dùng lại. Tạo mới: đặt tên cho bộ lựa chọn mới. Lưu cấu hình: ghi lựa chọn trên màn hình để các lịch dùng nó. Nạp lại: đọc bản đã lưu. Quét ngay/Quét lại dùng lựa chọn hiện tại nhưng không tự ghi đè cấu hình. Khi đổi cấu hình, app hỏi nếu còn thay đổi chưa lưu.")
        row.addWidget(profile_help.toggle)
        self.scan_options = QWidget(); options_layout = QVBoxLayout(self.scan_options); options_layout.setContentsMargins(0, 0, 0, 0)
        toolrow = QHBoxLayout()
        self.engine = QComboBox(); self.engine.addItems(["wiztree", "native", "windirstat", "gdu"])
        self.engine.setFixedWidth(92)
        self.engine.setToolTip("WizTree là mặc định. Native duyệt thư mục và bảo đảm không đọc vùng Chrome/Edge bị loại trừ.")
        row.addWidget(QLabel("Công cụ")); row.addWidget(self.engine)
        self.strict = QCheckBox("Không đọc/quét Chrome/Edge"); self.strict.setChecked(True)
        self.strict.setToolTip("Không stat/đọc metadata/đi vào thư mục trình duyệt. Cần chọn native, không chỉ lọc kết quả WizTree.")
        toolrow.addWidget(self.strict)
        self.mft = QCheckBox("MFT nhanh toàn ổ (Admin)"); toolrow.addWidget(self.mft)
        self.mft.setToolTip("Đọc chỉ mục NTFS. Khi cần quyền Admin, app tự hiện UAC; bạn bấm Yes để tiếp tục. Không tương thích cấm đọc Chrome/Edge.")
        self.strict.toggled.connect(lambda checked: self.mft.setChecked(False) if checked else None)
        self.mft.setEnabled(False)
        options_layout.addLayout(toolrow)
        self.scope_note = QLabel(); self.scope_note.setWordWrap(True);self.scope_note.hide()
        for control in (self.engine,):
            control.currentTextChanged.connect(self.update_scope_note)
        self.strict.toggled.connect(self.update_scope_note); self.mft.toggled.connect(self.update_scope_note)
        root_row = row
        self.roots = QLineEdit(); self.roots.setPlaceholderText("Nhiều ổ/folder, phân cách bằng dấu ;")
        root_row.addWidget(QLabel("Phạm vi"));root_row.addWidget(self.roots,1)
        self.roots.setMinimumWidth(180);self.roots.setMaximumWidth(360)
        pick=self.button("",self.pick_root,root_row);pick.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon));pick.setToolTip('Chọn thư mục/ổ quét')
        self.excludes = QLineEdit(); self.excludes.setPlaceholderText("Đường dẫn/glob loại trừ, phân cách ;")
        self.excludes.setMinimumWidth(160);self.excludes.setMaximumWidth(300)
        self.threshold = QDoubleSpinBox(); self.threshold.setRange(0, 1000000); self.threshold.setValue(10); self.threshold.setSuffix(" MB")
        self.threshold.setDecimals(0)
        self.threshold.setFixedWidth(105);toolrow.setSpacing(6)
        toolrow.addWidget(QLabel("File lớn hơn")); toolrow.addWidget(self.threshold)
        toolrow.addWidget(QLabel("Loại trừ thêm"));toolrow.addWidget(self.excludes,1)
        expand=self.button('',self.edit_excludes,toolrow);expand.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown));expand.setToolTip('Mở rộng danh sách loại trừ, mỗi dòng một đường dẫn / glob')
        self.threshold.setToolTip("Chỉ lọc file trong bảng. Vẫn lưu metadata file nhỏ để tính tổng thư mục và phát hiện file tăng vượt ngưỡng.")
        scan_help=self.help_box(options_layout, "scan", "Cấu hình quét lưu phạm vi/công cụ/loại trừ/ngưỡng. Lưu cấu hình để lịch chạy dùng lựa chọn mới; Quét ngay chỉ dùng lựa chọn trên màn hình. WizTree/MFT đọc metadata toàn ổ nên không đáp ứng cấm đọc Chrome/Edge. Giữ bảo vệ thì chọn native. Ngưỡng 10 MB chỉ lọc bảng file, không bỏ dung lượng file nhỏ khỏi tổng thư mục.")
        toolrow.addWidget(scan_help.toggle);toolrow.addStretch()
        self.scan_options_toggle=scan_help.toggle
        layout.addWidget(self.scan_options)
        self.configuration_scroll=configuration
        outer_layout.addWidget(configuration)
        layout = outer_layout
        actions = row
        self.scan_button = self.button("Quét ngay", self.begin_scan, actions)
        self.scan_button.setProperty('actionRole','primary');self.scan_button.setObjectName('scanNow')
        self.scan_button.setToolTip('Bắt đầu quét với phạm vi và các lựa chọn đang hiển thị; không tự xóa file.')
        self.stop_button = self.button("Dừng", self.cancel_scan, actions); self.stop_button.setEnabled(False)
        self.stop_button.setToolTip("Hủy lượt quét của app, chờ giải phóng tiến trình/khóa rồi mới Quét lại. Không đóng WizTree mở riêng.")
        self.rescan_button = self.button("Quét lại", self.begin_scan, actions)
        self.button("Bố cục", self.reset_layout, actions).setToolTip('Khôi phục kích thước cột và hai khung kết quả mặc định')
        row.addStretch()
        self.background_summary = QLabel("Bộ chạy nền: đang kiểm tra...")
        self.background_summary.hide()
        background_controls=self.make_background_controls(layout,compact=True)
        self.button("Lịch chạy",lambda:self.tabs.setCurrentIndex(3),background_controls)
        background_controls.addStretch()
        from .browser_ui import DiskBrowser
        self.browser = DiskBrowser(self); layout.addWidget(self.browser, 1)
        self.older, self.newer = self.browser.older, self.browser.newer
        self.tree, self.table, self.scan_splitter = self.browser.tree, self.browser.table, self.browser.splitter
        self.kind, self.change, self.search = self.browser.kind, self.browser.change, self.browser.search
        self.scan_info, self.page_label = self.browser.info, self.browser.page_label

    def toggle_scan_options(self, expanded):
        # Only explanations collapse; essential scan controls always stay visible.
        self.scan_options.setVisible(True)
        self.help_boxes['scan'].show_transient(expanded)

    def refresh_profiles(self):
        current = self.profile_combo.currentText()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear(); self.profile_combo.addItems(load()["profiles"])
        self.profile_combo.setCurrentText(current if current in load()["profiles"] else "daily")
        self.profile_combo.blockSignals(False)
        self.load_profile(self.profile_combo.currentText())
        if hasattr(self, "schedule_profile"):
            old = self.schedule_profile.currentText()
            self.schedule_profile.clear(); self.schedule_profile.addItems(load()["profiles"])
            self.schedule_profile.setCurrentText(old)

    def load_profile(self, name):
        if not name:
            return
        if self.loaded_profile and self.loaded_values is not None and self.profile_dirty():
            choice = QMessageBox.question(self, "Cấu hình chưa lưu", "Lưu các thay đổi của cấu hình đang sửa trước khi chuyển/nạp lại?",
                                          QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if choice == QMessageBox.StandardButton.Cancel:
                self.profile_combo.blockSignals(True); self.profile_combo.setCurrentText(self.loaded_profile); self.profile_combo.blockSignals(False)
                return
            if choice == QMessageBox.StandardButton.Save and not self.save_profile(name=self.loaded_profile):
                self.profile_combo.blockSignals(True); self.profile_combo.setCurrentText(self.loaded_profile); self.profile_combo.blockSignals(False)
                return
        p = profile(name)
        self.roots.setText(";".join(p["roots"]))
        self.excludes.setText(";".join(p.get("excludes", [])))
        self.strict.setChecked(p.get("strict_browsers", False))
        self.engine.setCurrentText(p.get("engine", "wiztree"))
        self.mft.setChecked(bool(p.get('mft') and p.get('engine','wiztree')=='wiztree' and not p.get('strict_browsers')))
        self.threshold.setValue(p["threshold_mb"])
        self.loaded_profile = name; self.loaded_values = self.edited_profile()
        self.update_scope_note()
        self.refresh_history()
        self.compare()

    def profile_dirty(self):
        try:
            return self.edited_profile() != self.loaded_values
        except ValueError:
            return True

    def update_scope_note(self, *_):
        enabled=not self.strict.isChecked() and self.engine.currentText()=='wiztree'
        self.mft.setEnabled(enabled and not getattr(self,'scan_worker',None))
        if not enabled:self.mft.setChecked(False)
        if self.strict.isChecked() and self.engine.currentText() != "native":
            text = "Cần chọn native để bảo đảm không đọc/quét Chrome/Edge; app sẽ hỏi trước khi đổi công cụ."
        else:
            text = f"Công cụ đã chọn: {self.engine.currentText()} • " + ("Chrome/Edge: không đọc/quét" if self.strict.isChecked() else "Chrome/Edge: không được loại trừ trong cấu hình này")
            if self.mft.isChecked():
                text += " • MFT: tự hỏi UAC khi chạy nếu chưa có quyền Admin"
        self.scope_note.setText(text)

    def edited_profile(self):
        roots = [x.strip() for x in self.roots.text().split(";") if x.strip()]
        if not roots:
            raise ValueError("Chọn ít nhất một ổ/thư mục")
        return {"roots": roots, "excludes": [x.strip() for x in self.excludes.text().split(";") if x.strip()],
                "strict_browsers": self.strict.isChecked(), "engine": self.engine.currentText(), "threshold_mb": self.threshold.value(), "mft": self.mft.isChecked()}

    def save_profile(self, name=None):
        try:
            if self.strict.isChecked() and self.engine.currentText()!='native':
                raise ValueError('Bảo vệ Chrome/Edge chỉ hỗ trợ native. Chọn native hoặc bỏ bảo vệ trước khi lưu.')
            cfg = load(); cfg["profiles"][name or self.profile_combo.currentText()] = self.edited_profile(); save(cfg)
            self.loaded_values = self.edited_profile()
            self.status.setText("Đã lưu cấu hình quét; lịch chạy dùng cấu hình đã lưu.")
            return True
        except Exception as exc:
            self.fail(str(exc))
            return False

    def new_profile(self):
        name, ok = QInputDialog.getText(self, "Cấu hình quét mới", "Tên cấu hình (ví dụ: ổ-C-hằng-ngày)")
        if ok and name.strip():
            cfg = load()
            if name in cfg["profiles"]:
                return self.fail("Tên cấu hình đã tồn tại")
            cfg["profiles"][name] = {**self.edited_profile(), "engine": "wiztree"}; save(cfg)
            self.refresh_profiles(); self.profile_combo.setCurrentText(name)

    def edit_excludes(self):
        dialog=QDialog(self);dialog.setWindowTitle('Loại trừ thêm — mỗi dòng một quy tắc');dialog.resize(650,330)
        layout=QVBoxLayout(dialog);layout.addWidget(QLabel('Đường dẫn hoặc glob; áp dụng cho lần quét tiếp theo, không sửa lịch sử.'))
        editor=QPlainTextEdit();editor.setPlainText('\n'.join(x.strip() for x in self.excludes.text().split(';') if x.strip()));layout.addWidget(editor)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            rules=list(dict.fromkeys(x.strip() for x in editor.toPlainText().splitlines() if x.strip()))
            self.excludes.setText(';'.join(rules));self.excludes.setToolTip('\n'.join(rules) or 'Không có loại trừ thêm')

    def pick_root(self):
        path = QFileDialog.getExistingDirectory(self, "Chọn phạm vi")
        if path:
            old = self.roots.text().strip()
            self.roots.setText(old + (";" if old else "") + path)

    def begin_scan(self):
        if getattr(self,'update_in_progress',False):
            self.status.setText('Đang cập nhật app; chờ hoàn tất hoặc hủy cập nhật trước khi quét.');return
        if self.scan_worker is not None:
            return
        try:
            from .scanner import scan
            if self.strict.isChecked() and self.engine.currentText() != "native":
                if QMessageBox.question(self, "Công cụ và phạm vi không tương thích", "Bạn đang yêu cầu không đọc/quét Chrome/Edge. WizTree/MFT không bảo đảm điều này. Chuyển lựa chọn trên màn hình sang native để quét? Không thay đổi cấu hình đã lưu.") != QMessageBox.StandardButton.Yes:
                    return
                self.engine.setCurrentText("native")
            p, name = self.edited_profile(), self.profile_combo.currentText()
            self.status.setText("Đang bắt đầu quét...")
            self.job(lambda worker: scan(name, p, worker.stop, worker.event.emit), self.scan_done, scan_job=True)
        except Exception as exc:
            self.fail(str(exc))

    def cancel_scan(self):
        if self.scan_worker is not None:
            self.scan_worker.stop.set()
            self.stop_button.setEnabled(False)
            self.status.setText("Đang dừng tiến trình và lưu trạng thái; chờ nút Quét lại sáng lên...")
        else:
            try:
                from .scan_control import request_stop
                result = request_stop(); self.status.setText(result["message"])
            except Exception as exc:
                self.fail(str(exc))

    def poll_scan_state(self):
        if self.scan_worker is not None:
            return
        from .scan_control import current
        try:
            state = current()
            self.stop_button.setEnabled(state["running"])
            self.scan_button.setEnabled(not state["running"] and not self.admin_busy); self.rescan_button.setEnabled(not state["running"] and not self.admin_busy)
        except Exception:
            pass

    def scan_done(self, result):
        if result.get('before_start'):
            self.status.setText(result.get('message','Đã hủy cấp quyền; tác vụ chưa chạy.'))
            self.scan_progress.setRange(0, 100); self.scan_progress.setValue(0); self.scan_progress.setFormat("Chưa bắt đầu quét")
            return
        status = {"cancelled": "Đã dừng", "failed": "Không hoàn tất", "complete": "Hoàn tất", "partial": "Hoàn tất • có giới hạn"}.get(result["status"], result["status"])
        if result["status"] == "partial":
            if result.get("coverage_unverified"):
                status += " (engine ngoài: chưa xác minh toàn phạm vi)"
            elif result.get("errors"):
                status += f" ({result['errors']:,} mục/vùng không đọc được)"
            elif result.get("recovered"):
                status += " (phục hồi metadata, không quét mới)"
        if result["status"] in ("complete", "partial"):
            self.scan_progress.setRange(0, 100); self.scan_progress.setValue(100)
            self.scan_progress.setFormat(f"100% • Đã lưu {result['files']:,} file")
        else:
            self.scan_progress.setRange(0, 100); self.scan_progress.setFormat('%p% • '+status)
        store=Store(readonly=True)
        try:
            try: label=scan_name(store.scan(result['scan_id']))
            except ValueError: label="Lượt quét"
        finally: store.close()
        from .presentation import storage_delta
        storage_text=storage_delta(result.get("storage"))
        outcome=f"Đã đọc/lưu {result['files']:,} file, {result.get('folders',0):,} thư mục/liên kết" if result['status'] in ('complete','partial') else status
        if result.get('errors'):outcome+=f" • {result['errors']:,} mục/vùng không đọc được"
        self.status.setText(f"{label}: {outcome} • {whole(result['seconds'])}s • Δ data/log {storage_text}")
        self.status.setToolTip(result.get('coverage_note',''))
        if result.get('completed_steps'):
            self.last_scan_brief=f"Lượt quét vừa chạy: {outcome} • tổng {size(result.get('logical'))} • đã tổng hợp dung lượng và lưu lịch sử/data/log."
            self.completed_steps.setText(self.last_scan_brief+'\nĐã xong: '+' • '.join(result['completed_steps']));self.completed_steps.setToolTip(self.completed_steps.text());self.completed_steps.show()
            self.status_scroll.show()
        self.refresh_history()
        if result["status"] in ("complete", "partial"):
            self.newer.setCurrentIndex(self.newer.findData(result["scan_id"]))
            self.compare()
        elif result["status"] == "failed":
            self.fail(result.get("error", "Quét thất bại; xem Nhật ký."))
        self.tray.showMessage('Đã lưu kết quả quét' if result['status'] in ('complete','partial') else status, self.status.text())
        if result.get("alerts"):
            self.tray.showMessage("Cảnh báo dung lượng", "\n".join(result["alerts"]))

    def latest_id(self):
        return self.latest_signature()[0]

    def latest_signature(self):
        s = Store(readonly=True)
        row = s.db.execute("SELECT id,status FROM scans ORDER BY id DESC LIMIT 1").fetchone(); s.close()
        return (row[0], row[1]) if row else (0, "")

    def refresh_history(self):
        self.browser.refresh(self.profile_combo.currentText())
        if hasattr(self,'history_panel'):self.history_panel.invalidate()

    def swap_snapshots(self):
        a, b = self.older.currentData(), self.newer.currentData()
        if a and b:
            self.older.setCurrentIndex(self.older.findData(b)); self.newer.setCurrentIndex(self.newer.findData(a))

    def page(self, direction):
        self.browser.load_more() if direction > 0 else self.browser.reload()

    def compare(self, reset=True):
        self.browser.compare()

    def expand_folder(self, item):
        self.browser.expand(item)

    def context_table(self, point):
        self.browser.table_menu(point)

    def context_tree(self, point):
        self.browser.tree_menu(point)

    def path_menu(self, path, position, extra=None):
        menu = QMenu(self)
        if extra:
            menu.addAction('Xem dữ liệu hai mốc',extra);menu.addSeparator()
        for label, action in [("Mở", "open"), ("Mở thư mục chứa", "reveal"), ("Properties", "properties"), ("Đổi tên", "rename"), ("Đưa vào Thùng rác", "trash")]:
            act = menu.addAction(label)
            act.setEnabled(Path(path).exists())
            act.triggered.connect(lambda checked=False, a=action: self.file_action(path, a))
        menu.addAction("Sao chép đường dẫn", lambda: QApplication.clipboard().setText(path))
        menu.exec(position)

    def file_action(self, path, action):
        try:
            from .platform_ops import manual_action
            name = None
            if action == "rename":
                name, ok = QInputDialog.getText(self, "Đổi tên", path, text=Path(path).name)
                if not ok:
                    return
            if action in ("rename", "trash"):
                note = "Đưa vào Thùng rác trên cùng ổ chưa giải phóng dung lượng.\n" if action == "trash" else ""
                if QMessageBox.question(self, "Xác nhận", note + f"{action}: {path}") != QMessageBox.StandardButton.Yes:
                    return
            manual_action(path, action, name)
            self.status.setText(f"Đã thực hiện {action}; quét lại để cập nhật snapshot")
        except Exception as exc:
            self.fail(str(exc))

    def export(self):
        scan_id = self.newer.currentData()
        if not scan_id:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Xuất snapshot", f"scan-{scan_id}.csv", "CSV (*.csv)")
        if path:
            def export(_):
                s=Store(readonly=True)
                try:s.export(scan_id,path)
                finally:s.close()
                return path
            self.job(export,lambda value:self.status.setText("Đã xuất "+value))

    def delete_snapshot(self):
        scan_id = self.newer.currentData()
        if not scan_id:return
        store=Store(readonly=True)
        try: label=scan_label(store.scan(scan_id))
        finally: store.close()
        if QMessageBox.question(self, "Xóa lượt quét", f"Xóa metadata của {label} và các cache so sánh phụ thuộc? Không xóa file đã quét, cấu hình hay log.") == QMessageBox.StandardButton.Yes:
            def remove(_):
                cache=Comparisons()
                try:cache.delete_for_scan(scan_id)
                finally:cache.close()
                s=Store()
                try:s.delete_scan(scan_id)
                finally:s.close()
                return True
            self.job(remove,lambda _:(self.refresh_history(),self.compare()))

    def make_system(self):
        layout = self.add_tab("Hệ thống")
        from .update_ui import AppUpdatePanel
        self.app_update_panel=AppUpdatePanel(self);layout.addWidget(self.app_update_panel)
        from .storage_ui import StoragePanel
        self.storage_panel=StoragePanel(self);layout.addWidget(self.storage_panel)
        row = QHBoxLayout()
        self.button("Làm mới phiên bản / môi trường", self.doctor, row)
        self.update_all_button = self.button("Kiểm tra cập nhật tất cả", lambda: self.check_updates(), row)
        self.update_all_button.setToolTip("Chỉ tra phiên bản từ nhà phát hành/PyPI/GitHub. Không cài hay sửa Python/pip hệ thống.")
        for label, tool in [("WinSxS Analyze", "winsxs"), ("DriverStore / PnPUtil", "drivers"), ("Shadow storage", "shadowstorage")]:
            self.system_check_buttons.append(self.button(label, lambda checked=False, t=tool: self.system_check(t), row))
        self.system_check_buttons.append(self.button('Thử UAC / quyền Admin',self.test_admin,row))
        layout.addLayout(row)
        self.environment_note = QLabel(); self.environment_note.setWordWrap(True); layout.addWidget(self.environment_note)
        self.package_table = QTableWidget(0, 6)
        self.package_table.setHorizontalHeaderLabels(["Gói / công cụ", "Đang dùng", "Mới nhất", "Trạng thái", "Đường dẫn", "Thao tác"])
        self.package_table.setAlternatingRowColors(True)
        self.columns(self.package_table, "packages", [210, 115, 110, 260, 310, 290])
        layout.addWidget(self.package_table, 1)
        self.help_box(layout, "system", "Icon phân biệt công cụ quét, Python riêng và thư viện của app. Kiểm tra cập nhật chỉ tra cứu, không tự cài. Nguồn mở trang nhà phát hành để xem bản mới; Python/thư viện vẫn được khóa bằng uv.lock trong môi trường riêng. Cài engine OSS chỉ cài bản đã pin vào .venv/tools, không phải tự nâng cấp lên bản mới nhất. Các kiểm tra DISM/PnPUtil ở đây chỉ phân tích, không xóa.")
        toolrow = QHBoxLayout()
        self.tool_kind = QComboBox(); self.tool_kind.addItems(["wiztree", "windirstat", "gdu"])
        self.tool_path = QLineEdit()
        toolrow.addWidget(self.tool_kind); toolrow.addWidget(self.tool_path)
        self.button("Chọn / Lưu executable", self.pick_engine, toolrow)
        self.button("Cài engine OSS portable", self.install_engines, toolrow)
        layout.addLayout(toolrow)
        row2 = QHBoxLayout()
        self.bench_repeats = QSpinBox(); self.bench_repeats.setRange(1, 10); self.bench_repeats.setValue(3)
        self.bench_engines = QLineEdit("native,wiztree")
        row2.addWidget(QLabel("Đo cấu hình quét đang chọn")); row2.addWidget(self.bench_engines); row2.addWidget(self.bench_repeats)
        self.benchmark_button = self.button("Đo thời gian / tài nguyên", self.begin_benchmark, row2)
        layout.addLayout(row2)
        settings = QHBoxLayout()
        self.retention = QSpinBox(); self.retention.setRange(2, 3650); self.retention.setValue(load()["retention_days"])
        self.max_snapshots = QSpinBox(); self.max_snapshots.setRange(2, 10000); self.max_snapshots.setValue(load()["max_snapshots"])
        self.db_limit = QSpinBox(); self.db_limit.setRange(50, 50000); self.db_limit.setValue(load()["database_limit_mb"])
        for label, box in [("Giữ ngày", self.retention), ("Tối đa snapshot/profile", self.max_snapshots), ("Giới hạn DB MB", self.db_limit)]:
            settings.addWidget(QLabel(label)); settings.addWidget(box)
        self.button("Lưu giới hạn", self.save_limits, settings)
        layout.addLayout(settings)
        sources=QHBoxLayout()
        self.source_limit=QSpinBox();self.source_limit.setRange(0,10000);self.source_limit.setValue(load().get('source_archive_limit_mb',512))
        self.source_days=QSpinBox();self.source_days.setRange(0,3650);self.source_days.setValue(load().get('source_archive_retention_days',90))
        sources.addWidget(QLabel('CSV WizTree nén: tối đa MB'));sources.addWidget(self.source_limit)
        sources.addWidget(QLabel('Giữ tối đa ngày'));sources.addWidget(self.source_days)
        sources.addWidget(QLabel('0 = không giữ nguồn; lịch sử vẫn lưu.'));sources.addStretch();layout.addLayout(sources)
        self.system_details_toggle = QToolButton(); self.system_details_toggle.setText("> Chẩn đoán chi tiết"); self.system_details_toggle.setCheckable(True)
        layout.addWidget(self.system_details_toggle)
        self.system_output = QPlainTextEdit(); self.system_output.setReadOnly(True); self.system_output.setMaximumHeight(170); self.system_output.hide(); layout.addWidget(self.system_output)
        self.system_details_toggle.toggled.connect(self.system_output.setVisible)
        self.doctor()

    def doctor(self):
        from .diagnostics import doctor
        from .updates import inventory
        info = doctor()
        self.system_output.setPlainText(json.dumps(info, ensure_ascii=False, indent=2))
        self.environment_note.setText(f"App {info['app_version']} • Python {info['python'].split()[0]} • Môi trường riêng: {'Có' if info['isolated'] else 'Không'} • Quyền: {'Administrator' if info['admin'] else 'Thông thường'}")
        self.package_rows = inventory(); self.package_table.setRowCount(len(self.package_rows))
        self.update_buttons = []
        for i, item in enumerate(self.package_rows):
            for j, value in enumerate([item["name"], item["installed"], "—", "Chưa kiểm tra" if item["checkable"] else "Runtime khóa riêng", item["path"]]):
                cell = QTableWidgetItem(str(value)); cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable); cell.setToolTip(str(value))
                if j == 0:
                    icon = QStyle.StandardPixmap.SP_DriveHDIcon if item["kind"] == "engine" else QStyle.StandardPixmap.SP_ComputerIcon if item["kind"] == "runtime" else QStyle.StandardPixmap.SP_FileIcon
                    cell.setIcon(self.style().standardIcon(icon))
                self.package_table.setItem(i, j, cell)
            host = QWidget(); actions = QHBoxLayout(host); actions.setContentsMargins(2, 2, 2, 2)
            check = self.button("Kiểm tra", lambda name=item["id"]: self.check_updates([name]), actions); check.setEnabled(item["checkable"])
            self.update_buttons.append((check, item["checkable"]))
            self.button("Nguồn", lambda url=item["source"]: self.open_source(url), actions)
            self.button("Đường dẫn", lambda p=item["path"]: self.reveal_tool(p), actions)
            self.package_table.setCellWidget(i, 5, host)

    def open_source(self, url):
        import webbrowser
        webbrowser.open(url)

    def reveal_tool(self, path):
        try:
            from .platform_ops import open_item
            if not path:
                raise ValueError("Chưa cấu hình đường dẫn công cụ")
            open_item(path, "reveal")
        except Exception as exc:
            self.fail(str(exc))

    def check_updates(self, names=None):
        if getattr(self, "updating_versions", False):
            return
        from .updates import check_updates
        self.updating_versions = True; self.update_all_button.setEnabled(False)
        for button, _ in self.update_buttons:
            button.setEnabled(False)
        self.status.setText("Đang tra phiên bản từ nguồn chính thức; không cài/cập nhật gói...")
        def done(rows):
            for result in rows:
                index = next((i for i, item in enumerate(self.package_rows) if item["id"] == result["id"]), None)
                if index is not None:
                    self.package_table.item(index, 2).setText(result["latest"] or "—")
                    self.package_table.item(index, 3).setText(result["status"])
                    self.package_table.item(index, 3).setToolTip(result["status"])
            self.status.setText("Đã kiểm tra phiên bản. Không có gói nào được cài hoặc cập nhật.")
        worker = self.job(lambda _: check_updates(names), done)
        def finished():
            self.updating_versions = False; self.update_all_button.setEnabled(True)
            for button, enabled in self.update_buttons:
                button.setEnabled(enabled)
        worker.finished.connect(finished)

    def system_check(self, tool):
        from .diagnostics import system_check
        self.job(lambda worker: system_check(tool,notify=worker.event.emit,stop=worker.stop), self.system_result)

    def test_admin(self):
        from .elevation import probe
        self.job(lambda worker:probe(notify=worker.event.emit,stop=worker.stop),self.system_result)

    def system_result(self, value):
        self.system_output.setPlainText(display_json(value)); self.system_details_toggle.setChecked(True)
        self.status.setText('Tác vụ đã kết thúc an toàn sau yêu cầu dừng; xem kết quả bên dưới.' if value.get('cancel_requested') else 'Đã hoàn tất. Xem kết quả chi tiết trong tab Hệ thống.')

    def pick_engine(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn engine portable", "", "Executable (*.exe)")
        if path:
            cfg = load(); cfg["engines"][self.tool_kind.currentText()] = path; save(cfg); self.tool_path.setText(path); self.doctor()

    def save_limits(self):
        cfg = load(); cfg.update(retention_days=self.retention.value(), max_snapshots=self.max_snapshots.value(), database_limit_mb=self.db_limit.value(),
            source_archive_limit_mb=self.source_limit.value(),source_archive_retention_days=self.source_days.value()); save(cfg)
        self.status.setText("Đã lưu giới hạn lịch sử/database")

    def install_engines(self):
        from .install_engines import install
        if QMessageBox.question(self, "Cài công cụ riêng của app", "Cài WinDirStat/gdu bản đã pin vào .venv/tools? Không cài vào hệ thống và không tự cập nhật gói Python.") != QMessageBox.StandardButton.Yes:
            return
        self.job(lambda _: install(), lambda result: (self.doctor(), self.system_result(result)))

    def begin_benchmark(self):
        from .benchmark import benchmark
        p, name = self.edited_profile(), self.profile_combo.currentText()
        engines = [x.strip() for x in self.bench_engines.text().split(",") if x.strip()]
        if not engines:
            return self.fail("Chọn engine benchmark")
        self.status.setText("Đang đo; engine chạy lần lượt, không chạy đồng thời")
        repeats = self.bench_repeats.value()
        self.job(lambda worker: benchmark(name, p, engines, repeats, worker.event.emit, cancel=worker.stop), lambda value: (self.system_output.setPlainText(display_json(value)), self.system_details_toggle.setChecked(True)), scan_job=True)

    def make_cleanup(self):
        layout = self.add_tab("Xóa tự động")
        from .cleanup import rules
        layout.addWidget(QLabel("Tự nhận diện → xem đề xuất → bạn xác nhận. Cache xóa vĩnh viễn; công cụ Windows dùng quy trình riêng."))
        group = QGroupBox("Nhóm kiểm tra/dọn")
        rule_layout = QVBoxLayout(group)
        self.rule_boxes = {}
        for name, info in rules().items():
            box = QCheckBox(f"{name} — {info['title']}")
            box.setToolTip(info["risk"] + "\n" + "\n".join(info["roots"]))
            rule_layout.addWidget(box); self.rule_boxes[name] = box
        row = QHBoxLayout()
        row.addWidget(group)
        info = QPlainTextEdit(); info.setReadOnly(True)
        info.setPlainText("Không xóa project, .venv, settings, video/draft CapCut, WinSxS/DriverStore bằng lệnh xóa file.\n\nPlan có hạn 1 giờ. Trước khi xóa app kiểm tra lại file và junction.\n\nNVIDIA update: chỉ chọn xác nhận bên dưới khi cập nhật đã hoàn tất.\n\nWindows Update mở Disk Cleanup để bạn chọn; không xóa toàn bộ SoftwareDistribution.")
        row.addWidget(info); layout.addLayout(row)
        controls = QHBoxLayout()
        self.update_done = QCheckBox("NVIDIA update đã hoàn tất"); controls.addWidget(self.update_done)
        self.cleanup_action_buttons.append(self.button("Quét tạo đề xuất", self.make_plan, controls))
        self.plan_combo = QComboBox(); controls.addWidget(self.plan_combo)
        self.button("Mở plan", self.load_plan, controls)
        self.button("Hỏi AI", self.ai_plan, controls)
        layout.addLayout(controls)
        self.plan_info = QLabel("Chưa có đề xuất"); self.plan_info.setWordWrap(True); layout.addWidget(self.plan_info)
        self.plan_table = QTableWidget(0, 4); self.plan_table.setHorizontalHeaderLabels(["Chọn", "Đường dẫn", "Dung lượng", "Lý do / tác động"])
        self.columns(self.plan_table, "cleanup", [70, 500, 130, 460])
        layout.addWidget(self.plan_table, 1)
        buttons = QHBoxLayout()
        self.button("Chọn tất cả", lambda: self.set_plan_checks(True), buttons)
        self.button("Bỏ chọn", lambda: self.set_plan_checks(False), buttons)
        buttons.addStretch()
        self.cleanup_action_buttons.append(self.button("OK — xử lý các mục đã chọn", self.apply_plan, buttons))
        layout.addLayout(buttons)
        self.help_box(layout, "cleanup", "Quét tạo đề xuất chưa xóa gì. Chọn các dòng cần xử lý rồi OK; app yêu cầu xác nhận và kiểm tra lại file trước khi xóa. Tắt/Gỡ lịch chạy nền không xóa snapshot hay dữ liệu quét. WinSxS/DriverStore không được xóa tay; dùng quy trình Windows được hỗ trợ.")

    def make_plan(self):
        from .cleanup import plan
        selected = [x for x, b in self.rule_boxes.items() if b.isChecked()]
        if not selected:
            return self.fail("Chọn ít nhất một nhóm")
        update_done=self.update_done.isChecked()
        self.job(lambda worker: plan(selected,update_done,notify=worker.event.emit,stop=worker.stop), self.plan_done)

    def plan_done(self, plan):
        self.current_plan = plan
        self.refresh_plans()
        self.plan_combo.setCurrentText(plan["id"])
        self.populate_plan()
        self.status.setText('Đã tạo đề xuất; chưa xóa gì. Chọn các mục và xác nhận trước khi xử lý.')

    def refresh_plans(self):
        s = Store(readonly=True); rows = s.db.execute("SELECT id FROM plans ORDER BY created DESC LIMIT 100").fetchall(); s.close()
        self.plan_combo.clear(); self.plan_combo.addItems([x[0] for x in rows])

    def load_plan(self):
        if self.plan_combo.currentText():
            from .cleanup import get_plan
            self.current_plan = get_plan(self.plan_combo.currentText()); self.populate_plan()

    def populate_plan(self):
        p = self.current_plan
        self.plan_info.setText(f"Plan {p['id']} • {len(p['entries']):,} mục • {size(p['bytes'])} dự kiến • blocked: {len(p['blocked'])} • lỗi đọc: {len(p['errors'])}. {p['note']}")
        self.plan_table.setRowCount(len(p["entries"]))
        for i, e in enumerate(p["entries"]):
            check = QTableWidgetItem(); check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled); check.setCheckState(Qt.CheckState.Checked)
            self.plan_table.setItem(i, 0, check)
            for j, text in enumerate([e["path"], size(e["size"]), e["rule"] + " — " + e["risk"]], 1):
                cell = QTableWidgetItem(text); cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable); self.plan_table.setItem(i, j, cell)

    def set_plan_checks(self, checked):
        for i in range(self.plan_table.rowCount()):
            self.plan_table.item(i, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def apply_plan(self):
        if not self.current_plan:
            return
        p = self.current_plan
        paths = [p["entries"][i]["path"] for i in range(len(p["entries"])) if self.plan_table.item(i, 0).checkState() == Qt.CheckState.Checked]
        if not paths:
            return self.fail("Chưa chọn mục")
        confirmation, ok = QInputDialog.getText(self, "Xác nhận xóa", f"{len(paths)} mục. Cache bị xóa vĩnh viễn.\nGõ: {p['confirmation']}")
        if ok:
            from .cleanup import select, apply
            try:
                select(p["id"], paths)
                self.job(lambda worker: apply(p["id"], confirmation,notify=worker.event.emit,stop=worker.stop), self.cleanup_result)
            except Exception as exc:
                self.fail(str(exc))

    def cleanup_result(self,value):
        self.status.setText('Đã dừng an toàn; các mục chưa xử lý được giữ nguyên. Xem kết quả dọn.' if value.get('cancelled') else 'Đã hoàn tất xử lý đề xuất. Xem kết quả dọn.')
        self.refresh_plans();self.show_text(value,'Kết quả dọn')

    def ai_dialog(self, scan_id=None, plan_id=None):
        from .report import ai_question, open_provider
        provider, ok = QInputDialog.getItem(self, "Hỏi AI", "Dịch vụ (mở web; đăng nhập tùy dịch vụ)", list(load()["ai_providers"]), 0, False)
        if not ok:
            return
        text = ai_question(scan_id, plan_id, True)
        box = QMessageBox(self); box.setWindowTitle("Xem trước câu hỏi"); box.setText("Chỉ metadata đã chọn, đã che username. Sao chép câu hỏi và mở trang AI?"); box.setDetailedText(text); box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if box.exec() == QMessageBox.StandardButton.Ok:
            QApplication.clipboard().setText(text); open_provider(provider)
            self.status.setText("Đã sao chép câu hỏi; dán vào trang AI. App chưa gửi tự động.")

    def ai_snapshot(self):
        if self.newer.currentData():
            self.ai_dialog(scan_id=self.newer.currentData())

    def ai_plan(self):
        if self.current_plan:
            self.ai_dialog(plan_id=self.current_plan["id"])

    def make_schedule(self):
        layout = self.add_tab("Lịch chạy")
        row = QHBoxLayout()
        self.schedule_profile = QComboBox(); self.schedule_profile.addItems(load()["profiles"])
        self.mode = QComboBox()
        for text, value in [("Theo giờ mỗi ngày", "daily"), ("Mỗi N giờ", "hours"), ("Mỗi N ngày", "days"), ("Theo thứ trong tuần", "weekly"), ("Mỗi N tuần", "weeks"), ("Mỗi N tháng", "months"), ("Mỗi N năm", "years")]:
            self.mode.addItem(text, value)
        self.interval = QSpinBox(); self.interval.setRange(1, 10000)
        self.times = QLineEdit("09:00"); self.times.setPlaceholderText("08:00,14:00,20:00")
        self.weekdays = QLineEdit("0"); self.weekdays.setPlaceholderText("0=Thứ Hai ... 6=CN")
        for label, field in [("Cấu hình quét", self.schedule_profile), ("Lặp", self.mode), ("Mỗi N", self.interval), ("Giờ/ngày", self.times), ("Thứ", self.weekdays)]:
            row.addWidget(QLabel(label)); row.addWidget(field)
        self.button("Thêm lịch", self.add_schedule, row); layout.addLayout(row)
        self.times.setToolTip("Theo giờ mỗi ngày: 08:00,14:00,20:00 là 3 lần/ngày. Chạy bù một lần khi máy thức lại.")
        self.weekdays.setToolTip("0 = Thứ Hai, 1 = Thứ Ba, …, 6 = Chủ nhật. Ví dụ 0,2,4.")
        self.interval.setToolTip("Mỗi N giờ/ngày/tuần/tháng/năm, tính từ thời điểm tạo lịch. Không dùng với lịch theo giờ mỗi ngày.")
        self.mode.currentIndexChanged.connect(self.schedule_mode_changed); self.schedule_mode_changed()
        self.help_box(layout, "schedule", "Một lịch chọn cấu hình đã lưu và thời điểm chạy. Theo giờ mỗi ngày: nhập 08:00,14:00,20:00 để chạy 3 lần/ngày. Theo thứ: 0=Thứ Hai … 6=Chủ nhật. Mỗi N đơn vị tính từ lúc tạo; tháng/năm giữ mốc lịch, máy ngủ sẽ chạy bù một lần. Bật/Tắt lịch chỉ đổi lịch được chọn. Bộ chạy nền bên dưới phải được đăng ký và bật để lịch chạy khi đóng GUI. Chạy ngay không cần đợi đến lịch.")
        self.schedule_table = QTableWidget(0, 6); self.schedule_table.setHorizontalHeaderLabels(["ID", "Cấu hình quét", "Kiểu lịch", "Giờ / khoảng lặp", "Trạng thái", "Lần kế tiếp (UTC+07)"])
        self.columns(self.schedule_table, "schedule", [130, 180, 190, 180, 105, 260]); layout.addWidget(self.schedule_table)
        controls = QHBoxLayout()
        self.button("Xem 10 lần tới", self.preview_schedule, controls)
        self.button("Bật/tắt lịch", self.toggle_schedule, controls)
        self.button("Xóa lịch", self.remove_schedule, controls)
        self.run_schedule_button = self.button("Chạy ngay", self.run_schedule_now, controls)
        self.button("Quét + lập đề xuất tự động", self.link_cleanup_schedule, controls)
        layout.addLayout(controls)
        self.make_background_controls(layout)
        self.help_box(layout, "background", "Cài đặt đăng ký bộ chạy nền. Bật cho phép chạy lịch; Tắt giữ đăng ký nhưng ngừng chạy lịch (service được tắt cả tự khởi động). Gỡ chỉ bỏ đăng ký, không xóa cấu hình/lịch sử. Tắt Task Scheduler không cưỡng bức kết thúc lượt đang chạy; dùng Dừng quét riêng. Chỉ đăng ký một trong Task Scheduler hoặc Windows Service. Service cần quyền Admin/UAC. Task hiện kiểm tra lịch mỗi 5 phút và chạy khi tài khoản đăng nhập.")
        self.schedule_output = QPlainTextEdit(); self.schedule_output.setReadOnly(True); self.schedule_output.setMaximumHeight(150); layout.addWidget(self.schedule_output)

    def schedule_mode_changed(self, *_):
        mode = self.mode.currentData()
        self.interval.setEnabled(mode not in ("daily", "weekly"))
        self.times.setEnabled(mode in ("daily", "weekly")); self.weekdays.setEnabled(mode == "weekly")

    def make_background_controls(self, layout,compact=False):
        box=QWidget() if compact else QGroupBox('Trạng thái và điều khiển chạy nền'); group=QVBoxLayout(box)
        if compact:
            group.setContentsMargins(0,0,0,0);group.setSpacing(2)
            box.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        row = QHBoxLayout()
        choice = QComboBox(); choice.addItem("Task Scheduler", "task"); choice.addItem("Windows Service (Admin)", "service")
        if compact:choice.setMaximumWidth(175)
        choice.setCurrentIndex(1 if self.preferences.values.get("background/kind") == "service" else 0)
        row.addWidget(QLabel("Bộ chạy nền")); row.addWidget(choice)
        label = ElidingLabel("Chưa kiểm tra") if compact else QLabel("Chưa kiểm tra")
        if compact:
            label.setProperty('compactStatus',True);label.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
            label.setMinimumWidth(82);label.setMaximumWidth(230);row.setSpacing(4);row.addWidget(label)
            choice.setMaximumWidth(145)
        else:label.setWordWrap(True);row.addStretch()
        buttons = {}
        for action, text in [("install", "Cài đặt"), ("enable", "Bật"), ("disable", "Tắt"), ("remove", "Gỡ")]:
            buttons[action] = self.button(text, lambda a=action, c=choice: self.background_control(c.currentData(), a), row)
            buttons[action].setEnabled(False)
        buttons["disable"].setToolTip("Ngừng chạy lịch nhưng giữ đăng ký/cấu hình. Khác với Gỡ.")
        buttons["remove"].setToolTip("Bỏ đăng ký bộ chạy nền. Không xóa lịch, snapshot, cấu hình hay dữ liệu đã quét.")
        self.button("Trạng thái", self.refresh_background, row)
        manager=self.button('Mở Windows' if compact else 'Mở quản lý Windows',lambda c=choice:self.open_background_manager(c.currentData()),row)
        manager.setToolTip('Mở Task Scheduler (DiskGrowthMonitor-Local) hoặc Services (DiskGrowthMonitor). Không cài/bật/chạy dịch vụ.')
        realtime=QCheckBox('Realtime' if compact else 'Realtime trạng thái'); realtime.setChecked(self.preferences.values.get('background/realtime',True))
        realtime.setToolTip('Tự kiểm tra trạng thái Task/Service mỗi 3 giây khi hiện cửa sổ; không quét file/ổ đĩa.')
        realtime.toggled.connect(self.set_realtime);self.realtime_boxes.append(realtime);row.addWidget(realtime)
        group.addLayout(row)
        if not compact:group.addWidget(label)
        layout.addWidget(box)
        self.background_panels.append((choice, label, buttons))
        choice.currentIndexChanged.connect(lambda *_: self.background_selection_changed(choice.currentData()))
        return row

    def open_background_manager(self, kind):
        from .background import open_manager
        try: open_manager(kind)
        except Exception as exc: self.fail(str(exc))

    def set_realtime(self, enabled):
        self.preferences.set('background/realtime',enabled)
        for box in self.realtime_boxes:
            box.blockSignals(True);box.setChecked(enabled);box.blockSignals(False)
        if enabled:self.refresh_background()

    def poll_realtime(self):
        self.realtime_timer.setInterval(15000 if self.isMinimized() or not self.isVisible() else 3000)
        if self.preferences.values.get('background/realtime',True): self.refresh_background()

    def background_selection_changed(self, kind):
        self.preferences.set("background/kind", kind)
        for combo, _, _ in self.background_panels:
            combo.blockSignals(True); combo.setCurrentIndex(combo.findData(kind)); combo.blockSignals(False)
        self.render_background()

    def refresh_background(self):
        if self.background_worker is not None or self.background_action_busy:
            return
        from .background import status
        worker = self.job(lambda _: status(), self.background_done)
        self.background_worker = worker
        worker.finished.connect(lambda: setattr(self, "background_worker", None))

    def background_done(self, result):
        self.background_state = result; self.render_background()

    def render_background(self):
        descriptions = []
        for combo, label, buttons in self.background_panels:
            state = self.background_state.get(combo.currentData(), {})
            installed, enabled = state.get("installed"), state.get("enabled")
            running = state.get("running", False)
            text = "Không đọc được trạng thái" if state.get("error") else "Chưa đăng ký" if installed is False else "Đang kiểm tra" if installed is None else "Đã đăng ký • " + ("Đã bật" if enabled else "Đã tắt" if enabled is False else "Chưa rõ bật/tắt")
            if installed:
                text += " • " + ("Đang chạy" if running else "Đã dừng")
            if combo.currentData() == "task" and installed:
                text += " • " + ("Quyền cao nhất" if state.get("run_level") == "HighestAvailable" else "Quyền thông thường")
            if installed:
                from .installation import verified_background_owner
                owned=state.get('owned',verified_background_owner(combo.currentData(),state))
                text += ' • Bản app này' if owned else ' • Chưa xác minh chủ sở hữu' if state.get('owner_status')=='unknown' else ' • Thuộc bản app khác'
            else: owned=True
            checked=self.background_state.get('checked_at')
            if checked: text += ' • Kiểm tra: '+local_time(checked)
            paths=[a.get('path','')+' '+a.get('arguments','') for a in state.get('actions',[])]
            if state.get('binary_path'):paths.append(state['binary_path'])
            upcoming=self.background_state.get('schedules',{}).get('next_run')
            if upcoming:text += ' • Lịch tiếp: '+local_time(upcoming)
            label.setText(text); label.setToolTip(state.get('error') or text+'\n'+'\n'.join(paths))
            if label.property('compactStatus'):label.setFixedWidth(min(230,max(82,label.fontMetrics().horizontalAdvance(text)+8)))
            available = not self.background_action_busy and not self.admin_busy
            buttons["install"].setEnabled(available and installed is False)
            buttons["enable"].setEnabled(available and owned and installed is True and (enabled is False or (combo.currentData() == "service" and not running)))
            buttons["disable"].setEnabled(available and owned and installed is True and (enabled is not False or running))
            buttons["remove"].setEnabled(available and owned and installed is True)
        for kind, title in [("task", "Task Scheduler"), ("service", "Service")]:
            state = self.background_state.get(kind, {})
            if state.get("installed"):
                descriptions.append(title + (": bật" if state.get("enabled") else ": tắt"))
        schedules=self.background_state.get('schedules',{})
        self.background_summary.setText("Chạy nền: " + (" • ".join(descriptions) or "chưa đăng ký") + f" • {schedules.get('enabled_count',0)} lịch đang bật")
        if schedules.get('next_run'):self.background_summary.setText(self.background_summary.text()+' • Kế tiếp: '+local_time(schedules['next_run']))
        external = self.background_state.get("scan", {})
        if self.scan_worker is None:
            self.stop_button.setEnabled(external.get("running", False))
            self.scan_button.setEnabled(not external.get("running", False) and not self.admin_busy); self.rescan_button.setEnabled(not external.get("running", False) and not self.admin_busy)

    def background_control(self, kind, action):
        if self.background_action_busy:
            return
        if action == "remove" and QMessageBox.question(self, "Gỡ bộ chạy nền", "Chỉ bỏ đăng ký bộ chạy nền; giữ cấu hình, lịch chạy và lịch sử. Nếu service đang chạy, sẽ yêu cầu nó dừng trước. Tiếp tục?") != QMessageBox.StandardButton.Yes:
            return
        from .background import control, status
        self.background_action_busy = True; self.render_background()
        def execute(worker):
            result = control(kind, action,notify=worker.event.emit,stop=worker.stop)
            return {"result": result, "status": status()}
        def done(value):
            self.schedule_output.setPlainText(display_json(value['result']))
            self.background_done(value["status"])
            self.status.setText('Đã hoàn tất thao tác bộ chạy nền; xem trạng thái Task/Service.')
        worker = self.job(execute, done)
        def finished():
            self.background_action_busy = False; self.render_background(); self.refresh_background()
        worker.finished.connect(finished)

    def refresh_schedules(self):
        from .schedule import listing
        self.schedule_rows = listing()
        self.schedule_table.setRowCount(len(self.schedule_rows))
        for i, s in enumerate(self.schedule_rows):
            mode = next((self.mode.itemText(i) for i in range(self.mode.count()) if self.mode.itemData(i) == s["mode"]), s["mode"])
            values = [s["id"], s["profile"], mode, ",".join(s["times"]) + f" / N={s['interval']}", "Bật" if s["enabled"] else "Tắt", local_time(s["next_run"])]
            for j, value in enumerate(values):
                self.schedule_table.setItem(i, j, QTableWidgetItem(value))

    def add_schedule(self):
        try:
            from .schedule import add
            mode = self.mode.currentData()
            times = self.times.text().split(",") if mode in ("daily", "weekly") else ["09:00"]
            weekdays = [int(x) for x in self.weekdays.text().split(",")] if mode == "weekly" else [0]
            add({"profile": self.schedule_profile.currentText(), "mode": mode, "interval": self.interval.value(), "times": times, "weekdays": weekdays})
            self.refresh_schedules()
            self.render_background()
        except Exception as exc:
            self.fail(str(exc))

    def selected_schedule(self):
        row = self.schedule_table.currentRow()
        return self.schedule_rows[row] if row >= 0 else None

    def preview_schedule(self):
        spec = self.selected_schedule()
        if spec:
            from .schedule import preview
            self.schedule_output.setPlainText("\n".join(preview(spec)))

    def toggle_schedule(self):
        spec = self.selected_schedule()
        if spec:
            from .schedule import modify
            modify(spec["id"], not spec["enabled"]); self.refresh_schedules()
            self.render_background()

    def remove_schedule(self):
        spec = self.selected_schedule()
        if spec and QMessageBox.question(self, "Xóa lịch", "Xóa lịch được chọn? Không xóa kết quả quét hay gỡ bộ chạy nền.") == QMessageBox.StandardButton.Yes:
            from .schedule import modify
            modify(spec["id"], delete=True); self.refresh_schedules()
            self.render_background()

    def run_schedule_now(self):
        spec = self.selected_schedule()
        if spec:
            self.profile_combo.setCurrentText(spec["profile"])
            if self.profile_combo.currentText() != spec["profile"]:
                return
            from .schedule import run_now
            self.job(lambda worker: run_now(spec["id"], worker.stop, worker.event.emit), self.scan_done, scan_job=True)

    def link_cleanup_schedule(self):
        spec = self.selected_schedule()
        if spec:
            selected = [x for x, b in self.rule_boxes.items() if b.isChecked()]
            cfg = load()
            for s in cfg["schedules"]:
                if s["id"] == spec["id"]:
                    s["cleanup_rules"] = selected
            save(cfg); self.schedule_output.setPlainText(f"Sau quét lập đề xuất: {selected}. Vẫn chờ OK; không tự xóa.")

    def make_log(self):
        layout = self.add_tab("Nhật ký")
        row = QHBoxLayout()
        self.debug = QCheckBox("Debug — ghi chi tiết thao tác/lệnh"); self.debug.setChecked(load()["debug"])
        self.debug.toggled.connect(self.set_debug); row.addWidget(self.debug)
        self.log_type = QComboBox(); self.log_type.addItems(["app.log", "debug.log", "audit.jsonl"]); row.addWidget(self.log_type)
        self.button("Làm mới", self.load_log, row)
        layout.addLayout(row)
        from .log_ui import LogPanel
        self.log_panel=LogPanel(self); self.log_view=self.log_panel.view; layout.addWidget(self.log_panel)
        self.log_type.currentTextChanged.connect(lambda _:self.load_log())
        self.load_log()

    def set_debug(self, enabled):
        cfg = load(); cfg["debug"] = enabled; save(cfg)
        from .logging_utils import setup
        setup(enabled)

    def load_log(self):
        self.log_panel.refresh(True)

    def make_history(self):
        from .history_ui import HistoryPanel
        layout=self.add_tab("Lịch sử"); self.history_panel=HistoryPanel(self); layout.addWidget(self.history_panel)

    def show_history(self):
        self.history_panel.ensure_loaded(); self.tabs.setCurrentIndex(5)

    def poll_background(self):
        if self.preferences.values.get('background/realtime',True): self.refresh_background()
        signature = self.latest_signature()
        if signature != self.last_scan_signature:
            self.refresh_history(); self.last_scan_id = signature[0]; self.last_scan_signature = signature
            if signature[1] in ("complete", "partial"):
                self.tray.showMessage("Có kết quả quét mới", "Mở tab Kiểm tra để xem thay đổi dung lượng.")
        s = Store(readonly=True); count = s.db.execute("SELECT COUNT(*) FROM plans WHERE status='pending'").fetchone()[0]; s.close()
        if count > self.last_plan_count:
            self.refresh_plans(); self.tray.showMessage("Có đề xuất dọn", f"{count} plan đang chờ bạn duyệt; chưa xóa gì.")
        self.last_plan_count = count

    def closeEvent(self, event):
        if any(w.isRunning() for w in self.workers):
            self.status.setText("Đang có tác vụ; dừng quét và chờ hoàn tất trước khi thoát.")
            event.ignore()
            return
        self.tray.hide()
        for timer in self.findChildren(QTimer):timer.stop()
        self.save_layout()
        from .history_backend import close_indexes
        close_indexes()
        event.accept()


def start(tray=False,smoke_test=False):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Disk Growth Monitor")
    app.setStyle("Fusion")
    if not smoke_test:
        # Handles successful updates made by older coordinators too. Never
        # remove failed/recovery sessions or current scan/comparison data.
        try:
            from .storage_maintenance import cleanup_updates
            cleanup_updates()
        except (OSError,ValueError,RuntimeError):pass
    window = Window()
    if not tray:
        window.show()
    report={}
    if smoke_test:
        import time
        from .config import home
        deadline=time.monotonic()+30
        def verify():
            if window.workers:
                if time.monotonic()<deadline:QTimer.singleShot(100,verify);return
                report['error']='GUI startup timeout'
                for worker in list(window.workers):worker.stop.set()
                QTimer.singleShot(100,verify);return
            image_path=home()/'data/gui-smoke.png'
            report.update(title=window.windowTitle(),tabs=window.tabs.count(),rows=window.table.model().rowCount(),image_saved=window.grab().save(str(image_path)))
            window.close();app.quit()
        QTimer.singleShot(100,verify)
    app.exec()
    if smoke_test:
        if report.get('error') or not report.get('image_saved'):raise RuntimeError(str(report))
        return report
