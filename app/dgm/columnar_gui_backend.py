"""Data adapters for the established GUI, not a replacement GUI.

The UI's small paged models keep their original contract. All file-record SQL
stays in the shared Parquet/RAM core; no legacy SQLite/JSONL indexes are opened.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import threading
import time

from . import production_core as core
from .columnar_compare import Comparison
from .columnar_ingest import PATH_KEY_VERSION
from .columnar_store import atomic_json, read_json
from .disk_measurements import captured_volumes
from .platform_ops import ProcessCancelled


def scan_row(scan):
    scope=dict(scan.get('scope') or {})
    if 'mft' in scan:scope['mft']=scan['mft']
    summary=dict(scan.get('legacy_summary') or {})
    summary.update(scan['summary'])
    summary.update(provenance={'app_version':scan.get('app_version')},
        normalization={'version':scan.get('path_key_version'),
                       'state':'verified' if scan.get('path_key_version')==PATH_KEY_VERSION else 'needs-source',
                       'app_version':scan.get('app_version'),
                       'warnings':scan.get('warnings',[])},
        import_complete=True,canonical_normalization_version=scan.get('path_key_version'),
        contract={'version':scan.get('columnar_version',1)},coverage=scan.get('coverage') or {},
        coverage_unverified=bool((scan.get('coverage') or {}).get('unknown_roots')),
        scan_method={'mft':scan.get('mft')},
        volumes={v['root']:{'total':v.get('capacity') if v['valid'] else None,
                           'used':v.get('used') if v['valid'] else None,
                           'free':v.get('free') if v['valid'] else None}
                 for v in captured_volumes(scan)})
    summary['errors']=len(scan.get('errors') or [])
    return {**scan,'config':json.dumps(scope,ensure_ascii=False),
            'summary':json.dumps(summary,ensure_ascii=False),'ordinal':scan['id']}


def summary_row(raw):
    """Lossless aliases for the old cards/history. Unknown is never zeroed."""
    result=dict(raw,older=scan_row(raw['older']) if raw.get('older') else None,
                newer=scan_row(raw['newer']))
    for side in ('old','new'):
        for group,prefix in (('common',''),('private','private_')):
            result[prefix+side+'_totals']=raw.get(group+'_'+side)
        scan=raw.get('older' if side=='old' else 'newer')
        result['full_'+side+'_totals']=json.loads(scan_row(scan)['summary']) if scan else None
    for group in ('common','private'):
        result[group+'_counts']=[r for r in raw['counts'] if (r['presence']=='both')==(group=='common')]
        for name,suffix in (('',''),('_disk','_allocated')):
            result['verified_'+group+suffix+'_delta']=raw.get('verified_'+group+name+'_delta')
            result['unverified_'+group+suffix+'_files']=raw.get('unknown_'+group+name+'_files',0)
    for suffix in ('','_allocated'):
        values=[result.get('verified_'+g+suffix+'_delta') for g in ('common','private')]
        result['verified'+suffix+'_delta']=sum(values) if all(v is not None for v in values) else None
        result['unverified_all'+suffix+'_files']=sum(result['unverified_'+g+suffix+'_files'] for g in ('common','private'))
    a,b=raw.get('older'),raw['newer']
    result['compatibility']={'engines':[s['engine'] for s in (a,b) if s],
        'cross_engine':bool(a and a['engine']!=b['engine']),
        'coverage_unverified':any((s.get('coverage') or {}).get('unknown_roots') for s in (a,b) if s)}
    if raw.get('summary_incomplete'):
        result.pop('unverified_all_files',None)
    return result


def detail_row(row,pair):
    # The old model switches Cũ/Mới using allocated_* itself. Its ± column is
    # always logical, independent of selected measurement/sort/filter/status.
    return dict(row,pair_id=pair,path_id=row['item_id'],
        before=row['logical_before'],after=row['logical_after'],delta=row['logical_delta'],
        old_revision=row['old_row'],new_revision=row['new_row'],
        created=row.get('new_created') if row['new_row'] is not None else row.get('old_created'),
        modified=row.get('new_modified') if row['new_row'] is not None else row.get('old_modified'),
        unverified_count=row.get('unknown_files',0),
        unverified_disk_count=row.get('unknown_disk_files',0))


class CatalogStore:
    def __init__(self,repo,**_):self.repo=repo
    def close(self):pass
    def scans(self):return [scan_row(row) for row in self.repo.list()]
    def scan(self,ident):return scan_row(self.repo.get(ident))
    def previous(self,ident):return core.previous(ident,self.repo)
    def export(self,ident,path):return core.export(ident,path)
    def delete_scan(self,ident):return self.delete_scans([ident])
    def delete_scans(self,ids):
        self.repo.delete(ids);return len(ids)
    def clear_scans(self):return self.delete_scans([r['id'] for r in self.repo.list()])


class Sessions:
    """One query per active pair, serialized across GUI worker threads.

    Closing a short-lived handle does NOT close the query: cursors and the
    bounded model's evicted-page reloads need the same RAM session. The window
    releases unused pairs when workers become idle (at most details + tree).
    """
    def __init__(self,repo):
        self.repo=repo;self.lock=threading.RLock();self.queries={}
    def handle(self,stop=None,notify=None):return Handle(self,stop,notify)
    @contextmanager
    def operation(self,stop=None):
        while not self.lock.acquire(timeout=.05):
            if stop is not None and stop.is_set():raise ProcessCancelled('Đã dừng tải')
        try:
            if stop is not None and stop.is_set():raise ProcessCancelled('Đã dừng tải')
            yield
        finally:self.lock.release()
    def retain(self,keys):
        with self.operation():
            for key in list(self.queries):
                if key not in keys:self.queries.pop(key).close()
    def close(self):self.retain(set())
    def history(self):
        records=read_json(self.repo.root/'comparisons.json',[])
        scans={r['id']:r for r in self.repo.list()};rows=[]
        for record in records:
            if record['newer'] not in scans or record['older'] is not None and record['older'] not in scans:continue
            raw=record.get('overview')
            if raw is None:
                # Pre-adapter summaries lack verified deltas. Keep them unknown
                # until explicitly reopened; never rebuild a join on startup.
                raw=dict(record,older=scans.get(record['older']),newer=scans[record['newer']],summary_incomplete=True)
            summary=summary_row(raw)
            rows.append(dict(record,status='archived',summary=json.dumps(summary,ensure_ascii=False)))
        return rows


class Handle:
    def __init__(self,pool,stop,notify):self.pool=pool;self.stop=stop;self.notify=notify
    def close(self):pass
    def prepare(self,older,newer,**_):
        key=(older or None,newer)
        with self.pool.operation(self.stop):
            if key not in self.pool.queries:
                query=Comparison(newer,older or None,repository=self.pool.repo,stop=self.stop,notify=self.notify)
                try:core.remember_comparison(self.pool.repo,query)
                except BaseException:query.close();raise
                self.pool.queries[key]=query
            elif self.notify:
                query=self.pool.queries[key]
                self.notify('progress',{'percent':100,'stage':'done','message':'Dùng lại phiên so sánh RAM; không ghép/chuẩn hóa lại',
                    'timing':{'operation':'comparison','operation_id':query.token,'state':'complete','seconds':0,
                              'stages':[{'key':'reuse','name':'Dùng lại phiên RAM của cặp mốc','seconds':0,'state':'complete'}]}})
            return key
    def summary(self,pair):
        with self.pool.operation(self.stop):return summary_row(self.pool.queries[pair].summary())
    def page(self,pair,**options):
        changes=options.get('changes','all')
        if isinstance(changes,(list,tuple)):
            if not changes:return {'rows':[],'cursor':None,'total':0}
            options['changes']=','.join(changes)
        measure=options.pop('measure','allocated');sort=options.get('sort','delta')
        options['sort']={'disk_old':'allocated_old','disk_new':'allocated_new',
                         'allocated':'allocated_delta','delta':'logical_delta'}.get(sort,sort)
        with self.pool.operation(self.stop):
            query=self.pool.queries[pair]
            started=time.perf_counter()
            result=query.page(measure=measure,stop=self.stop,**options)
            return dict(result,rows=[detail_row(row,pair) for row in result['rows']],query_seconds=time.perf_counter()-started)
    def roots(self,pair,roots,measure='allocated'):
        with self.pool.operation(self.stop):
            return [detail_row(r,pair) for r in self.pool.queries[pair].tree(measure=measure,stop=self.stop)['rows']]
    def history(self):return self.pool.history()
    def _remove(self,test):
        with self.pool.operation(self.stop):
            with self.pool.repo.locked():
                path=self.pool.repo.root/'comparisons.json';rows=read_json(path,[])
                atomic_json(path,[r for r in rows if not test(r)])
    def delete(self,ident):self._remove(lambda r:r['id']==ident)
    def delete_for_scan(self,ident):self.delete_for_scans([ident])
    def delete_for_scans(self,ids):
        ids=set(ids)
        with self.pool.operation(self.stop):
            self.pool.retain({k for k in self.pool.queries if k[0] not in ids and k[1] not in ids})
            self._remove(lambda r:r['older'] in ids or r['newer'] in ids)
    def clear_all(self):
        with self.pool.operation(self.stop):self.pool.close();self._remove(lambda r:True)
