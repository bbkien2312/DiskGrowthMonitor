"""Review-first cleanup UI. All execution routes use the CLI cleanup API."""
import json
import time
from PySide6.QtCore import Qt,QSignalBlocker,QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QTabWidget,QSplitter,
    QTreeWidget,QTreeWidgetItem,QCheckBox,QComboBox,QLineEdit,QTableWidget,QTableWidgetItem,QMessageBox,QInputDialog,QMenu)
from .cleanup import rules,get_plan,plan_page
from .cleanup_catalog import GROUPS,PROTECTED,REFERENCES
from .presentation import size,local_time
from .panel_widgets import text_label,cards,explanation,setup_table
from .store import Store


class CleanupPanel(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.known=rules();self.current=None;self.selected=set();self.rows=[]
        self.worker=None;self.offset=0;self.sort='size';self.direction='desc';self.rendering=False
        layout=QVBoxLayout(self);layout.setContentsMargins(5,4,5,4);layout.setSpacing(6)
        layout.addWidget(text_label('Dọn dẹp có duyệt','pageTitle'))
        layout.addWidget(text_label('Chọn nhóm → Quét tạo đề xuất → Xem điều kiện → Duyệt đúng các mục đã chọn. Tự động chỉ kiểm tra/nhắc duyệt, không tự xóa.','safeBanner'))
        self.cards=cards(layout,[('ĐỦ ĐIỀU KIỆN','Chưa quét','Không phải toàn bộ dung lượng thư mục'),
            ('MỤC ĐỀ XUẤT','—','Không chọn sẵn tất cả'),('CÔNG CỤ / CHƯA ƯỚC LƯỢNG','—','Không gán dung lượng chưa biết thành 0 B'),('ĐANG CHỌN','0 B','Chỉ các mục bạn đã tick')])
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        preview=QWidget();v=QVBoxLayout(preview);v.setContentsMargins(0,3,0,0)
        splitter=QSplitter();v.addWidget(splitter,1)
        self.catalog=QTreeWidget();self.catalog.setHeaderLabels(['Nhóm kiểm tra','Điều kiện']);self.catalog.setMinimumWidth(280)
        self.catalog.setColumnWidth(0,215);self.catalog.setColumnWidth(1,110)
        self.rule_boxes={}
        for category in GROUPS:
            parent=QTreeWidgetItem([category,'']);self.catalog.addTopLevelItem(parent);parent.setExpanded(True)
            for key,info in self.known.items():
                if info.get('group')!=category:continue
                item=QTreeWidgetItem(['','Toàn bộ' if info.get('tool') in ('nuget','recycle') else f"≥ {info['days']} ngày" if not info.get('tool') else 'Windows'])
                item.setData(0,Qt.ItemDataRole.UserRole,key);parent.addChild(item)
                check=QCheckBox(info['title']);check.setToolTip(info['title']);self.rule_boxes[key]=check;self.catalog.setItemWidget(item,0,check)
                check.toggled.connect(lambda _,item=item:self.catalog.setCurrentItem(item))
        self.catalog.currentItemChanged.connect(self.describe);splitter.addWidget(self.catalog)
        manual_group=QTreeWidgetItem(['App / kiểm tra thủ công','']);self.catalog.addTopLevelItem(manual_group);manual_group.setExpanded(True)
        for title,key in [('Dung lượng / lịch sử của app','__storage__'),('File lớn / trùng / folder rỗng','__manual__')]:
            item=QTreeWidgetItem([title,'Chỉ xem']);item.setData(0,Qt.ItemDataRole.UserRole,key);manual_group.addChild(item)
        right=QWidget();r=QVBoxLayout(right);r.setContentsMargins(3,0,0,0);r.setSpacing(5)
        self.rule_title=text_label('Chọn một nhóm để xem giải thích','miniTitle');r.addWidget(self.rule_title)
        self.roots=text_label('Chỉ các đường dẫn allowlist; không dọn project hay profile trình duyệt.','subTitle');r.addWidget(self.roots)
        grid=QGridLayout();self.explanations={}
        for i,title in enumerate(('Đây là gì?','Có thể dọn khi nào?','Giữ lại / không đụng','Xử lý / tác động')):
            box,label=explanation(title);self.explanations[title]=label;grid.addWidget(box,i//2,i%2)
        r.addLayout(grid)
        self.update_done=QCheckBox('Tôi xác nhận NVIDIA update đã hoàn tất, máy ổn và không cần gói cũ');r.addWidget(self.update_done)
        self.condition_note=text_label('Tuổi tính theo thời gian sửa cuối. File cũ không đồng nghĩa không còn dùng. Cache xóa vĩnh viễn; không lưu bản sao nội dung.','subTitle');r.addWidget(self.condition_note)
        filterrow=QHBoxLayout();self.plan_combo=QComboBox();self.plan_combo.setMinimumWidth(170)
        filterrow.addWidget(self.plan_combo,1);owner.button('Mở đề xuất',self.load,filterrow)
        self.filter=QComboBox();self.filter.addItem('Mọi nhóm','')
        for key,info in self.known.items():self.filter.addItem(info['title'],key)
        self.search=QLineEdit();self.search.setPlaceholderText('Tìm đường dẫn trong đề xuất…')
        filterrow.addWidget(self.filter);filterrow.addWidget(self.search,1);r.addLayout(filterrow)
        self.filter.currentIndexChanged.connect(self.reset_page);self.search.returnPressed.connect(self.reset_page)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['Chọn','Đường dẫn','Đủ điều kiện','Nhóm','Phương pháp'])
        setup_table(self.table);self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        owner.columns(self.table,'cleanup-v140',[52,370,110,155,260]);self.table.itemChanged.connect(self.checked)
        self.table.horizontalHeader().setSortIndicatorShown(True);self.table.horizontalHeader().sectionClicked.connect(self.sort_column)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.doubleClicked.connect(self.reveal_row);r.addWidget(self.table,1)
        pager=QHBoxLayout();self.page_info=text_label('Chưa có đề xuất','subTitle');pager.addWidget(self.page_info,1)
        self.page_size=QComboBox();self.page_size.addItems(['20','50','100','200','500','1000']);pager.addWidget(self.page_size)
        self.page_size.currentIndexChanged.connect(self.reset_page)
        owner.button('Trang trước',lambda:self.move_page(-1),pager);owner.button('Trang sau',lambda:self.move_page(1),pager)
        owner.button('Chọn trang',lambda:self.set_checks(True),pager);owner.button('Bỏ chọn trang',lambda:self.set_checks(False),pager)
        r.addLayout(pager);splitter.addWidget(right);splitter.setSizes([320,1050]);self.tabs.addTab(preview,'Kiểm tra & đề xuất')
        protected=QWidget();p=QVBoxLayout(protected)
        p.addWidget(text_label('Lớn, cũ hoặc không đổi không có nghĩa là dư thừa. Các vùng này không dùng rule xóa file tự động.','warningBanner'))
        table=QTableWidget(len(PROTECTED),3);table.setHorizontalHeaderLabels(['Vùng bảo vệ','Đây là gì?','Cách xử lý phù hợp']);setup_table(table)
        for i,row in enumerate(PROTECTED):
            for j,value in enumerate(row):table.setItem(i,j,QTableWidgetItem(value))
        owner.columns(table,'cleanup-protected-v140',[310,400,550]);p.addWidget(table,1);self.tabs.addTab(protected,'Không nên xóa')
        from .manual_inspection_ui import ManualInspectionPanel
        self.manual=ManualInspectionPanel(owner);self.tabs.addTab(self.manual,'Kiểm tra thủ công')
        sources=QWidget();s=QVBoxLayout(sources)
        s.addWidget(text_label('Chỉ dùng rule do app review; không nạp XML/script/lệnh ngoài chạy Admin. Tích hợp code/rule phải đáp ứng giấy phép.','warningBanner'))
        for name,idea,url,note in REFERENCES:
            box,label=explanation(name,idea+'\n'+note);s.addWidget(box)
            owner.button('Xem nguồn '+name,lambda url=url:owner.open_source(url),s)
        s.addStretch();self.tabs.addTab(sources,'Rule & nguồn tham khảo')
        self.info=text_label('Chưa có đề xuất. Mọi mục chỉ là xem trước, chưa được phép xóa.','subTitle');layout.addWidget(self.info)
        buttons=QHBoxLayout();self.scan_button=owner.button('Quét tạo đề xuất',self.make,buttons);self.scan_button.setProperty('actionRole','primary')
        self.stop_button=owner.button('Dừng tác vụ dọn',self.cancel,buttons);self.stop_button.setEnabled(False)
        self.stop_button.setToolTip('Dừng trước mục tiếp theo. Công cụ Windows/DISM đang chạy cần chờ kết thúc an toàn, không cưỡng bức tắt.')
        owner.button('Hỏi AI / xem trước',owner.ai_plan,buttons)
        owner.button('Dọn metadata hết hạn',self.prune,buttons);buttons.addStretch()
        self.apply_button=owner.button('Duyệt các mục đã chọn',self.apply,buttons);self.apply_button.setProperty('actionRole','danger');self.apply_button.setEnabled(False)
        owner.cleanup_action_buttons.extend([self.scan_button,self.apply_button]);layout.addLayout(buttons)
        self.timer=QTimer(self);self.timer.timeout.connect(self.update_selection);self.timer.start(1000)
        first=self.catalog.topLevelItem(0).child(0);self.catalog.setCurrentItem(first)

    def describe(self,item,*_):
        key=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if key=='__storage__':self.owner.tabs.setCurrentIndex(1);self.owner.system_tabs.setCurrentIndex(2);return
        if key=='__manual__':self.tabs.setCurrentWidget(self.manual);return
        if not key:return
        rule=self.known[key];self.rule_title.setText(rule['title']);self.roots.setText('\n'.join(rule['roots']))
        for title,text in zip(self.explanations,(rule['what'],rule['when'],rule['keep'],rule['method']+'\n'+rule['risk'])):
            self.explanations[title].setText(text)

    def run(self,fn,done):
        if self.worker is not None:return
        self.scan_button.setEnabled(False);self.apply_button.setEnabled(False);self.stop_button.setEnabled(True)
        self.worker=self.owner.job(fn,done)
        def finished():
            self.worker=None;self.scan_button.setEnabled(not self.owner.admin_busy);self.stop_button.setEnabled(False);self.update_selection()
        self.worker.finished.connect(finished)

    def cancel(self):
        if self.worker:self.worker.stop.set();self.stop_button.setEnabled(False)

    def make(self):
        from .cleanup import plan
        selected=[key for key,box in self.rule_boxes.items() if box.isChecked()]
        if not selected:self.owner.status.setText('Chọn ít nhất một nhóm kiểm tra ở catalog bên trái.');return
        confirmed=self.update_done.isChecked()
        self.run(lambda worker:plan(selected,confirmed,compact=True,stop=worker.stop,notify=worker.event.emit),self.loaded)

    def refresh_plans(self):
        prior=self.plan_combo.currentData();s=Store(readonly=True)
        try:rows=s.db.execute('SELECT id,created,status FROM plans ORDER BY created DESC LIMIT 100').fetchall()
        finally:s.close()
        self.plan_combo.clear()
        for row in rows:self.plan_combo.addItem(local_time(row['created'])+' • '+{'pending':'Chờ duyệt','applied':'Đã xử lý','expired':'Hết hạn','cancelled':'Đã dừng'}.get(row['status'],row['status']),row['id'])
        self.plan_combo.setCurrentIndex(max(0,self.plan_combo.findData(prior)))

    def load(self):
        key=self.plan_combo.currentData()
        if key:self.run(lambda _:get_plan(key,compact=True),self.loaded)

    def loaded(self,content):
        self.current=content;self.owner.current_plan=content;self.selected.clear();self.selected_sizes={};self.offset=0
        self.refresh_plans();self.plan_combo.setCurrentIndex(max(0,self.plan_combo.findData(content['id'])))
        tools=content.get('tool_count',0);count=content.get('entry_count',len(content['entries']))
        estimate='Chưa ước lượng' if tools and tools==count else size(content['bytes'])+(' + ?' if tools else '')
        self.cards[0].set_value(estimate,'Chỉ file đủ tuổi/điều kiện; tool chưa biết byte không gán 0 B')
        self.cards[1].set_value(f"{content.get('entry_count',len(content['entries'])):,}")
        self.cards[2].set_value(str(content.get('tool_count',0)),'Dung lượng công cụ chưa được ước lượng')
        self.info.setText(f"Chưa xóa gì • {len(content.get('blocked',[]))} nhóm bị chặn • {content.get('error_count',len(content.get('errors',[]))):,} lỗi đọc • {content['note']}")
        self.reload_page()

    def reload_page(self):
        if not self.current:return
        if not self.current.get('entry_archive'):
            self.table.setRowCount(0);self.rows=[];self.page_info.setText('Đề xuất cũ/hết hạn; hãy quét tạo đề xuất mới.');return
        result=plan_page(self.current['id'],offset=self.offset,limit=int(self.page_size.currentText()),sort=self.sort,direction=self.direction,rule=self.filter.currentData(),search=self.search.text().strip())
        self.rows=result['entries'];self.total=result['total'];self.rendering=True
        self.table.setRowCount(len(self.rows))
        for i,entry in enumerate(self.rows):
            check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if entry['path'] in self.selected else Qt.CheckState.Unchecked);self.table.setItem(i,0,check)
            rule=self.known.get(entry['rule'],{})
            for j,value in enumerate((entry['path'],size(entry.get('size')) if entry.get('size') is not None else 'Chưa ước lượng',rule.get('title',entry['rule']),rule.get('method',entry.get('risk',''))),1):
                item=QTableWidgetItem(value);item.setToolTip(value);self.table.setItem(i,j,item)
        self.rendering=False;self.update_selection()

    def reset_page(self,*_):self.offset=0;self.reload_page()
    def move_page(self,direction):
        if not self.current:return
        target=max(0,self.offset+direction*int(self.page_size.currentText()))
        if target<getattr(self,'total',0) or target==0:self.offset=target;self.reload_page()
    def sort_column(self,column):
        key={1:'path',2:'size',3:'rule'}.get(column)
        if key:
            self.direction='asc' if self.sort==key and self.direction=='desc' else 'desc';self.sort=key
            self.table.horizontalHeader().setSortIndicator(column,Qt.SortOrder.DescendingOrder if self.direction=='desc' else Qt.SortOrder.AscendingOrder);self.reset_page()
    def checked(self,item):
        if self.rendering or item.column()!=0:return
        entry=self.rows[item.row()];path=entry['path']
        if item.checkState()==Qt.CheckState.Checked:self.selected.add(path);self.selected_sizes[path]=entry.get('size')
        else:self.selected.discard(path);self.selected_sizes.pop(path,None)
        self.update_selection()
    def set_checks(self,checked):
        for i in range(self.table.rowCount()):self.table.item(i,0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    def update_selection(self):
        if not self.current:return
        remaining=max(0,int(self.current['expires']-time.time()));valid=self.current.get('status')=='pending' and remaining>0
        chosen=sum(value or 0 for value in getattr(self,'selected_sizes',{}).values())
        unknown=sum(value is None for value in getattr(self,'selected_sizes',{}).values())
        estimate='Chưa ước lượng' if unknown and unknown==len(self.selected) else size(chosen)+(' + ?' if unknown else '')
        self.cards[3].set_value(estimate,f'{len(self.selected):,} mục • {unknown} công cụ chưa ước lượng')
        self.page_info.setText(f"{self.offset+1 if self.rows else 0}–{self.offset+len(self.rows)} / {getattr(self,'total',0):,} mục • "+(f'Còn {remaining//60}p {remaining%60}s' if valid else 'Đã xử lý / hết hạn'))
        self.apply_button.setEnabled(valid and bool(self.selected) and self.worker is None and not self.owner.admin_busy)
    def apply(self):
        if not self.current or not self.selected:return
        p=self.current;paths=list(self.selected)
        from .elevation import digest
        reviewed_digest=digest(p)
        confirmation,ok=QInputDialog.getText(self,'Duyệt xử lý đề xuất',f"{len(paths):,} mục đã chọn, gồm cả mục ở trang khác.\nXóa cache là vĩnh viễn; tool có thể xử lý TOÀN BỘ cache/Thùng rác hoặc bảo trì Windows theo phương pháp đã ghi.\nKhông có bản sao phục hồi nội dung. Nhập chính xác: {p['confirmation']}")
        if not ok:return
        if confirmation!=p['confirmation']:self.owner.status.setText('Sai chuỗi xác nhận; chưa xử lý mục nào.');return
        from .cleanup import select,apply
        def work(worker):
            select(p['id'],paths,compact=True,expected_digest=reviewed_digest)
            return apply(p['id'],confirmation,stop=worker.stop,notify=worker.event.emit)
        def done(result):
            self.current=get_plan(p['id'],compact=True);self.owner.current_plan=self.current;self.selected.clear();self.selected_sizes.clear()
            self.refresh_plans();self.update_selection();self.owner.cleanup_result(result)
        self.run(work,done)
    def prune(self):
        if self.worker:return
        from .cleanup_storage import prune
        def work(_):
            store=Store()
            try:prune(store)
            finally:store.close()
            return True
        self.run(work,lambda _:(self.refresh_plans(),self.owner.status.setText('Đã dọn metadata đề xuất hết hạn; không xóa file trên ổ đã quét.')))
    def context_menu(self,point):
        index=self.table.indexAt(point)
        if not index.isValid():return
        entry=self.rows[index.row()];menu=QMenu(self)
        menu.addAction('Mở vị trí file',lambda:self.owner.file_action(entry['path'],'reveal'))
        menu.addAction('Thuộc tính',lambda:self.owner.file_action(entry['path'],'properties'))
        menu.addAction('Sao chép đường dẫn',lambda:__import__('PySide6.QtWidgets',fromlist=['QApplication']).QApplication.clipboard().setText(entry['path']))
        menu.exec(self.table.viewport().mapToGlobal(point))
    def reveal_row(self,index):
        if index.isValid():self.owner.file_action(self.rows[index.row()]['path'],'reveal')
