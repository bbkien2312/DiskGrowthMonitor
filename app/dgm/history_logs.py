"""Canonical compressed text metadata. No source-file contents are ever opened."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
from pathlib import Path
import uuid
import time

FORMAT='dgm-metadata-jsonl-1'
FIELDS=('path','parent','kind','logical','allocated','created','modified','identity','attrs','status','note')
MAX_LINE=4*1024*1024


def line(value):
    return (json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')


def write(path,header,records,*,stop=None,notify=None):
    """Publish only after a complete read-back verifies every uncompressed byte."""
    from .platform_ops import ProcessCancelled
    from .storage_maintenance import safe
    path=safe(Path(path));path.parent.mkdir(parents=True,exist_ok=True)
    from .storage_safety import SpaceGuard
    guard=SpaceGuard(operation='ghi log metadata nén')
    partial=path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
    digest=hashlib.sha256();count=0;last=0
    try:
        with partial.open('xb') as raw:
            with gzip.GzipFile(filename='',fileobj=raw,mode='wb',compresslevel=3,mtime=0) as stream:
                stream.write(line({'format':FORMAT,**header}))
                for value in records:
                    if stop is not None and stop.is_set():raise ProcessCancelled('Đã dừng ghi log; bản lịch sử cũ được giữ')
                    encoded=line(value);digest.update(encoded);stream.write(encoded);count+=1
                    if count%10000==0:guard.check()
                    if notify and count%10000==0 and time.monotonic()-last>1:
                        notify('info',{'message':f'Đã ghi {count:,} bản ghi metadata vào log nén'})
                        notify('metadata-progress',{'records':count});last=time.monotonic()
                footer={'records':count,'sha256':digest.hexdigest()};stream.write(line({'end':footer}))
            raw.flush();os.fsync(raw.fileno())
        verified=verify(partial,stop=stop)
        if verified!=footer:raise ValueError('Log ghi và đọc lại không khớp')
        partial.replace(path)
        return {**footer,'bytes':path.stat().st_size,'file':path.name}
    finally:partial.unlink(missing_ok=True)


def read(path,*,stop=None):
    """Strict framing, checksum, line limit and gzip CRC. Caller must exhaust it."""
    from .platform_ops import ProcessCancelled
    from .storage_maintenance import safe
    path=safe(Path(path));digest=hashlib.sha256();count=0
    with gzip.open(path,'rb') as stream:
        first=stream.readline(MAX_LINE+1)
        if len(first)>MAX_LINE:raise ValueError('Header log quá dài')
        header=json.loads(first)
        if not isinstance(header,dict) or header.get('format')!=FORMAT:raise ValueError('Sai định dạng log metadata')
        yield 'header',header
        while True:
            if stop is not None and stop.is_set():raise ProcessCancelled('Đã dừng đọc log metadata')
            encoded=stream.readline(MAX_LINE+1)
            if not encoded or len(encoded)>MAX_LINE:raise ValueError('Log thiếu phần kết thúc hoặc dòng quá dài')
            value=json.loads(encoded)
            if isinstance(value,dict) and 'end' in value:
                expected={'records':count,'sha256':digest.hexdigest()}
                if value['end']!=expected:raise ValueError('Checksum/số bản ghi log không khớp')
                if stream.read(1):raise ValueError('Log có dữ liệu sau phần kết thúc')
                yield 'end',expected
                break
            digest.update(encoded);count+=1
            yield 'record',value


def verify(path,*,stop=None):
    final=None
    for kind,value in read(path,stop=stop):
        if kind=='end':final=value
    if final is None:raise ValueError('Log chưa hoàn tất')
    return final
