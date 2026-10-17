param([Parameter(Mandatory=$true)][string]$Installer,
      [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedSha256)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$taskInstaller=[System.IO.Path]::GetFullPath($Installer)
$taskExpected=@((Join-Path $taskRoot 'dist\release-v1.2.0\installer.exe'),(Join-Path $taskRoot 'dist\preview-chung-rieng\installer.exe'),(Join-Path $taskRoot 'dist\release-v1.3.0\installer.exe'),(Join-Path $taskRoot 'dist\release-v1.3.1\installer.exe'))
if($taskInstaller -notin $taskExpected){throw 'Only an explicitly allowed app-owned preview installer may be tested.'}
if((Get-FileHash -LiteralPath $taskInstaller -Algorithm SHA256).Hash -ne $ExpectedSha256){throw 'Preview checksum changed.'}
$taskOutput=Split-Path -Parent $taskInstaller
$taskStatus=Join-Path $taskOutput 'admin-smoke-status.json'
$taskPython=Join-Path $taskRoot '.venv\Scripts\python.exe'
$env:PYTHONUTF8='1'
$taskTemp=Join-Path $taskRoot 'data\test-tmp'
if((Test-Path -LiteralPath $taskTemp) -and ((Get-Item -LiteralPath $taskTemp -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Test temp must not be a reparse point.'}
New-Item -ItemType Directory -Path $taskTemp -Force | Out-Null
$env:TEMP=$taskTemp
$env:TMP=$env:TEMP
Set-Location -LiteralPath $taskRoot
try {
    if(-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator approval required.'}
    @{phase='fresh-install';fixture_only=$true}|ConvertTo-Json|Out-File -LiteralPath $taskStatus -Encoding utf8
    & $taskPython (Join-Path $taskRoot 'packaging\smoke_release.py') $taskInstaller 2>&1 | Out-File -LiteralPath (Join-Path $taskOutput 'fresh-install-smoke.log') -Encoding utf8
    if($LASTEXITCODE -ne 0){throw 'Fresh-install smoke failed; see fresh-install-smoke.log'}
    @{phase='inplace-update';fixture_only=$true}|ConvertTo-Json|Out-File -LiteralPath $taskStatus -Encoding utf8
    & $taskPython (Join-Path $taskRoot 'packaging\smoke_inplace_update.py') $taskInstaller (Join-Path $taskRoot 'dist\installer.exe') 2>&1 | Out-File -LiteralPath (Join-Path $taskOutput 'inplace-update-smoke.log') -Encoding utf8
    if($LASTEXITCODE -ne 0){throw 'In-place-update smoke failed; see inplace-update-smoke.log'}
    @{phase='complete';fixture_only=$true;success=$true}|ConvertTo-Json|Out-File -LiteralPath $taskStatus -Encoding utf8
} catch {
    @{phase='failed';fixture_only=$true;success=$false;error=$_.Exception.Message}|ConvertTo-Json|Out-File -LiteralPath $taskStatus -Encoding utf8
    exit 1
}
