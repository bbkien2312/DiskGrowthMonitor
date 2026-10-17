"""Resolve canonical JSONL history and disposable SQLite indexes (legacy compatible)."""
from contextlib import contextmanager
from .config import home,load


def log_mode():
    if (home()/'data/history/catalog.json').exists():return True
    # Selecting JSONL is a request to convert, not permission to hide existing
    # SQLite history behind a newly created empty catalog.
    if (home()/'data/history.sqlite').exists():return False
    return load().get('history_format')!='sqlite'


def comparison_history():
    """Read summaries only; never migrate schemas or load snapshot records."""
    if log_mode():
        from .history_index import read_json
        records=read_json(home()/'data/history/catalog.json',{}).get('comparisons',[])
        # Catalog summaries survive exit, detailed rows do not. Do not claim
        # that a released temporary index is still ready to serve pages.
        import sqlite3
        path=home()/'data/tmp/history-index/comparisons.sqlite';states={}
        if path.exists():
            db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
            try:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='pairs' AND type='table'").fetchone():
                    states=dict(db.execute('SELECT id,status FROM pairs'))
            except sqlite3.OperationalError:
                pass  # An unavailable disposable cache does not hide summaries.
            finally:db.close()
        records=[{**row,'status':states.get(row['id'],'archived') if row.get('status')=='ready' else row.get('status')} for row in records]
    else:
        import sqlite3
        path=home()/'data/comparisons.sqlite'
        if not path.exists():return []
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30)
        db.row_factory=sqlite3.Row
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='pairs' AND type='table'").fetchone():return []
            records=[dict(row) for row in db.execute('SELECT * FROM pairs')]
        finally:db.close()
    latest={}
    for row in records:
        key=(row.get('older') or 0,row['newer'])
        if key not in latest or row['id']>latest[key]['id']:latest[key]=row
    return sorted(latest.values(),key=lambda row:row['id'],reverse=True)


def database_path(name,*,create=True):
    if not log_mode():return home()/'data'/name
    if create:return manager().index/name
    return home()/'data/tmp/history-index'/name


def manager():
    from .history_index import get_manager
    return get_manager()


@contextmanager
def snapshots(ids,*,stop=None,notify=None):
    if not log_mode():yield;return
    with manager().snapshots(ids,stop=stop,notify=notify):yield


def close_indexes():
    from .history_index import close_managers
    close_managers()


def clear_indexes():
    if not log_mode():raise ValueError('Chuyển lịch sử sang JSONL trước khi bỏ chỉ mục SQLite')
    from .history_index import mutex,read_json
    from .elevation import alive
    current=manager()
    with mutex():
        state=read_json(current.state_path,{})
        if current.pins or any(alive(o) and o['pid']!=current.identity['pid'] for o in state.get('owners',[])):
            raise RuntimeError('Chỉ mục đang được app/CLI khác sử dụng; đóng các bản khác trước')
        before=sum(p.stat().st_size for p in current.index.glob('*.sqlite*') if p.is_file())
        current.close()
        remaining=sum(p.stat().st_size for p in current.index.glob('*.sqlite*') if p.is_file())
        return {'reclaimed_bytes':max(0,before-remaining),'history_logs_preserved':True,'remaining_index_bytes':remaining}
