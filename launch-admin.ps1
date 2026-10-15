$taskRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
$taskExe=Join-Path $taskRoot '.venv\Scripts\pythonw.exe'
if(!(Test-Path -LiteralPath $taskExe)){throw 'Run install-local.cmd first.'}
$taskArgs='-m dgm --home "'+$taskRoot+'" gui'
Start-Process -FilePath $taskExe -ArgumentList $taskArgs -WorkingDirectory $taskRoot -Verb RunAs -WindowStyle Normal

