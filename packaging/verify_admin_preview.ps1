param([Parameter(Mandatory=$true)][string]$Installer,
      [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ExpectedSha256,
      [switch]$OnlyInPlace)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$taskInstaller=[System.IO.Path]::GetFullPath($Installer)
$taskExpected=@((Join-Path $taskRoot 'dist\release-v1.2.0\installer.exe'),(Join-Path $taskRoot 'dist\preview-chung-rieng\installer.exe'),(Join-Path $taskRoot 'dist\release-v1.3.0\installer.exe'),(Join-Path $taskRoot 'dist\release-v1.3.1\installer.exe'),(Join-Path $taskRoot 'dist\release-v1.4.0\installer.exe'))
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
function Invoke-OwnedQa([string]$Script,[string]$LogName,[string[]]$Arguments) {
    # Windows PowerShell 5 treats native stderr as a terminating error under
    # Stop. Capture both streams directly so a Python traceback is retained.
    $taskOut=Join-Path $taskOutput ($LogName+'.log')
    $taskErr=Join-Path $taskOutput ($LogName+'.stderr.log')
    $taskQuoted=(@($Script)+$Arguments | ForEach-Object {'"'+$_+'"'}) -join ' '
    $taskRun=Start-Process -FilePath $taskPython -ArgumentList $taskQuoted -WorkingDirectory $taskRoot -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $taskOut -RedirectStandardError $taskErr
    if($taskRun.ExitCode -ne 0){
        $taskDetail=(Get-Content -LiteralPath $taskErr -Tail 35) -join "`n"
        throw ($LogName+' failed (exit '+$taskRun.ExitCode+'): '+$taskDetail)
    }
}
try {
    if(-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator approval required.'}
    if($OnlyInPlace){
        $taskFresh=Get-Content -LiteralPath (Join-Path $taskOutput 'release-test-results.json') -Raw | ConvertFrom-Json
        if($taskFresh.installer_sha256 -ne $ExpectedSha256 -or $taskFresh.fixture_only -ne $true){throw 'Fresh install must already pass for this exact installer.'}
    }else{
        @{phase='fresh-install';fixture_only=$true}|ConvertTo-Json|Out-File -LiteralPath $taskStatus -Encoding utf8
        Invoke-OwnedQa (Join-Path $taskRoot 'packaging\smoke_release.py') 'fresh-install-smoke' @($taskInstaller)
    }
    @{phase='inplace-update';fixture_only=$true}|ConvertTo-Json|Out-File -LiteralPath $taskStatus -Encoding utf8
    Invoke-OwnedQa (Join-Path $taskRoot 'packaging\smoke_inplace_update.py') 'inplace-update-smoke' @($taskInstaller,(Join-Path $taskRoot 'dist\installer.exe'),(Join-Path $taskRoot 'dist\release-v1.3.1\installer.exe'))
    @{phase='complete';fixture_only=$true;success=$true}|ConvertTo-Json|Out-File -LiteralPath $taskStatus -Encoding utf8
} catch {
    @{phase='failed';fixture_only=$true;success=$false;error=$_.Exception.Message}|ConvertTo-Json|Out-File -LiteralPath $taskStatus -Encoding utf8
    exit 1
}
