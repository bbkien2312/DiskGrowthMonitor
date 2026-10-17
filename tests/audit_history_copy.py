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
    args=parser.parse_args();source=args.source.resolve();dest=args.destination.resolve()
    if dest.drive.casefold()!='d:' or not dest.is_relative_to(Path('D:/DiskGrowthMonitor/artifacts')):
        raise ValueError('QA destination must be below D:/DiskGrowthMonitor/artifacts')
    if dest.exists() and not args.resume_copy:raise ValueError('Use a fresh QA directory; never overwrite an existing database')
    print('Hashing original database (readonly)',flush=True);before=digest(source)
    if args.resume_copy:
        assert (dest/'data/history.sqlite').exists()
        run_checks(source,dest,before)
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
    run_checks(source,dest,before)


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
        try:cache.prepare(13,14)
        except ValueError as exc:assert 'Chrome/Edge' in str(exc)
        else:raise AssertionError('Different browser scope was silently accepted')
    finally:cache.close();store.close()
    after=digest(source);assert before==after,'Original DB changed during QA; investigate external writer'
    result={'source_sha256_before':before,'source_sha256_after':after,'original_unchanged':True,
            'reports':reports,'comparisons':comparisons}
    (dest/'qa-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: original hash unchanged; report '+str(dest/'qa-report.json'),flush=True)


if __name__=='__main__':main()
