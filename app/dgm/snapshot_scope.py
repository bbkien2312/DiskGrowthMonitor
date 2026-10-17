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
        if left!=right:
            if left is None or right is None:
                result.append(f'{label}: chưa ghi nhận ở một mốc (không coi là khác ổ/phạm vi)')
            else:result.append(f'{label}: cũ={a.get(key)}; mới={b.get(key)}')
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
    excludes=list(dict.fromkeys(a.get('excludes',[])+b.get('excludes',[])+(a.get('implicit_excludes') or [])+(b.get('implicit_excludes') or [])))
    return {'roots':roots,'excludes':excludes,'strict_browsers':bool(a['strict_browsers'] or b['strict_browsers']),
            'legacy_scope_unknown':a.get('implicit_excludes') is None or b.get('implicit_excludes') is None}


def contains(policy,path):
    if not any(inside(path,r) for r in policy['roots']):return False
    if policy['strict_browsers'] and browser_path(path):return False
    value=str(path).replace('\\','/').casefold().rstrip('/')
    return not any(inside(path,p) or fnmatch.fnmatch(value,str(p).replace('\\','/').casefold().rstrip('/')) for p in policy['excludes'])


REASONS = {'browser':'Chrome/Edge', 'scope':'Phạm vi / loại trừ thêm',
           'coverage':'Quyền / vùng chưa xác minh (có thể gồm vùng MFT đọc thêm)',
           'legacy':'Lịch sử thiếu bằng chứng độ bao phủ',
           'metadata':'Metadata thiếu / thay đổi khi quét', 'collision':'Đường dẫn không duy nhất'}


def option_label(scan):
    """Requested settings are not evidence that an engine actually used MFT."""
    cfg=json.loads(scan['config']);summary=json.loads(scan.get('raw_summary',scan['summary']) or '{}')
    browser='Không' if cfg.get('strict_browsers') else 'Có'
    provenance=summary.get('scan_method',{})
    mft=provenance.get('mft')
    if scan['engine']=='native':mft=False
    elif scan['engine']=='wiztree' and 'mft' in cfg:mft=bool(cfg['mft'])
    return f"Chrome/Edge: {browser} • MFT: {'Có' if mft else 'Không' if mft is False else 'Chưa ghi nhận'}"
