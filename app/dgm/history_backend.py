"""Resolve canonical JSONL history and disposable SQLite indexes (legacy compatible)."""
from contextlib import contextmanager
from .config import home,load


def log_mode():
    if (home()/'data/history/catalog.json').exists():return True
    choice=load().get('history_format')
    if choice=='sqlite':return False
    if choice=='jsonl':return True
    return not (home()/'data/history.sqlite').exists()


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
