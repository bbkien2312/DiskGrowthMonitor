param([switch]$Offline,[switch]$SkipSync)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
if(!$SkipSync){& (Join-Path $taskRoot 'install-local.ps1') -Dev -Offline:$Offline}
Set-Location -LiteralPath $taskRoot
$env:PYINSTALLER_CONFIG_DIR=Join-Path $taskRoot '.runtime\pyinstaller-cache'
$env:TEMP=Join-Path $taskRoot 'data\tmp'
$env:TMP=$env:TEMP
$env:PYTHONUTF8='1'
$taskPython=Join-Path $taskRoot '.venv\Scripts\python.exe'
& $taskPython (Join-Path $taskRoot 'packaging\generate_icons.py')
if($LASTEXITCODE -ne 0){throw 'Application icon generation failed'}
& $taskPython (Join-Path $taskRoot 'packaging\collect_licenses.py')
if($LASTEXITCODE -ne 0){throw 'License collection failed'}
$taskOriginalPath=$env:PATH
try {
    # Only change this build process, never System/User PATH. Unrelated tools such
    # as Poppler can carry incompatible DLLs with the same name as Windows ICU.
    $env:PATH=@((Join-Path $taskRoot '.venv\Scripts'),(Join-Path $env:SystemRoot 'System32'),$env:SystemRoot) -join ';'
    & $taskPython -m PyInstaller --noconfirm --clean (Join-Path $taskRoot 'packaging\DiskGrowthMonitor.spec')
} finally {$env:PATH=$taskOriginalPath}
if($LASTEXITCODE -ne 0){throw 'Application build failed'}
Copy-Item -LiteralPath (Join-Path $taskRoot 'packaging\qt-licenses') -Destination (Join-Path $taskRoot 'dist\DiskGrowthMonitor\.venv\licenses\Qt') -Recurse -Force
Write-Output ('Application: '+(Join-Path $taskRoot 'dist\DiskGrowthMonitor'))

