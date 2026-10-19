"""Elevated read-only QA of installed snapshots; all writes stay in D: artifacts.

Run explicitly, not collected by pytest. No scan, installer, Task or Service calls.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QMessageBox
from dgm import config,production_core as core
from dgm.columnar_store import Repository,sha256,read_json
from dgm.main_ui import MainWindow


class ReadOnlySnapshots(Repository):
    def __init__(self,source,work):
        self.source=source.resolve()
        self.captured=read_json(self.source/'catalog.json')
        super().__init__(work)
        self.production=True

    def catalog(self):return self.captured

    def path(self,scan):
        # Validate the catalog filename with the regular implementation first.
        validated=super().path(scan)
        return self.source/'snapshots'/validated.name


def run(output):
    from dgm.platform_ops import is_admin
    if not is_admin():raise PermissionError('Cần UAC để đọc snapshot đã được bản cài bảo vệ')
    source=Path(r'C:\Program Files (x86)\DiskGrowthMonitor-App\data\columnar')
    before=sha256(source/'catalog.json')
    config.ROOT=output/'isolated-home'
    cfg=config.defaults();cfg['profiles']['daily'].update(roots=['C:\\'],mft=True)
    config.save(cfg)
    repo=ReadOnlySnapshots(source,output/'read-only-audit-repo')
    originals={ident:repo.get(ident)['sha256'] for ident in (1,2,3)}
    core.repository=lambda:repo
    MainWindow.doctor=lambda self:None
    MainWindow.refresh_background=lambda self:None
    errors=[];QMessageBox.warning=lambda p,t,m,*_:errors.append(str(m))
    app=QApplication([]);app.setStyle('Fusion')
    window=MainWindow();window.resize(1440,980);window.show()
    window.setWindowTitle(window.windowTitle()+' — BẢN SỬA THỬ · đọc mốc đã lưu, không quét ổ')
    report={'live_scan':False,'source_writes':False,'snapshots_copied':False,'pairs':[]}

    def settle():
        deadline=time.monotonic()+180;idle=0
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            if errors:raise AssertionError(errors)
            idle=idle+1 if not window.workers else 0
            if idle>=5:return
        raise AssertionError('GUI không hoàn tất trong 180 giây')

    try:
        settle();ui=window.browser
        for old,new in ((0,1),(1,2),(1,3),(2,3)):
            ui.newer.setCurrentIndex(ui.newer.findData(new))
            ui.older.setCurrentIndex(ui.older.findData(old))
            started=time.perf_counter();ui.compare();settle()
            assert ui.measurement.currentData()=='logical'
            assert ui.model.total>0 and ui.model.loaded==20
            assert ui.tree.topLevelItemCount() and ui.tree.topLevelItem(0).text(1) not in ('?','—')
            sizes=[ui.model.data(ui.model.index(i,3)) for i in range(ui.model.loaded)]
            assert any(value not in ('?','—') for value in sizes)
            expected_grown=sum(r['count'] for r in ui.current_summary['counts_logical'] if r['change']=='grown')
            assert f'{expected_grown:,} tăng' in window.status.text()
            assert ui.metrics[1].caption.text()=='KÍCH THƯỚC FILE MỚI'
            if old==0:
                assert ui.metrics[0].value.text()=='—' and ui.metrics[2].private_value.text()=='—'
                assert all(ui.model.data(ui.model.index(i,4))=='—' for i in range(ui.model.loaded))
            elapsed=time.perf_counter()-started
            name=f'pair-{new}-vs-{old}.png';assert window.grab().save(str(output/name))
            first_loaded=ui.model.loaded;page_started=time.perf_counter()
            ui.page_size.setCurrentIndex(ui.page_size.findData(100));ui.load_more();settle()
            assert ui.model.loaded==min(first_loaded+100,ui.model.total)
            query=window.sessions.queries[ui.ids]
            summary=query.summary()
            report['pairs'].append({'older':old or None,'newer':new,'measurement':ui.measurement.currentData(),
                'rows':summary['rows'],'filtered_rows':ui.model.total,'loaded_after_more':ui.model.loaded,
                'common_rows':summary['common_rows'],'private_rows':summary['private_rows'],
                'partition_valid':summary['partition_valid'],'comparison_and_first_page_seconds':elapsed,
                'load_more_seconds':time.perf_counter()-page_started,'screenshot':name,
                'measurement_note':ui.measurement_notice.text(),
                'status':window.status.text(),
                'verified_logical_delta':summary['verified_common_delta'],
                'unknown_disk_files':summary['unknown_common_disk_files']})
            ui.page_size.setCurrentIndex(ui.page_size.findData(20))
        # Explicit allocated mode: no silent disappearance under the size filter.
        ui.measurement.setCurrentIndex(ui.measurement.findData('allocated'));settle()
        assert ui.model.total>0 and ui.measurement_notice.isVisible()
        report['explicit_disk_view']={'rows':ui.model.total,'notice':ui.measurement_notice.text()}
        assert window.grab().save(str(output/'explicit-disk-unknown.png'))
    finally:
        for worker in window.workers:worker.stop.set()
        settle();window.close();app.processEvents()
    for ident,expected in originals.items():
        assert sha256(repo.path(repo.get(ident)))==expected
    assert sha256(source/'catalog.json')==before
    report.update(original_checksums_unchanged=True,original_catalog_unchanged=True,
                  work_bytes_after_close=repo.footprint()['work_bytes'],errors=errors)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    args=parser.parse_args();output=Path(args.output).resolve()
    allowed=Path(__file__).resolve().parents[1]/'artifacts'
    if not output.is_relative_to(allowed):raise ValueError('QA output phải nằm trong artifacts của dự án D:')
    output.mkdir(parents=True,exist_ok=True)
    try:
        report=run(output)
        (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    except BaseException:
        import traceback
        (output/'error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise
