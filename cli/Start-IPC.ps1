#!/usr/bin/env pwsh
#Requires -Version 5.1
<#
.SYNOPSIS
    IPC - Interactive CLI for Intune device inventory.

.DESCRIPTION
    Launch the interactive menu to store tokens, query device hardware
    inventory, and query software inventory from Intune managed devices.

.EXAMPLE
    ./Start-IPC.ps1
#>

$ErrorActionPreference = 'Stop'

# Use local module if running from the repo, otherwise install from PSGallery
$localModule = Join-Path $PSScriptRoot '..' 'IPC' 'IPC.psm1'
if (Test-Path $localModule) {
    Import-Module $localModule -Force
} else {
    if (-not (Get-Module -ListAvailable -Name IPC)) {
        Write-Host '[info] Installing IPC module from PowerShell Gallery...' -ForegroundColor Cyan
        Install-Module -Name IPC -Scope CurrentUser -Force
    }
    Import-Module IPC -Force
}

# ── Helpers ──────────────────────────────────────────────────────────────────

function Read-MaskedInput {
    param([string]$Prompt = 'Input')
    return (Read-Host -Prompt $Prompt -AsSecureString)
}

function Test-IPCIsWindowsHost {
    return [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
}

function Get-CLIValue {
    param(
        [Parameter(Mandatory)]$InputObject,
        [Parameter(Mandatory)][string[]]$Names,
        $Default = $null
    )

    if ($null -eq $InputObject) { return $Default }

    foreach ($name in $Names) {
        if ($InputObject -is [System.Collections.IDictionary]) {
            if ($InputObject.Contains($name) -and $null -ne $InputObject[$name]) {
                return $InputObject[$name]
            }
            continue
        }

        $property = $InputObject.PSObject.Properties[$name]
        if ($property -and $null -ne $property.Value) {
            return $property.Value
        }
    }

    return $Default
}

function Copy-ToClipboard {
    param([string]$Text)
    try {
        if (Test-IPCIsWindowsHost) {
            $Text | Set-Clipboard
        } elseif ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::MacOSX) {
            $Text | & pbcopy
        } else {
            $Text | & xclip -selection clipboard
        }
        return $true
    } catch {
        return $false
    }
}

function Show-Results {
    param($Data, [string]$Label = 'results')
    $rows = if ($Data -is [array]) { $Data } elseif ($Data -is [hashtable]) { @($Data) } else { @() }
    Write-Host "[ok] $($rows.Count) $Label returned." -ForegroundColor Green
    $json = $Data | ConvertTo-Json -Depth 20
    Write-Host $json
    if ($rows.Count -gt 0) {
        $copy = Read-Host 'Copy JSON to clipboard? [y/N]'
        if ($copy -eq 'y') {
            if (Copy-ToClipboard -Text $json) {
                Write-Host '[ok] Copied to clipboard.' -ForegroundColor Green
            } else {
                Write-Host '[warn] Could not access clipboard.' -ForegroundColor Yellow
            }
        }
    }
}

function Select-Devices {
    $name = Read-Host 'Device name (partial) or GUID'

    if ($name -match '^[0-9a-fA-F\-]{36}$') {
        return @(Get-IPCManagedDevice -DeviceId $name)
    }

    $winFilter = "operatingSystem eq 'Windows'"

    $matches_ = Get-IPCManagedDevices -Filter "startswith(deviceName,'$name') and $winFilter" `
        -Select @('id', 'deviceName', 'operatingSystem', 'complianceState')

    if ($matches_.Count -eq 0) {
        $allDevices = Get-IPCManagedDevices -Filter $winFilter `
            -Select @('id', 'deviceName', 'operatingSystem', 'complianceState')
        $matches_ = @($allDevices | Where-Object { $_.deviceName -like "*$name*" })
    }

    if ($matches_.Count -eq 0) {
        Write-Host '[warn] No devices found.' -ForegroundColor Yellow
        return @()
    }

    Write-Host "`nFound $($matches_.Count) device(s):"
    for ($i = 0; $i -lt $matches_.Count; $i++) {
        $d = $matches_[$i]
        $dn = Get-CLIValue -InputObject $d -Names @('deviceName') -Default '?'
        $os = Get-CLIValue -InputObject $d -Names @('operatingSystem') -Default ''
        $cs = Get-CLIValue -InputObject $d -Names @('complianceState') -Default ''
        Write-Host ("  {0,3}.  {1,-30}  {2,-10}  {3}" -f ($i + 1), $dn, $os, $cs)
    }
    Write-Host "  all.  All $($matches_.Count) devices"

    $pick = Read-Host "`nPick number or 'all'"
    if ($pick -eq 'all') { return $matches_ }

    try {
        $idx = [int]$pick - 1
        if ($idx -ge 0 -and $idx -lt $matches_.Count) {
            return @($matches_[$idx])
        }
        Write-Host '[warn] Invalid selection.' -ForegroundColor Yellow
        return @()
    } catch {
        Write-Host '[warn] Invalid selection.' -ForegroundColor Yellow
        return @()
    }
}

function Resolve-MenuInventoryCategory {
    param(
        [Parameter(Mandatory)][string]$Category,
        [string[]]$AvailableCategories
    )

    $trimmed = $Category.Trim()
    if (-not $trimmed) { return $null }
    if ($trimmed -ieq 'all') { return 'all' }

    foreach ($available in $AvailableCategories) {
        if ($available -and $available -ieq $trimmed) { return $available }
    }

    $normalized = [regex]::Replace($trimmed.ToLowerInvariant(), '[^a-z0-9]', '')
    if ($normalized -in @('localaiagent', 'localaiagents')) {
        foreach ($available in $AvailableCategories) {
            if ($available -and $available -ieq 'LocalAiAgent') { return $available }
        }
        return 'LocalAiAgent'
    }

    foreach ($available in $AvailableCategories) {
        if (-not $available) { continue }
        $availableNormalized = [regex]::Replace($available.ToLowerInvariant(), '[^a-z0-9]', '')
        if ($availableNormalized -eq $normalized) { return $available }
    }

    return $null
}

# ── Menu ─────────────────────────────────────────────────────────────────────

$menu = @"

╔══════════════════════════════════════════════════╗
║           IPC - Device Inventory                 ║
╠══════════════════════════════════════════════════╣
║  1a  Store access token  (from Network tab)      ║
║  1b  Store refresh token (from Session Storage)  ║
║  1c  Clear all tokens                            ║
║  2   Get device inventory                        ║
║  3   Get software inventory                      ║
║  q   Quit                                        ║
╚══════════════════════════════════════════════════╝
"@

function Show-TokenStatus {
    $info = Get-IPCTokenInfo
    if (-not $info) {
        Write-Host '  ⚠  No token stored - use option 1a or 1b to authenticate.' -ForegroundColor Yellow
        Write-Host
        return
    }
    $status = if ($info.Expired) { '⚠  EXPIRED' } else { '✔  Valid' }
    $tokenType = if ($info.HasRefresh) { 'Refresh (auto-refresh enabled)' } else { 'Access (manual)' }
    Write-Host "  Status  : $status"
    Write-Host "  Type    : $tokenType"
    Write-Host "  User    : $($info.User)"
    Write-Host "  Tenant  : $($info.Tenant)"
    Write-Host "  Expiry  : $($info.ExpiresAt) ($($info.ExpiresIn))"
    Write-Host
}

# ── Main loop ────────────────────────────────────────────────────────────────

try {
    Initialize-IPCSecretVault -Interactive
} catch {
    Write-Error "Failed to initialize secret vault: $_"
    exit 1
}

while ($true) {
    Write-Host $menu
    Show-TokenStatus
    $choice = (Read-Host 'Choice').Trim().ToLower()
    Write-Host

    try {
        switch ($choice) {
            'q' { exit 0 }

            '1a' {
                $secureToken = Read-MaskedInput -Prompt 'Paste bearer token (hidden): '
                Set-IPCAccessToken -AccessToken $secureToken
            }

            '1b' {
                Write-Host 'How to get the refresh token:' -ForegroundColor Cyan
                Write-Host '  1. Open https://intune.microsoft.com in your browser and sign in.'
                Write-Host '  2. Open DevTools (F12) → Application tab → Session Storage.'
                Write-Host '  3. Look for an MSAL entry with credentialType "RefreshToken".'
                Write-Host '  4. Copy the "secret" field value.'
                Write-Host
                $tenantDomain = Read-Host 'Tenant domain (e.g. contoso.onmicrosoft.com) or tenant GUID'
                if (-not $tenantDomain.Trim()) {
                    Write-Host '[warn] Tenant is required for refresh token authentication.' -ForegroundColor Yellow
                    continue
                }
                $secureToken = Read-MaskedInput -Prompt 'Paste refresh token secret (hidden): '
                Set-IPCRefreshToken -RefreshToken $secureToken -Tenant $tenantDomain.Trim()
            }

            '1c' {
                $confirm = Read-Host 'Clear all stored tokens? [y/N]'
                if ($confirm.Trim().ToLower() -eq 'y') {
                    Clear-IPCTokens
                    Write-Host '[ok] All tokens cleared.' -ForegroundColor Green
                } else {
                    Write-Host '[info] Cancelled.' -ForegroundColor Cyan
                }
            }

            '2' {
                $devices = Select-Devices
                if ($devices.Count -eq 0) { continue }

                $firstId = Get-CLIValue -InputObject $devices[0] -Names @('id', 'deviceId') -Default ''
                Write-Host '[info] Loading inventory categories...' -ForegroundColor Cyan
                $categories = Get-IPCDeviceInventoryCategories -DeviceId $firstId
                if ($categories.Count -eq 0) {
                    Write-Host '[warn] No inventory categories found for this device.' -ForegroundColor Yellow
                    continue
                }

                $catNames = @($categories | ForEach-Object { Get-CLIValue -InputObject $_ -Names @('id', 'inventoryId') -Default '' } | Where-Object { $_ })

                Write-Host "`nAvailable categories ($($catNames.Count)):"
                for ($i = 0; $i -lt $catNames.Count; $i++) {
                    Write-Host ("  {0,3}.  {1}" -f ($i + 1), $catNames[$i])
                }
                Write-Host "  all.  All $($catNames.Count) categories"

                $rawPick = (Read-Host "`nPick number(s) comma-separated or 'all'").Trim().ToLower()
                $selectedCats = @()
                if ($rawPick -eq 'all') {
                    $selectedCats = $catNames
                } else {
                    foreach ($part in $rawPick.Split(',')) {
                        $part = $part.Trim()
                        try {
                            $idx = [int]$part - 1
                            if ($idx -ge 0 -and $idx -lt $catNames.Count) {
                                $selectedCats += $catNames[$idx]
                            }
                        } catch {
                            $resolvedCat = Resolve-MenuInventoryCategory -Category $part -AvailableCategories $catNames
                            if ($resolvedCat) { $selectedCats += $resolvedCat }
                        }
                    }
                }
                $selectedCats = @($selectedCats | Select-Object -Unique)
                if ($selectedCats.Count -eq 0) {
                    Write-Host '[warn] No valid categories selected.' -ForegroundColor Yellow
                    continue
                }

                $deviceIdToName = @{}
                foreach ($d in $devices) {
                    $did = Get-CLIValue -InputObject $d -Names @('id', 'deviceId') -Default ''
                    $deviceIdToName[$did] = Get-CLIValue -InputObject $d -Names @('deviceName') -Default $did
                }
                $deviceIds = @($deviceIdToName.Keys)
                $totalReqs = $deviceIds.Count * $selectedCats.Count
                $totalChunks = [math]::Ceiling($totalReqs / 20)
                Write-Host "[info] Batching $totalReqs request(s) in $totalChunks Graph batch call(s)..." -ForegroundColor Cyan

                $progressBlock = {
                    param($done, $total)
                    Write-Host "`r  $done/$total requests complete..." -NoNewline
                }

                try {
                    $batchResult = Get-IPCInventoryBatch -DeviceIds $deviceIds -Categories $selectedCats -OnChunk $progressBlock
                    Write-Host
                } catch {
                    Write-Host "`n[error] Batch failed: $_" -ForegroundColor Red
                    continue
                }

                $grouped = @{}
                foreach ($deviceId in $batchResult.Keys) {
                    $deviceName = Get-CLIValue -InputObject $deviceIdToName -Names @($deviceId) -Default $deviceId
                    $grouped[$deviceName] = $batchResult[$deviceId]
                }

                foreach ($deviceId in $deviceIds) {
                    $deviceName = $deviceIdToName[$deviceId]
                    $deviceCats = if ($batchResult.ContainsKey($deviceId) -and $null -ne $batchResult[$deviceId]) { $batchResult[$deviceId] } else { @{} }
                    foreach ($cat in $selectedCats) {
                        if (-not $deviceCats.ContainsKey($cat)) {
                            Write-Host "[warn] $deviceName/$cat : not available (skipped)" -ForegroundColor Yellow
                        }
                    }
                }

                $singleGrouped = $grouped.Values | Select-Object -First 1
                $output = if ($grouped.Count -gt 1) { $grouped } elseif ($null -ne $singleGrouped) { $singleGrouped } else { @{} }
                $total = 0
                if ($output -is [hashtable]) {
                    foreach ($v in $output.Values) { if ($v -is [array]) { $total += $v.Count } }
                }
                Write-Host "[ok] $($grouped.Count) device(s), $total total instance(s)." -ForegroundColor Green
                $json = $output | ConvertTo-Json -Depth 20
                Write-Host $json
                $copy = Read-Host 'Copy JSON to clipboard? [y/N]'
                if ($copy -eq 'y') {
                    if (Copy-ToClipboard -Text $json) {
                        Write-Host '[ok] Copied to clipboard.' -ForegroundColor Green
                    } else {
                        Write-Host '[warn] Could not access clipboard.' -ForegroundColor Yellow
                    }
                }
            }

            '3' {
                $devices = Select-Devices
                if ($devices.Count -eq 0) { continue }

                $deviceIdToName = @{}
                foreach ($d in $devices) {
                    $did = Get-CLIValue -InputObject $d -Names @('id', 'deviceId') -Default ''
                    $deviceIdToName[$did] = Get-CLIValue -InputObject $d -Names @('deviceName') -Default $did
                }
                $deviceIds = @($deviceIdToName.Keys)
                $totalChunks = [math]::Ceiling($deviceIds.Count / 20)
                Write-Host "[info] Batching $($deviceIds.Count) request(s) in $totalChunks Graph batch call(s)..." -ForegroundColor Cyan

                $progressBlock = {
                    param($done, $total)
                    Write-Host "`r  $done/$total requests complete..." -NoNewline
                }

                try {
                    $batchResult = Get-IPCSoftwareInventoryBatch -DeviceIds $deviceIds -OnChunk $progressBlock
                    Write-Host
                } catch {
                    Write-Host "`n[error] Batch failed: $_" -ForegroundColor Red
                    continue
                }

                $allApps = @{}
                foreach ($deviceId in $batchResult.Keys) {
                    $deviceName = Get-CLIValue -InputObject $deviceIdToName -Names @($deviceId) -Default $deviceId
                    $allApps[$deviceName] = $batchResult[$deviceId]
                }

                foreach ($deviceId in $deviceIds) {
                    $deviceName = $deviceIdToName[$deviceId]
                    if (-not $batchResult.ContainsKey($deviceId)) {
                        Write-Host "[warn] $deviceName : software inventory not available (skipped)" -ForegroundColor Yellow
                    }
                }

                $singleApps = $allApps.Values | Select-Object -First 1
                $output = if ($allApps.Count -gt 1) { $allApps } elseif ($null -ne $singleApps) { $singleApps } else { @() }
                $total = if ($output -is [array]) { $output.Count } else {
                    $s = 0; foreach ($v in $output.Values) { if ($v -is [array]) { $s += $v.Count } }; $s
                }
                Write-Host "[ok] $($allApps.Count) device(s), $total total application(s)." -ForegroundColor Green
                Show-Results -Data $output -Label 'applications'
            }

            default {
                Write-Host '[?] Unknown option.' -ForegroundColor Yellow
            }
        }
    } catch {
        if ($_ -match 'expired') {
            Write-Host '[error] Your token has expired.' -ForegroundColor Red
            Write-Host '[info]  Use option 1a to paste a fresh access token, or option 1b to store a refresh token.' -ForegroundColor Cyan
        } elseif ($_ -match 'No token') {
            Write-Host '[error] No token stored yet - use option 1a or 1b to authenticate.' -ForegroundColor Red
        } else {
            Write-Host "[error] $_" -ForegroundColor Red
        }
    }
}
