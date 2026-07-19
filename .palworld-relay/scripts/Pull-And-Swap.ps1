$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
Start-RelayLog 'pull-and-swap'

$prePullBackup = $null
$stage = $null
$localDataHash = $null
try {
    Write-Step 'Preflight safety checks'
    Assert-PalworldStopped
    Assert-ValidWorld
    Ensure-GitRepo
    Ensure-Origin
    $localPlayer = Ensure-LocalPlayer
    Protect-LocalData

    $prePullBackup = New-SafetyBackup 'before-pull'
    $localDataHash = (Get-FileHash -LiteralPath (Join-Path $prePullBackup 'LocalData.sav') -Algorithm SHA256).Hash
    Write-Step 'Checking GitHub for the latest world'
    try {
        Invoke-Git fetch --prune origin main
    } catch {
        throw "Could not fetch the latest world from GitHub. The live saves were not changed. Check the internet connection and GitHub access, then try again.`n$($_.Exception.Message)"
    }

    $delta = Get-BranchDelta
    if ($delta.LocalOnly -gt 0 -and $delta.RemoteOnly -gt 0) {
        throw "Local and GitHub histories have diverged ($($delta.LocalOnly) local commit(s), $($delta.RemoteOnly) GitHub commit(s)). A force pull would destroy someone's progress, so the relay stopped. Push/resolve the local commits first; backup: $prePullBackup"
    }
    if ($delta.LocalOnly -gt 0) {
        throw "This PC has $($delta.LocalOnly) local commit(s) that GitHub does not have. This commonly means an earlier push failed. Do not pull over it. Run 2-PUSH-WORLD.bat (it will push the existing commit) or run: git push -u origin main"
    }

    $changes = @(Get-GitStatusLines)
    if ($changes.Count -gt 0) {
        Write-Step 'Local changes would block the pull'
        Write-Host 'These local files contain changes that are not on GitHub:' -ForegroundColor Yellow
        $changes | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
        Write-Host ''
        Write-Host 'Before continuing, ask the other player: Did you host/play, and did you push the progress we should keep?' -ForegroundColor Yellow
        Write-Host "If you continue, this PC's current files stay in backup $prePullBackup and in a named Git stash. GitHub becomes the active world; nothing is force-reset." -ForegroundColor Yellow
        if (-not (Read-YesNo 'Continue with the GitHub world and keep these local changes in backup/stash?')) {
            throw "Pull cancelled by user. No GitHub world was installed. Local backup: $prePullBackup"
        }
        New-RelayStash
    }

    Write-Step 'Building and validating the GitHub world in isolation'
    $stage = New-RemoteSnapshot (Join-Path $prePullBackup 'LocalData.sav')
    $stageStatePath = Join-Path $stage '.palworld-relay\state.json'
    $candidateState = Get-Json $stageStatePath
    $remoteHost = [string]$candidateState.currentHost
    if ($remoteHost -notin @('Shine', 'Hazeki')) {
        throw "GitHub state.json has an invalid currentHost: $remoteHost"
    }
    Invoke-WorldValidation $remoteHost $stage

    if ($remoteHost -ne $localPlayer) {
        Write-Step "Swapping the staged world from $remoteHost to $localPlayer"
        Invoke-CharacterSwap $remoteHost $localPlayer $stage
        $candidateState.currentHost = $localPlayer
        $candidateState.updatedUtc = [DateTime]::UtcNow.ToString('o')
        Invoke-WorldValidation $localPlayer $stage
    } else {
        Write-Host "GitHub is already prepared for $localPlayer as host." -ForegroundColor Green
    }

    $stagedLocalDataHash = (Get-FileHash -LiteralPath (Join-Path $stage 'LocalData.sav') -Algorithm SHA256).Hash
    if ($stagedLocalDataHash -ne $localDataHash) {
        throw "The staged swap changed LocalData.sav, which stores this PC's map/fog progress. The live world was not changed. Original map backup: $prePullBackup"
    }

    if ($delta.RemoteOnly -gt 0) {
        Write-Step "Fast-forwarding Git to $($delta.RemoteOnly) new GitHub commit(s)"
        try {
            Invoke-Git merge --ff-only origin/main
        } catch {
            throw "Git could not fast-forward to origin/main. No force pull was attempted. The validated candidate remains at $stage and the original world backup is $prePullBackup.`n$($_.Exception.Message)"
        }
    } else {
        Write-Host 'Git is already at the latest GitHub commit.' -ForegroundColor DarkGray
    }

    Write-Step "Installing the validated $localPlayer-host world"
    Install-WorldSnapshot $stage $candidateState 'install-validated-pull' | Out-Null
    Protect-LocalData
    $installedLocalData = Join-Path $script:WorldRoot 'LocalData.sav'
    $installedLocalDataHash = (Get-FileHash -LiteralPath $installedLocalData -Algorithm SHA256).Hash
    if ($installedLocalDataHash -ne $localDataHash) {
        Copy-Item -LiteralPath (Join-Path $prePullBackup 'LocalData.sav') -Destination $installedLocalData -Force
        throw "LocalData.sav changed during installation, so the relay restored this PC's original map/fog data from $prePullBackup. The world must be inspected before opening Palworld."
    }
    Invoke-WorldValidation $localPlayer $script:WorldRoot
    Move-StagingToBackup $stage 'validated-pull-candidate'
    $stage = $null

    Write-Host ''
    Write-Host "READY: $localPlayer is the host. Character, all six inventory/equipment containers, dynamic items, party, Palbox, dimensional Pal storage sidecars, Pal ownership/provenance, guild links, player-file layout, and this PC's map data all passed validation." -ForegroundColor Green
    Write-Host 'You may now open Palworld and load this world.' -ForegroundColor Green
    Write-Host 'Git status will be dirty after a host swap: the old client save becomes the host save and the old host becomes a client save. This is expected; after playing, close Palworld and run 2-PUSH-WORLD.bat.' -ForegroundColor Yellow
    if ($script:LastStashName) {
        Write-Host "The earlier local changes remain safely stored as: $script:LastStashName" -ForegroundColor Yellow
    }
} catch {
    Write-Host ''
    Write-Host 'PULL/SWAP STOPPED - DO NOT OPEN THIS WORLD UNTIL THE MESSAGE BELOW IS RESOLVED' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($prePullBackup) { Write-Host "Pre-pull restore path: $prePullBackup" -ForegroundColor Yellow }
    if ($script:LastStashName) { Write-Host "Git stash kept: $script:LastStashName" -ForegroundColor Yellow }
    if ($stage -and (Test-Path -LiteralPath $stage)) { Write-Host "Staged candidate kept for inspection: $stage" -ForegroundColor Yellow }
    exit 1
} finally {
    Stop-RelayLog
}
