"""Opt-in real-history QA: readonly backup, then operate exclusively on D:."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(4*1024*1024):h.update(chunk)
    return h.hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--resume-copy',action='store_true',help='Use the existing QA database, never recopy over it')
    parser.add_argument('--partitions',action='store_true',help='Audit approved automatic common/private partitions on scans 13-16')
    args=parser.parse_args();source=args.source.resolve();dest=args.destination.resolve()
    if dest.drive.casefold()!='d:' or not dest.is_relative_to(Path('D:/DiskGrowthMonitor/artifacts')):
        raise ValueError('QA destination must be below D:/DiskGrowthMonitor/artifacts')
    if dest.exists() and not args.resume_copy:raise ValueError('Use a fresh QA directory; never overwrite an existing database')
    print('Hashing original database (readonly)',flush=True);before=digest(source)
    if args.resume_copy:
        assert (dest/'data/history.sqlite').exists()
        (run_partitions if args.partitions else run_checks)(source,dest,before)
        return
    (dest/'data').mkdir(parents=True)
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True);out=sqlite3.connect(dest/'data/history.sqlite')
    backup_started=time.monotonic();last=[0]
    def progress(status,remaining,total):
        if time.monotonic()-last[0]>5:
            print(f'Backup status={status}, pages={total-remaining}/{total}',flush=True);last[0]=time.monotonic()
        if time.monotonic()-backup_started>180:raise TimeoutError('Readonly backup could not complete in 180s; no source writes attempted')
    try:
        src.execute('BEGIN');src.execute('SELECT COUNT(*) FROM scans').fetchone()
        src.backup(out,pages=8192,progress=progress)
    finally:src.close();out.close()
    print('Copied history to D:, original is never opened writable',flush=True)
    (run_partitions if args.partitions else run_checks)(source,dest,before)


def run_checks(source,dest,before):
    from dgm import config
    config.ROOT=dest;config.save(config.defaults())
    from dgm.store import Store
    from dgm.normalization import Normalizations
    from dgm.comparison import Comparisons
    store=Store();originals={i:store.scan(i,effective=False) for i in (10,11,12,13,14)}
    norm=Normalizations();reports=[]
    try:
        for ident in originals:
            started=time.perf_counter();report=norm.inspect(ident,apply=True)
            print(json.dumps({'scan':ident,'seconds':round(time.perf_counter()-started,2),'before':report['before'],
                'after':report['after'],'state':report['state'],'changed_rows':report['changed_rows']},ensure_ascii=False),flush=True)
            assert store.scan(ident,effective=False)==originals[ident]
            reports.append(report)
    finally:norm.close()
    cache=Comparisons();comparisons=[]
    try:
        for a,b,common in ((10,14,False),(12,13,False),(13,14,True)):
            print(f'Comparing {a}->{b}, common={common}',flush=True)
            started=time.perf_counter();pair=cache.prepare(a,b,common=common);summary=cache.summary(pair)
            page=cache.page(pair,limit=20)
            comparisons.append({'older':a,'newer':b,'common':common,'seconds':round(time.perf_counter()-started,2),
                'rows':summary['rows'],'first_page':len(page['rows']),'old_totals':summary['old_totals'],'new_totals':summary['new_totals']})
            for totals in (summary['old_totals'],summary['new_totals']):assert totals['logical'] is not None
            # Root, summary and file-row totals agree (don't sum folder totals).
            for root in (summary.get('common_scope') or json.loads(summary['newer']['config']))['roots']:
                from dgm.config import comparison_path_key
                row=cache.db.execute('SELECT c.* FROM diff_rows c JOIN source.paths p ON p.id=c.path_id WHERE pair_id=? AND p.path_key=?',
                    (pair,comparison_path_key(root))).fetchone()
                if len((summary.get('common_scope') or json.loads(summary['newer']['config']))['roots'])==1 and row:
                    assert row['after']==summary['new_totals']['logical']
                    assert row['before']==summary['old_totals']['logical']
            print(json.dumps({k:v for k,v in comparisons[-1].items() if k not in ('old_totals','new_totals')},ensure_ascii=False),flush=True)
            started=time.perf_counter();assert cache.prepare(a,b,common=common)==pair
            comparisons[-1]['cached_seconds']=round(time.perf_counter()-started,3)
        pair=cache.prepare(13,14)
        assert cache.summary(pair)['common_scope']['strict_browsers']
    finally:cache.close();store.close()
    after=digest(source);assert before==after,'Original DB changed during QA; investigate external writer'
    result={'source_sha256_before':before,'source_sha256_after':after,'original_unchanged':True,
            'reports':reports,'comparisons':comparisons}
    (dest/'qa-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: original hash unchanged; report '+str(dest/'qa-report.json'),flush=True)


def run_partitions(source,dest,before):
    from dgm import config
    config.ROOT=dest;config.save(config.defaults())
    from dgm.store import Store
    from dgm.comparison import Comparisons
    store=Store();originals={sid:store.scan(sid,effective=False) for sid in (13,14,15,16)}
    print('Baseline auto selection:',store.previous(15),store.previous(16),flush=True)
    assert store.previous(15)==14 and store.previous(16)==15
    last=[0]
    def notify(event,data):
        if time.monotonic()-last[0]>8:
            print(event,str(data)[:230],flush=True);last[0]=time.monotonic()
    c=Comparisons(notify=notify);results=[]
    try:
        for a,b in ((13,15),(14,16),(15,16)):
            from dgm.comparison import VERSION
            archived=c.db.execute("SELECT id,summary FROM pairs WHERE older=? AND newer=? AND version=? AND status='archived'",(a,b,VERSION)).fetchone()
            started=time.perf_counter()
            if archived:pair=archived['id'];summary=json.loads(archived['summary'])
            else:pair=c.prepare(a,b);summary=c.summary(pair)
            result={'older':a,'newer':b,'seconds':round(time.perf_counter()-started,3),
                    'common_old':summary['old_totals'],'common_new':summary['new_totals'],
                    'private_old':summary['private_old_totals'],'private_new':summary['private_new_totals'],
                    'common_rows':summary['common_rows'],'private_rows':summary['private_rows'],'reasons':summary['private_reasons']}
            for side in ('old','new'):
                common=summary[side+'_totals'];private=summary['private_'+side+'_totals'];full=summary['full_'+side+'_totals']
                assert common['logical']+private['logical']==full['logical']
                assert common['allocated_known']+private['allocated_known']==full['allocated_known']
                assert common['allocated_unknown_files']+private['allocated_unknown_files']==full['allocated_unknown_files']
            if archived:
                result['detail_checks']='Archived summary reconciled; detailed rows not repeated on this resumed run'
                results.append(result)
                print(f'{a}->{b}: archived summary reconciles (detailed checks passed before archive)',flush=True)
                continue
            result.update(verify_partition_rows(c,pair,summary,config.comparison_path_key('C:\\')))
            started=time.perf_counter();assert c.prepare(a,b)==pair;result['cached_seconds']=round(time.perf_counter()-started,3)
            assert all(store.scan(sid,effective=False)==row for sid,row in originals.items())
            results.append(result)
            print(json.dumps({k:v for k,v in result.items() if not isinstance(v,(dict,list))},ensure_ascii=False),flush=True)
    finally:c.close();store.close()
    after=digest(source);assert before==after
    report={'source_unchanged':True,'source_sha256':after,'results':results}
    (dest/'partition-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: partitions reconcile, original database hash unchanged',flush=True)


def verify_partition_rows(cache,pair,summary,root_key):
    """Check v8 presence partitions, not the retired v7 private_rows table."""
    timings={}
    for section in ('common','private'):
        started=time.perf_counter();page=cache.page(pair,section=section,kind='file',limit=20)
        timings[section+'_page_seconds']=round(time.perf_counter()-started,3)
        assert all((r['presence']=='both')==(section=='common') for r in page['rows'])
        # Confirmed additions/deletions in Riêng have real deltas; absent
        # coverage must stay unknown rather than being counted as deletion.
        assert all(r['delta'] is None for r in page['rows'] if not r['comparable'])
        for side,column in (('old','before'),('new','after')):
            root=cache.db.execute(f"SELECT x.{section}_{column} FROM folder_parts x JOIN source.paths p ON p.id=x.path_id WHERE x.pair_id=? AND p.path_key=?",(pair,root_key)).fetchone()
            totals=summary[('private_' if section=='private' else '')+side+'_totals']
            if totals['logical'] or root:
                assert root and root[0]==totals['logical'],(pair,section,side,root[0] if root else None,totals['logical'])
    return timings


if __name__=='__main__':main()
