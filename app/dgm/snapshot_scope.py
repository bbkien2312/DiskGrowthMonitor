"""Compare captured scope policies without querying today's filesystem."""
import fnmatch
import json
from .config import comparison_path_key, browser_path, inside


def captured(scan):
    cfg=json.loads(scan['config']); summary=json.loads(scan.get('raw_summary',scan['summary']) or '{}')
    recorded=summary.get('effective_scope')
    return recorded or {'roots':cfg['roots'],'excludes':cfg.get('excludes',[]),
                        'strict_browsers':cfg.get('strict_browsers',False),'implicit_excludes':None,'volumes':None}


def differences(old,new):
    a,b=captured(old),captured(new);result=[]
    for key,label in [('roots','Thư mục/ổ được chọn'),('excludes','Bộ loại trừ'),('strict_browsers','Loại trừ Chrome/Edge'),
                      ('implicit_excludes','Thư mục app tự loại trừ'),('volumes','Danh tính ổ')]:
        left,right=a.get(key),b.get(key)
        if isinstance(left,list):left=sorted(comparison_path_key(x) for x in left)
        if isinstance(right,list):right=sorted(comparison_path_key(x) for x in right)
        if left!=right:result.append(f'{label}: cũ={a.get(key)}; mới={b.get(key)}')
    return result


def common_policy(old,new):
    a,b=captured(old),captured(new)
    if a.get('volumes') and b.get('volumes') and set(a['volumes'])!=set(b['volumes']):
        raise ValueError('Hai mốc thuộc danh tính ổ khác nhau; không tự ghép thành cùng ổ.')
    roots=[]
    for x in a['roots']:
        for y in b['roots']:
            if inside(x,y):roots.append(x)
            elif inside(y,x):roots.append(y)
    roots=sorted(set(roots),key=len)
    roots=[r for i,r in enumerate(roots) if not any(inside(r,p) for p in roots[:i])]
    if not roots:raise ValueError('Hai lượt không có vùng quét chung.')
    excludes=list(dict.fromkeys(a.get('excludes',[])+b.get('excludes',[])+(a.get('implicit_excludes') or [])+(b.get('implicit_excludes') or [])))
    return {'roots':roots,'excludes':excludes,'strict_browsers':bool(a['strict_browsers'] or b['strict_browsers']),
            'legacy_scope_unknown':a.get('implicit_excludes') is None or b.get('implicit_excludes') is None}


def contains(policy,path):
    if not any(inside(path,r) for r in policy['roots']):return False
    if policy['strict_browsers'] and browser_path(path):return False
    value=str(path).replace('\\','/').casefold().rstrip('/')
    return not any(inside(path,p) or fnmatch.fnmatch(value,str(p).replace('\\','/').casefold().rstrip('/')) for p in policy['excludes'])
