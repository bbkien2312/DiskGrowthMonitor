param([switch]$Apply,[string]$ArchiveName='result-logs-v2.1.0')
$ErrorActionPreference='Stop'
$taskProject=Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$taskArtifacts=[IO.Path]::GetFullPath((Join-Path $taskProject 'artifacts'))
if($ArchiveName -notmatch '^result-logs-v[0-9]+\.[0-9]+\.[0-9]+$'){throw 'Invalid result archive name'}
$taskArchive=Join-Path $taskArtifacts $ArchiveName

function Assert-OwnedArtifact([string]$Path) {
    $checked=[IO.Path]::GetFullPath($Path)
    if(-not $checked.StartsWith($taskArtifacts+'\',[StringComparison]::OrdinalIgnoreCase)){throw "Outside artifacts: $checked"}
    $ancestor=$checked
    while($ancestor -and $ancestor -ne [IO.Path]::GetPathRoot($ancestor)){
        if(Test-Path -LiteralPath $ancestor){
            if((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Reparse point is not allowed: $ancestor"}
        }
        $ancestor=Split-Path -Parent $ancestor
    }
    return $checked
}

function Get-OwnedFiles([string]$Directory) {
    $pending=[Collections.Generic.Stack[string]]::new();$pending.Push((Assert-OwnedArtifact $Directory))
    while($pending.Count){
        $current=$pending.Pop()
        foreach($item in Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop){
            if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Do not follow/delete a reparse subtree: $($item.FullName)"}
            if($item.PSIsContainer){$pending.Push($item.FullName)}else{$item}
        }
    }
}

New-Item -ItemType Directory -Path (Assert-OwnedArtifact $taskArchive) -Force | Out-Null
$taskReport=[ordered]@{project=$taskProject;scope=$taskArtifacts;apply=[bool]$Apply;utc=[DateTime]::UtcNow.ToString('o');
    policy='Keep small QA logs/results/docs, remove regenerable fixtures, screenshots, snapshots, caches and research checkouts only in artifacts. Root logs/results are untouched.';
    targets=@();preserved=@();removed_file_bytes=0L;archived_file_bytes=0L;estimated_reclaimed_file_bytes=0L}
$taskReportPath=Join-Path $taskArchive $(if($Apply){'cleanup-result.json'}else{'cleanup-plan.json'})
if(Test-Path -LiteralPath $taskReportPath){
    $taskReportPath=Join-Path $taskArchive (([IO.Path]::GetFileNameWithoutExtension($taskReportPath))+'-'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')+'.json')
}
function Save-Report { [IO.File]::WriteAllText($taskReportPath,($taskReport | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false)) }
try {
    $targets=@(Get-ChildItem -LiteralPath $taskArtifacts -Directory -Force | Where-Object {$_.Name -ne $ArchiveName -and $_.Name -notlike 'result-logs-v*'})
    foreach($target in $targets){
        $targetPath=Assert-OwnedArtifact $target.FullName
        $files=@(Get-OwnedFiles $targetPath)
        $size=[long](($files | Measure-Object Length -Sum).Sum)
        $record=[ordered]@{name=$target.Name;bytes=$size;files=$files.Count;state='planned'}
        $taskReport.targets+=,$record
        foreach($file in $files){
            $relative=$file.FullName.Substring($targetPath.Length+1)
            $depth=($relative -split '[\\/]').Count
            $keep=$depth -le 2 -and $file.Length -le 4MB -and (
                $file.Extension -in @('.log','.xml','.md','.txt') -or
                ($file.Extension -eq '.json' -and $file.BaseName -match '(result|report|audit|summary|status|proof|benchmark)'))
            if(-not $keep){continue}
            $destination=Assert-OwnedArtifact (Join-Path (Join-Path $taskArchive $target.Name) $relative)
            $sourceHash=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            if($Apply){
                New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
                if(Test-Path -LiteralPath $destination){
                    if((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $sourceHash){throw "Archived result differs: $destination"}
                }else{Copy-Item -LiteralPath $file.FullName -Destination $destination -ErrorAction Stop}
                if((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $sourceHash){throw "Copy verification failed: $destination"}
            }
            $taskReport.preserved+=@{source=$file.FullName;archive=$destination;bytes=$file.Length;sha256=$sourceHash}
            $taskReport.archived_file_bytes+=$file.Length
        }
        Save-Report
        if($Apply){
            # Re-resolve exact absolute targets and check for links/changes
            # immediately before recursive deletion. Never delete artifacts itself.
            $checked=Assert-OwnedArtifact $targetPath
            $again=@(Get-OwnedFiles $checked)
            if($again.Count -ne $files.Count -or [long](($again | Measure-Object Length -Sum).Sum) -ne $size){throw "Target changed during cleanup: $checked"}
            Remove-Item -LiteralPath $checked -Recurse -Force -ErrorAction Stop
            $record.state='removed';$taskReport.removed_file_bytes+=$size
        }
        Save-Report
    }
    $taskReport.estimated_reclaimed_file_bytes=$taskReport.removed_file_bytes-$taskReport.archived_file_bytes
    $taskReport.success=$true;Save-Report
} catch {
    $taskReport.success=$false;$taskReport.error=$_.Exception.Message;Save-Report;throw
}
[pscustomobject]$taskReport | Select-Object success,apply,removed_file_bytes,archived_file_bytes,estimated_reclaimed_file_bytes,
    @{Name='directories';Expression={$_.targets.Count}},@{Name='preserved_results';Expression={$_.preserved.Count}} | ConvertTo-Json
