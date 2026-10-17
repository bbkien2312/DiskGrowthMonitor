import ctypes
import os
from pathlib import Path
import runpy
import struct

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QPalette
from dgm import branding
from test_v2_gui import gui


def test_window_application_and_tray_use_same_multisize_icon(gui):
    app,w=gui
    icon=branding.application_icon()
    assert not icon.isNull()
    assert {size.width() for size in icon.availableSizes()}==set(branding.ICON_SIZES)
    assert w.windowIcon().cacheKey()==app.windowIcon().cacheKey()==w.tray.icon().cacheKey()==icon.cacheKey()
    for size in (16,20,24,32,48,256):
        image=icon.pixmap(QSize(size,size),1.0).toImage()
        assert image.width()==size and image.height()==size
        assert image.pixelColor(0,0).alpha()==0
        assert image.pixelColor(size//2,size//2).alpha()==255
    taskbar=icon.pixmap(32,32)
    assert abs(taskbar.deviceIndependentSize().width()-32)<=1
    assert taskbar.width()==round(32*taskbar.devicePixelRatio())
    if folder:=os.environ.get('DGM_QA_IMAGES'):
        path=Path(folder);path.mkdir(parents=True,exist_ok=True)
        assert branding.render_icon(256).save(str(path/'app-icon.png'))


def test_ico_contains_all_resolutions_and_is_readable_by_qt(gui,tmp_path):
    build_icon=runpy.run_path(str(Path(__file__).resolve().parents[1]/'packaging/generate_icons.py'))['build_icon']
    path=build_icon(tmp_path/'app.ico');data=path.read_bytes()
    assert struct.unpack_from('<HHH',data)==(0,1,len(branding.ICON_SIZES))
    expected_offset=6+16*len(branding.ICON_SIZES)
    for index,size in enumerate(branding.ICON_SIZES):
        width,height,colors,reserved,planes,depth,length,offset=struct.unpack_from('<BBBBHHII',data,6+index*16)
        assert (width or 256,height or 256,colors,reserved,planes,depth)==(size,size,0,0,1,32)
        assert offset==expected_offset
        assert data[offset:offset+8]==b'\x89PNG\r\n\x1a\n'
        expected_offset+=length
    assert expected_offset==len(data)
    icon=QIcon(str(path));assert not icon.isNull()
    assert {size.width() for size in icon.availableSizes()}==set(branding.ICON_SIZES)


def test_taskbar_id_matches_installer_shortcuts(monkeypatch):
    if os.name=='nt':
        calls=[]
        def setter(value):calls.append(value);return 0
        monkeypatch.setattr(ctypes.windll.shell32,'SetCurrentProcessExplicitAppUserModelID',setter)
        assert branding.set_taskbar_identity()
        assert calls==[branding.APP_USER_MODEL_ID]
    installer=(Path(__file__).resolve().parents[1]/'packaging/installer.iss').read_text(encoding='utf-8')
    assert installer.count(f'AppUserModelID: "{branding.APP_USER_MODEL_ID}"')==2


def test_primary_actions_are_distinct_bold_and_disabled_remains_clear(gui):
    app,w=gui
    assert w.scan_button.property('actionRole')=='primary'
    assert w.browser.compare_button.property('actionRole')=='compare'
    assert w.stop_button.property('actionRole')=='danger'
    for button in (w.scan_button,w.rescan_button,w.new_profile_button,w.save_profile_button,w.browser.compare_button):
        assert button.font().weight()>=600
    assert w.scan_button.font().weight()>=700
    assert w.scan_button.palette().color(QPalette.ColorRole.Button).name()=='#1768b0'
    assert w.scan_button.palette().color(QPalette.ColorRole.ButtonText).name()=='#ffffff'
    w.scan_button.setEnabled(False);app.processEvents()
    assert w.scan_button.palette().color(QPalette.ColorGroup.Disabled,QPalette.ColorRole.ButtonText).name()=='#8c98a5'
    w.scan_button.setEnabled(True);app.processEvents()
    assert w.scan_button.palette().color(QPalette.ColorRole.Button).name()=='#1768b0'
    assert w.browser.tree_label.property('zone')=='tree' and w.browser.scope_label.property('zone')=='details'
