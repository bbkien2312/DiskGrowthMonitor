"""A non-clipping footer with explicit phases and opt-in timing/result details."""
from __future__ import annotations
import time
from PySide6.QtCore import Qt,QTimer,QEvent
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QToolButton,QScrollArea,QSizePolicy


def seconds(value):
    return 'chưa ghi nhận' if value is None else '<1 s' if 0<=value<1 else f'{int(value+.5):,} s'


class StatusPanel(QWidget):
    def __init__(self,owner,status,progress,completed):
        super().__init__();self.owner=owner;self.status=status;self.progress=progress;self.completed=completed
        self.operations={};self.results='';self.legacy='';self.page_time=None;self.tree_time=None
        self.updated={};self.flow_started=None;self.flow_finished=None
        self.flow_state='running'
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,2,0,2);layout.setSpacing(3)
        self.row=QHBoxLayout();self.row.setSpacing(5);self.row.addWidget(status,1)
        self.toggle=QToolButton();self.toggle.setCheckable(True)
        self.toggle.setAccessibleName('Hiện hoặc ẩn thời gian và kết quả công việc')
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setStyleSheet('QToolButton{padding:2px 6px;}')
        self.toggle.setChecked(bool(owner.preferences.values.get('status/details-v3',False)))
        self.row.addWidget(self.toggle);layout.addLayout(self.row)
        layout.addWidget(progress)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(105);self.scroll.setWidget(completed);layout.addWidget(self.scroll,1)
        completed.show();self.toggle.toggled.connect(self.expanded)
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.render);self.timer.start()
        self.expanded(self.toggle.isChecked(),persist=False)

    def event(self,event):
        result=super().event(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.Polish) and hasattr(self,'scroll'):
            QTimer.singleShot(0,self.fit_height)
        return result

    def fit_height(self):
        self.status.ensurePolished();self.progress.ensurePolished();self.toggle.ensurePolished()
        text_height=self.status.fontMetrics().height()+4
        bar_height=self.progress.fontMetrics().height()+8
        # The global theme's 12px min-height used to override setMinimumHeight.
        self.progress.setStyleSheet(f'QProgressBar{{min-height:{bar_height}px;max-height:{bar_height}px;}}')
        self.progress.setFixedHeight(bar_height)
        self.status.setFixedHeight(max(text_height,self.toggle.sizeHint().height()))
        self.layout().activate()
        minimum=self.status.height()+bar_height+3+4
        if self.toggle.isChecked():minimum+=self.scroll.minimumHeight()+3
        self.setMinimumHeight(minimum);self.updateGeometry()
        splitter=getattr(self.owner,'workspace_splitter',None)
        if splitter and splitter.count()==2:
            sizes=splitter.sizes()
            wanted=max(minimum,sizes[1]) if self.toggle.isChecked() else minimum
            splitter.setSizes([max(100,sum(sizes)-wanted),wanted])

    def expanded(self,show,persist=True):
        self.scroll.setVisible(show)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if show else Qt.ArrowType.RightArrow)
        self.toggle.setText('Thu gọn chi tiết' if show else 'Chi tiết thời gian / kết quả')
        if persist:self.owner.preferences.set('status/details-v3',show)
        self.render();self.fit_height()

    def begin(self,reset=False):
        if reset:
            self.operations.clear();self.updated.clear();self.results='';self.page_time=None;self.tree_time=None;self.legacy=''
        self.flow_started=time.perf_counter();self.flow_finished=None;self.flow_state='running';self.render()

    def accept(self,timing):
        operation=timing['operation'];self.operations[operation]=timing
        self.updated[operation]=time.perf_counter();self.render()

    def finish_results(self,summary,mode='allocated',page_time=None,tree_time=None):
        counts=summary.get('counts_logical') if mode=='logical' else summary.get('counts')
        counts=counts or []
        def tally(change,kind=None):return sum(r['count'] for r in counts if r['change']==change and (kind is None or r['kind']==kind))
        parts=[]
        for key,label in (('grown','Tăng'),('shrunk','Giảm'),('new','Mới'),('missing','Mất'),('same','Không đổi'),
                          ('not_scanned','Không quét'),('unknown','Chưa xác định'),('partial','Có phần chưa rõ'),
                          ('modified','Sửa metadata'),('replaced','Thay thế'),('first_scan','Mốc ban đầu')):
            parts.append(f'{label}: {tally(key):,} (file {tally(key,"file"):,} · thư mục {tally(key,"folder"):,} · liên kết {tally(key,"link"):,})')
        self.results=('TOÀN CẶP MỐC — '+('Trên đĩa' if mode=='allocated' else 'Logical')+
            f" · {summary.get('rows',0):,} đường dẫn; không chỉ các dòng đã tải.\n"+'\n'.join(parts)+
            '\nKhông quét/chưa xác định không được tính thành file đã xóa; tổng dung lượng chỉ cộng file.')
        if page_time is not None:self.page_time=page_time
        if tree_time is not None:self.tree_time=tree_time
        self.render()

    def complete_flow(self):
        self.flow_finished=time.perf_counter();self.flow_state='complete';self.render()

    def cancel_flow(self,state='cancelled'):
        now=time.perf_counter()
        for key,report in list(self.operations.items()):
            if report['state']!='running':continue
            extra=max(0,now-self.updated[key]);report=dict(report,state=state,seconds=report['seconds']+extra)
            report['stages']=[dict(r,state=state,seconds=r['seconds']+extra) if r['state']=='running' else r for r in report['stages']]
            self.operations[key]=report
        self.flow_finished=now;self.flow_state=state;self.render()

    def render(self):
        if not hasattr(self,'scroll'):return
        now=time.perf_counter();lines=[]
        if self.flow_started is not None:
            end=self.flow_finished or now
            title={'running':'Đang thực thi luồng','complete':'Đã hoàn thành luồng','cancelled':'Đã dừng luồng','failed':'Luồng gặp lỗi'}[self.flow_state]
            lines.append(title+' · Tổng thời gian: '+seconds(end-self.flow_started))
        for key,label in (('scan','QUÉT / NHẬP / LƯU'),('import','NHẬP LỊCH SỬ'),('comparison','SO SÁNH')):
            report=self.operations.get(key)
            if not report:continue
            extra=max(0,now-self.updated[key]) if report['state']=='running' else 0
            state={'running':'đang chạy','complete':'đã xong','cancelled':'đã dừng','failed':'lỗi'}.get(report['state'],report['state'])
            lines.append(f"\n{label} · {seconds(report.get('seconds',0)+extra)} · {state}")
            for row in report.get('stages',[]):
                running=row['state']=='running'
                icon='…' if running else '✓' if row['state']=='complete' else '!' if row['state']=='failed' else '■'
                lines.append(f"{icon} {row['name']} — {seconds(row['seconds']+(extra if running else 0))}")
        if self.tree_time is not None:lines.append('\n✓ Nạp gốc cây thư mục: '+seconds(self.tree_time))
        if self.page_time is not None:lines.append('✓ Truy vấn / nạp trang chi tiết gần nhất: '+seconds(self.page_time))
        if self.results:lines.append('\n'+self.results)
        if self.legacy and not self.operations:lines.append(self.legacy)
        self.completed.setText('\n'.join(lines) or 'Chưa có công việc. Thời gian từng công đoạn và kết quả sẽ cập nhật tại đây.')

    def legacy_details(self,text):
        self.legacy=text;self.render()
