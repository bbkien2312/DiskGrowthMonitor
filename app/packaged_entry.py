import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    os.environ.setdefault("DGM_HOME", str(Path(sys.executable).parent))
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

