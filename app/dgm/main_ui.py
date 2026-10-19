"""Keep the established GUI; replace only its file-record data services."""
from __future__ import annotations

import json
import time
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QLabel,QHBoxLayout,QMessageBox

from .gui import Window as FeatureWindow
from .browser_ui import DiskBrowser
from .history_ui import HistoryPanel
from .task_ui import TaskPanel
from .columnar_gui_backend import CatalogStore,Sessions
from .columnar_jobs import Jobs
from .config import load,save
from . import production_core as core
from .comparison_policy import choose_measure


class ColumnarBrowser(DiskBrowser):
    """Original widgets/model, with backend-specific data/presentation only."""
    def __init__(self,owner):
        self._measurement_pair=None;self._forced_measurement=False
        super().__init__(owner)
        self.measurement_notice=QLabel();self.measurement_notice.setWordWrap(True)
        self.measurement_notice.setObjectName('measurementNotice')
        self.measurement_notice.setStyleSheet('background:#fff5da;color:#715214;border:1px solid #ead6a3;border-radius:4px;padding:3px 7px;')
        self.measurement_notice.hide()
        self.layout().insertWidget(self.layout().indexOf(self.info),self.measurement_notice)
        self.units.blockSignals(True)
        self.units.setCurrentIndex(int(owner.preferences.values.get('units/columnar-binary',True)))
        self.units.blockSignals(False)
        from .presentation import set_units
        set_units(self.units.currentData())
        from .columnar_compare import REASONS
        if self.reason.findData('volume')<0:self.reason.addItem(REASONS['volume'],'volume')
        self.tree.headerItem().setToolTip(1,'Chiếm trên đĩa tại mốc mới của cặp đang dùng cho cây')

    def options(self):return dict(super().options(),measure=self.measurement.currentData())

    def compare(self):
        pair=self.selected_tree_ids()
        if pair:
            old,new=pair
            self.configure_measurement(self.owner.repo.get(new),self.owner.repo.get(old) if old else None)
        if self.newer.currentData():self.owner.start_comparison_progress()
        super().compare()

    def configure_measurement(self,newer,older=None):
        pair=(older['id'] if older else None,newer['id'])
        if self._measurement_pair!=pair:
            self._forced_measurement=False;self._measurement_pair=pair
        requested=self.owner.preferences.values.get('measurement/mode','allocated')
        policy=choose_measure(newer,older,requested if self._forced_measurement or requested=='logical' else 'auto')
        self.measurement.blockSignals(True)
        self.measurement.setCurrentIndex(self.measurement.findData(policy['measurement']))
        self.measurement.blockSignals(False)
        self.model.measurement=policy['measurement']
        self.model.headerDataChanged.emit(Qt.Orientation.Horizontal,0,8)
        self.measurement_policy=policy
        self.show_measurement_notice()

    def show_measurement_notice(self):
        if not hasattr(self,'measurement_notice'):return
        note=getattr(self,'measurement_policy',{}).get('measurement_note','')
        if note:note+=' Ngưỡng dung lượng vẫn giữ mục chưa rõ số đo.'
        self.measurement_notice.setText(note);self.measurement_notice.setVisible(bool(note))

    def measurement_changed(self,*args):
        self._forced_measurement=True
        pair=getattr(self,'ids',None) or self.selected_tree_ids()
        if pair:
            older,newer=pair
            self.measurement_policy=choose_measure(self.owner.repo.get(newer),self.owner.repo.get(older) if older else None,self.measurement.currentData())
            self.show_measurement_notice()
        super().measurement_changed(*args)

    def cancel_loading(self):
        super().cancel_loading()
        self.owner._scan_pipeline=False;self.owner._loading_result=False
        self.owner.status_panel.cancel_flow()
        self.owner.scan_progress.setFormat('%p% • Đã yêu cầu dừng tải; giữ các mốc lịch sử')

    def tree_measurement(self):return getattr(self,'_tree_measure',self.measurement.currentData())

    def measurement_options(self):return {'measure':self.tree_measurement()}

    def refresh_tree(self,previous,newer_id,pair_id=None):
        requested=self.measurement.currentData()
        policy=choose_measure(self.owner.repo.get(newer_id),self.owner.repo.get(previous) if previous else None,
                              requested if self._forced_measurement or requested=='logical' else 'auto')
        self._tree_measure=policy['measurement']
        super().refresh_tree(previous,newer_id,pair_id)
        self.tree.headerItem().setToolTip(1,('Chiếm trên đĩa' if self._tree_measure=='allocated' else 'Kích thước file')+
            ' tại mốc mới của cặp dùng cho cây.\n'+policy['measurement_note'])

    def snapshot_rows(self,rows,profile):
        # Keep this profile's latest first, but include adopted/imported mốc
        # whose source profile has no saved scanning configuration in this app.
        return [r for r in rows if r['profile']==profile]+[r for r in rows if r['profile']!=profile]

    def add_node(self,row,parent=None):
        super().add_node(row,parent)
        item=parent.child(parent.childCount()-1) if parent else self.tree.topLevelItem(self.tree.topLevelItemCount()-1)
        from .disk_measurements import exact
        disk=self.tree_measurement()=='allocated'
        value=row.get('allocated_after' if disk else 'after')
        item.setToolTip(1,('Chiếm trên đĩa' if disk else 'Kích thước file')+' toàn nhánh tại mốc mới: '+exact(value)+
            '\nGồm Chung và Riêng; không cộng số này thêm vào tổng file.')

    def units_changed(self):
        self.owner.preferences.set('units/columnar-binary',self.units.currentData())
        super().units_changed()

    def show_saved_overview(self):
        selected=self.selected_tree_ids()
        for row in self.owner.comparison_history():
            if (row['older'],row['newer'])==selected:
                summary=json.loads(row['summary'])
                if summary.get('disks_new') is not None:
                    self.configure_measurement(summary['newer'],summary.get('older'))
                    self.render_metrics(summary)
                    self.info.setText('Tóm tắt đã lưu • '+self.result_caption+' • Bấm So sánh để nạp cây và chi tiết.');return
        self.info.setText('Đã nạp danh sách mốc • Bấm So sánh để đọc Parquet; không dựng lại chỉ mục lúc mở app.')

    def view_summary(self,summary):
        summary=dict(summary)
        # Saved/startup summaries can be re-rendered without a fresh query.
        # Keep the physical counts when switching to/from the logical view.
        summary.setdefault('counts_allocated',summary.get('counts',[]))
        if self.measurement.currentData()=='logical' and 'counts_logical' in summary:
            summary['counts']=summary['counts_logical']
        else:summary['counts']=summary['counts_allocated']
        for group in ('common','private'):
            summary[group+'_counts']=[r for r in summary['counts'] if (r['presence']=='both')==(group=='common')]
        return summary

    def render_metrics(self,summary):
        summary=self.view_summary(summary)
        super().render_metrics(summary)
        if not summary.get('older'):
            self.metrics[0].set_parts('DUNG LƯỢNG CŨ','—','—','Chưa có mốc','Chưa có mốc')
            self.metrics[2].set_parts('CHÊNH LỆCH','—','—','Mốc ban đầu','Mốc ban đầu')
            self.metrics[2].setToolTip('Chưa có mốc trước để tính tăng/giảm. Dung lượng Mới là số đo tại lần quét đầu, không phải tăng từ 0 B.')
        if self.measurement.currentData()=='logical':
            for card,title in zip(self.metrics[:3],('KÍCH THƯỚC FILE CŨ','KÍCH THƯỚC FILE MỚI','CHÊNH LỆCH · FILE')):
                card.set_parts(title,card.value.text(),card.private_value.text(),card.note.text(),card.private_note.text())
        from .presentation import size
        lines=[]
        for row in summary.get('disks_new',[]):
            valid=row['valid']
            line=(f"{row['root']} · Sức chứa {size(row.get('capacity') if valid else None)} · "
                  f"Windows đã dùng {size(row.get('used') if valid else None)} · "
                  f"File đã đo {size(row.get('allocated_known'))} · Chưa quy vào file {size(row.get('residual'))}")
            if row.get('status')=='unknown-allocation':line+=' · Có file chưa rõ cấp phát'
            if row.get('status')=='mismatch' or not valid:line+=' · CẢNH BÁO số đo'
            lines.append(line+'\n'+row.get('note',row.get('warning','')))
        if not lines:lines=['Mốc này chưa ghi đủ số đo Windows; không dùng dung lượng ổ hiện tại để thay.']
        lines.append('Logical là kích thước theo đường dẫn, không phải Windows đã dùng. Tổng chỉ cộng file, không cộng chồng folder.\n'
                     'Phần chưa quy vào file không được gán thành file ≤10 MB hoặc kết luận tăng/giảm.')
        self.reconciliation_text='\n'.join(lines)
        self.reconciliation_label.setText(self.result_caption+' • '+lines[0].splitlines()[0])
        self.reconciliation_label.setToolTip(self.reconciliation_text)
        for index,key in ((0,'old'),(1,'new')):
            common=summary.get(key+'_totals') or {};private=summary.get('private_'+key+'_totals') or {}
            self.metrics[index].setToolTip(self.metrics[index].toolTip()+
                '\nTrên đĩa byte gốc — Chung: '+str(common.get('allocated'))+'; Riêng: '+str(private.get('allocated')))
        self.owner.status_panel.finish_results(summary,self.measurement.currentData())

    def show_reconciliation(self):
        summary=getattr(self,'current_summary',None)
        if summary:self.owner.show_text({'Tóm tắt':self.reconciliation_text,
            'Số đo ổ tại mốc cũ':summary.get('disks_old'),
            'Số đo ổ tại mốc mới':summary.get('disks_new'),
            'Chung + Riêng':summary.get('partition_valid')},'Đối soát byte gốc — không quét lại ổ')

    def cache_cleared(self):
        super().cache_cleared()
        self.tree_generation+=1;self.tree.clear();self.tree_ids=None;self.tree_pair=None
        self.current_summary=None
        for card in self.metrics:card.setText('—')


class ColumnarHistory(HistoryPanel):
    """Keep the original history tables, cards, selections and details."""
    def __init__(self,owner):
        super().__init__(owner)
        row=QHBoxLayout();self.migration=QLabel();self.migration.setWordWrap(True)
        row.addWidget(self.migration,1)
        self.import_button=owner.button('Nhập mốc JSONL cũ…',self.import_missing,row)
        self.layout().insertLayout(2,row)
        self.refresh_migration()

    def refresh_migration(self):
        plan=core.legacy_plan(repo=self.owner.repo);self.migration_plan=plan
        self.migration.setText(f"Parquet · {len(self.owner.repo.list())} mốc · còn {len(plan['pending_ids'])} mốc JSONL chưa nhập. "
                               'Chuyển một lần theo yêu cầu, giữ nguồn; không nạp file-record lúc mở app.')
        self.import_button.setEnabled(bool(plan['pending_ids']))

    def import_missing(self):
        if not self.require_idle():return
        self.refresh_migration();plan=self.migration_plan
        if not plan['pending_ids']:return
        if QMessageBox.question(self,'Nhập lịch sử cũ',f"Nhập {len(plan['pending_ids'])} mốc JSONL sang Parquet? Giữ nguồn, có thể dừng/tiếp tục.")!=QMessageBox.StandardButton.Yes:return
        job=self.owner.jobs.create('legacy',{'source_home':plan['source_home'],'ids':plan['pending_ids']})
        self.owner.execute_job(job)

    def normalize_selected(self):
        if not self.require_idle():return
        ids=[self.scan_rows[i.row()]['id'] for i in self.scans.selectionModel().selectedRows()]
        if not ids:QMessageBox.information(self,'Chọn lượt','Chọn một hoặc nhiều lượt Parquet để kiểm checksum.');return
        def verify(worker):
            results=[]
            for ident in ids:
                if worker.stop.is_set():break
                scan=self.owner.repo.get(ident);self.owner.repo.verify(scan)
                from .columnar_ingest import PATH_KEY_VERSION
                results.append({'id':ident,'path_key_version':scan.get('path_key_version'),'checksum':'Đúng',
                    'chuẩn hóa':'Đã chuẩn hóa khi nhập' if scan.get('path_key_version')==PATH_KEY_VERSION else
                                 'Khóa cũ: cần nhập lại từ nguồn; không tự ghi đè mốc'})
            return results
        self.owner.job(verify,lambda results:self.owner.show_text(results,'Kiểm Parquet — chuẩn hóa một lần khi nhập, không sửa dữ liệu'))

    def select_legacy(self):
        # Select only this catalog's old-key snapshots. Source JSONL IDs belong
        # to a separate namespace and are offered by the explicit import button.
        super().select_legacy()

    def recover_selected(self):
        self.owner.tabs.setCurrentIndex(4)
        self.owner.log_panel.tasks.refresh()
        self.owner.status.setText('Chọn tác vụ dang dở tại Nhật ký rồi bấm Tiếp tục. Parquet đã xuất đủ không cần phục hồi tổng.')


class ColumnarTasks(TaskPanel):
    """Use the existing queue UI for the new small JSON journals."""
    def refresh(self):
        if not self.isVisible() and self.signature is not None:return
        from .presentation import local_time
        from PySide6.QtWidgets import QTableWidgetItem
        rows=self.owner.jobs.list();signature=json.dumps(rows,sort_keys=True)
        if signature==self.signature:return
        selected=self.selected();ident=selected['id'] if selected else None
        self.rows=rows;self.signature=signature;self.table.setRowCount(len(rows))
        for index,row in enumerate(rows):
            state=row['state']
            values=({'scan':'Quét','legacy':'Nhập JSONL','csv':'Nhập WizTree CSV'}[row['operation']],
                {'complete':'Đã xong','pending':'Chưa chạy','stopped':'Đã dừng','failed':'Lỗi','interrupted':'Dang dở','running':'Đang chạy'}.get(state,state),
                row.get('error') or ('Đã lưu nguồn đủ' if row.get('sources') else 'Quét dở chạy lại; nguồn đủ dùng tiếp'),
                local_time(row['updated']),row.get('metadata',{}).get('app_version','—'))
            for col,value in enumerate(values):
                item=QTableWidgetItem(str(value));item.setToolTip(str(value));self.table.setItem(index,col,item)
            if row['id']==ident:self.table.setCurrentCell(index,0)
        if rows and self.table.currentRow()<0:self.table.setCurrentCell(0,0)
        self.update_buttons()

    def update_buttons(self):
        row=self.selected();self.resume_button.setEnabled(bool(row and not row.get('running') and row['state']!='complete' and not self.owner.workers))

    def continue_selected(self):
        row=self.selected()
        if not row or row.get('running') or row['state']=='complete' or self.owner.workers:return
        if QMessageBox.question(self,'Tiếp tục tác vụ','Nguồn đã xuất đủ được dùng tiếp; quét dở phải chạy lại từ đầu. Không tự xóa file thật.')==QMessageBox.StandardButton.Yes:
            self.owner.execute_job(row)

    def new_scan(self):
        row=self.selected()
        if not row or row['operation']!='scan' or self.owner.workers:return
        if QMessageBox.question(self,'Quét mới','Quét lại hiện trạng bằng phạm vi tác vụ này? Giữ mốc và điểm lưu cũ.')==QMessageBox.StandardButton.Yes:
            args=row['arguments'];self.owner.job(lambda w:core.scan(args['name'],args['profile'],w.stop,w.event.emit),self.owner.scan_done,scan_job=True)

    def details(self):
        row=self.selected()
        if row:self.owner.show_text(self.owner.jobs.get(row['id']),'Điểm tiếp tục — chỉ metadata')

    def remove(self):
        row=self.selected()
        if not row or self.owner.workers:return
        if QMessageBox.question(self,'Bỏ điểm tiếp tục','Chỉ bỏ JSON/export tạm của tác vụ, giữ snapshot, file thật và Task/Service?')==QMessageBox.StandardButton.Yes:
            try:self.owner.jobs.discard(row['id']);self.signature=None;self.refresh()
            except Exception as exc:self.owner.fail(str(exc))


class MainWindow(FeatureWindow):
    browser_type=ColumnarBrowser
    history_type=ColumnarHistory
    task_type=ColumnarTasks

    def ask_run_profile(self,name,saved,edited):
        from .config import profile_caption
        box=QMessageBox(self);box.setWindowTitle('Chọn cấu hình quét trước khi chạy')
        box.setText(f'Cấu hình {name} đang có thay đổi chưa lưu.\n'
                    f'Đã lưu: {profile_caption(saved)}\nĐang chọn: {profile_caption(edited)}\n'
                    'Quét tay và lịch chạy đều dùng cấu hình đã lưu. Bạn muốn lưu thay đổi trước khi chạy?')
        box.setDetailedText(json.dumps({'Đã lưu':saved,'Đang chọn':edited},ensure_ascii=False,indent=2))
        save_button=box.addButton('Lưu và chạy',QMessageBox.ButtonRole.AcceptRole)
        stored_button=box.addButton('Dùng cấu hình đã lưu',QMessageBox.ButtonRole.ActionRole)
        box.addButton('Hủy',QMessageBox.ButtonRole.RejectRole);box.setDefaultButton(save_button)
        box.exec()
        return 'save' if box.clickedButton() is save_button else 'saved' if box.clickedButton() is stored_button else 'cancel'

    def resolve_run_profile(self,name):
        from .config import profile
        saved=profile(name)
        if self.profile_combo.currentText()==name and self.profile_dirty():
            choice=self.ask_run_profile(name,saved,self.edited_profile())
            if choice=='cancel':return None
            if choice=='save':
                if not self.save_profile(name=name):return None
                saved=profile(name)
                self.refresh_schedules()
        return saved

    def scan_profile_for_run(self):
        return self.resolve_run_profile(self.profile_combo.currentText())

    def prepare_schedule_run(self,spec):
        return self.resolve_run_profile(spec['profile']) is not None

    def __init__(self):
        self._scan_pipeline=False;self._loading_result=False;self._detail_ready=False;self._tree_ready=False
        self.repo=core.repository();self.sessions=Sessions(self.repo);self.jobs=Jobs(self.repo)
        # Non-file feature metadata still needs a tiny compatibility store in
        # an explicitly SQLite-configured, otherwise-empty installation.
        from .history_backend import log_mode
        from .config import home
        if not log_mode() and not (home()/'data/history.sqlite').exists():
            from .store import Store
            store=Store(migrate_keys=False);store.close()
        self.scan_store=lambda **kwargs:CatalogStore(self.repo,**kwargs)
        self.comparison_store=self.sessions.handle
        self.comparison_history=self.sessions.history
        from .presentation import set_size_formatter
        from .disk_measurements import size
        set_size_formatter(size)
        super().__init__()
        self.threshold.valueChanged.connect(lambda *_:self.browser.reload() if self.browser.pair_id else None)

    def job(self,*args,**kwargs):
        worker=super().job(*args,**kwargs)
        if worker:
            worker.dgm_generation=self.browser.generation if hasattr(self,'browser') else None
            worker.finished.connect(lambda:QTimer.singleShot(0,self.release_unused_sessions))
        return worker

    def accept_progress_event(self,worker,event,data):
        timing=data.get('timing') or {}
        return timing.get('operation')!='comparison' or getattr(worker,'dgm_generation',None)==self.browser.generation

    def release_unused_sessions(self):
        if self.workers:return
        browser=self.browser
        keep={key for key in (getattr(browser,'ids',None),browser.tree_ids) if key}
        if self.closing_after_stop:keep=set()
        self.sessions.retain(keep)

    def latest_signature(self):
        rows=self.repo.list()
        return (rows[0]['id'],rows[0]['status']) if rows else (0,'')

    def startup_history_maintenance(self):pass

    def progress(self,event,data):
        if event=='progress' and 'percent' in data:
            timing=data.get('timing')
            value=max(0,min(100,int(data['percent'])))
            if timing:
                self.status_panel.accept(timing)
                operation=timing['operation']
                if operation in ('scan','import'):
                    value=int(value*.60);prefix='Quét / nhập / lưu'
                else:
                    value=(60 if self._scan_pipeline else 0)+int(value*(.35 if self._scan_pipeline else .95));prefix='So sánh'
                message=prefix+' · '+data.get('message','')
            else:message=data.get('message','')
            self.scan_progress.setValue(value);self.scan_progress.setFormat('%p% • '+data.get('message',''))
            self.scan_progress.setFormat('%p% • '+message);self.scan_progress.setToolTip(message)
            self.status.setText(message);return
        super().progress(event,data)

    def set_scan_busy(self,busy):
        if busy and not self._scan_ui_busy:
            self._scan_pipeline=True;self._loading_result=False;self.status_panel.begin(reset=True)
        super().set_scan_busy(busy)

    def start_comparison_progress(self):
        if not self._scan_pipeline:self.status_panel.begin(reset=True)
        self._loading_result=True;self._detail_ready=False
        self._tree_ready=self.browser.tree_mode.currentData()!='selected'
        self.scan_progress.setValue(60 if self._scan_pipeline else 0)
        self.scan_progress.setFormat('%p% • So sánh · đang chuẩn bị cặp mốc trong RAM')
        self.status.setText('So sánh · đang chuẩn bị cặp mốc; chưa nạp bảng chi tiết')

    def detail_loading(self,group,pair):
        if pair!=getattr(self.browser,'ids',None):return
        self._page_started=time.perf_counter()
        text=f"Nạp chi tiết · đang truy vấn {self.browser.page_size.currentData():,} mục theo bộ lọc và thứ tự toàn tập"
        self.status.setText(text);self.scan_progress.setToolTip(text)
        self.scan_progress.setValue(96);self.scan_progress.setFormat('%p% • Nạp trang chi tiết / hiển thị bảng')

    def detail_loaded(self,ids,summary,page,elapsed):
        if ids!=getattr(self.browser,'ids',None):return
        self._detail_ready=True
        self.status_panel.finish_results(summary,self.browser.measurement.currentData(),page_time=elapsed)
        # Allow the model/layout/paint events queued by accept() to run first.
        QTimer.singleShot(0,self.finish_result_progress)

    def tree_loaded(self,ids,elapsed):
        if ids!=getattr(self.browser,'ids',None):return
        self._tree_ready=True;self.status_panel.tree_time=elapsed;self.status_panel.render()
        self.finish_result_progress()

    def finish_result_progress(self):
        if not self._detail_ready or (self._loading_result and not self._tree_ready):return
        restoring=self.browser.restore_target>self.browser.model.loaded and bool(self.browser.model.next_cursor)
        if restoring:return
        self._loading_result=False;self._scan_pipeline=False;self.status_panel.complete_flow()
        self.scan_progress.setValue(100)
        self.scan_progress.setFormat(f"100% • Đã nạp cây / bảng · {self.browser.model.loaded:,}/{self.browser.model.total:,} dòng; xem Chi tiết thời gian / kết quả")

    def fail(self,message):
        self._scan_pipeline=False;self._loading_result=False
        self.status_panel.cancel_flow('failed')
        super().fail(message)

    def scan_done(self,result):
        if result.get('before_start'):
            self._scan_pipeline=False;self.status.setText(result.get('message','Đã hủy UAC; chưa chạy tác vụ'));return
        if result.get('status')=='cancelled':
            self._scan_pipeline=False;self._loading_result=False;self.status_panel.cancel_flow()
            self.status.setText('Đã dừng an toàn; tiếp tục tại Nhật ký. Quét dở chạy lại; nguồn đã xuất đủ dùng tiếp.');return
        if result.get('timing'):self.status_panel.accept(result['timing'])
        self.refresh_history()
        if result.get('status') in ('complete','partial'):
            self.newer.setCurrentIndex(self.newer.findData(result['scan_id']))
            self.status.setText(f"Đã lưu mốc #{result['scan_id']} · {result.get('files',0):,} file; tiếp tục so sánh và nạp bảng")
            self.compare()
        else:self.fail(result.get('error','Quét không hoàn tất; xem Nhật ký'))

    def execute_job(self,job):
        if self.workers:return
        def done(rows):
            self.refresh_history();self.history_panel.refresh_migration();self.history_panel.invalidate()
            self.log_panel.tasks.signature=None;self.log_panel.tasks.refresh()
            if rows:
                self.newer.setCurrentIndex(self.newer.findData(rows[-1]['id']));self.compare()
        self.job(lambda w:self.jobs.execute(job['id'],stop=w.stop,notify=w.event.emit),done,scan_job=True)

    def make_system(self):
        super().make_system()
        self.db_limit.setValue(self.repo.limits['history_mb'])
        for field in (self.retention,self.max_snapshots,self.source_limit,self.source_days):
            field.setEnabled(False);field.setToolTip('Chính sách cũ không áp dụng cho Parquet. Chọn mốc tại Lịch sử khi cần dọn.')
        self.storage_panel.compact_button.setText('Đóng phiên RAM / dọn tạm')
        self.storage_panel.migrate_button.setText('SQLite cũ → JSONL (nguồn cũ)')
        self.storage_panel.clear_index_button.setText('Dọn tạm Parquet, giữ mốc')
        self.storage_panel.note.setText('Parquet lưu metadata; DuckDB so sánh trong RAM và dọn spill khi đóng phiên. '
            'Không tự xóa mốc. Lịch sử JSONL cũ được nhập một lần theo yêu cầu tại Lịch sử.')

    def save_limits(self):
        cfg=load();limits={**cfg.get('columnar_limits',{}),'history_mb':self.db_limit.value()}
        cfg['columnar_limits']=limits;save(cfg);self.repo.limits.update(limits)
        self.status.setText('Đã lưu giới hạn Parquet; không tự xóa mốc cũ.')

    def clear_comparison(self):
        if self.workers:raise RuntimeError('Chờ tác vụ hiện tại trước khi đóng phiên RAM')
        self.sessions.close();self.browser.cache_cleared()

    def closeEvent(self,event):
        if not self.workers:self.sessions.close()
        super().closeEvent(event)
        if event.isAccepted():
            from .presentation import set_size_formatter
            set_size_formatter()
