"""Shell calls are mocked; all GUI checks use Qt offscreen and synthetic rows."""
import os
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QTableView

from dgm import cleanup,platform_ops
from dgm.diagnostics import diagnostic_rows
from dgm.report import ai_data,ai_prompt
from test_milestone_startup_ui import gui_factory,settle


@pytest.mark.skipif(sys.platform!='win32',reason='Windows Shell API')
def test_reveal_uses_shell_and_falls_back_to_parent(tmp_path,monkeypatch):
    from dgm import logging_utils
    from win32com.shell import shell
    folder=tmp_path/'folder with spaces';folder.mkdir()
    path=folder/'sample.dat';path.write_bytes(b'fixture')
    calls=[]
    monkeypatch.setattr(logging_utils,'audit',lambda *a,**k:None)
    monkeypatch.setattr(os,'startfile',lambda name:calls.append(('open',name)))
    monkeypatch.setattr(shell,'SHParseDisplayName',lambda name,flags:(calls.append(('parse',name)) or 'item',0))
    monkeypatch.setattr(shell,'SHOpenFolderAndSelectItems',lambda item,items,flags:calls.append(('select',item)))
    platform_ops.open_item(path,'reveal')
    assert calls==[('parse',str(path)),('select','item')]
    platform_ops.open_item(path,'parent')
    assert calls[-1]==('open',str(folder))
    monkeypatch.setattr(shell,'SHOpenFolderAndSelectItems',lambda *a:(_ for _ in ()).throw(OSError('shell unavailable')))
    assert 'Không bôi chọn' in platform_ops.open_item(path,'reveal')
    assert 'không còn' in platform_ops.open_item(folder/'missing.dat','reveal')
    assert calls[-1]==('open',str(folder))
    with pytest.raises(ValueError,match='không còn là thư mục'):
        platform_ops.open_item(path,'open-folder')


@pytest.mark.skipif(sys.platform!='win32',reason='Windows junction')
def test_open_folder_with_spaces_accepts_junction(tmp_path,monkeypatch):
    from dgm import logging_utils
    from dgm.app_move import _junction
    from win32com.shell import shell
    target=tmp_path/'target with spaces';target.mkdir()
    link=tmp_path/'link with spaces'
    _junction(link,target)
    calls=[]
    monkeypatch.setattr(logging_utils,'audit',lambda *a,**k:None)
    monkeypatch.setattr(os,'startfile',calls.append)
    try:
        assert link.resolve()==target
        platform_ops.open_item(link,'open-folder')
        assert calls==[str(link)]
        # Invoke the real Windows parser, but do not open Explorer on the user's desktop.
        selected=[]
        monkeypatch.setattr(shell,'SHOpenFolderAndSelectItems',lambda pidl,*a:selected.append(pidl))
        platform_ops.open_item(link,'reveal')
        assert selected and calls==[str(link)]
    finally:
        link.rmdir()


def test_app_move_row_double_click_and_menu_keep_path_scope(gui_factory,tmp_path,monkeypatch):
    app,open_window,errors=gui_factory
    w=open_window();panel=w.app_move_panel
    w.tabs.setCurrentWidget(panel.parentWidget())
    path=tmp_path/'Example data';path.mkdir()
    panel.rows=[{'path':str(path),'role':'data','evidence':'fixture','bytes':0,'confidence':'manual'}]
    panel.fill();app.processEvents()
    calls=[]
    monkeypatch.setattr(w,'file_action',lambda p,a:calls.append((p,a)))
    monkeypatch.setattr(w,'path_menu',lambda p,pos,**kw:calls.append((p,kw)))
    index=panel.table.model().index(0,1)
    panel.table.doubleClicked.emit(index)
    panel.preview={'entries':[{'source':str(path),'target':'F:\\Example data'}]}
    panel.path_menu(panel.table.visualRect(index).center())
    assert calls==[(str(path),'open-folder'),(str(path),{'browse_only':True,'destination':'F:\\Example data'})]
    assert not errors


def test_diagnostics_tables_keep_raw_output_and_report_error(gui_factory):
    app,open_window,errors=gui_factory
    w=open_window()
    w.system_result({'tool':'winsxs','exit':0,'stdout':'Actual Size of Component Store : 10.51 GB','stderr':''})
    panel=w.system_output
    assert [panel.tabs.tabText(i) for i in range(panel.tabs.count())]==['Tổng quan','Số liệu','Log gốc']
    assert panel.tabs.widget(1).item(0,1).text()=='Dung lượng thực'
    assert panel.tabs.widget(2).toPlainText().endswith('10.51 GB')
    w.system_result({'tool':'drivers','exit':5,'stdout':'Other locale: data','stderr':'Access denied'})
    assert 'báo lỗi' in w.status.text() and panel.tabs.widget(1).toPlainText().endswith('Access denied')
    assert diagnostic_rows('drivers','Published Name: oem8.inf\nProvider Name: Test\nPublished Name: oem9.inf')[-1]==('2','Tên INF','oem9.inf')
    assert not errors


def test_ai_preview_uses_reviewed_pair_filters_browsers_and_omits_raw_metadata(gui_factory):
    from dgm.ai_preview_ui import AiPreview
    app,open_window,errors=gui_factory
    w=open_window()
    def fetch():
        return {'older':{'id':4,'started':'2026-11-10T09:00:00+07:00'},'newer':{'id':9},
                'scope_compatible':False,'internal':'do not copy'},[
                    {'path':f'C:\\Users\\Private Name\\Work\\item{i}.bin','change':'grown',
                     'allocated_delta':i*1024,'internal':'do not copy'} for i in range(13)]+[
                    {'path':'C:\\Users\\Private Name\\AppData\\Local\\Google\\Chrome\\secret','change':'grown'}]
    data=ai_data(9,older=4,pair_explicit=True,fetch=fetch)
    assert len(data['rows'])==13
    prompt=ai_prompt(data)
    assert '#4' in prompt and '#9' in prompt and '<USER>' in prompt
    assert 'Private Name' not in prompt and 'do not copy' not in prompt and 'secret' not in prompt
    assert 'item9.bin' in prompt and 'item10.bin' not in prompt and '{' not in prompt
    opened=[]
    dialog=AiPreview(w,data,{'Test':'https://example.com'},opened.append)
    dialog.rows.item(0,0).setCheckState(Qt.CheckState.Unchecked)
    assert 'item0.bin' not in dialog.prompt.toPlainText() and dialog.count.text().startswith('9 mục')
    assert not opened and not errors


def test_cleanup_blocked_group_and_review_across_pages(gui_factory,isolated,monkeypatch):
    import time
    app,open_window,errors=gui_factory
    root=isolated/'fixture-cache';root.mkdir()
    for i in range(22):
        path=root/f'fixture{i:02}.bin'
        path.write_bytes(b'x'*(i+1));os.utime(path,(time.time()-86400*10,)*2)
    known=cleanup.rules()
    known['user-temp']={**known['user-temp'],'roots':[str(root)]}
    monkeypatch.setattr(cleanup,'rules',lambda:known)
    w=open_window();panel=w.cleanup_panel
    content=cleanup.plan(['user-temp','nvidia-update','recycle-bin'],compact=True)
    panel.loaded(content)
    assert panel.rule_items['nvidia-update'].text(2)=='Bị chặn'
    assert 'Cần xác nhận NVIDIA' in panel.info.text()
    assert panel.table.rowCount()==20 and panel.rule_items['recycle-bin'].text(2)=='Công cụ'
    panel.table.item(0,0).setCheckState(Qt.CheckState.Checked)
    panel.move_page(1);panel.table.item(0,0).setCheckState(Qt.CheckState.Checked)
    panel.result_tabs.setCurrentIndex(1)
    panel.set_checks(True)
    assert len(panel.selected)==2 and panel.table.item(0,0).checkState()==Qt.CheckState.Unchecked
    seen=[]
    def inspect(dialog):
        model=dialog.findChild(QTableView).model()
        seen.extend(model.data(model.index(i,0)) for i in range(model.rowCount()))
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    panel.apply()
    assert set(seen)==panel.selected and len(seen)==2
    assert len(list(root.iterdir()))==22 and not errors


def test_result_table_keeps_skips_outside_first_page(gui_factory):
    from dgm.details_ui import cleanup_dialog
    from PySide6.QtWidgets import QComboBox
    app,open_window,errors=gui_factory
    w=open_window()
    dialog=cleanup_dialog(w,{'items':[{'path':'fixture-first','status':'deleted','bytes':4}],
        'skipped_items':[{'path':'fixture-later','status':'skipped','error':'Đang dùng'}],
        'items_truncated':True,'counts':{'deleted':1000,'skipped':1},'free_delta':-12,'deleted_bytes':4000})
    dialog.findChild(QComboBox).setCurrentIndex(1)
    table=dialog.detail.tabs.currentWidget()
    assert table.item(0,0).text()=='fixture-later'
    assert table.item(0,3).text()=='Đang dùng'
    assert not errors


def test_cleanup_refuses_changed_catalog_after_review(gui_factory,isolated,monkeypatch):
    import threading
    import time
    from types import SimpleNamespace
    from PySide6.QtWidgets import QInputDialog

    _,open_window,errors=gui_factory
    root=isolated/'review-cache';root.mkdir()
    for name in ('first.bin','second.bin'):
        path=root/name;path.write_bytes(b'fixture');os.utime(path,(time.time()-86400*10,)*2)
    known=cleanup.rules();known['user-temp']={**known['user-temp'],'roots':[str(root)]}
    monkeypatch.setattr(cleanup,'rules',lambda:known)
    panel=open_window().cleanup_panel
    content=cleanup.plan(['user-temp'],compact=True);panel.loaded(content)
    panel.set_checks(True)
    monkeypatch.setattr(panel,'review_selected',lambda paths:True)
    monkeypatch.setattr(QInputDialog,'getText',lambda *a:(content['confirmation'],True))
    monkeypatch.setattr(panel,'run',lambda work,done:work(
        SimpleNamespace(stop=threading.Event(),event=SimpleNamespace(emit=lambda *a:None))))

    select=cleanup.select
    def race(*args,**kwargs):
        reviewed=select(*args,**kwargs)
        select(content['id'],[str(root/'second.bin')],compact=True)
        return reviewed
    monkeypatch.setattr(cleanup,'select',race)
    with pytest.raises(ValueError,match='thay đổi sau khi duyệt'):
        panel.apply()
    assert sorted(p.name for p in root.iterdir())==['first.bin','second.bin']
    assert not errors
