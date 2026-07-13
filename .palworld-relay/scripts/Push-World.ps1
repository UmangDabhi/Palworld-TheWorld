$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
Start-RelayLog 'push-world'

try {
    Write-Step 'Preflight safety checks'
    Assert-PalworldStopped
    Assert-ValidWorld
    Ensure-GitRepo
    Ensure-Origin
    $localPlayer = Ensure-LocalPlayer

    Write-Step "Preparing world for push as $localPlayer"
    $state = Get-State
    $state.currentHost = $localPlayer
    Save-State $state

    New-SafetyBackup 'before-push' | Out-Null
    Write-Step 'Committing and pushing world to GitHub'
    Invoke-Git add -- .

    $changes = (& git -C $script:WorldRoot status --porcelain)
    if (-not $changes) {
        Write-Host 'Nothing new to push.' -ForegroundColor Green
        return
    }

    Invoke-Git commit -m "save: Palworld session from $localPlayer"
    Invoke-Git push -u origin main
    Write-Host 'World pushed. The other player can run 1-PULL-AND-SWAP.bat now.' -ForegroundColor Green
} catch {
    Write-Error $_
    exit 1
} finally {
    Stop-RelayLog
}
