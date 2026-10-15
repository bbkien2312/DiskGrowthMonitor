param([switch]$Offline,[switch]$SkipAppBuild,[string]$OutputDirectory='dist')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $taskRoot
$env:TEMP=Join-Path $taskRoot 'data\tmp'
$env:TMP=$env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
if(!$SkipAppBuild){& (Join-Path $taskRoot 'build-app.ps1') -Offline:$Offline}
$taskCompilerRoot=Join-Path $taskRoot '.runtime\packaging'
$taskCompiler=Join-Path $taskCompilerRoot 'inno-6.7.3\ISCC.exe'
if(!(Test-Path -LiteralPath $taskCompiler)){
    if($Offline){throw 'Portable Inno Setup compiler missing. Run once without -Offline.'}
    New-Item -ItemType Directory -Path $taskCompilerRoot -Force | Out-Null
    $taskSetup=Join-Path $taskCompilerRoot 'innosetup-6.7.3.exe'
    Invoke-WebRequest -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $taskSetup
    if((Get-FileHash -LiteralPath $taskSetup -Algorithm SHA256).Hash -ne '9C73C3BAE7ED48D44112A0F48E66742C00090BDB5BEF71D9D3C056C66E97B732'){throw 'Compiler checksum mismatch'}
    $taskCompilerDir=Join-Path $taskCompilerRoot 'inno-6.7.3'
    $taskSetupProcess=Start-Process -FilePath $taskSetup -ArgumentList @('/PORTABLE=1','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS','/CURRENTUSER',('/DIR="'+$taskCompilerDir+'"')) -WindowStyle Hidden -Wait -PassThru
    if($taskSetupProcess.ExitCode -ne 0){throw 'Portable compiler setup failed'}
}
$taskVersion=& (Join-Path $taskRoot '.venv\Scripts\python.exe') -c 'from dgm import __version__; print(__version__)'
$taskOutput=if([System.IO.Path]::IsPathRooted($OutputDirectory)){[System.IO.Path]::GetFullPath($OutputDirectory)}else{[System.IO.Path]::GetFullPath((Join-Path $taskRoot $OutputDirectory))}
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
& $taskCompiler (('/DAppVersion=')+$taskVersion) ('/DInstallerOutputDir='+$taskOutput) (Join-Path $taskRoot 'packaging\installer.iss')
if($LASTEXITCODE -ne 0){throw 'Installer build failed'}
& (Join-Path $taskRoot '.venv\Scripts\python.exe') (Join-Path $taskRoot 'packaging\checksum.py') $taskOutput
if($LASTEXITCODE -ne 0){throw 'Release checksum generation failed'}
