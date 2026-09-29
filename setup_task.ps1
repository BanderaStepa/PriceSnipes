# Creates (or replaces) the Windows scheduled task "White Build price check".
# Run from the repo folder:  powershell -ExecutionPolicy Bypass -File setup_task.ps1
#
# - runs run_pricecheck.bat every 4 hours, starting at the next full hour
# - "Run only when user is logged on"
# - start as soon as possible after a missed start, wake the computer to run,
#   stop the task if it runs longer than 30 minutes

$ErrorActionPreference = "Stop"

$TaskName = "White Build price check"
$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bat      = Join-Path $Here "run_pricecheck.bat"

if (-not (Test-Path $Bat)) { throw "run_pricecheck.bat not found in $Here" }
if (-not (Test-Path (Join-Path $Here ".venv\Scripts\python.exe"))) {
    Write-Warning "No .venv found in $Here - do the setup steps in README.md first."
}

$now   = Get-Date
$start = $now.Date.AddHours($now.Hour + 1)   # next full hour

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$Bat`"" -WorkingDirectory $Here
# No -RepetitionDuration: on Windows 10/11 this repeats indefinitely.
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 4)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Checks the White Build prices and emails the report (every 4 h)." -Force | Out-Null

Write-Host "Scheduled task '$TaskName' created."
Write-Host "First run: $start, then every 4 hours."
Write-Host "Check it in Task Scheduler (taskschd.msc) > Task Scheduler Library > $TaskName."
Write-Host "Delete it with:  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
