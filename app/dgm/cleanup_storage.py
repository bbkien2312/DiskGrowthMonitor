"""Compressed plan metadata and bounded disposable indexes, shared by CLI/GUI.

The catalog never stores the full entries/results array. No user file contents
or recovery copies are saved. An index is untrusted: apply reads the archive.
"""
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from .config import home, load

NAME = re.compile(r'[0-9a-f]{12}-[0-9a-f]{12}')


def location(name, index=False):
    if not NAME.fullmatch(name):raise ValueError('Tên archive đề xuất không hợp lệ')
    base = home() / ('data/tmp/cleanup-index' if index else 'data/cleanup/plans')
    base.mkdir(parents=True, exist_ok=True)
    path = base / (name + ('.sqlite' if index else '.jsonl.gz'))
    if path.resolve() != path.absolute():raise ValueError('Không dùng reparse/symlink cho đề xuất')
    return path


def checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):value.update(chunk)
    return value.hexdigest()


def write_entries(plan_id, entries, stop=None):
    from .platform_ops import ProcessCancelled
    name = plan_id + '-' + uuid.uuid4().hex[:12]
    archive, index = location(name), location(name, True)
    cfg = load();cap = int(cfg.get('cleanup_index_limit_mb',128)) * 1_000_000
    def used(folder,pattern):
        return sum(p.stat().st_size for p in folder.glob(pattern) if p.is_file() and p.resolve()==p.absolute())
    con = sqlite3.connect(index)
    try:
        from .storage_safety import SpaceGuard
        guard=SpaceGuard(operation='lưu metadata đề xuất dọn')
        con.execute('CREATE TABLE entries(seq INTEGER PRIMARY KEY,path TEXT COLLATE NOCASE UNIQUE,rule TEXT,size INTEGER,payload TEXT)')
        count = total = tools = 0;groups = {}
        with gzip.open(archive, 'wt', encoding='utf-8', compresslevel=6) as output:
            for entry in entries:
                if stop is not None and stop.is_set():raise ProcessCancelled('Đã dừng lập đề xuất; chưa xóa gì.')
                encoded=json.dumps(entry, ensure_ascii=False, separators=(',', ':'))
                cursor=con.execute('INSERT OR IGNORE INTO entries(path,rule,size,payload) VALUES(?,?,?,?)',
                                   (entry['path'],entry['rule'],entry.get('size'),encoded))
                if not cursor.rowcount:continue
                output.write(encoded+'\n');count+=1;total+=entry.get('size') or 0;tools+=bool(entry.get('tool'))
                group=groups.setdefault(entry['rule'],{'count':0,'bytes':0,'tools':0})
                group['count']+=1;group['bytes']+=entry.get('size') or 0;group['tools']+=bool(entry.get('tool'))
                if count % 256 == 0:
                    guard.check()
                    con.commit()
                    if used(index.parent,'*.sqlite')>cap:raise RuntimeError('Đề xuất vượt hạn mức tổng chỉ mục; dọn đề xuất hết hạn/chọn ít nhóm hơn. Chưa xóa gì.')
        con.execute('CREATE INDEX by_size ON entries(size,seq)');con.execute('CREATE INDEX by_rule ON entries(rule,seq)');con.commit()
        if used(index.parent,'*.sqlite')>cap or used(archive.parent,'*.jsonl.gz')>int(cfg.get('cleanup_plan_limit_mb',64))*1_000_000:
            raise RuntimeError('Đề xuất vượt hạn mức lưu trữ; chọn ít nhóm hơn. Chưa xóa gì.')
        return {'entry_archive':{'name':name,'sha256':checksum(archive)},'entry_count':count,
                'bytes':total,'tool_count':tools,'groups':groups,'entries':[]}
    except BaseException:
        con.close();archive.unlink(missing_ok=True);index.unlink(missing_ok=True)
        raise
    finally:con.close()


def entries(content):
    ref=content.get('entry_archive')
    if not ref:
        yield from content.get('entries',[]);return
    path=location(ref['name'])
    if checksum(path)!=ref['sha256']:raise ValueError('Archive đề xuất đã thay đổi; tạo đề xuất mới, không xóa.')
    count=0
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream:
            if len(line)>1_000_000:raise ValueError('Dòng metadata quá lớn')
            value=json.loads(line)
            if not isinstance(value,dict):raise ValueError('Metadata đề xuất không hợp lệ')
            count+=1;yield value
    if count!=content.get('entry_count'):raise ValueError('Số mục đề xuất không khớp; không xóa.')


@contextmanager
def open_index(content):
    ref=content.get('entry_archive')
    if not ref:raise ValueError('Đề xuất cũ; tạo đề xuất mới để phân trang an toàn.')
    path=location(ref['name'],True)
    if not path.exists():
        # Rebuild from compressed metadata, never from the original filesystem.
        con=sqlite3.connect(path)
        try:
            con.execute('CREATE TABLE entries(seq INTEGER PRIMARY KEY,path TEXT COLLATE NOCASE UNIQUE,rule TEXT,size INTEGER,payload TEXT)')
            cap=int(load().get('cleanup_index_limit_mb',128))*1_000_000
            for number,entry in enumerate(entries(content)):
                con.execute('INSERT OR IGNORE INTO entries(path,rule,size,payload) VALUES(?,?,?,?)',
                            (entry['path'],entry['rule'],entry.get('size'),json.dumps(entry,ensure_ascii=False)))
                if number%256==0:
                    con.commit()
                    if sum(p.stat().st_size for p in path.parent.glob('*.sqlite') if p.is_file())>cap:
                        raise RuntimeError('Đọc đề xuất vượt hạn mức tổng chỉ mục; dọn metadata hết hạn trước')
            con.execute('CREATE INDEX by_size ON entries(size,seq)');con.execute('CREATE INDEX by_rule ON entries(rule,seq)')
            con.commit()
        except BaseException:
            con.close();path.unlink(missing_ok=True);raise
        finally:con.close()
    con=sqlite3.connect(path);con.row_factory=sqlite3.Row
    try:yield con
    finally:con.close()


def page(content, *, offset=0, limit=20, sort='size', direction='desc', rule='', search=''):
    limit=int(limit);offset=int(offset)
    if not 1<=limit<=1000 or offset<0:raise ValueError('Trang đề xuất không hợp lệ')
    column={'path':'path','size':'size','rule':'rule'}.get(sort)
    if column is None or direction not in ('asc','desc'):raise ValueError('Sắp xếp đề xuất không hợp lệ')
    where=[];args=[]
    if rule:where.append('rule=?');args.append(rule)
    if search:where.append('instr(lower(path),lower(?))>0');args.append(search)
    clause=' WHERE '+' AND '.join(where) if where else ''
    with open_index(content) as con:
        count=con.execute('SELECT COUNT(*) FROM entries'+clause,args).fetchone()[0]
        rows=con.execute(f'SELECT payload FROM entries{clause} ORDER BY {column} {direction},seq ASC LIMIT ? OFFSET ?',[*args,limit,offset])
        result=[json.loads(row[0]) for row in rows]
    return {'entries':result,'total':count,'offset':offset,'limit':limit}


def remove_artifacts(content):
    ref=content.get('entry_archive')
    if ref:
        for indexed in (False,True):location(ref['name'],indexed).unlink(missing_ok=True)


def prune(store,*,reserve_slot=False):
    """Expiry removes only app-owned plan metadata, retaining a small audit summary."""
    for row in store.db.execute('SELECT id,content,status,result FROM plans').fetchall():
        if row['status']=='applying':
            from .elevation import alive
            running=json.loads(row['result']) if row['result'] else {}
            if alive(running.get('owner',{})):continue
        content=json.loads(row['content'])
        if content.get('expires',0)>=time.time() or content.get('entries_purged'):continue
        if not content.get('entry_archive'):content['entry_count']=len(content.get('entries',[]))
        remove_artifacts(content);content['entries']=[];content.pop('entry_archive',None);content['entries_purged']=True
        status='expired' if row['status']=='pending' else 'failed' if row['status']=='applying' else row['status']
        store.db.execute('UPDATE plans SET content=?,status=? WHERE id=?',(json.dumps(content,ensure_ascii=False),status,row['id']))
        result=json.loads(row['result']) if row['result'] else None
        if isinstance(result,dict) and len(result.get('items',[]))>20:
            result['items']=result['items'][:20];result['items_truncated']=True
            store.db.execute('UPDATE plans SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),row['id']))
    store.db.commit()
    cfg=load();keep=max(1,int(cfg.get('cleanup_max_summaries',200)));days=max(1,int(cfg.get('cleanup_summary_days',30)))
    current=time.time();completed=[];live=0
    for row in store.db.execute('SELECT id,status,content FROM plans ORDER BY created DESC').fetchall():
        content=json.loads(row['content'])
        if row['status']=='applying' or row['status']=='pending' and content.get('expires',0)>=current:
            live+=1;continue
        completed.append((row['id'],content))
    if reserve_slot and live>=keep:raise RuntimeError('Đã đạt hạn mức đề xuất đang chờ; dùng đề xuất hiện có hoặc chờ hết hạn.')
    available=max(0,keep-live-int(reserve_slot))
    for index,(plan_id,content) in enumerate(completed):
        if index>=available or content.get('expires',current)-3600<current-days*86400:
            remove_artifacts(content);store.db.execute('DELETE FROM plans WHERE id=?',(plan_id,))
    store.db.commit()
    # Orphans are only our metadata files, at least one hour old; never user files.
    active={json.loads(row[0]).get('entry_archive',{}).get('name') for row in store.db.execute('SELECT content FROM plans')}
    for indexed,pattern in ((False,'*.jsonl.gz'),(True,'*.sqlite')):
        base=home()/('data/tmp/cleanup-index' if indexed else 'data/cleanup/plans')
        for path in base.glob(pattern):
            name=path.name.removesuffix('.sqlite' if indexed else '.jsonl.gz')
            if NAME.fullmatch(name) and name not in active and path.stat().st_mtime<time.time()-3600:
                try:location(name,indexed).unlink()
                except OSError:pass


@contextmanager
def claim(content):
    """Bind the exact reviewed catalog before any execution; reject replay/races."""
    from .store import Store
    from .elevation import digest
    store=Store()
    try:
        store.db.execute('BEGIN IMMEDIATE')
        row=store.db.execute('SELECT * FROM plans WHERE id=?',(content['id'],)).fetchone()
        current=json.loads(row['content']) if row else {}
        if row:current.update(status=row['status'],result=json.loads(row['result']) if row['result'] else None)
        if current.get('status')!='pending' or digest(current)!=digest(content) or current.get('expires',0)<time.time():
            raise ValueError('Đề xuất đã thay đổi/được xử lý/hết hạn; xem và duyệt lại, không chạy lại')
        from .elevation import owner
        store.db.execute("UPDATE plans SET status='applying',result=? WHERE id=?",(json.dumps({'owner':owner()}),content['id']));store.db.commit()
    finally:store.close()
    try:yield
    except BaseException as exc:
        store=Store()
        try:
            store.db.execute("UPDATE plans SET status='failed',result=? WHERE id=? AND status='applying'",
                             (json.dumps({'error':str(exc),'note':'Tác vụ đã dừng; xem audit trước khi tạo đề xuất khác.'},ensure_ascii=False),content['id']))
            store.db.commit()
        finally:store.close()
        raise
