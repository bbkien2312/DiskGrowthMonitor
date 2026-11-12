"""Keep the established GUI; replace only its file-record data services."""
from __future__ import annotations

import json
import time
from PySide6.QtCore import QTimer,Qt,QSignalBlocker
from PySide6.QtWidgets import QLabel,QHBoxLayout,QMessageBox

from .gui import Window as FeatureWindow
from .browser_ui import DiskBrowser
from .comparison_summary_ui import ComparisonSummaryPanel
from .history_ui import HistoryPanel
from .task_ui import TaskPanel
from .columnar_gui_backend import CatalogStore,Sessions
from .columnar_jobs import Jobs
from .config import load,save
from . import production_core as core
from .comparison_policy import choose_measure
from .delta_accounting import COMPARISON_VERSION
from .disk_measurements import display_delta,display_size,exact
from .platform_ops import ProcessCancelled
from .snapshot_scope import capture_time


class ColumnarBrowser(DiskBrowser):
    """Original widgets/model, with backend-specific data/presentation only."""
    independent_scan_profile=True

    def __init__(self,owner):
        self._measurement_pair=None;self._forced_measurement=False;self.unified=True
        self._milestones=[];self._follow_latest=True
        super().__init__(owner)
        self.summary_panel=ComparisonSummaryPanel();self.result_stack.addWidget(self.summary_panel)
        self._summary_worker=None;self._summary_cache=None;self.current_summary=None
        self.result_tabs.currentChanged.connect(self.show_result_tab)
        self.summary_panel.retry.connect(self.refresh_summary_panel)
        self.measurement_notice=QLabel();self.measurement_notice.setWordWrap(True)
        self.measurement_notice.setObjectName('measurementNotice')
        self.measurement_notice.setStyleSheet('background:#fff5da;color:#715214;border:1px solid #ead6a3;border-radius:4px;padding:3px 7px;')
        self.measurement_notice.hide()
        self.layout().insertWidget(self.layout().indexOf(self.info),self.measurement_notice)
        self.sections.hide()
        self.model.header_labels[4]='± File'
        self.presence.setToolTip('Lọc hiện diện: Cả hai / Chỉ Cũ / Chỉ Mới. Không thay đổi tổng cây hoặc thống kê.')
        self.reason.setToolTip('Lý do chưa xác minh: khác phạm vi, loại trừ, lỗi đọc hoặc dữ liệu thiếu. Không đồng nghĩa đã xóa.')
        for column,hint in {0:'TT: trạng thái; ↑ tăng, ↓ giảm, + thêm, − mất, ? chưa rõ, ≈ chưa quy rõ nguyên nhân.',
            2:'Cũ: số đo lưu trong mốc cũ; — chưa có mốc, × không có, ∅ không quét, ? thiếu số đo.',
            3:'Mới: số đo lưu trong mốc mới; không đọc kích thước file hiện tại.',
            4:'± File: chênh kích thước file ghi nhận. Chỉ Cũ đóng góp âm; chỉ Mới đóng góp dương. Không phải Windows đã dùng.',
            6:'± Đĩa: chênh cấp phát ghi nhận, không chỉ file có ở cả hai mốc. Hardlink có thể chuyển nơi quy byte; tổng ổ chỉ tính một lần.'}.items():
            self.model.header_hints[column]=hint
        self.legend_toggle.setToolTip('Giải thích trạng thái, ký hiệu và màu. Chỉ xuất hiện một mốc chưa đủ để khẳng định thêm/xóa thật.')
        self.units.blockSignals(True)
        self.units.setCurrentIndex(int(owner.preferences.values.get('units/columnar-binary',True)))
        self.units.blockSignals(False)
        from .presentation import set_units
        set_units(self.units.currentData())
        from .columnar_compare import REASONS
        if self.reason.findData('volume')<0:self.reason.addItem(REASONS['volume'],'volume')
        self.tree.headerItem().setToolTip(1,'Chiếm trên đĩa tại mốc mới của cặp đang dùng cho cây')
        self.newer.setToolTip('Mốc mới nhất đứng đầu và tự theo lượt mới. Chọn một mốc cũ hơn để ghim. Danh sách không phụ thuộc profile quét.')
        self.older.setToolTip('Chỉ liệt kê mốc trước Mới. Tự động lấy mốc trước cùng profile của lượt Mới.')
        self.newer.currentIndexChanged.connect(self.newer_changed)
        self.older.currentIndexChanged.connect(self.clear_selected_result)

    def options(self):return dict(super().options(),measure=self.measurement.currentData(),section='all')

    def before_compare(self,older,newer):
        self.configure_measurement(newer,older)
        self.auto_baseline=False;self.model.private=False
        self.owner.start_comparison_progress()

    def compare(self):
        self.current_summary=None;self.reset_summary_panel('Đang chuẩn bị cặp mốc…' if self.newer.currentData() else 'Chưa có mốc hợp lệ.')
        super().compare()
        self.model.private=False

    def show_result_tab(self,index):
        self.result_stack.setCurrentIndex(index)
        self.detail_paging.setVisible(index==0)
        if index==1:self.refresh_summary_panel()

    def reset_summary_panel(self,message):
        if self._summary_worker:self._summary_worker.stop.set()
        self._summary_worker=None;self._summary_cache=None
        self.pair_header.setText(message)
        self.summary_panel.overview.hide()
        self.summary_panel.verticalScrollBar().setValue(0)
        self.summary_panel.set_state(message)

    def refresh_summary_panel(self):
        if self.result_tabs.currentIndex()!=1:return
        summary=self.current_summary
        if not summary:return
        mode=self.measurement.currentData();baseline=not summary.get('older')
        counts={}
        for row in summary.get('counts',[]):
            counts[row['change']]=counts.get(row['change'],0)+row['count']
        count=lambda *keys:sum(counts.get(key,0) for key in keys)
        title='Cấp phát file' if mode=='allocated' else 'Kích thước file'
        disk_lines=[f"{row['root']} · ± Ổ {self.delta_text(row['used_delta'])}"
                    for row in summary.get('disk_changes',[]) if row.get('used_delta') is not None]
        if baseline:
            details=f"Mốc ban đầu · {self.metrics[3].value.text()} · chưa tính tăng/giảm."
        else:
            details=(f"{title} ghi nhận: {self.metrics[2].value.text()} · {self.metrics[3].value.text()}\n"
                     f"{count('grown'):,} tăng · {count('shrunk'):,} giảm · {count('new'):,} thêm · {count('missing'):,} mất"
                     f" · {count('not_scanned','unknown','partial'):,} không quét/chưa rõ.")
            details+='\n'+('  |  '.join(disk_lines) if disk_lines else 'Chưa có số đo Windows đủ để tính ± Ổ.')
            if summary.get('scope_compatible') is False:details+='\nKhác phạm vi quét: ± ghi nhận có thể gồm phần chỉ được quét ở một mốc.'
        self.summary_panel.overview.setText(details);self.summary_panel.overview.show()
        if baseline or not self.pair_id:
            self.summary_panel.set_state('Chọn thêm mốc Cũ để xếp hạng thay đổi.' if baseline else
                                         'Bấm So sánh để nạp top thư mục của cặp đã chọn.')
            return
        key=self.pair_id,mode
        unit='binary' if self.units.currentData() else 'decimal'
        if self._summary_cache and self._summary_cache[0]==key:
            self.summary_panel.render(self._summary_cache[1],unit,self.delta_text)
            return
        if self._summary_worker:
            if self._summary_worker.summary_key==key and not self._summary_worker.stop.is_set():return
            self._summary_worker.stop.set()
        self.summary_panel.set_state('Đang lấy top thư mục từ phiên so sánh…')
        def fetch(worker):
            handle=self.comparisons(worker.stop)
            try:return handle.folder_highlights(*key)
            except ProcessCancelled:return None
            finally:handle.close()
        def done(groups):
            if groups is None or self._summary_worker is not worker or key!=(self.pair_id,self.measurement.currentData()):return
            self._summary_cache=(key,groups);self.refresh_summary_panel()
        def finished():
            if self._summary_worker is not worker:return
            self._summary_worker=None
            if not self._summary_cache or self._summary_cache[0]!=key:
                if key==(self.pair_id,self.measurement.currentData()):
                    self.summary_panel.set_state('Chưa tải xong top thư mục.',retry=True)
        worker=self.owner.job(fetch,done)
        worker.summary_key=key;self._summary_worker=worker;self.track(worker)
        worker.finished.connect(finished)

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
        if self._summary_worker:self._summary_worker.stop.set()
        if self.result_tabs.currentIndex()==1:self.summary_panel.set_state('Đang đổi số đo cho cặp mốc…')
        pair=getattr(self,'ids',None) if self.pair_id else self.selected_tree_ids()
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
        self._milestones=rows
        return rows

    def milestone_order(self,row):return capture_time(row),row['id']
    def keep_newer(self,changed):return not self._follow_latest
    def keep_older(self,changed):return True

    def older_candidates(self,rows):
        newer=next((r for r in rows if r['id']==self.newer.currentData()),None)
        return [r for r in rows if capture_time(r)<capture_time(newer)] if newer else []

    def refresh(self,profile,*,load_tree=True):
        before=self.older.currentData(),self.newer.currentData()
        with QSignalBlocker(self.newer),QSignalBlocker(self.older):
            super().refresh(profile,load_tree=load_tree)
        after=self.older.currentData(),self.newer.currentData()
        if self.newer.currentIndex()<=0:self._follow_latest=True
        if before[1] and before!=after:self.clear_selected_result()

    def newer_changed(self,*_):
        self._follow_latest=self.newer.currentIndex()<=0
        with QSignalBlocker(self.older):
            self.populate_older(self._milestones,self.older.currentData())
        self.clear_selected_result()

    def clear_selected_result(self,*_):
        """Never display the old pair's result under a newly selected pair."""
        self.generation+=1;self.stop_details()
        self.ids=(None,None);self.pair_id=None;self.current_summary=None;self.result_caption=''
        self.reset_summary_panel('Đã chọn mốc · Bấm So sánh')
        self.model.reset();self.owner.rows=[];self.load_button.setEnabled(False)
        self.pending_open=None
        if self.tree_mode.currentData()=='selected':
            for worker in list(self.loading_workers):worker.stop.set()
            self.tree_generation+=1;self.tree.clear();self.tree_ids=None;self.tree_pair=None
        self.update_page_text()
        self.measurement_notice.hide()
        for metric,label in zip(self.metrics,('CŨ','MỚI','± GHI NHẬN','PHẠM VI')):
            metric.set_single(label,'—','Chưa so sánh cặp đã chọn')
            metric.setToolTip('')
        self.reconciliation_text=''
        self.reconciliation_label.setText('Đã đổi mốc · Bấm So sánh để nạp kết quả')
        self.reconciliation_label.setToolTip('')
        self.info.setText('Đã chọn cặp mới. Bấm So sánh để xem kết quả của đúng hai mốc.')
        self.info.setToolTip('')
        self.owner.status_panel.clear_comparison()
        if self.owner.scan_worker is None and not self.owner._scan_pipeline:
            self.owner.status.setText('Đã đổi mốc · chờ So sánh')
            self.owner.scan_progress.setValue(0)
            self.owner.scan_progress.setFormat('Chờ so sánh')

    def add_node(self,row,parent=None):
        super().add_node(row,parent)
        item=parent.child(parent.childCount()-1) if parent else self.tree.topLevelItem(self.tree.topLevelItemCount()-1)
        from .disk_measurements import exact
        disk=self.tree_measurement()=='allocated'
        value=row.get('allocated_after' if disk else 'after')
        difference=row.get('allocated_delta' if disk else 'delta')
        if difference is not None and not row.get('baseline'):
            item.setText(2,self.delta_text(difference))
        item.setToolTip(1,('Chiếm trên đĩa' if disk else 'Kích thước file')+' toàn nhánh tại mốc mới: '+exact(value)+
            '\nGồm mọi file ghi nhận trong nhánh; không cộng folder thêm lần nữa.')
        item.setToolTip(2,'± = tổng nhánh Mới − Cũ, gồm cả file chỉ có ở một mốc.\n'+exact(difference)+'\n'+
            ('Có phần chưa xác minh nguyên nhân; không khẳng định mọi mục vắng là đã xóa.' if row.get('attribution_uncertain') else 'Tính theo byte gốc, không theo các số đã làm tròn.'))

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
                    self.info.setText('Tóm tắt đã lưu • Bấm So sánh để nạp cây và chi tiết.');return
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
        self.current_summary=summary
        self.show_measurement_notice()
        if summary.get('scope_compatible') is False:
            self.measurement_notice.setText(self.measurement_notice.text()+' Khác phạm vi/loại trừ: ± ghi nhận không đồng nghĩa dung lượng ổ tăng/giảm do file. Xem ± Ổ riêng.')
            self.measurement_notice.show()
        from .presentation import size,local_time
        from .comparison_visuals import gui_change_color
        mode=self.measurement.currentData();baseline=not summary.get('older')
        unit='binary' if self.units.currentData() else 'decimal'
        precise_size=lambda value:display_size(value,unit,precise=True)
        title='Cấp phát file' if mode=='allocated' else 'Kích thước file'
        captions=[];totals_tips=[]
        from zoneinfo import ZoneInfo
        for index,side in ((0,'old'),(1,'new')):
            totals=summary.get('full_'+side+'_totals') or {}
            value=totals.get(mode);unknown=totals.get(mode+'_unknown_files',0)
            text='—' if index==0 and baseline else precise_size(value) if not unknown else 'Chưa rõ' if unknown>=totals.get('files',0) else '≥ '+precise_size(totals.get(mode+'_known'))
            self.metrics[index].set_single('CŨ' if index==0 else 'MỚI',text,
                'Chưa có mốc' if index==0 and baseline else title+(f' · {unknown:,} thiếu số đo' if unknown else ' · toàn phạm vi'))
            self.metrics[index].setToolTip(title+' của tất cả file tại mốc; không cộng chồng folder.\nByte gốc: '+str(value)+
                '\nLogical không phải Windows đã dùng. ≥ là phần đã đo, không phải tổng đầy đủ.')
            milestone=summary.get('older' if index==0 else 'newer')
            if milestone:
                when=capture_time(milestone).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%d/%m %H:%M')
                captions.append(f"{'Cũ' if index==0 else 'Mới'} #{milestone['id']} · {when} · {text}")
                totals_tips.append(local_time(milestone['started'])+' · '+exact(value))
            else:captions.append('Mốc ban đầu')
        delta=summary.get('observed_disk_delta' if mode=='allocated' else 'observed_delta')
        self.metrics[2].set_single('± GHI NHẬN','—' if baseline else self.delta_text(delta),
            'Mốc ban đầu' if baseline else 'Theo byte gốc · không trừ số đã rút gọn')
        self.metrics[2].value.setStyleSheet('color:'+gui_change_color(delta)+';')
        self.metrics[2].setToolTip('Hiệu tổng số đo hai snapshot, gồm cả đường dẫn chỉ Cũ/Mới.\n'
            'Không khẳng định mọi mục vắng là đã xóa. Phần đủ bằng chứng được ghi riêng trong Đối soát.\nByte gốc: '+str(delta))
        counts=summary.get('counts',[])
        files=sum(r['count'] for r in counts if r['kind']=='file');folders=sum(r['count'] for r in counts if r['kind']=='folder')
        self.metrics[3].set_single('PHẠM VI',f'{files:,} file · {folders:,} TM','Hai mốc · gồm cả mục chỉ một phía')
        self.metrics[3].setToolTip('Số đường dẫn trong hợp của hai snapshot; TM = thư mục. Không chỉ các dòng đã tải.\n'
            'Cây và tổng quan không đổi khi lọc, sắp xếp hoặc tải thêm.')
        a,b=summary.get('older'),summary.get('newer')
        self.result_caption=' → '.join(captions)
        self.pair_header.setText(' →\n'.join(captions))
        self.pair_header.setToolTip(self.result_caption+'\n'+title+' tại từng mốc (không phải Windows đã dùng):\n'+'\n'.join(totals_tips))
        lines=[]
        for change in summary.get('disk_changes',[]):
            if not baseline:lines.append(f"{change['root']} · ± Ổ {self.delta_text(change['used_delta'])} · ± Cấp phát {self.delta_text(change['files_delta'])} · Còn lại {self.delta_text(change['unassigned_delta'])}")
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
        self.reconciliation_text='\n'.join(lines)+('\nCũ: '+local_time(a['started']) if a else '')+'\nMới: '+local_time(b['started'])
        self.reconciliation_label.setText(lines[0].splitlines()[0])
        self.reconciliation_label.setToolTip(self.reconciliation_text)
        self.owner.status_panel.finish_results(summary,self.measurement.currentData())
        self.refresh_summary_panel()

    def delta_text(self,value):
        from .comparison_visuals import gui_change_text
        return gui_change_text(value) if value is None else display_delta(value,'binary' if self.units.currentData() else 'decimal')

    def show_reconciliation(self):
        summary=getattr(self,'current_summary',None)
        if summary:self.owner.show_text({'Tóm tắt':self.reconciliation_text,
            'Số đo ổ tại mốc cũ':summary.get('disks_old'),
            'Số đo ổ tại mốc mới':summary.get('disks_new'),
            'Hiệu tổng file':summary.get('observed_disk_delta'),
            'Đủ bằng chứng':summary.get('verified_allocated_delta'),
            'Kiểm tra cân bằng':summary.get('balance_checks'),
            'Cần tính lại chi tiết':summary.get('needs_recompare')},'Đối soát byte gốc — không quét lại ổ')

    def cache_cleared(self):
        super().cache_cleared()
        self.tree_generation+=1;self.tree.clear();self.tree_ids=None;self.tree_pair=None
        self.current_summary=None
        self.reset_summary_panel('Cache đã xóa · Bấm So sánh')
        for card in self.metrics:card.setText('—')


class ColumnarHistory(HistoryPanel):
    """Keep the original history tables, cards, selections and details."""
    def __init__(self,owner):
        super().__init__(owner)
        for table,labels in ((self.scans,('Lần / Profile','Lúc','Phạm vi','Tool','Lưu','File / TM','File / Đĩa','Giây','Δ Log','Độ phủ','Chuẩn','Tùy chọn')),
                             (self.pairs,('Mốc','Cũ','Mới','±','↑','↓','+','−','Khác','Δ Log'))):
            for index,label in enumerate(labels):
                old=table.horizontalHeaderItem(index).text();table.horizontalHeaderItem(index).setText(label)
                table.horizontalHeaderItem(index).setToolTip(old+' · Số đo giữ theo byte gốc; thời gian UTC+07.')
        self.summary_cards[0].layout().itemAt(0).widget().setText('± GHI NHẬN')
        self.summary_cards[1].layout().itemAt(0).widget().setText('CHƯA QUY RÕ')
        row=QHBoxLayout();self.migration=QLabel();self.migration.setWordWrap(True)
        row.addWidget(self.migration,1)
        self.import_button=owner.button('Nhập mốc JSONL cũ…',self.import_missing,row)
        self.layout().insertLayout(2,row)
        self.refresh_migration()

    def render_pair_summary(self,summary):
        mode=summary.get('history_measure','allocated')
        value=summary.get('observed_disk_delta' if mode=='allocated' else 'observed_delta')
        self.summary_cards[0].set_value(self.owner.browser.delta_text(value),'Hiệu toàn bộ hai mốc · '+('Đĩa' if mode=='allocated' else 'File'))
        self.summary_cards[1].set_value(f"{summary.get('unverified_all_files',0):,} file",'Chưa xác minh nguyên nhân, không phải thiếu toàn bộ dung lượng')
        self.scope_note.setText('± ghi nhận gồm mọi file hai mốc; chỉ Cũ/Mới không tự khẳng định xóa/thêm thật. '+
            ('Chi tiết dùng thuật toán cũ; sẽ tính lại khi mở, giữ nguyên snapshot.' if summary.get('needs_recompare') else 'Đã kiểm tra hiệu tổng bằng đóng góp file.'))

    def selection_details(self):
        super().selection_details()
        if self.tabs.currentIndex()!=1:return
        index=self.pairs.currentRow()
        if not 0<=index<len(self.pair_rows):return
        from .presentation import local_time
        show_delta=self.owner.browser.delta_text
        s=json.loads(self.pair_rows[index]['summary'] or '{}');a,b=s.get('older'),s.get('newer')
        lines=[('Cũ #'+str(a['id'])+' · '+local_time(a['started'])) if a else 'Chưa có mốc cũ',
               ('Mới #'+str(b['id'])+' · '+local_time(b['started'])) if b else 'Chưa có mốc mới',
               '± File: '+show_delta(s.get('observed_delta')),
               '± Cấp phát: '+show_delta(s.get('observed_disk_delta')),
               'Phần cấp phát đủ bằng chứng: '+show_delta(s.get('verified_allocated_delta'))]
        for row in s.get('disk_changes',[]):
            lines.append(row['root']+' · ± Ổ '+show_delta(row['used_delta'])+' · Còn lại '+show_delta(row['unassigned_delta']))
        lines.append('Hiệu tổng ghi nhận không tự khẳng định mọi đường dẫn vắng là đã xóa. Không gán phần còn lại cho file nhỏ.')
        if s.get('needs_recompare'):lines.append('Chi tiết cũ sẽ tính lại bằng thuật toán mới khi mở, không sửa snapshot.')
        self.details.setPlainText('\n'.join(lines))

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
            operation={'scan':'Quét','legacy':'Nhập JSONL','csv':'Nhập WizTree CSV'}[row['operation']]
            if row['operation']=='scan':
                operation+=' · '+row['arguments'].get('name','preview')
            values=(operation,
                {'complete':'Đã xong','pending':'Chưa chạy','stopped':'Đã dừng','failed':'Lỗi','interrupted':'Dang dở','running':'Đang chạy'}.get(state,state),
                ' • '.join(filter(None,(row.get('error'),row.get('checkpoint_note'))))
                or ('Đã lưu nguồn đủ' if row.get('sources') else 'Quét dở chạy lại; nguồn đủ dùng tiếp'),
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
        box=QMessageBox(self);box.setWindowTitle('Quét mới')
        box.setText('Quét lại hiện trạng bằng phạm vi này. Snapshot đã lưu luôn được giữ.')
        keep=box.addButton('Quét mới, giữ điểm cũ',QMessageBox.ButtonRole.AcceptRole)
        replace=None
        old=self.owner.jobs.checkpoints()
        if len(old)==1 and old[0]['id']==row['id']:
            replace=box.addButton('Thay điểm này nếu cần',QMessageBox.ButtonRole.ActionRole)
            box.setInformativeText('Chỉ thay điểm cũ nếu lượt mới lỗi sau khi export đủ và tạo được bản nén đã kiểm chứng.')
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep);box.exec()
        chosen=box.clickedButton()
        if chosen is keep or replace is not None and chosen is replace:
            args=row['arguments']
            options={'replace_checkpoint':row['id']} if chosen is replace else {}
            self.owner.job(lambda w:core.scan(args['name'],args['profile'],w.stop,w.event.emit,**options),self.owner.scan_done,scan_job=True)

    def set_limit(self):
        from PySide6.QtWidgets import QInputDialog
        repo=self.owner.jobs.repo
        value,ok=QInputDialog.getInt(self,'Giới hạn export mỗi lượt',
            'MiB cho một lượt quét đang chạy. Điểm tiếp tục cũ không trừ vào hạn mức này.',
            repo.limits['source_mb'],1,100000)
        if ok:
            cfg=load();cfg['resume_limit_mb']=value
            cfg['columnar_limits']={**cfg.get('columnar_limits',{}),'source_mb':value}
            save(cfg);repo.limits['source_mb']=value

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
        set_size_formatter(display_size)
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

    def notify_scan_saved(self,ident):
        try:row=self.repo.get(ident)
        except ValueError:return
        self.tray.showMessage('Có kết quả quét mới',
            f"Đã lưu mốc #{ident} · profile {row['profile']}. Mở Kiểm tra để so sánh.")

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
            name=result.get('profile') or self.repo.get(result['scan_id'])['profile']
            self.last_scan_brief=f"Đã lưu mốc #{result['scan_id']} · profile {name} · {result.get('files',0):,} file"
            self.status_panel.legacy_details(self.last_scan_brief)
            self.status.setText(self.last_scan_brief+'; tiếp tục so sánh và nạp bảng')
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
