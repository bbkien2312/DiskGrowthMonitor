import json
import os
from pathlib import Path
from PySide6.QtCore import Qt
from dgm.store import Store
from dgm.comparison import Comparisons
from test_v2 import entry,snapshot
from test_v2_gui import gui,pump


def test_split_metrics_tree_details_and_one_preparation(gui,isolated,monkeypatch):
    app,w=gui;s=Store();root=isolated/'virtual';branch=root/'System Volume Information';private=root/'Chrome'
    old=[entry(branch,'folder'),entry(branch/'f',value=40_000_000)]
    a=snapshot(s,root,old,name='daily')
    b=snapshot(s,root,[entry(branch,'folder'),entry(branch/'f',value=50_000_000),entry(private,'folder'),entry(private/'cache',value=80_000_000)],name='daily')
    cfg=json.loads(s.scan(b)['config']);cfg['strict_browsers']=False
    s.db.execute('UPDATE scans SET config=? WHERE id=?',(json.dumps(cfg),b));s.db.commit()
    calls=[];original=Comparisons.prepare
    def tracked(cache,*args,**kwargs):calls.append(args);return original(cache,*args,**kwargs)
    monkeypatch.setattr(Comparisons,'prepare',tracked)
    w.refresh_history();w.threshold.setValue(0);w.compare();pump(app,lambda:not w.workers)
    browser=w.browser
    assert calls==[(a,b)] and browser.tree_pair==browser.pair_id
    assert browser.metrics[0].value.text()=='40 MB' and browser.metrics[0].private_value.text()=='0 B'
    assert browser.metrics[1].value.text()=='50 MB' and browser.metrics[1].private_value.text()=='80 MB'
    assert browser.metrics[2].value.text()=='↑ +10 MB' and browser.metrics[2].private_value.text()=='Chưa rõ'
    assert browser.metrics[3].width()>browser.metrics[0].width()
    rootitem=browser.tree.topLevelItem(0);assert not rootitem.icon(0).isNull() and rootitem.data(0,Qt.ItemDataRole.UserRole)['row']['private_after']==80_000_000
    assert 'Chrome/Edge Có' in browser.newer.currentText() and 'MFT Không' in browser.newer.currentText()
    browser.sections.setCurrentIndex(1);pump(app,lambda:not w.workers)
    assert browser.model.private and browser.reason.isVisible()
    rows=[row for chunk in browser.model.chunks.values() for row in chunk]
    assert any(row['kind']=='file' and row['delta'] is None and row['change']=='not_scanned' for row in rows)
    assert calls==[(a,b)]
    browser.reason.setCurrentIndex(browser.reason.findData('coverage'));pump(app,lambda:not w.workers)
    assert browser.model.total==0
    assert browser.metrics[1].private_value.text()=='80 MB'  # Filter doesn't change global metrics.
    browser.reason.setCurrentIndex(0);pump(app,lambda:not w.workers)
    artifact=os.environ.get('DGM_QA_IMAGES')
    if artifact:
        folder=Path(artifact);folder.mkdir(parents=True,exist_ok=True)
        for width,height in ((1440,1000),(1000,760)):
            w.resize(width,height);app.processEvents();assert w.grab().save(str(folder/f'partitions-{width}.png'))
    s.close()


def test_mft_only_on_supported_engine_and_excludes_popup_control(gui):
    app,w=gui
    w.engine.setCurrentText('native');w.strict.setChecked(False);app.processEvents()
    assert not w.mft.isEnabled() and not w.mft.isChecked()
    w.engine.setCurrentText('wiztree');app.processEvents();assert w.mft.isEnabled()
    w.mft.setChecked(True);w.strict.setChecked(True);app.processEvents();assert not w.mft.isChecked() and not w.mft.isEnabled()
    assert w.roots.maximumWidth()<=360 and w.excludes.maximumWidth()<=300
