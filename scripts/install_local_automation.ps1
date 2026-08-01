param([switch]$Remove)

$ErrorActionPreference = "Stop"
$taskName = "RXN2 Local Automation"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed scheduled task: $taskName"
    exit 0
}

$runner = (Resolve-Path (Join-Path $PSScriptRoot "run_local_automation.ps1")).Path
$powerShell = "$PSHOME\powershell.exe"
$action = New-ScheduledTaskAction `
    -Execute $powerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -Daily -At "02:00"
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Imports verified Drive outputs, processes patent text, refreshes RXN2 coverage, and writes exception reports." `
    -Force | Out-Null
Write-Output "Installed scheduled task: $taskName (daily at 02:00, start when available)"
