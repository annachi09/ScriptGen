<#
One-time setup: registers a Windows Scheduled Task that runs
auto_git_commit.ps1 once a day at 6:00 PM.

Run this ONCE, yourself, in PowerShell (not as Administrator needed - this
registers a task for your own user account, which is what you want, since
git push needs to run as you with your cached GitHub credentials).

After running this once, you never need to run it again unless you want to
change the schedule or the task's own definition.
#>

$ScriptPath = Join-Path $PSScriptRoot "auto_git_commit.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -Daily -At 6:00PM

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable

Register-ScheduledTask -TaskName "AutoGitCommit" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Daily auto-commit+push for RJ's local dev projects (edited via auto_git_commit.ps1)"

Write-Host "Done. Task 'AutoGitCommit' registered - runs daily at 6:00 PM."
Write-Host "To test it right now instead of waiting: Start-ScheduledTask -TaskName 'AutoGitCommit'"
Write-Host "To check the log after it runs: Get-Content '$PSScriptRoot\auto_git_commit.log' -Tail 20"
