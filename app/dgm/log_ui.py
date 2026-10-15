import json
import re
from PySide6.QtCore import QTimer
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QCheckBox,QComboBox,QPlainTextEdit,QLabel
from .config import home
from .log_reader import read_tail,parse_lines

COLORS={"DEBUG":"#137bb7","INFO":"#44556a","WARNING":"#b87909","ERROR":"#cc3434","CRITICAL":"#b21f35"}
LEVELS=list(COLORS)


class LogColors(QSyntaxHighlighter):
    def highlightBlock(self,text):
        match=re.search(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b",text)
        state=LEVELS.index(match[1]) if match else self.previousBlockState() if self.previousBlockState()>=0 else LEVELS.index("INFO")
        self.setCurrentBlockState(state)
        style=QTextCharFormat();style.setForeground(QColor(COLORS[LEVELS[state]]))
        self.setFormat(0,len(text),style)


class LogPanel(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.last_signature=None
        layout=QVBoxLayout(self);row=QHBoxLayout()
        self.level=QComboBox();self.level.addItems(["Tất cả",*LEVELS]);row.addWidget(QLabel("Mức log"));row.addWidget(self.level)
        self.follow=QCheckBox("Bám dòng mới");self.follow.setChecked(True);row.addWidget(self.follow)
        self.live=QCheckBox("Tự cập nhật");self.live.setChecked(True);row.addWidget(self.live)
        layout.addLayout(row)
        self.view=QPlainTextEdit();self.view.setReadOnly(True);self.view.setMaximumBlockCount(4000)
        self.colors=LogColors(self.view.document());layout.addWidget(self.view)
        self.level.currentIndexChanged.connect(lambda:self.refresh(True))
        self.timer=QTimer(self);self.timer.timeout.connect(lambda:self.refresh() if self.live.isChecked() and self.isVisible() else None);self.timer.start(1000)

    def refresh(self,force=False):
        path=home()/"logs"/self.owner.log_type.currentText()
        try:
            stat=path.stat();signature=(str(path),stat.st_size,stat.st_mtime_ns,self.level.currentText())
            if signature==self.last_signature and not force:return
            text,_=read_tail(path)
            entries,_=parse_lines(text,None if self.level.currentText()=="Tất cả" else self.level.currentText(),audit=path.suffix=='.jsonl')
            lines=[line for _,line in entries]
            scrollbar=self.view.verticalScrollBar();position=scrollbar.value()
            self.view.setPlainText("\n".join(lines[-4000:]));self.last_signature=signature
            scrollbar.setValue(scrollbar.maximum() if self.follow.isChecked() else position)
        except FileNotFoundError:self.view.setPlainText("Chưa có log")
