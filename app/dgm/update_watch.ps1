param([Parameter(Mandatory=$true)][string]$Session)
$ErrorActionPreference='Stop'
$taskSession=[System.IO.Path]::GetFullPath($Session)
$taskInitial=Get-Content -LiteralPath $taskSession -Raw -Encoding UTF8 | ConvertFrom-Json
$taskDeadline=[DateTime]::UtcNow.AddMinutes(30)
$taskLog=Join-Path (Split-Path -Parent $taskSession) 'restart.log'
try {
    while([DateTime]::UtcNow -lt $taskDeadline){
        $taskState=Get-Content -LiteralPath $taskSession -Raw -Encoding UTF8 | ConvertFrom-Json
        if($taskState.phase -eq 'cancelled'){exit 0}
        if($taskState.phase -in @('complete','failed')){break}
        Start-Sleep -Milliseconds 500
    }
    if($taskState.phase -notin @('complete','failed')){throw 'Update observer timed out; installer success is not confirmed.'}
    $taskParent=Get-Process -Id $taskInitial.parent.pid -ErrorAction SilentlyContinue
    if($taskParent){
        $taskCreated=([DateTimeOffset]$taskParent.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()/1000.0
        if([Math]::Abs($taskCreated-$taskInitial.parent.created) -lt 0.02){exit 0}
    }
    $env:DGM_HOME=[string]$taskInitial.root
    $taskExe=[string]$taskInitial.reopen_command[0]
    $taskArguments=@($taskInitial.reopen_command | Select-Object -Skip 1)
    if($taskState.phase -eq 'complete'){
        $taskExe=Join-Path ([string]$taskState.install_root) 'DiskGrowthMonitor.exe'
        $taskArguments=@('--home',[string]$taskInitial.root,'gui')
    }
    # Quote arguments, not a shell command. This observer was started unelevated.
    $taskQuoted=@($taskArguments | ForEach-Object {'"'+([string]$_).Replace('"','\"')+'"'})
    Start-Process -FilePath $taskExe -ArgumentList $taskQuoted -WorkingDirectory ([string]$taskInitial.root) -WindowStyle Normal -Verb RunAs | Out-Null
    ('Reopened app; update phase='+$taskState.phase) | Out-File -LiteralPath $taskLog -Encoding utf8
} catch {
    $_.Exception.Message | Out-File -LiteralPath $taskLog -Encoding utf8
    exit 1
}
