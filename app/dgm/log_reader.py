"""Bounded, non-Qt log parsing shared by CLI and GUI."""
import json
import re
LEVELS=['DEBUG','INFO','WARNING','ERROR','CRITICAL']
LEVEL_PATTERN=re.compile(r'^(?:(?:\d{4}-\d{2}-\d{2}[ T]\S+(?:\s+[\d:,\.]+)?(?:\s+UTC)?|\d{2}:\d{2}:\d{2}|\d{4})\s+)?(?:\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]|(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b)')


def severity(line):
    match=LEVEL_PATTERN.match(line)
    return next((x for x in match.groups() if x),None) if match else None

def read_tail(path,max_bytes=500_000):
    with path.open('rb') as file:
        file.seek(0,2);end=file.tell();start=max(0,end-max_bytes);file.seek(start)
        text=file.read().decode('utf-8',errors='replace')
    return (text.partition('\n')[2] if start else text),end

def parse_lines(text,level=None,audit=False,current='INFO'):
    lines=[]
    for line in text.splitlines():
        if audit:
            try:
                item=json.loads(line);audit_level=item.get('level','INFO')
                line=f"{item.get('time','')} [{audit_level}] AUDIT {item.get('action','')} {json.dumps(item,ensure_ascii=False)}"
            except ValueError:pass
        found=severity(line)
        if found:current=found
        if level is None or current==level:lines.append((current,line))
    return lines,current


def operation(message):
    upper=message.upper()
    for key,label in (('SCAN','Quét'),('COMPARE','So sánh'),('NORMALIZ','Chuẩn hóa'),('CLEANUP','Dọn dẹp'),('STORAGE','Dung lượng app'),('UPDATE','Cập nhật'),('SCHEDULE','Lịch chạy')):
        if key in upper:
            match=re.search(r'\b'+key+r'\s+(\d+)\b',upper)
            return label+(' • '+match.group(1) if match else '')
    return 'Ứng dụng'


def events(text,audit=False):
    rows=[]
    for level,line in parse_lines(text,audit=audit)[0]:
        if rows and severity(line) is None:
            rows[-1]['detail']+='\n'+line;continue
        stamp=re.match(r'^(\d{4}-\d\d-\d\d[ T]\S+(?: [\d:,\.]+)?|\d\d:\d\d:\d\d|\d{4})\s+',line)
        message=LEVEL_PATTERN.sub('',line).strip()
        rows.append({'time':stamp.group(1) if stamp else '—','level':level,'operation':operation(message),
                     'message':message,'detail':line,'hint':hint(message,level)})
    return rows


def hint(message,level):
    value=message.casefold()
    if any(key in value for key in ('permission','access is denied','winerror 5','quyền')):return 'Xem vùng bị từ chối / quyền thực tế'
    if any(key in value for key in ('disk','space','dung lượng','no space')):return 'Xem dung lượng trống / data app'
    if any(key in value for key in ('checksum','hash','archive','chuẩn hóa')):return 'Kiểm tra nguồn metadata, không xóa dữ liệu gốc'
    if any(key in value for key in ('lock','đang có','đã thay đổi')):return 'Chờ tác vụ / tạo lại đề xuất'
    return 'Mở chi tiết / traceback' if level in ('ERROR','CRITICAL') else 'Xem bước đã làm và phần còn thiếu' if level=='WARNING' else ''


def redact(text):
    text=re.sub(r'(?i)([a-z]:[\\/]+Users[\\/]+)[^\\/\r\n"\']+',r'\1<USER>',text)
    text=re.sub(r'(?i)([a-z]:\\\\Users\\\\)[^\\"\']+',r'\1<USER>',text)
    text=re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|authorization|secret)["\']?\s*[:=]\s*["\']?)(?:Bearer\s+)?[^\s,"\'}]+',r'\1<REDACTED>',text)
    text=re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+','Bearer <REDACTED>',text)
    return text
