# Two entry points, one shared private runtime. No user config/data/logs.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules,copy_metadata
from PyInstaller.utils.win32.versioninfo import VSVersionInfo,FixedFileInfo,StringFileInfo,StringTable,StringStruct,VarFileInfo,VarStruct
import importlib.metadata

root=Path(SPECPATH).parent
version=importlib.metadata.version('disk-growth-monitor')
names=['disk-growth-monitor','PySide6-Essentials','shiboken6','typer','psutil','Send2Trash','APScheduler',
    'pywin32','python-dateutil','tzdata','ijson','tzlocal','six','rich','shellingham','annotated-doc',
    'markdown-it-py','mdurl','Pygments','colorama','duckdb','numpy']
datas=[(str(root/'build/licenses'),'licenses'),(str(root/'THIRD_PARTY.md'),'.'),(str(root/'app/dgm/update_helper.ps1'),'dgm'),(str(root/'app/dgm/update_watch.ps1'),'dgm'),(str(root/'app/dgm/assets/app-icon.svg'),'dgm/assets')]
for name in names:datas+=copy_metadata(name)
a=Analysis([str(root/'app/packaged_entry.py')],pathex=[str(root/'app')],binaries=[],datas=datas,
    hiddenimports=collect_submodules('dgm')+['win32timezone','win32com.shell.shell','win32com.shell.shellcon','ijson.backends.yajl2_c','numpy','_duckdb'],
    hookspath=[],runtime_hooks=[],excludes=['pytest','tkinter','PySide6.QtQml','PySide6.QtQuick','PySide6.QtWebEngineCore','IPython'])
# Qt 6.12 uses Windows' ICU API. Never bundle a same-named Conda/Poppler ICU
# exporting suffixed symbols: that shadows the OS library and breaks QtCore.
a.binaries=[item for item in a.binaries if Path(item[0]).name.casefold() not in ('icuuc.dll','icudt78.dll')]
pyz=PYZ(a.pure)
numbers=tuple(map(int,version.split('.')))+(0,)
resource=VSVersionInfo(ffi=FixedFileInfo(filevers=numbers,prodvers=numbers,mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),
    kids=[StringFileInfo([StringTable('040904B0',[StringStruct('FileDescription','Disk Growth Monitor - Parquet + DuckDB'),StringStruct('FileVersion',version),
       StringStruct('ProductName','Disk Growth Monitor - Parquet + DuckDB'),StringStruct('ProductVersion',version)])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])
gui=EXE(pyz,a.scripts,[],exclude_binaries=True,name='DiskGrowthMonitor',console=False,uac_admin=True,upx=False,version=resource,icon=str(root/'build/icons/app.ico'),contents_directory='.venv')
cli=EXE(pyz,a.scripts,[],exclude_binaries=True,name='dgm',console=True,upx=False,version=resource,icon=str(root/'build/icons/app.ico'),contents_directory='.venv')
bundle=COLLECT(gui,cli,a.binaries,a.datas,strip=False,upx=False,name='DiskGrowthMonitor')
