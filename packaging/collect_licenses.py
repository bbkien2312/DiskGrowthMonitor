"""Generate release notices from installed packages, never user data."""
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys

root=Path(__file__).resolve().parent.parent
destination=root/'build/licenses';destination.mkdir(parents=True,exist_ok=True)
names=['PySide6-Essentials','shiboken6','typer','psutil','Send2Trash','APScheduler','pywin32','python-dateutil',
       'tzdata','ijson','tzlocal','six','rich','shellingham','annotated-doc','markdown-it-py','mdurl','Pygments','colorama']
notices=[]
for name in names:
    distribution=metadata.distribution(name);copied=[]
    for file in distribution.files or []:
        if not any(word in str(file).casefold() for word in ('license','licence','copyright','notice','copying')):continue
        source=Path(distribution.locate_file(file))
        if not source.is_file():continue
        relative=Path(*[p for p in Path(file).parts if p not in ('.','..')])
        target=destination/name/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target);copied.append(str(target.relative_to(destination)))
    if not copied:raise RuntimeError(f'Missing license files for {name}')
    notices.append({'name':name,'version':distribution.version,'license':distribution.metadata.get('License-Expression') or distribution.metadata.get('License','See bundled notices'),'files':copied})
python_license=Path(sys.base_prefix)/'LICENSE.txt'
if not python_license.is_file():raise RuntimeError('Python runtime license missing')
shutil.copy2(python_license,destination/'PYTHON-LICENSE.txt')
(destination/'INDEX.json').write_text(json.dumps(notices,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Collected notices for Python and {len(notices)} runtime distributions')
