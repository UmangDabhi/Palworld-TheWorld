$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:RelayRoot = Split-Path -Parent $PSScriptRoot
$script:WorldRoot = Split-Path -Parent $script:RelayRoot
$script:StatePath = Join-Path $script:RelayRoot 'state.json'
$script:PlayersPath = Join-Path $script:RelayRoot 'players.json'
$script:LocalPath = Join-Path $script:RelayRoot 'local.json'
$script:HostGuid = '00000000000000000000000000000001'
$script:ExpectedOrigin = 'https://github.com/UmangDabhi/Palworld-TheWorld.git'
$script:LogPath = $null

function Start-RelayLog([string]$Action) {
    $logDir = Join-Path $script:RelayRoot 'logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $script:LogPath = Join-Path $logDir ('{0}-{1}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $Action)
    Start-Transcript -Path $script:LogPath -Append | Out-Null
    Write-Host "Log file: $script:LogPath" -ForegroundColor DarkGray
    Write-Host "World folder: $script:WorldRoot" -ForegroundColor DarkGray
    Write-Host "GitHub repo: $script:ExpectedOrigin" -ForegroundColor DarkGray
}

function Stop-RelayLog {
    try {
        if ($script:LogPath) {
            Write-Host "Saved log: $script:LogPath" -ForegroundColor DarkGray
            Stop-Transcript | Out-Null
        }
    } catch {
        # Best-effort logging only; never hide the real script result.
    }
}

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git was not found. Install Git for Windows, then run this BAT again.'
    }
    & git -C $script:WorldRoot @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Git failed: git $($Arguments -join ' ')" }
}

function Assert-PalworldStopped {
    $running = Get-Process -Name 'Palworld-Win64-Shipping', 'Palworld' -ErrorAction SilentlyContinue
    if ($running) { throw 'Palworld is running. Close the game completely before pulling, pushing, or swapping.' }
}

function Assert-CleanForPull {
    if (-not (Test-Path -LiteralPath (Join-Path $script:WorldRoot '.git') -PathType Container)) { return }
    $status = (& git -C $script:WorldRoot status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to check Git status.' }
    if ($status) {
        throw "Local world has unpushed/uncommitted changes. Run 2-PUSH-WORLD.bat first, or make a manual backup before pulling.`n$status"
    }
}

function Get-Json([string]$Path) {
    Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Save-Json([string]$Path, $Value) {
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-State { Get-Json $script:StatePath }
function Get-Players { Get-Json $script:PlayersPath }

function Save-State($State) {
    $State.updatedUtc = [DateTime]::UtcNow.ToString('o')
    Save-Json $script:StatePath $State
}

function Assert-ValidWorld {
    foreach ($required in @('Level.sav', 'LevelMeta.sav', 'Players')) {
        if (-not (Test-Path -LiteralPath (Join-Path $script:WorldRoot $required))) {
            throw "This does not look like the Palworld world folder. Missing: $required"
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $script:WorldRoot "Players\$script:HostGuid.sav"))) {
        throw "Missing Players\$script:HostGuid.sav. This is not a normal local co-op host world."
    }
    $playerSaves = @(Get-PlayerSaveGuids)
    if ($playerSaves.Count -lt 2) {
        throw "Expected at least two player saves before using the relay. Found $($playerSaves.Count)."
    }
}

function Ensure-GitRepo {
    if (-not (Test-Path -LiteralPath (Join-Path $script:WorldRoot '.git') -PathType Container)) {
        Write-Step 'Initializing Git in this Palworld world folder'
        Invoke-Git init
        Invoke-Git branch -M main
    }
}

function Ensure-Origin {
    $remotes = @(& git -C $script:WorldRoot remote)
    if ($remotes -contains 'origin') {
        $origin = (& git -C $script:WorldRoot remote get-url origin)
        if ($origin.Trim() -ne $script:ExpectedOrigin) {
            throw "Git origin is not the expected Palworld-TheWorld repo.`nExpected: $script:ExpectedOrigin`nFound:    $($origin.Trim())"
        }
        return
    }

    Write-Step 'Connecting this world folder to GitHub'
    Invoke-Git remote add origin $script:ExpectedOrigin
}

function Ensure-LocalPlayer {
    if (Test-Path -LiteralPath $script:LocalPath -PathType Leaf) {
        $local = Get-Json $script:LocalPath
        if ($local.player -in @('Shine', 'Hazeki')) { return [string]$local.player }
    }

    Write-Host ''
    Write-Host 'Which character should THIS PC load as HOST?' -ForegroundColor Cyan
    Write-Host '1 = Shine  (Umang / current host character)'
    Write-Host '2 = Hazeki (friend character)'
    $choice = Read-Host 'Choose exactly 1 or 2'
    if ($choice -eq '1') {
        $player = 'Shine'
    } elseif ($choice -eq '2') {
        $player = 'Hazeki'
    } else {
        throw 'Invalid choice. Run the BAT again and choose 1 for Shine or 2 for Hazeki.'
    }
    Save-Json $script:LocalPath ([pscustomobject]@{ player = $player })
    Write-Host "Saved local player: $player" -ForegroundColor Green
    return $player
}

function Get-PlayerSaveGuids {
    $players = Join-Path $script:WorldRoot 'Players'
    if (-not (Test-Path -LiteralPath $players -PathType Container)) { return @() }
    return @(Get-ChildItem -LiteralPath $players -Filter '*.sav' -File | ForEach-Object { $_.BaseName.ToUpperInvariant() })
}

function New-SafetyBackup([string]$Label) {
    $destination = Join-Path $script:RelayRoot ('backups\{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $Label)
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Get-ChildItem -LiteralPath $script:WorldRoot -Filter '*.sav' -File -ErrorAction SilentlyContinue |
        Copy-Item -Destination $destination -Force
    Copy-Item -LiteralPath (Join-Path $script:WorldRoot 'Players') -Destination $destination -Recurse -Force
    Write-Host "Safety backup: $destination" -ForegroundColor DarkGray
    return $destination
}

function Invoke-CharacterSwap([string]$CurrentClientGuid, [string]$IncomingClientGuid) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $python) { throw 'Python was not found. Install Python 3, then run this BAT again.' }

    $toolRoot = Join-Path $script:RelayRoot 'tools'
    $env:PYTHONPATH = $toolRoot
    $tool = Join-Path $toolRoot 'swap_coop_host.py'
    & $python.Source $tool $script:WorldRoot $CurrentClientGuid $IncomingClientGuid
    if ($LASTEXITCODE -ne 0) {
        throw 'Character swap failed. Do not open the world; restore from .palworld-relay\backups if needed.'
    }
}

