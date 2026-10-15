"""Bounded, non-Qt log parsing shared by CLI and GUI."""
import json
import re
LEVELS=['DEBUG','INFO','WARNING','ERROR','CRITICAL']

def read_tail(path,max_bytes=500_000):
    with path.open('rb') as file:
        file.seek(0,2);end=file.tell();start=max(0,end-max_bytes);file.seek(start)
        text=file.read().decode('utf-8',errors='replace')
    return (text.partition('\n')[2] if start else text),end

def parse_lines(text,level=None,audit=False,current='INFO'):
    lines=[]
    for line in text.splitlines():
        if audit:
            try:
                item=json.loads(line);severity=item.get('level','INFO')
                line=f"{item.get('time','')} {severity} AUDIT {item.get('action','')} {json.dumps(item,ensure_ascii=False)}"
            except ValueError:pass
        match=re.search(r'\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b',line)
        if match:current=match[1]
        if level is None or current==level:lines.append((current,line))
    return lines,current
