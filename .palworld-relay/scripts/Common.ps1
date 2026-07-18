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
$script:LastBackupPath = $null
$script:LastStashName = $null

function Start-RelayLog([string]$Action) {
    $logDir = Join-Path $script:RelayRoot 'logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $script:LogPath = Join-Path $logDir ('{0}-{1}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'), $Action)
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
        # Logging must never hide the real relay result.
    }
}

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-GitAvailable {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git was not found. Install Git for Windows, then run this BAT again.'
    }
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    Assert-GitAvailable
    & git -C $script:WorldRoot @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "Git command failed (exit $code): git $($Arguments -join ' ')"
    }
}

function Invoke-GitText {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    Assert-GitAvailable
    $output = @(& git -C $script:WorldRoot @Arguments 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "Git command failed (exit $code): git $($Arguments -join ' ')`n$($output -join "`n")"
    }
    return $output
}

function Assert-PalworldStopped {
    $running = Get-Process -Name 'Palworld-Win64-Shipping', 'Palworld' -ErrorAction SilentlyContinue
    if ($running) {
        $processes = ($running | ForEach-Object { "$($_.ProcessName) (PID $($_.Id))" }) -join ', '
        throw "Palworld is still running: $processes. Close the game completely before pulling, pushing, swapping, or changing saves."
    }
}

function Get-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required relay file is missing: $Path"
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        throw "Relay JSON is invalid: $Path`n$($_.Exception.Message)"
    }
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
    foreach ($required in @('Level.sav', 'LevelMeta.sav', 'WorldOption.sav', 'LocalData.sav', 'Players')) {
        if (-not (Test-Path -LiteralPath (Join-Path $script:WorldRoot $required))) {
            throw "This is not the complete Palworld local co-op world. Missing: $required"
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $script:WorldRoot "Players\$script:HostGuid.sav"))) {
        throw "Missing Players\$script:HostGuid.sav. The relay only supports a local co-op host world, not a dedicated server."
    }
    if (@(Get-PlayerSaveGuids).Count -ne 2) {
        throw "Expected exactly two ordinary player saves for Shine and Hazeki. Found: $((Get-PlayerSaveGuids) -join ', '). Files ending in _dps.sav are valid dimensional Pal storage sidecars and are checked separately."
    }
}

function Ensure-GitRepo {
    if (-not (Test-Path -LiteralPath (Join-Path $script:WorldRoot '.git') -PathType Container)) {
        throw 'This world is not the configured Git relay yet. Clone the Palworld-TheWorld repo into the matching live world folder first.'
    }
}

function Ensure-Origin {
    $remotes = @(Invoke-GitText remote)
    if ($remotes -notcontains 'origin') {
        throw "Git remote 'origin' is missing. Expected: $script:ExpectedOrigin"
    }
    $origin = [string](Invoke-GitText remote get-url origin)
    if ($origin.Trim() -ne $script:ExpectedOrigin) {
        throw "Git origin is not the expected Palworld-TheWorld repo.`nExpected: $script:ExpectedOrigin`nFound:    $($origin.Trim())"
    }
}

function Ensure-LocalPlayer {
    if (Test-Path -LiteralPath $script:LocalPath -PathType Leaf) {
        $local = Get-Json $script:LocalPath
        if ($local.player -in @('Shine', 'Hazeki')) { return [string]$local.player }
    }

    Write-Host ''
    Write-Host 'Which character should THIS PC load as HOST?' -ForegroundColor Cyan
    Write-Host '1 = Shine  (Umang)'
    Write-Host '2 = Hazeki (Harsh)'
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

function Get-OtherPlayer([string]$Player) {
    if ($Player -eq 'Shine') { return 'Hazeki' }
    if ($Player -eq 'Hazeki') { return 'Shine' }
    throw "Unknown player name: $Player"
}

function Get-PlayerSaveGuids {
    $players = Join-Path $script:WorldRoot 'Players'
    if (-not (Test-Path -LiteralPath $players -PathType Container)) { return @() }
    return @(Get-ChildItem -LiteralPath $players -Filter '*.sav' -File |
        Where-Object { $_.BaseName -match '^[0-9A-Fa-f]{32}$' } |
        ForEach-Object { $_.BaseName.ToUpperInvariant() })
}

function Protect-LocalData {
    $tracked = @((Invoke-GitText ls-files -- LocalData.sav))
    if ($tracked.Count -gt 0) {
        Invoke-Git update-index --skip-worktree -- LocalData.sav
    }
}

function Get-GitStatusLines {
    return @(Invoke-GitText status --porcelain=v1 --untracked-files=all)
}

function Get-BranchDelta {
    $raw = ((Invoke-GitText rev-list --left-right --count 'HEAD...origin/main') -join '').Trim()
    $parts = @($raw -split '\s+')
    if ($parts.Count -ne 2) { throw "Could not understand Git branch state: $raw" }
    return [pscustomobject]@{ LocalOnly = [int]$parts[0]; RemoteOnly = [int]$parts[1] }
}

function Read-YesNo([string]$Prompt) {
    while ($true) {
        $answer = (Read-Host "$Prompt [y/n]").Trim().ToLowerInvariant()
        if ($answer -eq 'y') { return $true }
        if ($answer -eq 'n') { return $false }
        Write-Host 'Enter y to continue or n to stop.' -ForegroundColor Yellow
    }
}

function New-RelayStash {
    $script:LastStashName = 'palworld-relay-before-pull-{0}' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
    Invoke-Git stash push --include-untracked -m $script:LastStashName
    Write-Host "Local changes kept in Git stash: $script:LastStashName" -ForegroundColor Yellow
    Write-Host 'Inspect later: git stash list' -ForegroundColor DarkGray
    Write-Host 'Restore manually only after checking progress: git stash apply' -ForegroundColor DarkGray
}

function New-SafetyBackup([string]$Label) {
    $destination = Join-Path $script:RelayRoot ('backups\{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'), $Label)
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Get-ChildItem -LiteralPath $script:WorldRoot -Filter '*.sav' -File -ErrorAction SilentlyContinue |
        Copy-Item -Destination $destination -Force
    Copy-Item -LiteralPath (Join-Path $script:WorldRoot 'Players') -Destination $destination -Recurse -Force
    $relayState = Join-Path $destination 'relay-state'
    New-Item -ItemType Directory -Path $relayState -Force | Out-Null
    foreach ($path in @($script:StatePath, $script:PlayersPath, $script:LocalPath)) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Copy-Item -LiteralPath $path -Destination $relayState -Force
        }
    }

    $head = 'not-available'
    try { $head = ((Invoke-GitText rev-parse HEAD) -join '').Trim() } catch {}
    $files = @(Get-ChildItem -LiteralPath $destination -Recurse -File -Filter '*.sav' | ForEach-Object {
        [pscustomobject]@{
            path = $_.FullName.Substring($destination.Length + 1)
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    })
    Save-Json (Join-Path $destination 'manifest.json') ([pscustomobject]@{
        createdUtc = [DateTime]::UtcNow.ToString('o')
        label = $Label
        gitHead = $head
        files = $files
    })
    $script:LastBackupPath = $destination
    Write-Host "Safety backup: $destination" -ForegroundColor DarkGray
    return $destination
}

function New-RemoteSnapshot([string]$LocalDataSource) {
    $workRoot = Join-Path $script:RelayRoot 'work'
    $stage = Join-Path $workRoot ('{0}-remote-candidate' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    $archive = Join-Path $stage 'repo.zip'
    Invoke-Git archive --format=zip --output=$archive origin/main
    Expand-Archive -LiteralPath $archive -DestinationPath $stage -Force
    Remove-Item -LiteralPath $archive -Force
    if (Test-Path -LiteralPath $LocalDataSource -PathType Leaf) {
        Copy-Item -LiteralPath $LocalDataSource -Destination (Join-Path $stage 'LocalData.sav') -Force
    }
    return $stage
}

function Move-StagingToBackup([string]$Stage, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Stage -PathType Container)) { return }
    $destination = Join-Path $script:RelayRoot ('backups\{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'), $Label)
    Move-Item -LiteralPath $Stage -Destination $destination
    Write-Host "Validated staging copy kept at: $destination" -ForegroundColor DarkGray
}

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
    if (-not $python) { throw 'Python 3 was not found. Install Python 3, then run this BAT again.' }
    return $python.Source
}

function Invoke-SaveTool {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $python = Get-PythonCommand
    $toolRoot = Join-Path $script:RelayRoot 'tools'
    $tool = Join-Path $toolRoot 'swap_coop_host.py'
    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $toolRoot
        & $python $tool @Arguments
        $code = $LASTEXITCODE
    } finally {
        $env:PYTHONPATH = $oldPythonPath
    }
    if ($code -ne 0) {
        throw "Save validation/swap failed (exit $code). No live save should be opened until this is resolved."
    }
}

function Get-HostMapping([string]$HostPlayer) {
    $players = Get-Players
    $clientPlayer = Get-OtherPlayer $HostPlayer
    $hostClientGuid = [string]$players.$HostPlayer.clientGuid
    $clientGuid = [string]$players.$clientPlayer.clientGuid
    if ([string]::IsNullOrWhiteSpace($hostClientGuid) -or [string]::IsNullOrWhiteSpace($clientGuid)) {
        throw "players.json is missing a client GUID for $HostPlayer or $clientPlayer."
    }
    return [pscustomobject]@{
        HostPlayer = $HostPlayer
        ClientPlayer = $clientPlayer
        HostClientGuid = $hostClientGuid
        ClientGuid = $clientGuid
    }
}

function Invoke-WorldValidation([string]$HostPlayer, [string]$Root = $script:WorldRoot) {
    $mapping = Get-HostMapping $HostPlayer
    Invoke-SaveTool validate $Root $mapping.HostClientGuid $mapping.ClientGuid
}

function Invoke-CharacterSwap([string]$CurrentHost, [string]$IncomingHost, [string]$Root) {
    $current = Get-HostMapping $CurrentHost
    $incoming = Get-HostMapping $IncomingHost
    Invoke-SaveTool swap $Root $current.HostClientGuid $incoming.HostClientGuid
}

function Install-WorldSnapshot([string]$Source, $State, [string]$Label) {
    foreach ($required in @('Level.sav', 'LevelMeta.sav', 'WorldOption.sav', 'Players')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Source $required))) {
            throw "Validated staging copy is incomplete. Missing: $required"
        }
    }
    $rollback = New-SafetyBackup "before-$Label"
    Write-Host "Rollback path for this replacement: $rollback" -ForegroundColor Yellow

    $work = Join-Path $script:RelayRoot ('work\{0}-install' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $newPlayers = Join-Path $work 'Players-new'
    $oldPlayers = Join-Path $work 'Players-old'
    Copy-Item -LiteralPath (Join-Path $Source 'Players') -Destination $newPlayers -Recurse -Force
    $temps = @{}
    foreach ($name in @('Level.sav', 'LevelMeta.sav', 'WorldOption.sav')) {
        $temp = Join-Path $work $name
        Copy-Item -LiteralPath (Join-Path $Source $name) -Destination $temp -Force
        $temps[$name] = $temp
    }

    try {
        Move-Item -LiteralPath (Join-Path $script:WorldRoot 'Players') -Destination $oldPlayers
        Move-Item -LiteralPath $newPlayers -Destination (Join-Path $script:WorldRoot 'Players')
        foreach ($name in $temps.Keys) {
            Move-Item -LiteralPath $temps[$name] -Destination (Join-Path $script:WorldRoot $name) -Force
        }
        Save-State $State
        Move-Item -LiteralPath $oldPlayers -Destination (Join-Path $rollback 'Players-original')
    } catch {
        $failure = $_
        if (Test-Path -LiteralPath $oldPlayers -PathType Container) {
            $installedPlayers = Join-Path $script:WorldRoot 'Players'
            if (Test-Path -LiteralPath $installedPlayers -PathType Container) {
                Move-Item -LiteralPath $installedPlayers -Destination (Join-Path $rollback 'Players-failed-install')
            }
            Move-Item -LiteralPath $oldPlayers -Destination $installedPlayers
        }
        foreach ($name in @('Level.sav', 'LevelMeta.sav', 'WorldOption.sav')) {
            $backupFile = Join-Path $rollback $name
            if (Test-Path -LiteralPath $backupFile -PathType Leaf) {
                Copy-Item -LiteralPath $backupFile -Destination (Join-Path $script:WorldRoot $name) -Force
            }
        }
        $stateBackup = Join-Path $rollback 'relay-state\state.json'
        if (Test-Path -LiteralPath $stateBackup -PathType Leaf) {
            Copy-Item -LiteralPath $stateBackup -Destination $script:StatePath -Force
        }
        throw "Installing the validated world failed and the previous live files were restored from $rollback`n$($failure.Exception.Message)"
    }
    return $rollback
}
