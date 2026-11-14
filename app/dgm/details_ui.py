"""Structured, read-only reports for the desktop. Raw output is opt-in."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication,QComboBox,QDialog,QDialogButtonBox,QHeaderView,
                               QPlainTextEdit,QTabWidget,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget)

from .panel_widgets import setup_table,text_label
from .presentation import size


def value_text(value):
    if value is None or value == '':
        return '—'
    if isinstance(value,bool):
        return 'Có' if value else 'Không'
    if isinstance(value,(list,tuple)) and not any(isinstance(v,(list,tuple,dict)) for v in value):
        return ' · '.join(map(value_text,value)) or '—'
    if isinstance(value,float):
        return f'{value:.3f}'.rstrip('0').rstrip('.')
    return str(value)


def fields(data,prefix=''):
    if isinstance(data,dict):
        for key,value in data.items():
            yield from fields(value,prefix+' › '+str(key) if prefix else str(key))
    elif isinstance(data,(list,tuple)) and any(isinstance(v,(dict,list,tuple)) for v in data):
        for index,value in enumerate(data,1):
            yield from fields(value,f'{prefix} #{index}' if prefix else f'#{index}')
    else:
        yield prefix or 'Kết quả',value_text(data)


def table(headers,rows):
    widget=QTableWidget(0,len(headers))
    widget.setHorizontalHeaderLabels(headers)
    setup_table(widget)
    widget.verticalHeader().hide()
    widget.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.setSortingEnabled(False)
    for i in range(len(headers)-1):
        widget.setColumnWidth(i,245 if len(headers)==2 else 145)
    fill(widget,rows)
    if headers[0]=='Đường dẫn' or headers[0]=='Nguồn':
        widget.setColumnWidth(0,300)
    return widget


def fill(widget,rows):
    rows=list(rows)
    widget.setRowCount(len(rows))
    for i,row in enumerate(rows):
        for j,value in enumerate(row):
            text=value_text(value)
            item=QTableWidgetItem(text.replace('\n','  ·  '))
            item.setToolTip(text)
            widget.setItem(i,j,item)


class DetailPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.tabs=QTabWidget();layout.addWidget(self.tabs)
        self.setMinimumHeight(200)

    def populate(self, sections, raw=None):
        while self.tabs.count():
            widget=self.tabs.widget(0);self.tabs.removeTab(0);widget.deleteLater()
        for name,headers,rows in sections:
            self.tabs.addTab(table(headers,rows),name)
        if raw:
            original=QPlainTextEdit();original.setReadOnly(True);original.setPlainText(raw)
            self.tabs.addTab(original,'Log gốc')


class ReportDialog(QDialog):
    def __init__(self,parent,title,sections,*,description='',raw=None,filter_rows=None):
        super().__init__(parent)
        self.setWindowTitle(title);self.resize(880,500)
        layout=QVBoxLayout(self)
        if description:layout.addWidget(text_label(description,'subTitle'))
        self.detail=DetailPanel();self.detail.populate(sections,raw)
        if filter_rows:
            combo=QComboBox()
            for label,rows in filter_rows.items():combo.addItem(label,rows)
            layout.addWidget(combo)
            def apply_filter(_):
                fill(self.detail.tabs.widget(1),combo.currentData())
                self.detail.tabs.setCurrentIndex(1)
            combo.currentIndexChanged.connect(apply_filter)
        layout.addWidget(self.detail,1)
        footer=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        footer.rejected.connect(self.reject)
        copy=footer.addButton('Sao chép bảng hiện tại',QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(self.copy)
        layout.addWidget(footer)

    def copy(self):
        current=self.detail.tabs.currentWidget()
        if isinstance(current,QPlainTextEdit):
            text=current.toPlainText()
        else:
            headers=[current.horizontalHeaderItem(j).text() for j in range(current.columnCount())]
            rows=[[current.item(i,j).toolTip().replace('\n',' · ') for j in range(current.columnCount())]
                  for i in range(current.rowCount())]
            text='\n'.join('\t'.join(row) for row in [headers,*rows])
        QApplication.clipboard().setText(text)


def key_section(name,data):
    return name,('Mục','Giá trị'),list(fields(data))


def environment_sections(info):
    main={'Phiên bản app':info.get('app_version'),'Bản':info.get('edition'),
          'Python':info.get('python'),'Quyền Admin':info.get('admin'),
          'Runtime riêng':info.get('isolated'),'Đóng gói EXE':info.get('frozen'),
          'Thư mục dữ liệu':info.get('home'),'Chương trình':info.get('executable'),
          'Định dạng lịch sử':info.get('history_format')}
    engines=[(key,item.get('version'),'Có' if item.get('exists') else 'Không có',item.get('path'))
             for key,item in info.get('engines',{}).items()]
    packages=[(key,version,'Gói runtime','') for key,version in info.get('packages',{}).items()]
    disks=[(key,item.get('filesystem'),size(item.get('free')),size(item.get('total')))
           for key,item in info.get('disks',{}).items()]
    return [key_section('Môi trường',main),('Công cụ',('Tên','Phiên bản','Trạng thái','Đường dẫn'),engines+packages),
            ('Ổ đĩa',('Phân vùng','Hệ thống file','Còn trống','Tổng'),disks)]


def cleanup_dialog(parent,result):
    counts=result.get('counts',{})
    delta=result.get('free_delta')
    measured=('+' if delta>0 else '−' if delta<0 else '')+size(abs(delta)) if delta is not None else '—'
    totals={'File đã xóa':counts.get('deleted',0),'Bỏ qua':counts.get('skipped',0),
            'Công cụ đã chạy / mở':counts.get('tool',0),'Chưa xử lý':result.get('not_processed',0),
            'Dung lượng file đã xóa':size(result.get('deleted_bytes')),
            'Thay đổi dung lượng trống ổ C:':measured,'Đã yêu cầu dừng':result.get('cancelled',False)}
    items=result.get('items',[])
    def row(item):
        status=item.get('status')
        return (item.get('path'),{'deleted':'Đã xóa','skipped':'Bỏ qua','tool':'Công cụ'}.get(status,status),
                size(item.get('bytes')),item.get('error') or item.get('note') or
                ('Cần khởi động lại' if item.get('requires_reboot') else ''))
    data=[row(item) for item in items]
    note=result.get('note','')
    if result.get('items_truncated'):
        note+=f' Bảng giữ {len(data):,} dòng đầu; số tổng vẫn tính toàn bộ lượt.'
    skipped=result.get('skipped_items',[item for item in items if item.get('status')=='skipped'])
    if result.get('skipped_truncated'):
        note+=f' Giữ {len(skipped):,} lý do bỏ qua đầu tiên; xem Nhật ký để biết thêm.'
    return ReportDialog(parent,'Kết quả dọn',[key_section('Tổng quan',totals),
        ('Từng mục',('Đường dẫn','Kết quả','Dung lượng','Lý do'),data)],description=note,
        filter_rows={'Mọi kết quả':data,'Bỏ qua / lỗi':[row(i) for i in skipped]})


def move_dialog(parent,record):
    def status(item):
        if item.get('retired'):return 'Đã dọn bản cũ'
        if item.get('switched'):return 'Đã đổi đường dẫn'
        if record['state']=='undone':return 'Đã hoàn tác'
        return 'Chưa đổi đường dẫn'
    rows=[(item['source'],item['target'],item['backup'],size(item.get('bytes')),status(item))
          for item in record['entries']]
    states={'complete':'Đã chuyển','interrupted':'Chưa hoàn tất','undone':'Đã hoàn tác',
            'retired':'Đã dọn bản cũ','copying':'Đang sao chép','switching':'Đang đổi đường dẫn',
            'undoing':'Đang hoàn tác','retiring':'Đang dọn'}
    incomplete=[row for row,item in zip(rows,record['entries'])
                if record['state']=='interrupted' and not item.get('switched')]
    return ReportDialog(parent,'Nhật ký chuyển ứng dụng',[
        key_section('Tổng quan',{'Mã lượt':record['id'],'Thời gian':record.get('created'),
                    'Trạng thái':states.get(record['state'],record['state']),'Lỗi / lưu ý':record.get('error')}),
        ('Đường dẫn',('Nguồn','Đích','Bản cũ','Dung lượng','Kết quả'),rows)],
        filter_rows={'Mọi thư mục':rows,'Mục chưa chuyển xong':incomplete} if record['state']=='interrupted' else None)
