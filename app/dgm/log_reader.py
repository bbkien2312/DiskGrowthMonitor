"""Bounded, non-Qt log parsing shared by CLI and GUI."""
import json
import re
LEVELS=['DEBUG','INFO','WARNING','ERROR','CRITICAL']
LEVEL_PATTERN=re.compile(r'^(?:(?:\d{4}-\d{2}-\d{2}[ T]\S+(?:\s+[\d:,\.]+)?(?:\s+UTC)?|\d{2}:\d{2}:\d{2}|\d{4})\s+)?(?:\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]|(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b)')


def severity(line):
    match=LEVEL_PATTERN.match(line)
    return next((x for x in match.groups() if x),None) if match else None

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
                item=json.loads(line);audit_level=item.get('level','INFO')
                line=f"{item.get('time','')} [{audit_level}] AUDIT {item.get('action','')} {json.dumps(item,ensure_ascii=False)}"
            except ValueError:pass
        found=severity(line)
        if found:current=found
        if level is None or current==level:lines.append((current,line))
    return lines,current
