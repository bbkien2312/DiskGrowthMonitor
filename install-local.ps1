param([switch]$Dev, [switch]$Offline)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $taskRoot
$env:UV_PYTHON_INSTALL_DIR=Join-Path $taskRoot '.runtime\python'
$env:UV_CACHE_DIR=Join-Path $taskRoot '.runtime\cache'
$env:UV_PYTHON_BIN_DIR=Join-Path $taskRoot '.runtime\bin'
$env:UV_TOOL_DIR=Join-Path $taskRoot '.venv\tools'
$env:PYTHONUTF8='1'
$env:PYTHONNOUSERSITE='1'
New-Item -ItemType Directory -Path (Join-Path $taskRoot 'data\tmp') -Force | Out-Null
$env:TEMP=Join-Path $taskRoot 'data\tmp'
$env:TMP=$env:TEMP
$taskUv=Join-Path $taskRoot '.runtime\uv\uv.exe'
if(!(Test-Path -LiteralPath $taskUv)){
    if($Offline){throw 'uv is missing; run setup online once.'}
    New-Item -ItemType Directory -Path (Join-Path $taskRoot '.runtime\uv') -Force | Out-Null
    $taskZip=Join-Path $taskRoot '.runtime\uv-download.zip'
    Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/download/0.13.1/uv-x86_64-pc-windows-msvc.zip' -OutFile $taskZip
    Expand-Archive -LiteralPath $taskZip -DestinationPath (Join-Path $taskRoot '.runtime\uv') -Force
}
$taskArgs=@('sync','--locked','--python','3.12','--managed-python')
if($Dev){$taskArgs+=@('--extra','dev')}
if($Offline){$taskArgs+='--offline'}
& $taskUv @taskArgs
if($LASTEXITCODE -ne 0){throw ('uv sync failed: '+$LASTEXITCODE)}
Write-Output ('Ready: '+$taskRoot)
Write-Output 'Run launch-gui.cmd or launch-cli.cmd --help'

