"""Review exactly what will be copied; opening a provider never submits it."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication,QComboBox,QDialog,QDialogButtonBox,QHBoxLayout,
                               QPlainTextEdit,QTabWidget,QVBoxLayout,QWidget)

from .details_ui import table
from .panel_widgets import text_label
from .report import DEFAULT_AI_ROWS,ai_prompt


class AiPreview(QDialog):
    def __init__(self,parent,data,providers,open_provider):
        super().__init__(parent)
        self.setWindowTitle('Hỏi AI — xem trước nội dung');self.resize(880,530)
        self.data=data
        self.open_provider=open_provider
        layout=QVBoxLayout(self)
        layout.addWidget(text_label('Chỉ sao chép nội dung đã chọn. Đường dẫn đã che tên người dùng; hãy bỏ chọn mục nhạy cảm. App không tự gửi câu hỏi.','safeBanner'))
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        data_page=QWidget();v=QVBoxLayout(data_page)
        self.facts=table(('Ngữ cảnh','Giá trị'),data['facts'])
        self.facts.setMaximumHeight(145);v.addWidget(self.facts)
        self.rows=table(('Gửi','Đường dẫn','Nhóm / trạng thái','Dung lượng','Lưu ý'),
                        [('',*row) for row in data['rows']])
        self.rows.setColumnWidth(0,52);self.rows.setColumnWidth(1,320)
        for i in range(self.rows.rowCount()):
            cell=self.rows.item(i,0)
            cell.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
            cell.setCheckState(Qt.CheckState.Checked if i<DEFAULT_AI_ROWS else Qt.CheckState.Unchecked)
        v.addWidget(self.rows,1)
        self.tabs.addTab(data_page,'Dữ liệu gửi')
        self.prompt=QPlainTextEdit();self.prompt.setReadOnly(True)
        self.tabs.addTab(self.prompt,'Prompt')
        footer=QHBoxLayout()
        self.count=text_label('')
        footer.addWidget(self.count,1)
        self.provider=QComboBox();self.provider.addItems(list(providers));footer.addWidget(self.provider)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton('Sao chép prompt',QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(self.copy)
        browse=buttons.addButton('Sao chép và mở AI',QDialogButtonBox.ButtonRole.ActionRole)
        browse.setEnabled(bool(providers));browse.clicked.connect(self.copy_open)
        footer.addWidget(buttons);layout.addLayout(footer)
        self.rows.itemChanged.connect(self.refresh)
        self.refresh()

    def refresh(self,*_):
        selected=[i for i in range(self.rows.rowCount()) if self.rows.item(i,0).checkState()==Qt.CheckState.Checked]
        self.prompt.setPlainText(ai_prompt(self.data,selected))
        self.count.setText(f'{len(selected)} mục · {len(self.prompt.toPlainText()):,} ký tự')

    def copy(self):
        QApplication.clipboard().setText(self.prompt.toPlainText())
        self.parent().status.setText('Đã sao chép prompt đã duyệt. Chưa gửi lên AI.')

    def copy_open(self):
        self.copy()
        try:
            self.open_provider(self.provider.currentText())
        except Exception as exc:
            self.parent().fail(str(exc));return
        self.accept()
