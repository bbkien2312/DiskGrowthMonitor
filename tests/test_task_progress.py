from dgm.scanner import scan
from dgm.comparison import Comparisons
from dgm.benchmark import benchmark
from dgm.store import Store


def check_progress(events):
    groups={}
    for event,data in events:
        if event=='task-progress':groups.setdefault(data['id'],[]).append(data)
    assert groups
    for group in groups.values():
        values=[item['percent'] for item in group]
        assert values==sorted(values) and values[0]==0 and values[-1]==100
        assert all(0<=value<=100 for value in values)
        assert group[-1]['completed']


def test_scan_and_comparison_report_monotonic_steps_with_saved_summary(isolated):
    root=isolated/'fixture';root.mkdir();file=root/'one.bin';file.write_bytes(b'a'*100)
    p={'roots':[str(root)],'engine':'native','strict_browsers':True,'excludes':[]}
    events=[];notify=lambda event,data:events.append((event,data))
    a=scan('demo',p,notify=notify);check_progress(events)
    assert any('1 file' in text for text in a['completed_steps'])
    file.write_bytes(b'b'*200);b=scan('demo',p)
    events.clear();cache=Comparisons(notify=notify)
    try:
        pair=cache.prepare(a['scan_id'],b['scan_id']);check_progress(events)
        assert cache.summary(pair)['completed_steps']
        events.clear();page=cache.page(pair);check_progress(events)
        assert page['total']==2
    finally:cache.close()


def test_benchmark_combines_multiple_runs_without_resetting_percent(isolated):
    root=isolated/'fixture';root.mkdir();(root/'one.bin').write_bytes(b'test')
    events=[]
    result=benchmark('demo',{'roots':[str(root)],'engine':'native','strict_browsers':True,'excludes':[]},engines=('native',),repeats=2,notify=lambda event,data:events.append((event,data)))
    assert len(result)==2
    check_progress(events)
    assert len({data['id'] for event,data in events if event=='task-progress'})==1


def test_failed_benchmark_reports_error_instead_of_claiming_measurement_completed(isolated,monkeypatch):
    import importlib
    module=importlib.import_module('dgm.benchmark')
    def fail(*args,**kwargs):raise RuntimeError('fixture engine failure')
    monkeypatch.setattr(module,'scan',fail)
    events=[]
    result=module.benchmark('demo',{},engines=('native',),repeats=1,notify=lambda event,data:events.append((event,data)))
    assert result[0]['status']=='failed' and result[0]['error']=='fixture engine failure'
    text=' '.join(events[-1][1]['completed'])
    assert 'Đã đo' not in text and '1 lỗi' in text
