<#
Auto git commit + push for RJ's local dev projects.

Fired daily by a Windows Scheduled Task (see setup_auto_git_task.ps1 in this
same folder for the one-time registration commands). For each project path
in $Projects below:
  - skips if the folder doesn't exist
  - skips if it isn't a git repo yet (no .git folder) - that project needs
    the one-time "git init + create GitHub repo + first push" setup done
    by hand first (see ScriptGen's README, "Version control (git)" section,
    for that flow - it's the same for every project)
  - skips if there's nothing to commit (git status --porcelain is empty)
  - otherwise: git add -A, commit with a timestamped message, git push

Auth: relies on Git Credential Manager already having cached your GitHub
login from that first manual push. If a project's push fails with an auth
prompt, run "git push" by hand once from that folder to re-cache it - this
script never handles credentials itself.

Edit $Projects to add/remove projects.
#>

$Projects = @(
    "C:\MISC_RMA\ClaudeDev\ScriptGen\ScriptGen"
    # "C:\MISC_RMA\ClaudeDev\sqlvault-v2.2-standalone\sqlvault-app"   # confirm this path - see chat
    # Add the rest here once you confirm their local folder paths:
    # "C:\MISC_RMA\ClaudeDev\<ewa-bulk-checker-folder>",
    # "C:\MISC_RMA\ClaudeDev\<cost-billing-tracker-folder>",
    # "C:\MISC_RMA\ClaudeDev\<issue-tracker-folder>",
    # "C:\MISC_RMA\ClaudeDev\<pocketcore-folder>",
    # "C:\MISC_RMA\ClaudeDev\<sql-sync-tool-folder>"
)

$LogFile = Join-Path $PSScriptRoot "auto_git_commit.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

foreach ($proj in $Projects) {
    if (-not (Test-Path $proj)) {
        Log "SKIP  $proj  (folder not found)"
        continue
    }
    if (-not (Test-Path (Join-Path $proj ".git"))) {
        Log "SKIP  $proj  (not a git repo yet - run one-time git init/push setup first)"
        continue
    }

    Push-Location $proj
    try {
        $status = git status --porcelain
        if ([string]::IsNullOrWhiteSpace($status)) {
            Log "OK    $proj  (nothing to commit)"
        } else {
            git add -A
            $msg = "Auto-commit: $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
            git commit -m $msg | Out-Null
            $pushOutput = git push 2>&1
            Log "PUSH  $proj  -> $msg"
            Log "      $pushOutput"
        }
    } catch {
        Log "ERROR $proj  -> $_"
    } finally {
        Pop-Location
    }
}
