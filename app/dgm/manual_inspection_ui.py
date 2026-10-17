from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLineEdit,QComboBox,QSpinBox,QFileDialog,QMessageBox,QTableWidget,QTableWidgetItem
from .panel_widgets import text_label,setup_table
from .presentation import size


class ManualInspectionPanel(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.worker=None;self.result=None
        layout=QVBoxLayout(self);layout.addWidget(text_label('Kiểm tra thủ công — không tự xóa','pageTitle'))
        layout.addWidget(text_label('Chỉ folder bạn chọn, không mặc định toàn C:. File lớn/trùng hoặc folder rỗng vẫn có thể cần cho công việc. Bỏ qua hệ thống, project .git/.venv, app và profile trình duyệt.','warningBanner'))
        row=QHBoxLayout();self.root=QLineEdit();self.root.setPlaceholderText('Chọn một folder dữ liệu, ví dụ Downloads…');row.addWidget(self.root,1)
        owner.button('Chọn folder',self.pick,row);layout.addLayout(row)
        row=QHBoxLayout();self.mode=QComboBox()
        for label,key in [('File lớn','large'),('File trùng SHA-256','duplicates'),('Thư mục rỗng','empty')]:self.mode.addItem(label,key)
        row.addWidget(self.mode);self.minimum=QSpinBox();self.minimum.setRange(0,100000);self.minimum.setValue(10);self.minimum.setSuffix(' MB tối thiểu');row.addWidget(self.minimum)
        self.reference=QLineEdit();self.reference.setPlaceholderText('Thư mục tham chiếu cần giữ; phân cách bằng ;');row.addWidget(self.reference,1)
        self.run_button=owner.button('Kiểm tra folder đã chọn',self.run,row);self.run_button.setProperty('actionRole','primary')
        self.stop_button=owner.button('Dừng',self.cancel,row);self.stop_button.setEnabled(False);layout.addLayout(row)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['Đường dẫn','Size','Nhóm / bảo vệ','Ý nghĩa']);setup_table(self.table)
        owner.columns(self.table,'manual-inspection-v140',[540,110,190,650]);layout.addWidget(self.table,1)
        self.table.doubleClicked.connect(self.reveal)
        self.info=text_label('Chưa đọc file nào. Tìm trùng cần đọc nội dung để hash, sẽ hỏi xác nhận trước.','subTitle');layout.addWidget(self.info)
        buttons=QHBoxLayout();owner.button('Mở vị trí mục đã chọn',lambda:self.action('reveal'),buttons);owner.button('Thuộc tính',lambda:self.action('properties'),buttons)
        owner.button('Xem Czkawka (tùy chọn)',lambda:owner.open_source('https://github.com/qarmin/czkawka'),buttons);buttons.addStretch();layout.addLayout(buttons)
    def pick(self):
        path=QFileDialog.getExistingDirectory(self,'Chọn folder dữ liệu để kiểm tra')
        if path:self.root.setText(path)
    def run(self):
        if self.worker is not None:return
        mode=self.mode.currentData()
        if mode=='duplicates' and QMessageBox.question(self,'Đọc nội dung file để tìm trùng','Chỉ đọc file trong folder đã chọn để tính SHA-256. Giới hạn mặc định 256 MB nội dung, 100.000 file, 60 giây; không xóa gì. Tiếp tục?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        root=self.root.text().strip()
        if not root:self.info.setText('Chọn folder trước; không mặc định quét ổ C:.');return
        from .manual_inspection import inspect_folder
        minimum=self.minimum.value()*1_000_000;references=[p.strip() for p in self.reference.text().split(';') if p.strip()]
        self.run_button.setEnabled(False);self.stop_button.setEnabled(True);self.info.setText('Đang đọc phạm vi đã chọn; không xóa gì…')
        self.worker=self.owner.job(lambda worker:inspect_folder(root,mode,min_bytes=minimum,reference=references,stop=worker.stop),self.loaded)
        def done():self.worker=None;self.run_button.setEnabled(True);self.stop_button.setEnabled(False)
        self.worker.finished.connect(done)
    def loaded(self,result):
        self.result=result;self.table.setRowCount(len(result['rows']))
        for i,row in enumerate(result['rows']):
            for j,value in enumerate((row['path'],size(row['size']),('THAM CHIẾU • giữ' if row['reference'] else 'Nhóm '+str(row['group']) if row.get('group') else 'Chỉ xem'),row['note'])):
                item=QTableWidgetItem(value);item.setToolTip(value);self.table.setItem(i,j,item)
        self.info.setText(f"{'Đã kiểm tra phạm vi' if result['complete'] else 'Có giới hạn / chưa đọc hết'} • {result['files_seen']:,} file • hash {size(result['hashed_bytes'])} • {len(result['rows']):,} dòng. "+result['note'])
    def cancel(self):
        if self.worker:self.worker.stop.set();self.stop_button.setEnabled(False);self.info.setText('Đã yêu cầu dừng; không có mục nào bị xóa.')
    def action(self,action):
        index=self.table.currentRow()
        if self.result and 0<=index<len(self.result['rows']):self.owner.file_action(self.result['rows'][index]['path'],action)
    def reveal(self,index):self.action('reveal')
