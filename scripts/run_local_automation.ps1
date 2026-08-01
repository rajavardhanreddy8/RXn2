$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDirectory = Join-Path $projectRoot "data\automation\logs"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$log = Join-Path $logDirectory ((Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
Set-Location $projectRoot
& python -B scripts\local_automation.py run *>&1 | Tee-Object -FilePath $log
exit $LASTEXITCODE
