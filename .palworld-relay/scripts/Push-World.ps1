$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
Start-RelayLog 'push-world'

$backup = $null
try {
    Write-Step 'Preflight safety checks'
    Assert-PalworldStopped
    Assert-ValidWorld
    Ensure-GitRepo
    Ensure-Origin
    $localPlayer = Ensure-LocalPlayer
    Protect-LocalData

    $state = Get-State
    if ([string]$state.currentHost -ne $localPlayer) {
        throw "This PC is configured for $localPlayer, but state.json says the world is prepared for $($state.currentHost). Run 1-PULL-AND-SWAP.bat first. The relay will not relabel or push an unverified host world."
    }

    Write-Step "Validating the complete $localPlayer-host world before commit"
    Invoke-WorldValidation $localPlayer $script:WorldRoot
    $backup = New-SafetyBackup 'before-push'

    Write-Step 'Checking that GitHub has no newer world'
    try {
        Invoke-Git fetch --prune origin main
    } catch {
        throw "Could not contact GitHub, so no commit was created and no push was attempted. Backup: $backup`n$($_.Exception.Message)"
    }
    $delta = Get-BranchDelta
    if ($delta.RemoteOnly -gt 0) {
        throw "GitHub has $($delta.RemoteOnly) newer commit(s). Pushing now could overwrite the other player's progress. Run 1-PULL-AND-SWAP.bat and review its backup/stash confirmation first."
    }

    $alreadyStaged = @(Invoke-GitText diff --cached --name-only)
    if ($alreadyStaged.Count -gt 0) {
        throw "Git already has staged files from a manual operation. The relay stopped to avoid committing the wrong content:`n$($alreadyStaged -join "`n")"
    }

    Write-Step 'Staging only relay configuration and shared world saves'
    Invoke-Git add -- .gitignore 1-PULL-AND-SWAP.bat 2-PUSH-WORLD.bat README-PALWORLD-RELAY.txt
    Invoke-Git add -- .palworld-relay/players.json .palworld-relay/state.json .palworld-relay/scripts .palworld-relay/tools
    Invoke-Git add -- Level.sav LevelMeta.sav WorldOption.sav Players

    $changes = @(Invoke-GitText diff --cached --name-only)
    if ($changes.Count -gt 0) {
        Write-Host 'Files included in this commit:' -ForegroundColor DarkGray
        $changes | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        Invoke-Git commit -m "save: validated Palworld session from $localPlayer"
    } else {
        Write-Host 'No new shared save changes need a commit.' -ForegroundColor DarkGray
    }

    $afterCommit = Get-BranchDelta
    if ($afterCommit.LocalOnly -eq 0) {
        Write-Host 'Nothing new to push. GitHub already has this validated world.' -ForegroundColor Green
        return
    }

    Write-Step "Pushing $($afterCommit.LocalOnly) local commit(s) to GitHub"
    try {
        Invoke-Git push -u origin main
    } catch {
        throw "GitHub push failed, but the commit and backup are safe on this PC. Do not rerun the push BAT just to create another commit; run it again only after fixing access, or use: git push -u origin main`nIf GitHub says 'Permission denied', Umang must add the GitHub account as a collaborator with write access.`nBackup: $backup`n$($_.Exception.Message)"
    }

    Write-Host ''
    Write-Host 'PUSH COMPLETE: GitHub now has the validated world and the latest pull/push/swap scripts.' -ForegroundColor Green
    Write-Host 'The other player can close Palworld and run 1-PULL-AND-SWAP.bat.' -ForegroundColor Green
} catch {
    Write-Host ''
    Write-Host 'PUSH STOPPED' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($backup) { Write-Host "Local restore path: $backup" -ForegroundColor Yellow }
    exit 1
} finally {
    Stop-RelayLog
}
