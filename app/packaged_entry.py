import os
import sys
import json
from pathlib import Path

from dgm.startup_admin import preflight
preflight()

if getattr(sys, "frozen", False):
    app_root=Path(sys.executable).parent
    data_root=app_root
    try:
        linked=json.loads((app_root/'data-home.json').read_text(encoding='utf-8-sig'))['home']
        candidate=Path(linked)
        if not candidate.is_absolute() or candidate.resolve()!=candidate.absolute():raise ValueError('Invalid data home')
        data_root=candidate
    except FileNotFoundError:pass
    os.environ.setdefault("DGM_HOME", str(data_root))
    temporary=Path(os.environ['DGM_HOME'])/'data/tmp'
    temporary.mkdir(parents=True,exist_ok=True)
    for key in ('TEMP','TMP','SQLITE_TMPDIR'):os.environ[key]=str(temporary)
    os.environ['PYTHONNOUSERSITE']='1'
    if sys.stdout is None:sys.stdout=open(os.devnull,'w',encoding='utf-8')
    if sys.stderr is None:sys.stderr=open(os.devnull,'w',encoding='utf-8')
from dgm.cli import main

if len(sys.argv) == 1:
    sys.argv.append("gui")
main()

