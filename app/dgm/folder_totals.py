"""Linear rollup with nearest recorded ancestors for sparse legacy exports."""
import ntpath


def rollup(folders,direct,stop):
    by_key={};totals={}
    for row in folders:
        by_key.setdefault(row['path_key'],[]).append(row['path_id'])
        totals[row['path_id']]=[0,0,0,0]
    memo={}
    def ancestor(key):
        if key in memo:return memo[key]
        seen=[]
        while key:
            seen.append(key);ids=by_key.get(key,[])
            if len(ids)==1:
                answer=ids[0];break
            parent=ntpath.dirname(key)
            if parent==key:answer=None;break
            key=parent
        else:answer=None
        for item in seen:memo[item]=answer
        return answer
    def add(target,value):
        for k in (0,1):target[k]=None if target[k] is None or value[k] is None else target[k]+value[k]
        target[2]+=value[2] if len(value)>2 else 1
        if len(value)>3:target[3]|=value[3]
    orphans=0
    for key,value in direct.items():
        parent=ancestor(key)
        if len(by_key.get(key,[]))!=1:orphans+=1
        if parent is not None:add(totals[parent],value)
    for row in folders:  # Caller supplies deepest-first order.
        if stop.is_set():
            from .platform_ops import ProcessCancelled
            raise ProcessCancelled()
        parent=ancestor(row['parent_key'])
        if parent is not None and parent!=row['path_id']:add(totals[parent],totals[row['path_id']])
    return totals,orphans
