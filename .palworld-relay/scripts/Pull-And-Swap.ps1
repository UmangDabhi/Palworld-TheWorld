$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
Start-RelayLog 'pull-and-swap'

try {
    Write-Step 'Preflight safety checks'
    Assert-PalworldStopped
    Assert-ValidWorld
    Ensure-GitRepo
    $localPlayer = Ensure-LocalPlayer

    if (Test-Path -LiteralPath (Join-Path $script:WorldRoot '.git') -PathType Container) {
        Assert-CleanForPull
        $remotes = @(& git -C $script:WorldRoot remote)
        if ($remotes -contains 'origin') {
            Ensure-Origin
            Write-Step 'Pulling latest world from GitHub'
            Invoke-Git pull --ff-only origin main
        }
    }

    Write-Step 'Checking character ownership'
    $state = Get-State
    $players = Get-Players
    if ($state.currentHost -eq $localPlayer) {
        Write-Host "Ready. This world is already prepared for $localPlayer as host." -ForegroundColor Green
        return
    }

    $oldHost = [string]$state.currentHost
    $oldClientGuid = [string]$players.$oldHost.clientGuid
    $incomingGuid = [string]$players.$localPlayer.clientGuid
    if ([string]::IsNullOrWhiteSpace($incomingGuid)) {
        throw "$localPlayer has no client GUID yet. That player must join once as a client before they can become host."
    }

    if ([string]::IsNullOrWhiteSpace($oldClientGuid)) {
        $known = @($script:HostGuid)
        foreach ($property in $players.PSObject.Properties) {
            if ($property.Value.clientGuid) { $known += [string]$property.Value.clientGuid }
        }
        $unknown = @(Get-PlayerSaveGuids | Where-Object { $_ -notin $known })
        if ($unknown.Count -ne 1) {
            Write-Host ''
            Write-Host 'ONE-TIME BOOTSTRAP NEEDED' -ForegroundColor Yellow
            Write-Host "We can make $localPlayer host, but first Palworld must create $oldHost's normal client GUID."
            Write-Host ''
            Write-Host "1. Open this world on this PC. It may temporarily show $oldHost as host."
            Write-Host "2. Share the join code with $oldHost."
            Write-Host "3. $oldHost joins once and creates/enters the temporary client slot."
            Write-Host '4. Both players close Palworld completely.'
            Write-Host '5. Run 1-PULL-AND-SWAP.bat again. Then the real characters will rotate correctly.'
            exit 2
        }
        $players.$oldHost.clientGuid = $unknown[0]
        $oldClientGuid = $unknown[0]
        Save-Json $script:PlayersPath $players
        Invoke-Git add -- .palworld-relay/players.json
        Invoke-Git commit -m "config: record $oldHost client GUID"
        $remotes = @(& git -C $script:WorldRoot remote)
        if ($remotes -contains 'origin') {
            Invoke-Git push origin main
        }
    }

    Write-Step "Swapping host character from $oldHost to $localPlayer"
    New-SafetyBackup 'before-swap' | Out-Null
    Invoke-CharacterSwap $oldClientGuid $incomingGuid
    $state.currentHost = $localPlayer
    Save-State $state

    Write-Host "Ready. $localPlayer is now the host character for this PC." -ForegroundColor Green
    Write-Host 'Open Palworld normally and load the world.' -ForegroundColor Green
} catch {
    Write-Error $_
    exit 1
} finally {
    Stop-RelayLog
}
