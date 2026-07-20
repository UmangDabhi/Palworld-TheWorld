$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
Start-RelayLog 'diagnose-world'

try {
    Write-Step 'Read-only safety checks'
    Assert-PalworldStopped
    Assert-ValidWorld
    Ensure-GitRepo
    Ensure-Origin

    $state = Get-State
    $players = Get-Players
    $currentHost = [string]$state.currentHost
    if ($currentHost -notin @('Shine', 'Hazeki')) {
        throw "state.json has an invalid currentHost: $currentHost"
    }
    $mapping = Get-HostMapping $currentHost

    Write-Step 'Machine and relay identity'
    $localPlayer = '<not configured>'
    if (Test-Path -LiteralPath $script:LocalPath -PathType Leaf) {
        $localPlayer = [string](Get-Json $script:LocalPath).player
    }
    $activeSteamUser = '<unavailable>'
    try {
        $activeUser = (Get-ItemProperty -LiteralPath 'HKCU:\Software\Valve\Steam\ActiveProcess' -Name ActiveUser).ActiveUser
        $activeSteamUser = '0x{0:X8}' -f [uint32]$activeUser
    } catch {
        # Steam may be fully closed while diagnostics are collected.
    }
    Write-Host "Local PC player: $localPlayer"
    Write-Host "Prepared host: $currentHost"
    Write-Host "Active Steam user: $activeSteamUser"
    if ($localPlayer -in @('Shine', 'Hazeki')) {
        $expectedSteamUser = [string]$players.$localPlayer.steamActiveUser
        Write-Host "Expected Steam user for ${localPlayer}: $expectedSteamUser"
        if (
            $activeSteamUser -ne '<unavailable>' -and
            $expectedSteamUser -and
            $activeSteamUser -ne $expectedSteamUser
        ) {
            Write-Host "STEAM_IDENTITY_WARNING: Active user $activeSteamUser does not match $localPlayer ($expectedSteamUser)." -ForegroundColor Red
        }
    }
    Write-Host "Shine client GUID: $($players.Shine.clientGuid)"
    Write-Host "Hazeki client GUID: $($players.Hazeki.clientGuid)"

    Write-Step 'Git state'
    Write-Host "Branch: $((Invoke-GitText branch --show-current) -join '')"
    Write-Host "HEAD: $((Invoke-GitText rev-parse HEAD) -join '')"
    try {
        Write-Host "origin/main: $((Invoke-GitText rev-parse origin/main) -join '')"
    } catch {
        Write-Host 'origin/main: unavailable locally' -ForegroundColor Yellow
    }
    $status = @(Get-GitStatusLines)
    if ($status.Count -eq 0) {
        Write-Host 'Working tree: clean'
    } else {
        Write-Host 'Working tree changes:' -ForegroundColor Yellow
        $status | ForEach-Object { Write-Host "  $_" }
    }
    $stashes = @(Invoke-GitText stash list --format='%gd %s')
    if ($stashes.Count -eq 0) {
        Write-Host 'Git stashes: none'
    } else {
        Write-Host 'Git stashes:'
        $stashes | Select-Object -First 10 | ForEach-Object { Write-Host "  $_" }
    }

    Write-Step 'Save files and hashes'
    foreach ($name in @('Level.sav', 'LevelMeta.sav', 'WorldOption.sav', 'LocalData.sav')) {
        $path = Join-Path $script:WorldRoot $name
        $file = Get-Item -LiteralPath $path
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        Write-Host "$name bytes=$($file.Length) modified=$($file.LastWriteTime.ToString('s')) sha256=$hash"
    }
    Get-ChildItem -LiteralPath (Join-Path $script:WorldRoot 'Players') -Filter '*.sav' -File |
        Sort-Object Name |
        ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
            Write-Host "Players\$($_.Name) bytes=$($_.Length) modified=$($_.LastWriteTime.ToString('s')) sha256=$hash"
        }

    Write-Step 'Parsed player identity and ownership'
    $python = Get-PythonCommand
    $toolRoot = Join-Path $script:RelayRoot 'tools'
    $tool = Join-Path $toolRoot 'diagnose_world.py'
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        throw "Missing diagnostic tool: $tool"
    }
    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $toolRoot
        & $python $tool $script:WorldRoot $mapping.HostClientGuid $mapping.ClientGuid
        if ($LASTEXITCODE -ne 0) {
            throw "Python save diagnosis failed with exit code $LASTEXITCODE"
        }
    } finally {
        $env:PYTHONPATH = $oldPythonPath
    }

    Write-Step 'Recent recovery points'
    $backupRoot = Join-Path $script:RelayRoot 'backups'
    Get-ChildItem -LiteralPath $backupRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 10 |
        ForEach-Object { Write-Host "BACKUP $($_.LastWriteTime.ToString('s')) $($_.FullName)" }
    $logRoot = Join-Path $script:RelayRoot 'logs'
    Get-ChildItem -LiteralPath $logRoot -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -ne $script:LogPath } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 10 |
        ForEach-Object { Write-Host "LOG $($_.LastWriteTime.ToString('s')) $($_.FullName)" }

    Write-Host ''
    Write-Host 'DIAGNOSIS_COMPLETE: No save file was changed.' -ForegroundColor Green
} catch {
    Write-Host ''
    Write-Host 'DIAGNOSIS_STOPPED' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    Stop-RelayLog
}
