"""Viewport containment, not just isVisible(): controls must be reachable."""
import json
import os
from pathlib import Path
import pytest
from PySide6.QtCore import Qt,QPoint
from dgm.store import Store
from dgm.ui_helpers import MetricCard
from test_v2 import entry,snapshot
from test_v2_gui import gui,pump


def settle(app):
    for _ in range(6):app.processEvents()


def result(w,app,isolated):
    s=Store();root=isolated/'virtual';sub=root/'System Volume Information'
    items=[entry(sub,'folder')]+[entry(sub/f'a-{i:03}.bin',value=20_000_000+i) for i in range(30)]
    snapshot(s,root,items,name='daily')
    snapshot(s,root,[{**r,'logical':r['logical']+1000000} if r['kind']=='file' else r for r in items],name='daily')
    s.close();w.refresh_history();w.compare();pump(app,lambda:not w.workers);settle(app)


def rect_in(widget,parent):
    from PySide6.QtCore import QRect
    return QRect(widget.mapTo(parent,QPoint(0,0)),widget.size())


def capture(w,name):
    if folder:=os.environ.get('DGM_QA_IMAGES'):
        path=Path(folder);path.mkdir(parents=True,exist_ok=True)
        assert w.grab().save(str(path/(name+'.png')))


@pytest.mark.parametrize('width,height',[(1440,940),(1366,768),(1280,720),(1024,768),(1000,680)])
def test_result_and_essential_controls_are_inside_viewport(gui,isolated,width,height):
    app,w=gui;w.resize(width,height);settle(app)
    before=[card.height() for card in w.browser.metrics]
    result(w,app,isolated)
    assert [card.height() for card in w.browser.metrics]==before
    assert w.width()==width and w.height()==height
    assert w.browser.compare_help.toggle.isChecked()
    scroll=w.scan_page_scroll
    assert scroll.verticalScrollBar().maximum()==0,{'size':(width,height),'overflow':scroll.verticalScrollBar().maximum(),'canvas':w.scan_canvas.size().toTuple()}
    # At narrow widths the outer horizontal bar exposes the full canvas;
    # no essential control is below/above the viewport at either edge.
    for control in (w.profile_combo,w.roots,w.engine,w.excludes,w.threshold,w.strict,w.mft,w.scan_button,w.stop_button,
                    w.older,w.newer,w.browser.page_size,w.browser.load_button,w.browser.cancel_button,w.tree,w.table):
        r=rect_in(control,scroll.viewport())
        assert r.top()>=0 and r.bottom()<scroll.viewport().height(),(control.objectName(),control.__class__.__name__,r,scroll.viewport().size())
    assert w.roots.mapTo(w.scan_canvas,QPoint()).y()==w.profile_combo.mapTo(w.scan_canvas,QPoint()).y()
    assert sum(w.tree.columnWidth(i) for i in range(3))<=w.tree.viewport().width()+3
    choice,label,buttons=w.background_panels[0]
    assert not label.geometry().intersects(buttons['install'].geometry()),(label.geometry(),buttons['install'].geometry(),label.minimumWidth(),label.maximumWidth())
    capture(w,f'compact-{width}x{height}')


def test_centered_titles_small_bounded_notes_and_single_row_values(gui):
    app,w=gui;w.resize(1440,940);settle(app)
    for card in w.browser.metrics:
        initial=card.height()
        card.set_parts('TOÀN PHẠM VI','999 GB','23 GB','Ghi chú rất dài không được tự kéo ngang thẻ khi số liệu xuất hiện',
                       'Phần này chưa đủ bằng chứng để kết luận tăng giảm; xem đầy đủ trong tooltip')
        settle(app)
        assert card.height()==initial
        assert card.height()<=100  # One numeric line + two small note lines, not the old two-value-row card.
        assert card.caption.alignment()&Qt.AlignmentFlag.AlignHCenter
        assert abs(card.caption.geometry().center().x()-card.rect().center().x())<=1
        for value,note in ((card.value,card.note),(card.private_value,card.private_note)):
            assert not value.wordWrap()
            if card is not w.browser.metrics[3]:
                assert value.height()<=card.value.fontMetrics().height()+1
            assert note.font().pixelSize()<value.font().pixelSize()
            assert len(note.display_lines())<=2 and note.toolTip()==note.text()
            assert 0<note.width()<=value.parentWidget().width()
            assert value.fontMetrics().horizontalAdvance(value.text())<=value.width()
        assert card.minimumWidth()<300
    for card in w.browser.metrics[:3]:
        assert card.value.font().weight()>=700


def test_small_window_has_scroll_fallback_and_no_missing_footer(gui,isolated):
    app,w=gui;w.resize(800,600);result(w,app,isolated)
    scroll=w.scan_page_scroll
    assert scroll.horizontalScrollBar().maximum()>0
    assert w.width()==800 and w.height()==600
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum());settle(app)
    for button in (w.browser.load_button,w.browser.cancel_button):
        assert scroll.viewport().rect().contains(rect_in(button,scroll.viewport()))
    capture(w,'compact-800x600-scroll-end')
    scroll.verticalScrollBar().setValue(0);scroll.horizontalScrollBar().setValue(0);settle(app)
    assert scroll.viewport().rect().contains(rect_in(w.profile_combo,scroll.viewport()))
    capture(w,'compact-800x600-scroll-start')


def test_large_sizes_and_counts_keep_numbers_and_units_whole(gui):
    app,w=gui;w.resize(1024,768);settle(app)
    for card in w.browser.metrics[:3]:
        card.set_parts('CHÊNH LỆCH','↑ +999,999 GiB','Không tính','Chỉ phần chung','Cũ 999 GiB\nMới 999 GiB')
    w.browser.metrics[3].set_parts('TOÀN PHẠM VI','1,234,567 file · 987,654 thư mục','123,456 file · 98,765 thư mục','Vùng chung','Không tính thay đổi')
    settle(app)
    for card in w.browser.metrics:
        for label in (card.value,card.private_value):
            assert '\n' not in label.text()
            assert label.fontMetrics().horizontalAdvance(label.text())<=label.width()
    capture(w,'compact-large-numbers')


def test_real_summary_counts_share_one_line_and_result_area_gets_height(gui,isolated):
    app,w=gui;w.resize(1280,720);result(w,app,isolated)
    summary=w.browser.metrics[3]
    for label in (summary.value,summary.private_value):
        assert '\n' not in label.text()
        assert ' file · ' in label.text() and label.text().endswith(' thư mục')
        assert label.fontMetrics().horizontalAdvance(label.text())<=label.width()
    assert len({card.height() for card in w.browser.metrics})==1
    assert summary.height()<=100
    compact_height=w.browser.splitter.height()
    heights=[card.height() for card in w.browser.metrics]
    # Emulate the previous extra value row: its height must come entirely out
    # of the results, not become blank space elsewhere after compacting.
    for card,height in zip(w.browser.metrics,heights):card.setFixedHeight(height+20)
    settle(app)
    assert w.browser.splitter.height()<=compact_height-18
    for card,height in zip(w.browser.metrics,heights):card.setFixedHeight(height)
    settle(app)
    assert w.browser.splitter.height()==compact_height


def test_long_progress_and_expanded_notes_do_not_grow_result_cards(gui,isolated):
    app,w=gui;w.resize(1280,720);result(w,app,isolated)
    before=[card.size().toTuple() for card in w.browser.metrics]
    for key in ('profile','scan'):w.help_boxes[key].toggle.setChecked(True)
    w.progress('task-progress',{'id':'layout-progress','task':'comparison','percent':100,
        'stage':'Đã xử lý dữ liệu','detail':'Đường dẫn rất dài '*40,'completed':['Đã xử lý bước '+str(i) for i in range(40)]})
    settle(app)
    # Appearance of a vertical scrollbar may consume its own 14 px horizontally;
    # notes must not increase card height or push their intrinsic width outward.
    assert [card.height() for card in w.browser.metrics]==[size[1] for size in before]
    assert all(card.width()<=size[0] for card,size in zip(w.browser.metrics,before))
    for key in ('profile','scan','compare'):
        box=w.help_boxes[key];assert box.scroll.height()<=48
    assert w.status.height()>=w.status.fontMetrics().height() and w.scan_progress.width()>w.width()*.8
    assert w.status.geometry().bottom()<w.status_panel.height()
    scroll=w.scan_page_scroll;scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum());settle(app)
    assert 0<=rect_in(w.browser.load_button,scroll.viewport()).top()<scroll.viewport().height()
    capture(w,'compact-expanded-help')


def test_notes_use_whole_column_and_prefer_one_line_without_widening_cards(gui):
    app,w=gui;w.resize(1440,940);settle(app);card=w.browser.metrics[1]
    card.set_parts('DUNG LƯỢNG MỚI','139 GB','23 GB','Cấp phát\n139 GB','Cấp phát\n23 GB');settle(app)
    assert card.value.font().pixelSize()==20 and card.value.font().weight()>=700
    assert card.note.display_lines()==['Cấp phát 139 GB']
    assert card.private_note.display_lines()==['Cấp phát 23 GB']
    assert card.note.width()>=card.value.width()-2
    before=card.minimumWidth();height=card.height()
    card.set_parts('DUNG LƯỢNG MỚI','139 GB','23 GB','Ghi chú dài '*40,'Ghi chú khác '*40);settle(app)
    assert card.minimumWidth()==before and card.height()==height
    assert len(card.note.display_lines())<=2 and len(card.private_note.display_lines())<=2
    w.resize(1000,680);settle(app)
    assert card.note.width()<=card.value.parentWidget().width()
