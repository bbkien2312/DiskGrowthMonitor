"""Small shared, accessible widgets for the secondary task panels."""
from PySide6.QtWidgets import QFrame,QVBoxLayout,QHBoxLayout,QLabel,QGroupBox,QTableWidget,QAbstractItemView
from .ui_helpers import CompactNote,ElidingLabel
from PySide6.QtCore import Qt


def text_label(text, name=None):
    label=QLabel(text);label.setTextFormat(Qt.TextFormat.PlainText);label.setWordWrap(True)
    if name:label.setObjectName(name)
    return label


class SummaryCard(QFrame):
    def __init__(self,caption,value='—',note=''):
        super().__init__();self.setObjectName('reviewCard');self.setMinimumWidth(0)
        layout=QVBoxLayout(self);layout.setContentsMargins(10,7,10,7);layout.setSpacing(3)
        layout.addWidget(text_label(caption,'cardCaption'))
        self.value=ElidingLabel();self.value.setObjectName('cardValue');layout.addWidget(self.value)
        self.note=CompactNote();self.note.setObjectName('cardNote');layout.addWidget(self.note)
        self.set_value(value,note)
    def set_value(self,value,note=None):
        self.value.setText(str(value))
        if note is not None:self.note.setText(note)


def cards(layout,items):
    row=QHBoxLayout();result=[]
    for caption,value,note in items:
        card=SummaryCard(caption,value,note);row.addWidget(card,1);result.append(card)
    layout.addLayout(row);return result


def explanation(title,text=''):
    group=QGroupBox(title);layout=QVBoxLayout(group);label=text_label(text);layout.addWidget(label)
    return group,label


def setup_table(table):
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True);table.setWordWrap(False)
    table.verticalHeader().setDefaultSectionSize(34);table.horizontalHeader().setStretchLastSection(True)
