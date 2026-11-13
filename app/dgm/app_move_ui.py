"""Preview-first desktop interface for relocating other apps' private folders."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLineEdit, QMessageBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import app_move
from .app_move_paths import discover, manual
from .panel_widgets import setup_table, text_label
from .presentation import size


class AppMovePanel(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.worker = self.preview = self.selection = None
        self.rows = []
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label('Chuyển ứng dụng sang phân vùng khác','pageTitle'))
        layout.addWidget(text_label('Chọn EXE/shortcut hoặc thêm thư mục dữ liệu riêng. Xem trước và chọn từng mục. '
                                    'Bản cài Windows/Store chỉ được nhận diện; không tự dời bộ cài.','subTitle'))
        row = QHBoxLayout()
        self.exe = QLineEdit();self.exe.setPlaceholderText('Đường dẫn .exe hoặc .lnk của ứng dụng khác')
        row.addWidget(self.exe,1)
        self.pick_button = owner.button('Chọn EXE / shortcut',self.pick_exe,row)
        self.discover_button = owner.button('Tìm thư mục',self.discover,row)
        self.add_button = owner.button('Thêm thư mục thủ công',self.add_folder,row)
        layout.addLayout(row)
        self.table = QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(['Thư mục','Loại','Dung lượng','Căn cứ / lưu ý'])
        setup_table(self.table)
        owner.columns(self.table,'app-move-v1',[400,123,105,425])
        layout.addWidget(self.table,1)
        row = QHBoxLayout()
        self.destination = QLineEdit();self.destination.setPlaceholderText('Phân vùng hoặc thư mục có sẵn, ví dụ F:\\')
        row.addWidget(self.destination,1)
        self.dest_button = owner.button('Chọn đích',self.pick_destination,row)
        self.preview_button = owner.button('Xem trước',self.prepare,row)
        self.apply_button = owner.button('Chuyển mục đã duyệt',self.apply,row)
        self.apply_button.setEnabled(False)
        self.cancel_button = owner.button('Dừng',self.cancel,row)
        self.cancel_button.setEnabled(False)
        layout.addLayout(row)
        self.note = text_label('Chưa chọn mục. Không quét toàn ổ. Thư mục phải nằm trên hai phân vùng NTFS cục bộ khác nhau.','subTitle')
        layout.addWidget(self.note)
        row = QHBoxLayout()
        self.records = QComboBox()
        self.records.setMinimumContentsLength(19)
        self.records.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self.records,1)
        owner.button('Nhật ký',self.show_record,row)
        owner.button('Hoàn tác',self.undo,row)
        owner.button('Dọn bản cũ',self.retire,row)
        layout.addLayout(row)
        self.table.itemChanged.connect(self.invalidate)
        self.destination.textChanged.connect(self.invalidate)
        self.exe.textChanged.connect(self.app_changed)
        self.refresh_history()

    def dragEnterEvent(self,event):
        urls = event.mimeData().urls()
        if not self.worker and len(urls)==1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in ('.exe','.lnk'):
            event.acceptProposedAction()

    def dropEvent(self,event):
        urls = event.mimeData().urls()
        if not self.worker and len(urls)==1 and urls[0].isLocalFile():
            self.exe.setText(urls[0].toLocalFile())
            self.discover()
            event.acceptProposedAction()

    def invalidate(self,*_):
        had_preview = self.preview is not None
        self.preview = None
        self.apply_button.setEnabled(False)
        if had_preview:
            self.table.blockSignals(True)
            try:
                for index,row in enumerate(self.rows):
                    item = QTableWidgetItem(row['evidence']);item.setToolTip(row['evidence'])
                    self.table.setItem(index,3,item)
            finally:
                self.table.blockSignals(False)
            self.note.setText('Lựa chọn đã đổi. Bấm Xem trước để kiểm tra lại đường dẫn.')

    def app_changed(self,*_):
        self.invalidate()
        if self.selection is not None:
            self.selection = None
            self.rows = [row for row in self.rows if row['confidence']=='manual' and row['role']=='data']
            self.fill()

    def pick_exe(self):
        path,_ = QFileDialog.getOpenFileName(self,'Chọn ứng dụng hoặc shortcut','','Ứng dụng (*.exe *.lnk)')
        if path:
            self.exe.setText(path)

    def pick_destination(self):
        path = QFileDialog.getExistingDirectory(self,'Chọn phân vùng / thư mục đích')
        if path:
            self.destination.setText(path)

    def work(self, function, done, message):
        if self.worker:
            return
        self.note.setText(message)
        self.cancel_button.setEnabled(True)
        inputs = (self.exe,self.destination,self.table,self.pick_button,self.discover_button,
                  self.add_button,self.dest_button,self.preview_button)
        for widget in (*inputs,self.apply_button):
            widget.setEnabled(False)
        self.worker = self.owner.job(function,done)
        self.worker.event.connect(self.progress)
        def finished():
            self.worker = None
            self.cancel_button.setEnabled(False)
            for widget in inputs:
                widget.setEnabled(True)
            self.refresh_history()
            self.apply_button.setEnabled(self.preview is not None)
        self.worker.finished.connect(finished)
        self.worker.error.connect(lambda _:self.note.setText('Tác vụ chưa hoàn tất. Xem thông báo và Nhật ký; không tự dọn bản cũ.'))

    def progress(self,event,detail):
        if event in ('info','cancelled'):
            self.note.setText(detail.get('message',''))

    def cancel(self):
        if self.worker:
            self.worker.stop.set()
            self.cancel_button.setEnabled(False)
            self.note.setText('Đã yêu cầu dừng an toàn…')

    def discover(self):
        path = self.exe.text().strip().strip('"')
        if not path:
            self.note.setText('Chọn EXE/shortcut, hoặc thêm thư mục thủ công.')
            return
        self.invalidate()
        def done(result):
            self.selection = result['app']['selection']
            manual_rows = [row for row in self.rows if row['confidence']=='manual' and row['role']=='data']
            detected = {Path(row['path']) for row in result['rows']}
            self.rows = result['rows'] + [row for row in manual_rows if Path(row['path']) not in detected]
            self.fill()
            self.note.setText(f'Tìm thấy {len(self.rows)} vị trí. Kiểm tra đúng ứng dụng rồi tích từng mục cần chuyển; '
                              'nếu còn thiếu hãy thêm thủ công.')
        self.work(lambda w:discover(path,stop=w.stop),done,'Đang đọc shortcut, đăng ký cài đặt và các vị trí khớp…')

    def add_folder(self):
        if self.worker:
            return
        path,accepted = QInputDialog.getText(self,'Thêm đường dẫn thủ công',
            'Dán đường dẫn tuyệt đối của thư mục dữ liệu riêng:')
        path = path.strip().strip('"')
        if not accepted or not path:
            return
        self.invalidate()
        def done(row):
            self.rows = [item for item in self.rows if Path(item['path'])!=Path(row['path'])]
            self.rows.append(row)
            self.fill()
            self.note.setText('Đã thêm thư mục. Tích chọn và bấm Xem trước.')
        self.work(lambda w:manual(path,stop=w.stop),done,'Đang đo thư mục bạn chỉ định…')

    def fill(self):
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(self.rows))
            for index,row in enumerate(self.rows):
                label = {'data':'Dữ liệu riêng','portable':'Có thể portable','blocked':'Không tự chuyển'}[row['role']]
                for column,value in enumerate((row['path'],label,size(row.get('bytes')),row['evidence'])):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if not column and row['role']!='blocked':
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(Qt.CheckState.Unchecked)
                    self.table.setItem(index,column,item)
        finally:
            self.table.blockSignals(False)

    def ask(self,title,message,detail=''):
        box = QMessageBox(QMessageBox.Icon.Question,title,message,
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if detail:
            box.setDetailedText(detail)
        return box.exec()==QMessageBox.StandardButton.Yes

    def prepare(self):
        if self.worker:
            return
        selected = [row for i,row in enumerate(self.rows) if self.table.item(i,0).checkState()==Qt.CheckState.Checked]
        dest = self.destination.text().strip().strip('"')
        exe = self.exe.text().strip().strip('"') or None
        if not selected or not dest:
            self.note.setText('Tích ít nhất một thư mục và chọn đích.')
            return
        portable = any(row['role']=='portable' for row in selected)
        if portable and not self.ask('Xác nhận portable',
                'Chỉ tiếp tục nếu đây là app portable. Không dùng cho app cài bằng installer, chạy service hoặc có updater phụ thuộc đường dẫn.'):
            return
        self.invalidate()
        def done(result):
            self.preview = result
            for i,row in enumerate(self.rows):
                entry = next((item for item in result['entries'] if Path(item['source'])==Path(row['path'])),None)
                if entry:
                    item = QTableWidgetItem('→ '+entry['target']);item.setToolTip(entry['target'])
                    self.table.blockSignals(True);self.table.setItem(i,3,item);self.table.blockSignals(False)
            self.note.setText(f"Cần {size(result['required'])} ở đích (gồm dự phòng) · đích trống {size(result['free'])} · "
                              f"bản cũ {size(result['bytes'])} vẫn giữ sau khi chuyển.")
        self.work(lambda w:app_move.plan([row['path'] for row in selected],dest,selection=exe,portable=portable,stop=w.stop),
                  done,'Đang kiểm tra nguồn, đích và chỗ trống…')

    def apply(self):
        if self.worker or not self.preview:
            return
        preview = self.preview
        details = '\n'.join(f"{row['source']}\n  → {row['target']}\n  Bản cũ: {row['backup']}" for row in preview['entries'])
        if not self.ask('Chuyển các thư mục đã chọn?',
                'Thoát ứng dụng đích trước. Sao chép, kiểm SHA-256 rồi tạo junction ở đường dẫn cũ. Giữ bản cũ để hoàn tác.',
                details):
            return
        self.invalidate()
        self.work(lambda w:app_move.execute(preview,stop=w.stop,notify=w.event.emit),
            lambda r:self.completed(r,'Đã chuyển. Mở app bằng shortcut cũ để kiểm tra; chưa dọn bản cũ.'),
            'Đang bắt đầu chuyển có kiểm chứng…')

    def refresh_history(self):
        current = self.records.currentData()
        self.records.clear()
        try:
            self.history = {record['id']:record for record in app_move.history()}
        except (OSError,ValueError) as exc:
            self.history = {}
            self.note.setText('Không đọc được nhật ký: '+str(exc))
        states = {'copying':'Đang sao chép','switching':'Đang đổi đường dẫn','complete':'Đã chuyển',
                  'interrupted':'Chưa hoàn tất','undoing':'Đang hoàn tác','undone':'Đã hoàn tác',
                  'retiring':'Đang dọn','retired':'Đã dọn bản cũ'}
        for key,record in self.history.items():
            source = Path(record['entries'][0]['source']).name
            self.records.addItem(f"{source} · {states.get(record['state'],record['state'])} · {key[:8]}",key)
        if not self.history:
            self.records.addItem('Chưa có lượt chuyển',None)
        elif self.records.findData(current)>=0:
            self.records.setCurrentIndex(self.records.findData(current))

    def completed(self,record,message):
        self.refresh_history()
        self.records.setCurrentIndex(self.records.findData(record['id']))
        self.note.setText(message)

    def show_record(self):
        if self.worker:
            return
        self.refresh_history()
        if record := self.history.get(self.records.currentData()):
            self.owner.show_text(record,'Nhật ký chuyển ứng dụng')

    def undo(self):
        if self.worker or not (key := self.records.currentData()):
            return
        if self.ask('Hoàn tác lượt chuyển?',
                    'Khôi phục thư mục gốc nếu hai bản chưa đổi. Giữ dữ liệu ở ổ đích; không ghi đè dữ liệu mới.'):
            self.work(lambda w:app_move.undo(key,stop=w.stop,notify=w.event.emit),
                      lambda r:self.completed(r,'Đã khôi phục thư mục gốc. Bản trên ổ đích vẫn còn.'),
                      'Đang xác minh trước khi hoàn tác…')

    def retire(self):
        if self.worker or not (key := self.records.currentData()):
            return
        record = self.history.get(key)
        if not record or record['state'] not in ('complete','retiring'):
            self.note.setText('Chọn lượt đã chuyển thành công.')
            return
        box = QMessageBox(QMessageBox.Icon.Warning,'Dọn bản cũ',
            'Hãy thử mở app rồi thoát hẳn. Chỉ dọn bản cũ nếu mọi file gốc vẫn có bản trùng ở đích. '
            'Thùng rác giữ khả năng phục hồi nhưng chưa trả chỗ trống ngay.',parent=self)
        trash = box.addButton('Đưa vào Thùng rác',QMessageBox.ButtonRole.ActionRole)
        permanent = box.addButton('Xóa bản trùng để lấy chỗ',QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        paths = '\n'.join(row['backup'] for row in record['entries'])
        box.setDetailedText(paths)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (trash,permanent):
            return
        delete = clicked is permanent
        if delete and not self.ask('Xóa vĩnh viễn đúng các bản cũ?',
                'Không thể hoàn tác việc xóa. Bản đang dùng ở ổ đích được giữ nguyên.',paths):
            return
        self.work(lambda w:app_move.retire(key,stop=w.stop,notify=w.event.emit,permanent=delete),
                  lambda r:self.completed(r,'Đã dọn bản trùng đã kiểm chứng.' if delete else
                                           'Đã đưa bản cũ vào Thùng rác. Bản ở ổ đích vẫn được giữ.'),
                  'Đang kiểm chứng lại bản cũ…')
