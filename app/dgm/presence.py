"""Presence is a display partition; evidence independently controls deltas.

Never inspect today's filesystem to decide what existed in a saved snapshot.
The source and normalized snapshots remain immutable; only the diff cache changes.
"""
import json
from .config import comparison_path_key, inside
from .snapshot_scope import captured, contains
from .folder_totals import rollup


class Presence:
    def partition_maps(self, overview):
        self.has_older=bool(overview['older'])
        snapshots={'old':overview['older'],'new':overview['newer']}
        policies={};uncertain={};legacy={}
        for side,scan in snapshots.items():
            if not scan:continue
            policy=captured(scan)
            policies[side]={**policy,'implicit_excludes':policy.get('implicit_excludes') or []}
            policies[side]['excludes']=policy.get('excludes',[])+(policy.get('implicit_excludes') or [])
            totals=json.loads(scan['summary'] or '{}');coverage=totals.get('coverage') or {}
            uncertain[side]=list(coverage.get('unknown_roots',[]))
            uncertain[side]+=[r['path'] for r in coverage.get('error_regions',[]) if isinstance(r,dict) and r.get('path')]
            uncertain[side]+=[r[0] for r in self.db.execute('SELECT path FROM source.errors WHERE scan_id=?',(scan['id'],))]
            legacy[side]=bool((totals.get('coverage_unverified') or scan['status']=='partial') and not coverage)

        def issue(path,side,present,key_count,status,logical):
            if present:
                tags=[]
                if key_count and key_count>1:tags.append('collision')
                if logical is None or status in ('changed-during-scan','unavailable-after-export'):tags.append('metadata')
                return '|'.join(tags)
            if side not in policies:return ''  # Initial snapshot is not a failed scan.
            if not contains(policies[side],path):
                from .config import browser_path
                return 'browser' if policies[side].get('strict_browsers') and browser_path(path) else 'scope'
            if any(inside(path,root) for root in uncertain[side]):return 'coverage'
            return 'legacy' if legacy[side] else ''

        self.db.create_function('dgm_presence_issue',6,issue)
        self.db.execute('DROP TABLE IF EXISTS temp.presence_flags')
        self.db.execute('''CREATE TEMP TABLE presence_flags AS
          SELECT n.path_key,
            EXISTS(SELECT 1 FROM compare_old x WHERE x.path_key=n.path_key) AS has_old,1 AS has_new,
            dgm_presence_issue(n.path,'old',o.path_id IS NOT NULL,o.key_count,o.status,o.logical) AS old_issue,
            dgm_presence_issue(n.path,'new',1,n.key_count,n.status,n.logical) AS new_issue
          FROM compare_new n LEFT JOIN compare_old o ON o.path_key=n.path_key AND n.key_count=1 AND o.key_count=1
          UNION ALL
          SELECT o.path_key,1,0,dgm_presence_issue(o.path,'old',1,o.key_count,o.status,o.logical),
            dgm_presence_issue(o.path,'new',0,NULL,NULL,NULL)
          FROM compare_old o WHERE NOT EXISTS(SELECT 1 FROM compare_new n WHERE n.path_key=o.path_key)''')
        # Colliding legacy keys retain every measured row, but may never become
        # a confident addition/deletion or be multiplied by a many-to-many join.
        self.db.execute('DROP TABLE IF EXISTS temp.presence_keys')
        self.db.execute('''CREATE TEMP TABLE presence_keys AS SELECT path_key,MAX(has_old) AS has_old,MAX(has_new) AS has_new,
          TRIM(REPLACE(GROUP_CONCAT(DISTINCT old_issue),',','|'),'|') AS old_issue,
          TRIM(REPLACE(GROUP_CONCAT(DISTINCT new_issue),',','|'),'|') AS new_issue
          FROM presence_flags GROUP BY path_key''')
        self.db.execute('CREATE UNIQUE INDEX temp.presence_key ON presence_keys(path_key)')
        original_old=overview.get('old_totals');original_new=overview['new_totals']
        overview['full_old_totals']=original_old;overview['full_new_totals']=original_new
        for side,other,original in (('old','new',original_old),('new','old',original_new)):
            for group,matched in (('common',True),('private',False)):
                view=f'presence_{group}_{side}'
                self.db.execute(f'DROP VIEW IF EXISTS temp.{view}')
                self.db.execute(f'''CREATE TEMP VIEW {view} AS SELECT t.* FROM compare_{side} t WHERE
                  {'EXISTS' if matched else 'NOT EXISTS'}(SELECT 1 FROM compare_{other} x WHERE x.path_key=t.path_key)''')
                key=side+'_totals' if matched else 'private_'+side+'_totals'
                overview[key]=self._map_totals(view,original) if side!='old' or self.has_older else None
        overview['partition_note']='Chung: có bản ghi ở hai mốc. Riêng: chỉ có ở một mốc. Chỉ tính thay đổi đủ bằng chứng, không coi không quét/chưa rõ là đã xóa. Allocated không cộng lặp hardlink.'

    def annotate_presence(self,pair):
        self.db.execute('''UPDATE diff_rows AS c SET
          presence=CASE WHEN k.has_old AND k.has_new THEN 'both' WHEN k.has_old THEN 'old' ELSE 'new' END,
          old_issue=k.old_issue,new_issue=k.new_issue
          FROM source.paths p JOIN presence_keys k ON p.path_key=k.path_key WHERE c.path_id=p.id AND c.pair_id=?''',(pair,))
        self.db.execute("UPDATE diff_rows SET reason=TRIM(old_issue||'|'||new_issue,'|') WHERE pair_id=?",(pair,))
        self.db.execute('''UPDATE diff_rows SET delta=NULL,allocated_delta=NULL,abs_delta=-1,comparable=0,
          change=CASE WHEN (presence='new' AND old_issue IN ('browser','scope')) OR
                           (presence='old' AND new_issue IN ('browser','scope')) THEN 'not_scanned' ELSE 'unknown' END
          WHERE pair_id=? AND reason<>'' ''',(pair,))
        self.db.execute("UPDATE diff_rows SET comparable=0,delta=NULL,allocated_delta=NULL,abs_delta=-1 WHERE pair_id=? AND change IN ('unknown','first_scan')",(pair,))

    def finish_presence(self,pair,overview):
        # Count file bytes, never folder aggregates. This also handles a path
        # changing file<->folder and a move crossing two different parents.
        self.db.execute('DROP TABLE IF EXISTS temp.measured_changes')
        self.db.execute('''CREATE TEMP TABLE measured_changes AS
          SELECT o.path_key,o.parent_key,k.has_old AND k.has_new AS shared,
            -o.logical AS delta,-o.allocated AS allocated,
            k.old_issue='' AND k.new_issue='' AND o.logical IS NOT NULL AS known
          FROM compare_old o JOIN presence_keys k ON k.path_key=o.path_key WHERE o.kind='file'
          UNION ALL SELECT n.path_key,n.parent_key,k.has_old AND k.has_new,n.logical,n.allocated,
            k.old_issue='' AND k.new_issue='' AND n.logical IS NOT NULL
          FROM compare_new n JOIN presence_keys k ON k.path_key=n.path_key WHERE n.kind='file' ''')
        folders=[dict(r) for r in self.db.execute('''SELECT c.*,p.path_key,p.parent_key FROM diff_rows c
          JOIN source.paths p ON p.id=c.path_id WHERE c.pair_id=? AND c.kind='folder' ORDER BY LENGTH(p.path) DESC''',(pair,))]
        self.db.execute('''UPDATE measured_changes SET parent_key=path_key WHERE path_key IN
          (SELECT p.path_key FROM diff_rows c JOIN source.paths p ON p.id=c.path_id WHERE c.pair_id=? AND c.kind='folder')''',(pair,))
        direct={'both':{},'only':{}}
        for side,other in (('old','new'),('new','old')):
            for r in self.db.execute(f'''SELECT CASE WHEN EXISTS(SELECT 1 FROM compare_{other} x WHERE x.path_key=t.path_key AND x.kind='folder')
                THEN t.path_key ELSE t.parent_key END AS parent_key,
              EXISTS(SELECT 1 FROM compare_{other} x WHERE x.path_key=t.path_key) AS matched,
              CASE WHEN SUM(t.logical IS NULL)>0 THEN NULL ELSE SUM(t.logical) END AS logical
              FROM compare_{side} t WHERE t.kind='file' GROUP BY 1,matched'''):
                target=direct['both' if r['matched'] else 'only'].setdefault(r['parent_key'],[0,0,0,0])
                index=0 if side=='old' else 1
                target[index]=None if target[index] is None or r['logical'] is None else target[index]+r['logical']
        shared,_=rollup(folders,direct['both'],self.stop);private,_=rollup(folders,direct['only'],self.stop)
        known={r['parent_key']:[r['delta'],r['allocated'],r['unknown'],0] for r in self.db.execute('''SELECT parent_key,
          COALESCE(SUM(CASE WHEN known THEN delta ELSE 0 END),0) AS delta,
          CASE WHEN SUM(known AND allocated IS NULL)>0 THEN NULL ELSE COALESCE(SUM(CASE WHEN known THEN allocated ELSE 0 END),0) END AS allocated,
          SUM(NOT known) AS unknown FROM measured_changes GROUP BY parent_key''')}
        verified,_=rollup(folders,known,self.stop)
        self.db.execute('DELETE FROM folder_parts WHERE pair_id=?',(pair,))
        parts=[];updates=[]
        for row in folders:
            pid=row['path_id'];s=shared[pid];p=private[pid];v=verified[pid]
            parts.append((pair,pid,s[0],s[1],p[0],p[1],v[2]))
            rollup_metadata=row['presence']=='both' and set(row['reason'].split('|'))<={'metadata',''}
            delta=v[0] if self.has_older and (not row['reason'] or rollup_metadata) else None
            allocated=v[1] if delta is not None else None
            change=row['change']
            if delta is not None and change not in ('moved','moved_from','new','missing','replaced'):
                change='partial' if v[2] else 'grown' if delta>0 else 'shrunk' if delta<0 else 'modified' if change=='modified' else 'same'
            updates.append((delta,abs(delta) if delta is not None else -1,allocated,change,int(delta is not None and not v[2]),pair,pid))
        self.db.executemany('INSERT INTO folder_parts VALUES(?,?,?,?,?,?,?)',parts)
        self.db.executemany('UPDATE diff_rows SET delta=?,abs_delta=?,allocated_delta=?,change=?,comparable=? WHERE pair_id=? AND path_id=?',updates)
        for group,condition in (('common',"presence='both'"),('private',"presence<>'both'"),('all','1')):
            counts=[dict(r) for r in self.db.execute(f'SELECT kind,change,COUNT(*) count FROM diff_rows WHERE pair_id=? AND {condition} GROUP BY kind,change',(pair,))]
            overview[group+'_counts']=counts
            overview[group+'_rows']=sum(r['count'] for r in counts)
            measured_condition={'common':'shared','private':'NOT shared','all':'1'}[group]
            totals=self.db.execute(f'''SELECT COALESCE(SUM(CASE WHEN known THEN delta ELSE 0 END),0),
              COUNT(DISTINCT CASE WHEN NOT known THEN path_key END) FROM measured_changes WHERE {measured_condition}''').fetchone()
            overview['verified_'+group+'_delta']=totals[0] if self.has_older else None
            overview['unverified_'+group+'_files']=totals[1] or 0
        overview['verified_delta']=overview['verified_all_delta']
        overview['counts']=overview['all_counts'];overview['rows']=overview['all_rows']
        overview['private_reasons']=[dict(r) for r in self.db.execute("SELECT reason,COUNT(*) count FROM diff_rows WHERE pair_id=? AND presence<>'both' AND kind='file' GROUP BY reason",(pair,))]


def enrich_presence(row):
    from .snapshot_scope import REASONS
    tags=set((row.get('reason') or '').replace(',','|').split('|'))
    row['reason_text']=' • '.join(v for k,v in REASONS.items() if k in tags)
    return row
