param([Parameter(Mandatory=$true)][string]$Session,[switch]$Quiet)
$ErrorActionPreference='Stop'
$env:PYTHONUTF8='1'
$taskSession=[System.IO.Path]::GetFullPath($Session)
$taskInfo=Get-Content -LiteralPath $taskSession -Raw -Encoding UTF8 | ConvertFrom-Json
$taskRoot=[System.IO.Path]::GetFullPath($taskInfo.root)
$taskInstallRoot=$taskRoot
if($taskInfo.install_root){$taskInstallRoot=[System.IO.Path]::GetFullPath($taskInfo.install_root)}
$taskCli=Join-Path $taskInstallRoot 'dgm.exe'
$taskBootstrap=$taskCli
$taskPrefix=@()
if($taskInfo.bootstrap){$taskBootstrap=[string]$taskInfo.bootstrap.executable;$taskPrefix=@($taskInfo.bootstrap.arguments)}
$env:DGM_HOME=$taskRoot
$env:DGM_SOURCE='1'
$taskLog=Join-Path (Split-Path -Parent $taskSession) 'coordinator.log'
$taskSuccess=$false
$taskError=''
try {
    # Never terminate the GUI. PID plus creation time guards against PID reuse.
    $taskDeadline=[DateTime]::UtcNow.AddMinutes(2)
    while($true){
        $taskParent=Get-Process -Id $taskInfo.parent.pid -ErrorAction SilentlyContinue
        if(!$taskParent){break}
        $taskCreated=([DateTimeOffset]$taskParent.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()/1000.0
        if([Math]::Abs($taskCreated-$taskInfo.parent.created) -ge 0.02){break}
        if([DateTime]::UtcNow -gt $taskDeadline){throw 'App did not close; no installer was started.'}
        Start-Sleep -Milliseconds 250
    }
    & $taskBootstrap @taskPrefix --home $taskRoot _app-update-stage $taskSession 2>&1 | Out-File -LiteralPath $taskLog -Append -Encoding utf8
    if($LASTEXITCODE -ne 0){throw 'Safe update preparation failed. See coordinator.log.'}
    $taskExpected=[string]$taskInfo.release.sha256
    # Get-FileHash may not autoload in Windows PowerShell launched from pwsh.
    # Hash installer bytes directly with the built-in .NET cryptography API.
    $taskHash=[System.Security.Cryptography.SHA256]::Create()
    $taskStream=[System.IO.File]::Open($taskInfo.installer,[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::Read)
    try {$taskActual=[System.BitConverter]::ToString($taskHash.ComputeHash($taskStream)).Replace('-','').ToLowerInvariant()}
    finally {$taskHash.Dispose();$taskStream.Dispose()}
    if($taskActual -ne $taskExpected){throw 'Installer SHA-256 mismatch.'}
    $taskArguments=@('/SILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/DIR="'+$taskInstallRoot+'"'),('/LOG="'+(Join-Path (Split-Path -Parent $taskSession) 'installer.log')+'"'))
    if($Quiet){$taskArguments[0]='/VERYSILENT'}
    if($taskInfo.portable){$taskArguments+='/PORTABLE=1'}
    $taskWindow='Normal';if($Quiet){$taskWindow='Hidden'}
    $taskInstall=Start-Process -FilePath $taskInfo.installer -ArgumentList $taskArguments -Wait -PassThru -WindowStyle $taskWindow
    if($taskInstall.ExitCode -notin @(0,3010)){throw ('Installer failed/cancelled: '+$taskInstall.ExitCode)}
    if($taskInstallRoot -ne $taskRoot){
        $taskLink=Join-Path $taskInstallRoot 'data-home.json'
        if((Test-Path -LiteralPath $taskLink) -and ((Get-Item -LiteralPath $taskLink).Attributes -band [System.IO.FileAttributes]::ReparsePoint)){throw 'Data-home link must not be a reparse point.'}
        $taskLinkJson=@{home=$taskRoot}|ConvertTo-Json -Compress
        [System.IO.File]::WriteAllText($taskLink,$taskLinkJson,(New-Object System.Text.UTF8Encoding($false)))
    }
    $taskSuccess=$true
} catch {
    $taskError=$_.Exception.Message
    $taskError | Out-File -LiteralPath $taskLog -Append -Encoding utf8
} finally {
    try {
        $taskFinish=@('--home',$taskRoot,'_app-update-finish',$taskSession)
        if($taskSuccess){$taskFinish+='--success'}
        if($taskSuccess){& $taskCli @taskFinish 2>&1 | Out-File -LiteralPath $taskLog -Append -Encoding utf8}
        else {& $taskBootstrap @taskPrefix @taskFinish 2>&1 | Out-File -LiteralPath $taskLog -Append -Encoding utf8}
        if($LASTEXITCODE -ne 0){$taskError+=' Runner recovery needs attention; see session.json.'}
    } catch {$taskError+=' Recovery failed: '+$_.Exception.Message}
    if(!$Quiet){
        Add-Type -AssemblyName PresentationFramework
        if($taskError){[System.Windows.MessageBox]::Show(($taskError+"`n"+$taskLog),'Disk Growth Monitor update') | Out-Null}
        else {('Installed and verified version '+$taskInfo.release.version+'; preserved history/config and restored runners.') | Out-File -LiteralPath $taskLog -Append -Encoding utf8}
    }
}
if($taskError){exit 1}
