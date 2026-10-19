import json
import re
from PySide6.QtCore import QTimer,QSignalBlocker,Qt
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QCheckBox,QComboBox,QPlainTextEdit,QLabel,QLineEdit,QTableWidget,QTableWidgetItem,QApplication,QFileDialog,QMessageBox,QDialog,QDialogButtonBox
from .config import home
from .log_reader import read_tail,parse_lines,severity
from .panel_widgets import cards,text_label,setup_table

COLORS={"DEBUG":"#137bb7","INFO":"#44556a","WARNING":"#b87909","ERROR":"#cc3434","CRITICAL":"#b21f35"}
LEVELS=list(COLORS)


class LogColors(QSyntaxHighlighter):
    def highlightBlock(self,text):
        level=severity(text)
        state=LEVELS.index(level) if level else self.previousBlockState() if self.previousBlockState()>=0 else LEVELS.index("INFO")
        self.setCurrentBlockState(state)
        style=QTextCharFormat();style.setForeground(QColor(COLORS[LEVELS[state]]))
        style.setBackground(QColor({'DEBUG':'#eaf6fc','WARNING':'#fff6dd','ERROR':'#fff0ed','CRITICAL':'#ffe6e6'}.get(LEVELS[state],'#fafcfd')))
        self.setFormat(0,len(text),style)


class LogPanel(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.last_signature=None
        layout=QVBoxLayout(self);layout.addWidget(text_label('Nhật ký theo công việc','pageTitle'))
        self.cards=cards(layout,[(level,'0','Trong phần log đang lọc') for level in ('ERROR','WARNING','INFO','DEBUG')])
        from .task_ui import TaskPanel
        self.tasks=getattr(owner,'task_type',TaskPanel)(owner);layout.addWidget(self.tasks)
        row=QHBoxLayout()
        self.level=QComboBox();self.level.addItems(["Tất cả",*LEVELS]);row.addWidget(QLabel("Mức log"));row.addWidget(self.level)
        self.operation=QComboBox();self.operation.addItem('Mọi công việc');row.addWidget(self.operation)
        self.search=QLineEdit();self.search.setPlaceholderText('Tìm thông báo / đường dẫn…');row.addWidget(self.search,1)
        self.follow=QCheckBox("Bám dòng mới");self.follow.setChecked(True);row.addWidget(self.follow)
        self.live=QCheckBox("Tự cập nhật");self.live.setChecked(True);row.addWidget(self.live)
        layout.addLayout(row)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Thời gian','Mức','Công việc','Thông báo','Gợi ý']);setup_table(self.table)
        from .browser_ui import ComparisonRowDelegate
        self.table.setItemDelegate(ComparisonRowDelegate(self.table))
        owner.columns(self.table,'logs-v140',[185,105,150,650,300]);layout.addWidget(self.table,1)
        self.table.itemSelectionChanged.connect(self.selected)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumBlockCount(4000);self.details.setMaximumHeight(130)
        self.details.setPlaceholderText('Chọn dòng để xem lệnh/traceback. Các gợi ý không tự cấp quyền thực thi.');layout.addWidget(self.details)
        self.view=QPlainTextEdit();self.view.setReadOnly(True);self.view.setMaximumBlockCount(4000)
        self.colors=LogColors(self.view.document());self.view.hide();layout.addWidget(self.view)
        buttons=QHBoxLayout();owner.button('Sao chép dòng',self.copy_selected,buttons)
        owner.button('Xuất hỗ trợ đã che thông tin',self.export_support,buttons)
        owner.button('Mở thư mục log',lambda:owner.file_action(str(home()/'logs'),'open'),buttons)
        self.raw=QCheckBox('Nguồn log / nhiều dòng');buttons.addWidget(self.raw);self.raw.toggled.connect(self.view.setVisible)
        self.raw.toggled.connect(lambda shown:self.table.setVisible(not shown));buttons.addStretch();layout.addLayout(buttons)
        self.note=text_label('Chỉ đọc tối đa 500 KB cuối log và giữ 4.000 dòng trong RAM; không tải toàn bộ log.','subTitle');layout.addWidget(self.note)
        self.level.currentIndexChanged.connect(lambda:self.refresh(True))
        self.operation.currentTextChanged.connect(lambda:self.refresh(True));self.search.textChanged.connect(lambda:self.refresh(True))
        self.timer=QTimer(self);self.timer.timeout.connect(lambda:self.refresh() if self.live.isChecked() and self.isVisible() else None);self.timer.start(1000)

    def refresh(self,force=False):
        path=home()/"logs"/self.owner.log_type.currentText()
        try:
            stat=path.stat();signature=(str(path),stat.st_size,stat.st_mtime_ns,self.level.currentText(),self.operation.currentText(),self.search.text())
            if signature==self.last_signature and not force:return
            text,_=read_tail(path)
            entries,_=parse_lines(text,None if self.level.currentText()=="Tất cả" else self.level.currentText(),audit=path.suffix=='.jsonl')
            lines=[line for _,line in entries]
            scrollbar=self.view.verticalScrollBar();position=scrollbar.value()
            self.view.setPlainText("\n".join(lines[-4000:]));self.last_signature=signature
            scrollbar.setValue(scrollbar.maximum() if self.follow.isChecked() else position)
            from .log_reader import events
            all_rows=events(text,audit=path.suffix=='.jsonl')[-4000:]
            prior=self.operation.currentText();blocker=QSignalBlocker(self.operation)
            self.operation.clear();self.operation.addItems(['Mọi công việc',*sorted({row['operation'] for row in all_rows})]);self.operation.setCurrentText(prior if self.operation.findText(prior)>=0 else 'Mọi công việc');blocker.unblock()
            needle=self.search.text().casefold();wanted=self.operation.currentText()
            self.rows=[r for r in all_rows if (wanted=='Mọi công việc' or r['operation']==wanted) and (self.level.currentText()=='Tất cả' or r['level']==self.level.currentText()) and (not needle or needle in r['detail'].casefold())]
            blocker=QSignalBlocker(self.table);old=self.table.currentRow();vertical=self.table.verticalScrollBar().value();self.table.setRowCount(len(self.rows))
            for i,row in enumerate(self.rows):
                for j,value in enumerate((row['time'],'['+row['level']+']',row['operation'],row['message'],row['hint'])):
                    cell=QTableWidgetItem(value);cell.setToolTip(value);cell.setForeground(QColor(COLORS.get(row['level'],COLORS['INFO'])))
                    cell.setBackground(QColor({'DEBUG':'#eaf6fc','WARNING':'#fff6dd','ERROR':'#fff0ed','CRITICAL':'#ffe6e6'}.get(row['level'],'#fafcfd')));self.table.setItem(i,j,cell)
            if self.rows:self.table.setCurrentCell(min(max(old,0),len(self.rows)-1),0)
            blocker.unblock();self.selected()
            if self.follow.isChecked():self.table.scrollToBottom()
            else:self.table.verticalScrollBar().setValue(vertical)
            for card,level in zip(self.cards,('ERROR','WARNING','INFO','DEBUG')):card.set_value(str(sum(r['level']==level or level=='ERROR' and r['level']=='CRITICAL' for r in self.rows)))
        except FileNotFoundError:
            self.view.setPlainText("Chưa có log");self.rows=[];self.table.setRowCount(0);self.details.clear()
            for card in self.cards:card.set_value('0')

    def selected(self):
        index=self.table.currentRow()
        if 0<=index<len(getattr(self,'rows',[])):
            row=self.rows[index];self.details.setPlainText(row['detail']+('\n\nGợi ý: '+row['hint'] if row['hint'] else ''))
    def copy_selected(self):
        index=self.table.currentRow()
        if 0<=index<len(getattr(self,'rows',[])):QApplication.clipboard().setText(self.rows[index]['detail'])
    def export_support(self):
        from .log_reader import redact
        from . import __version__
        from datetime import datetime,timezone
        text=json.dumps({'app_version':__version__,'captured':datetime.now(timezone.utc).isoformat(),'source':self.owner.log_type.currentText(),
            'scope':'500 KB cuối; các dòng đang lọc, không chứa config/history hay nội dung file',
            'events':[{key:redact(value) for key,value in row.items()} for row in getattr(self,'rows',[])]},ensure_ascii=False,indent=2)
        dialog=QDialog(self);dialog.setWindowTitle('Xem trước gói hỗ trợ — không tự gửi lên AI');dialog.resize(850,600)
        layout=QVBoxLayout(dialog);layout.addWidget(text_label('Kiểm tra lại thông tin nhạy cảm trước khi lưu/chia sẻ. Che tự động không bảo đảm nhận ra mọi bí mật.','warningBanner'))
        preview=QPlainTextEdit(text);preview.setReadOnly(True);layout.addWidget(preview)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        path,_=QFileDialog.getSaveFileName(self,'Lưu metadata hỗ trợ đã che thông tin','dgm-support.json','JSON (*.json)')
        if path:
            from pathlib import Path
            Path(path).write_text(text,encoding='utf-8');self.owner.status.setText('Đã lưu gói hỗ trợ do bạn duyệt; chưa gửi lên dịch vụ nào.')
