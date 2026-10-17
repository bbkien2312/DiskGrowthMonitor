import json
from dgm.browser_ui import DetailModel
from dgm.config import home
from dgm.store import Store
from test_v2 import entry,snapshot
from test_v2_gui import gui,pump


def test_variable_pages_model_lru_and_refetch_keep_append_cursor(gui):
    model=DetailModel();model.set_batch_size(1000);requests=[];model.need_page.connect(requests.append)
    start=0
    for group,length in enumerate((20,100,1000,50,1000)):
        rows=[{'path':str(i)} for i in range(start,start+length)]
        model.accept(group,{'rows':rows,'cursor':{'value':group},'total':10000});start+=length
    assert model.loaded==2170 and sum(map(len,model.chunks.values()))<=2000
    row=model.row_at(169);assert row is None or row['path']=='169'
    cursor=model.next_cursor
    assert model.row_at(0) is None and requests[-1]==0
    model.accept(0,{'rows':[{'path':str(i)} for i in range(20)],'cursor':{'value':'refetch-not-append'},'total':10000})
    assert model.next_cursor==cursor and model.row_at(0)['path']=='0'
    model.set_batch_size(20)
    assert model.cache_limit==2000 and model.loaded==2170
    model.paused=True;model.pending.clear();requests.clear()
    assert model.row_at(25) is None and not requests


def test_gui_top_bottom_20_plus_100_plus_1000_and_keep_count_on_sort(gui,isolated):
    app,w=gui;store=Store();root=isolated/'virtual'
    snapshot(store,root,[entry(root/f'f{i:04}',value=i+1) for i in range(2500)],name='daily')
    w.refresh_history();w.threshold.setValue(0);w.kind.setCurrentIndex(w.kind.findData('file'));w.compare();pump(app,lambda:not w.workers)
    browser=w.browser;assert browser.model.loaded==20
    browser.page_size.setCurrentIndex(browser.page_size.findData(100));assert browser.model.loaded==20
    browser.load_more();browser.load_more();pump(app,lambda:not w.workers)
    assert browser.model.loaded==120 and '100' in browser.load_button.text()
    browser.sort_clicked(3);pump(app,lambda:not w.workers)
    assert browser.model.loaded==120 and browser.model.row_at(0)['after']==2500 and browser.model.row_at(119)['after']==2381
    browser.sort_clicked(3);pump(app,lambda:not w.workers)
    assert browser.model.loaded==120 and browser.model.row_at(0)['after']==1 and browser.model.row_at(119)['after']==120
    browser.page_size.setCurrentIndex(browser.page_size.findData(1000));browser.load_more();pump(app,lambda:not w.workers)
    assert browser.model.loaded==1120 and browser.model.row_at(1119)['after']==1120
    browser.sort_clicked(1);pump(app,lambda:not w.workers)
    assert browser.direction=='asc' and browser.model.loaded==1120 and browser.model.row_at(0)['path'].endswith('f0000')
    assert sum(map(len,browser.model.chunks.values()))<=2000
    assert json.loads((home()/'data/ui-settings.json').read_text(encoding='utf-8'))['details/page-size']==1000
    store.close()


def test_gui_50_then_1000_and_folder_only_scope(gui,isolated):
    app,w=gui;store=Store();root=isolated/'virtual';branch=root/'chosen'
    items=[entry(branch,'folder'),entry(branch/'child','folder')]
    items += [entry(branch/f'f{i:04}',value=i+1) for i in range(1200)]
    items += [entry(branch/'child'/'nested',value=5000),entry(root/'outside',value=999999)]
    snapshot(store,root,items,name='daily')
    w.refresh_history();w.threshold.setValue(0);w.kind.setCurrentIndex(w.kind.findData('file'));w.compare();pump(app,lambda:not w.workers)
    browser=w.browser;browser.page_size.setCurrentIndex(browser.page_size.findData(50));browser.load_more();pump(app,lambda:not w.workers)
    assert browser.model.loaded==70
    browser.page_size.setCurrentIndex(browser.page_size.findData(1000));browser.load_more();pump(app,lambda:not w.workers)
    assert browser.model.loaded==1070
    totals=[x.text() for x in browser.metrics]
    browser.select_scope(str(branch));pump(app,lambda:not w.workers)
    assert browser.model.total==1201 and browser.model.loaded==1000
    browser.sort_clicked(1);pump(app,lambda:not w.workers)
    assert all(r['path'].startswith(str(branch)) for page in browser.model.chunks.values() for r in page)
    assert [x.text() for x in browser.metrics]==totals
    browser.recursive.setChecked(False);browser.reload();pump(app,lambda:not w.workers)
    assert browser.model.total==1200 and all(r['parent']==str(branch) for page in browser.model.chunks.values() for r in page)
    assert 'chỉ mục trực tiếp' in browser.scope_label.text()
    browser.select_scope(None);pump(app,lambda:not w.workers)
    assert browser.model.total==1202
    store.close()


def test_cancel_large_append_then_reload_does_not_append_stale_rows(gui,isolated,monkeypatch):
    import threading
    from dgm.comparison import Comparisons
    app,w=gui;store=Store();root=isolated/'virtual'
    snapshot(store,root,[entry(root/f'f{i:04}',value=i+1) for i in range(1300)],name='daily')
    w.refresh_history();w.threshold.setValue(0);w.kind.setCurrentIndex(w.kind.findData('file'));w.compare();pump(app,lambda:not w.workers)
    browser=w.browser;original=Comparisons.page;entered=threading.Event()
    def blocked(cache,*args,**kwargs):
        if kwargs.get('cursor') and kwargs.get('limit')==1000:
            entered.set();cache.stop.wait(5)
            if cache.stop.is_set():
                from dgm.platform_ops import ProcessCancelled
                raise ProcessCancelled('Đã dừng tải')
        return original(cache,*args,**kwargs)
    monkeypatch.setattr(Comparisons,'page',blocked)
    browser.page_size.setCurrentIndex(browser.page_size.findData(1000));browser.load_more();pump(app,entered.is_set)
    browser.cancel_loading();pump(app,lambda:not w.workers)
    assert browser.model.loaded==20 and browser.model.paused and not browser.load_button.isEnabled()
    monkeypatch.setattr(Comparisons,'page',original)
    browser.reload();pump(app,lambda:not w.workers)
    assert browser.model.loaded==1000 and browser.model.total==1300
    store.close()
