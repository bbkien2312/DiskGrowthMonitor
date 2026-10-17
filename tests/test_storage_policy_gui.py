from PySide6.QtWidgets import QMessageBox
from dgm import storage_maintenance as storage
from test_v2_gui import gui,pump
from test_storage_policy import session


def test_gui_storage_preview_cancel_and_cleanup_share_cli_backend(gui,monkeypatch):
    app,w=gui;panel=w.storage_panel;old,installer=session(1);new,_=session(2)
    panel.refresh();pump(app,lambda:not w.workers)
    assert panel.report['update_cleanup']['reclaimable_bytes']>0 and panel.keep.value()==1
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Cancel)
    panel.preview_cleanup();pump(app,lambda:not w.workers);app.processEvents()
    assert (old/'backup/history.sqlite').exists()
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    panel.preview_cleanup();pump(app,lambda:not w.workers);app.processEvents();pump(app,lambda:not w.workers)
    assert not (old/'backup').exists() and (new/'backup/history.sqlite').exists()
    assert not installer.exists() and not panel.all_backups.isChecked()


def test_gui_policy_saves_without_deleting_data(gui):
    from dgm.config import load
    app,w=gui;panel=w.storage_panel;folder,_=session(1)
    panel.keep.setValue(2);panel.reserve.setValue(1536);panel.save_policy();pump(app,lambda:not w.workers)
    assert load()['update_backup_keep']==2 and load()['storage_reserve_mb']==1536
    assert (folder/'backup/history.sqlite').exists()
    import os
    from pathlib import Path
    if os.environ.get('DGM_QA_IMAGES'):
        target=Path(os.environ['DGM_QA_IMAGES']);target.mkdir(parents=True,exist_ok=True)
        w.tabs.setCurrentIndex(1);pump(app,lambda:not w.workers)
        assert w.grab().save(str(target/'storage-panel.png'))
