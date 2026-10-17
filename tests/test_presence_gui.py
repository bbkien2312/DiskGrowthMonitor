import os
from pathlib import Path
from PySide6.QtCore import Qt,QEvent,QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication,QLabel,QPushButton,QToolTip,QDialog,QPlainTextEdit
from dgm.store import Store
from dgm.conditional_tooltips import clipped_text
from dgm.browser_ui import HEADERS,SORT_COLUMNS
from test_v2 import entry,snapshot
from test_v2_gui import gui,pump
from test_scope_partitions import configure


def setup_rows(w,app,isolated):
    s=Store();root=isolated/'virtual';browser=root/'Chrome';denied=root/'blocked'
    a=snapshot(s,root,[entry(root/'keep',value=10_000_000),entry(root/'gone',value=8_000_000),entry(denied,'folder'),entry(denied/'unknown',value=2_000_000)],name='daily')
    b=snapshot(s,root,[entry(root/'keep',value=12_000_000),entry(root/'new',value=4_000_000),entry(browser,'folder'),entry(browser/'cache',value=90_000_000)],name='daily')
    configure(s,b,strict_browsers=False)
    import json
    totals=json.loads(s.scan(b)['summary']);totals['coverage']={'unknown_roots':[str(denied)]};s.record_stage(b,totals);s.close()
    w.resize(1440,940);w.refresh_history();w.threshold.setValue(0);w.kind.setCurrentIndex(w.kind.findData('file'));w.compare();pump(app,lambda:not w.workers)
    return root


def test_status_first_colors_strikeout_presence_and_verified_metrics(gui,isolated):
    app,w=gui;setup_rows(w,app,isolated);b=w.browser
    assert HEADERS==['TT','Đường dẫn','Cũ','Mới','±','Loại','± Đĩa','Tạo','Sửa']
    assert SORT_COLUMNS.index('delta')==4 and SORT_COLUMNS.index('path')==1
    assert b.model.total==1 and b.metrics[2].value.text()=='↑ +2 MB'
    assert b.metrics[2].private_value.text()=='≈ ↓ −4 MB'
    before=[m.text() for m in b.metrics]
    b.sections.setCurrentIndex(1);pump(app,lambda:not w.workers)
    rows={Path(b.model.row_at(i)['path']).name:i for i in range(b.model.loaded)}
    assert set(rows)=={'gone','new','cache','unknown'}
    display=lambda name,col:b.model.data(b.model.index(rows[name],col))
    assert display('cache',2)=='∅' and display('cache',0)=='∅'
    assert 'Không quét Cũ' in b.model.data(b.model.index(rows['cache'],0),Qt.ItemDataRole.AccessibleTextRole)
    assert display('unknown',3)=='?' and 'Chỉ Cũ' in b.model.data(b.model.index(rows['unknown'],0),Qt.ItemDataRole.AccessibleTextRole)
    assert display('gone',3)=='×' and display('new',2)=='×'
    font=b.model.data(b.model.index(rows['gone'],1),Qt.ItemDataRole.FontRole)
    assert font.strikeOut()
    assert b.model.data(b.model.index(rows['cache'],1),Qt.ItemDataRole.FontRole) is None
    for name in ('gone','new','cache','unknown'):
        assert b.model.data(b.model.index(rows[name],0),Qt.ItemDataRole.BackgroundRole) is not None
    assert b.tree_mode.currentText()=='Cặp đang so sánh' and b.tree_mode.height()>=b.tree_mode.fontMetrics().height()+14
    if folder:=os.environ.get('DGM_QA_IMAGES'):
        path=Path(folder);path.mkdir(parents=True,exist_ok=True);assert w.grab().save(str(path/'presence-private.png'))
    b.presence.setCurrentIndex(b.presence.findData('old'));pump(app,lambda:not w.workers)
    assert b.model.total==2 and all(b.model.row_at(i)['presence']=='old' for i in range(b.model.loaded))
    b.change.select(['missing']);b.reload();pump(app,lambda:not w.workers)
    assert b.model.total==1 and b.model.row_at(0)['change']=='missing'
    assert [m.text() for m in b.metrics]==before


def test_clipped_only_tooltips_for_cells_and_labels(gui,isolated,monkeypatch):
    app,w=gui;setup_rows(w,app,isolated);b=w.browser;seen=[]
    monkeypatch.setattr(QToolTip,'showText',lambda *args:seen.append(args[1]))
    monkeypatch.setattr(QToolTip,'hideText',lambda:None)
    def hover(widget,point):
        event=QHelpEvent(QEvent.Type.ToolTip,point,widget.mapToGlobal(point))
        QApplication.sendEvent(widget,event)
    label=QLabel('Texte đầy đủ',w);label.setToolTip(label.text());label.setFixedSize(400,30);label.show();app.processEvents()
    hover(label,QPoint(5,5));assert seen==[] and clipped_text(label)==''
    label.setFixedWidth(20);hover(label,QPoint(5,5));assert seen==[label.text()]
    seen.clear();label.hide()
    b.table.setColumnWidth(1,80);app.processEvents()
    index=b.model.index(0,1);hover(b.table.viewport(),b.table.visualRect(index).center())
    assert seen==[b.model.row_at(0)['path']]
    seen.clear();b.table.setColumnWidth(2,140);app.processEvents()
    hover(b.table.viewport(),b.table.visualRect(b.model.index(0,2)).center());assert seen==[]
    icon_button=QPushButton('',w);icon_button.setToolTip('Chọn thư mục')
    assert clipped_text(icon_button) is None


def test_baseline_defaults_to_only_new_without_claiming_addition(gui,isolated):
    app,w=gui;s=Store();root=isolated/'virtual';snapshot(s,root,[entry(root/'first',value=20_000_000)],name='daily');s.close()
    w.refresh_history();w.compare();pump(app,lambda:not w.workers)
    b=w.browser;assert b.sections.currentIndex()==1 and b.model.total==2
    assert all(b.model.row_at(i)['change']=='first_scan' for i in range(b.model.loaded))
    assert b.model.data(b.model.index(0,2))=='—'
    assert b.metrics[2].note.text()=='Mốc ban đầu'


def test_manual_column_width_survives_reload_and_snapshot_details_are_read_only(gui,isolated,monkeypatch):
    app,w=gui;setup_rows(w,app,isolated);b=w.browser
    b.table.setColumnWidth(1,241);b.reload();pump(app,lambda:not w.workers)
    assert b.table.columnWidth(1)==241
    seen=[]
    def show(dialog):seen.append(dialog.findChild(QPlainTextEdit).toPlainText());return 0
    monkeypatch.setattr(QDialog,'exec',show)
    b.sections.setCurrentIndex(1);pump(app,lambda:not w.workers)
    row=next(b.model.row_at(i) for i in range(b.model.loaded) if b.model.row_at(i)['change']=='not_scanned')
    b.snapshot_details(row)
    assert 'Lần quét 01' in seen[0] and 'Lần quét 02' in seen[0] and 'Chrome/Edge' in seen[0] and '90000000' in seen[0]
    assert w.conditional_tooltips is app._dgm_tooltip_filter
