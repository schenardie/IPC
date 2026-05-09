#Requires -Version 7.0
<#
.SYNOPSIS
    IPC – Intune Properties Catalog Skill (PowerShell module).

.DESCRIPTION
    Interactive CLI and PowerShell module for querying hardware and software
    inventory from Intune managed devices via the Microsoft Graph beta API.

    No Azure app registration is required. IPC uses Microsoft Intune's
    own well-known public client ID.

    Supports two authentication methods:
      1. Paste an access token copied from browser DevTools (Network tab)
      2. Paste a refresh token copied from browser Session Storage (auto-refreshes)
#>

# ── Constants ────────────────────────────────────────────────────────────────

$script:INTUNE_CLIENT_ID = '5926fc8e-304e-4f59-8bed-58ca97cc39a4'
$script:BROKER_CLIENT_ID = 'c44b4083-3bb0-49c1-b47d-974e53cbdf3c'
$script:BROKER_URL       = 'https://portal.azure.com/'
$script:GRAPH_BASE_URL   = 'https://graph.microsoft.com/beta'
$script:VAULT_NAME       = 'IPCVault'
$script:SECRET_ACCESS    = 'ipc-access-token'
$script:SECRET_REFRESH   = 'ipc-refresh-token'
$script:SECRET_METADATA  = 'ipc-token-metadata'
$script:SECRET_TENANT    = 'ipc-tenant-id'
$script:BATCH_SIZE       = 20
$script:MAX_BATCH_RETRIES = 5
$script:DEFAULT_RETRY_AFTER = 30

# ── Secret Vault ─────────────────────────────────────────────────────────────

# Track whether the vault has been initialized this session
$script:_vaultInitialized = $false

function Initialize-IPCSecretVault {
    <#
    .SYNOPSIS
        Ensures SecretManagement + SecretStore modules are installed and the
        IPC vault is registered.
    .DESCRIPTION
        On first run, detects whether a SecretStore has been created before.
        When called with -Interactive (i.e. from the CLI), the user is shown a
        one-time setup prompt to choose whether the vault is password-protected
        or passwordless:

          • Passwordless  — seamless, no prompts, works with AI agents/skills.
          • With password — vault is encrypted; run Unlock-IPCVault once before
                            each agent/skill session.

        When called without -Interactive (module functions), a new store is
        always configured passwordless so non-interactive callers are never
        blocked.

        Once the store is created the configuration is persisted by SecretStore
        and this prompt never appears again.
    #>
    [CmdletBinding()]
    param(
        # Pass this switch when calling from an interactive terminal (CLI).
        # Enables the one-time vault setup prompt on first run.
        [switch]$Interactive
    )

    if ($script:_vaultInitialized) { return }

    # IMPORTANT: Check for a new store BEFORE importing SecretStore.
    # Import-Module creates the store directory as a side effect, which would
    # cause the check to return false even on a brand new system, skipping the
    # setup wizard and leaving the store in the default password-required state.
    $storePath = if ($IsWindows) {
        Join-Path ([System.Environment]::GetFolderPath('LocalApplicationData')) `
            'Microsoft' 'PowerShell' 'secretmanagement' 'localstore'
    } else {
        Join-Path $HOME '.secretmanagement' 'localstore'
    }
    $isNewStore = -not (Test-Path $storePath)

    foreach ($mod in @('Microsoft.PowerShell.SecretManagement', 'Microsoft.PowerShell.SecretStore')) {
        if (-not (Get-Module -ListAvailable -Name $mod)) {
            Write-Host "[info] Installing $mod ..." -ForegroundColor Cyan
            Install-Module -Name $mod -Scope CurrentUser -Force -AllowClobber
        }
        Import-Module $mod -ErrorAction Stop
    }

    if ($isNewStore) {
        $usePassword = $false

        if ($Interactive) {
            Write-Host ''
            Write-Host '  ┌─ IPC Vault Setup ───────────────────────────────────┐' -ForegroundColor Cyan
            Write-Host '  │                                                      │' -ForegroundColor Cyan
            Write-Host '  │  Protect the secret vault with a password?           │' -ForegroundColor Cyan
            Write-Host '  │                                                      │' -ForegroundColor Cyan
            Write-Host '  │  [N] No  — passwordless, always seamless,            │' -ForegroundColor Green
            Write-Host '  │           works with AI agents/skills out of the box │' -ForegroundColor Green
            Write-Host '  │                                                      │' -ForegroundColor Cyan
            Write-Host '  │  [y] Yes — encrypted vault; you must run             │' -ForegroundColor Yellow
            Write-Host '  │           Unlock-IPCVault before each agent session  │' -ForegroundColor Yellow
            Write-Host '  │                                                      │' -ForegroundColor Cyan
            Write-Host '  └──────────────────────────────────────────────────────┘' -ForegroundColor Cyan
            Write-Host ''
            $answer = Read-Host '  Password-protect vault? [y/N]'
            $usePassword = $answer.Trim().ToLower() -eq 'y'
            Write-Host ''
        }

        # Pre-create the localstore directory for both paths.
        # SecretStore 1.0.6 on macOS throws "Could not find a part of the path
        # .../localstore/storefile|storeconfig" when Reset-SecretStore is called
        # and the directory does not yet exist. New-Item -Force on an existing
        # directory is a safe no-op.
        New-Item -ItemType Directory -Path $storePath -Force | Out-Null

        if ($usePassword) {
            Write-Host '[info] Configuring vault with password protection...' -ForegroundColor Cyan
            Write-Host '[info] You will be prompted to set your vault password now.' -ForegroundColor Cyan
            # Use Reset-SecretStore (not Set-SecretStoreConfiguration) so the store is
            # created with the correct settings from the start instead of being
            # initialised with defaults first (which causes an extra password prompt).
            # -Force suppresses the ShouldContinue confirmation; the password-creation
            # prompt still appears as expected.
            Reset-SecretStore -Authentication Password -Interaction Prompt -Force -WarningAction SilentlyContinue
            Write-Host '[ok] Vault secured with a password.' -ForegroundColor Green
            Write-Host '[!] Run Unlock-IPCVault in your terminal before using the IPC agent or skill.' -ForegroundColor Yellow
        } else {
            # -Force suppresses the ShouldContinue prompt cross-platform.
            Reset-SecretStore -Authentication None -Interaction None -Force -WarningAction SilentlyContinue
            Write-Host '[ok] Vault configured (no password) — always seamless.' -ForegroundColor Green
        }
    }

    if (-not (Get-SecretVault -Name $script:VAULT_NAME -ErrorAction SilentlyContinue)) {
        Register-SecretVault -Name $script:VAULT_NAME -ModuleName Microsoft.PowerShell.SecretStore -DefaultVault
        Write-Host "[ok] Secret vault '$($script:VAULT_NAME)' registered." -ForegroundColor Green
    }

    # Test if vault is locked — if so, throw a clear error rather than
    # letting SecretStore prompt interactively (which blocks AI agents).
    try {
        Get-Secret -Name '__ipc_test__' -Vault $script:VAULT_NAME -ErrorAction Ignore | Out-Null
    } catch {
        if ($_.Exception.Message -match 'locked|password|PasswordRequired') {
            throw "IPC vault is locked. Run 'Unlock-IPCVault' in your terminal first, then retry."
        }
    }

    $script:_vaultInitialized = $true
}

function Unlock-IPCVault {
    <#
    .SYNOPSIS
        Unlocks the IPC SecretStore vault for the current session.
    .DESCRIPTION
        Run this once before starting an AI agent session (or after a long
        idle period). It prompts you for the vault password interactively so
        the AI agent can access stored tokens without needing your password.

        After unlocking, the vault stays open for 8 hours.
    .EXAMPLE
        Unlock-IPCVault
    #>
    [CmdletBinding()]
    param()

    Initialize-IPCSecretVault

    try {
        Unlock-SecretStore -PasswordTimeout 28800
        Write-Host "[ok] Vault unlocked for 8 hours. You can now run the AI agent." -ForegroundColor Green
    } catch {
        Write-Error "Failed to unlock vault: $_"
    }
}

# ── JWT Helpers ──────────────────────────────────────────────────────────────

function ConvertFrom-JwtPayload {
    <#
    .SYNOPSIS
        Decodes the payload section of a JWT without verifying the signature.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)]
        [string]$Token
    )

    try {
        $parts = $Token.Split('.')
        if ($parts.Count -lt 2) { return @{} }
        $payload = $parts[1]
        switch ($payload.Length % 4) {
            2 { $payload += '==' }
            3 { $payload += '='  }
        }
        $payload = $payload.Replace('-', '+').Replace('_', '/')
        $json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload))
        return ($json | ConvertFrom-Json -AsHashtable)
    } catch {
        return @{}
    }
}

function Resolve-AccessToken {
    <#
    .SYNOPSIS
        Normalizes user-pasted token input to the raw JWT access token.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)]
        [string]$RawToken
    )

    $token = $RawToken.Trim()

    if ($token -match '^(?i)authorization:\s*(.+)$') {
        $token = $Matches[1].Trim()
    }

    while ($token.Length -ge 2 -and $token[0] -eq $token[-1] -and ($token[0] -eq '"' -or $token[0] -eq "'")) {
        $token = $token.Substring(1, $token.Length - 2).Trim()
    }

    if ($token -match '^(?i)bearer\s+(.+)$') {
        $token = $Matches[1].Trim()
    }

    if ($token -match '([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)') {
        return $Matches[1]
    }

    return $token
}

function ConvertTo-FriendlyName {
    <#
    .SYNOPSIS
        Converts camelCase/PascalCase to Title Case: 'cycleCount' → 'Cycle Count'.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    $spaced = [regex]::Replace($Name, '(?<=[a-z0-9])([A-Z])', ' $1')
    return (Get-Culture).TextInfo.ToTitleCase($spaced)
}

function ConvertTo-CleanInstance {
    <#
    .SYNOPSIS
        Strips OData metadata and converts camelCase keys to friendly Title Case.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)]
        [hashtable]$Instance
    )

    $cleaned = [ordered]@{}

    foreach ($key in $Instance.Keys) {
        if ($key.StartsWith('@')) { continue }

        $value = $Instance[$key]

        if ($key -eq 'properties' -and $value -is [array]) {
            foreach ($prop in $value) {
                if ($prop -isnot [hashtable] -and $prop -is [System.Collections.IDictionary]) {
                    $prop = [hashtable]$prop
                } elseif ($prop -isnot [hashtable]) {
                    $propHt = @{}
                    foreach ($p in $prop.PSObject.Properties) { $propHt[$p.Name] = $p.Value }
                    $prop = $propHt
                }

                $propName = $prop['displayName'] ?? $prop['name'] ?? $prop['propertyName'] ?? $prop['id']
                $propValue = if ($prop.ContainsKey('value')) { $prop['value'] } else { $prop['propertyValue'] }

                if ($propName -and -not $cleaned.Contains((ConvertTo-FriendlyName $propName))) {
                    $cleaned[(ConvertTo-FriendlyName ([string]$propName))] = $propValue
                }
            }
            continue
        }

        $friendlyKey = if ($key -eq 'id') { 'Instance Name' } else { ConvertTo-FriendlyName $key }
        $cleaned[$friendlyKey] = $value
    }

    $instanceName = $cleaned['Instance Name']
    if ($instanceName -is [string] -and $instanceName.Contains('=')) {
        foreach ($part in $instanceName.Split(';')) {
            if (-not $part.Contains('=')) { continue }
            $eqIdx = $part.IndexOf('=')
            $k = $part.Substring(0, $eqIdx).Trim()
            $v = $part.Substring($eqIdx + 1).Trim()
            if ($k -and -not $cleaned.Contains((ConvertTo-FriendlyName $k))) {
                $cleaned[(ConvertTo-FriendlyName $k)] = $v
            }
        }
    }

    return $cleaned
}

# ── Token Management ─────────────────────────────────────────────────────────

function Set-IPCAccessToken {
    <#
    .SYNOPSIS
        Stores a bearer access token (copied from browser DevTools Network tab).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$AccessToken,

        [int]$ExpiresIn = 3600
    )

    Initialize-IPCSecretVault

    $token = Resolve-AccessToken -RawToken $AccessToken
    $payload = ConvertFrom-JwtPayload -Token $token

    if ($payload.ContainsKey('exp')) {
        $expiresAt = [double]$payload['exp']
    } elseif ($ExpiresIn -gt 0) {
        $expiresAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + $ExpiresIn
    } else {
        $expiresAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 3600
    }

    $metadata = @{ expires_at = $expiresAt } | ConvertTo-Json -Compress

    Set-Secret -Name $script:SECRET_ACCESS -Secret $token -Vault $script:VAULT_NAME
    Set-Secret -Name $script:SECRET_METADATA -Secret $metadata -Vault $script:VAULT_NAME

    # Clear any stored refresh token and tenant to avoid cross-tenant mismatches
    try { Remove-Secret -Name $script:SECRET_REFRESH -Vault $script:VAULT_NAME -ErrorAction Ignore } catch { }
    try { Remove-Secret -Name $script:SECRET_TENANT -Vault $script:VAULT_NAME -ErrorAction Ignore } catch { }

    $expiryUtc = [DateTimeOffset]::FromUnixTimeSeconds([long]$expiresAt).UtcDateTime.ToString('yyyy-MM-dd HH:mm:ss UTC')
    Write-Host "[ok] Access token stored (expiry: $expiryUtc)." -ForegroundColor Green
    Write-Host "[info] Refresh token cleared (use option 2 to store one for this tenant)." -ForegroundColor Cyan
}

function Set-IPCRefreshToken {
    <#
    .SYNOPSIS
        Stores a refresh token (copied from browser Session Storage) and
        acquires an access token via the BroCI (Nested App Authentication) flow.
    .DESCRIPTION
        In the browser, go to intune.microsoft.com → DevTools → Application →
        Session Storage → look for an MSAL entry with credentialType "RefreshToken"
        and copy the "secret" field value.

        The refresh token belongs to the Azure Portal SPA (c44b4083). IPC
        exchanges it for an Intune access token using the BroCI broker flow.

        Any previously stored access token is cleared to avoid cross-tenant issues.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RefreshToken,

        [Parameter(Mandatory)]
        [string]$Tenant
    )

    Initialize-IPCSecretVault

    $token = $RefreshToken.Trim()
    $tenantValue = $Tenant.Trim()

    Set-Secret -Name $script:SECRET_REFRESH -Secret $token -Vault $script:VAULT_NAME
    Set-Secret -Name $script:SECRET_TENANT -Secret $tenantValue -Vault $script:VAULT_NAME

    # Clear any existing access token to avoid cross-tenant mismatches
    try { Remove-Secret -Name $script:SECRET_ACCESS -Vault $script:VAULT_NAME -ErrorAction Ignore } catch { }
    try { Remove-Secret -Name $script:SECRET_METADATA -Vault $script:VAULT_NAME -ErrorAction Ignore } catch { }

    Write-Host "[ok] Refresh token stored for tenant '$tenantValue'." -ForegroundColor Green

    try {
        $null = Update-IPCAccessTokenFromRefresh
        Write-Host "[ok] Access token acquired via BroCI exchange." -ForegroundColor Green
    } catch {
        Write-Warning "Could not acquire access token from refresh token: $_"
        Write-Host "[info] You may need to also store an access token (option 1) or check the refresh token." -ForegroundColor Yellow
    }
}

function Update-IPCAccessTokenFromRefresh {
    <#
    .SYNOPSIS
        Exchanges the stored broker refresh token for a fresh Intune access
        token via the BroCI (Nested App Authentication) flow.
    .DESCRIPTION
        The refresh token belongs to the Azure Portal SPA (c44b4083).
        BroCI exchanges it for an Intune access token (5926fc8e) by passing
        broker parameters and Origin/Referer headers that satisfy the SPA
        cross-origin restriction.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $refreshToken = Get-Secret -Name $script:SECRET_REFRESH -Vault $script:VAULT_NAME -AsPlainText -ErrorAction Stop

    $tenant = 'common'
    try {
        $storedTenant = Get-Secret -Name $script:SECRET_TENANT -Vault $script:VAULT_NAME -AsPlainText -ErrorAction SilentlyContinue
        if ($storedTenant) { $tenant = $storedTenant }
    } catch { }

    $brokerHost = ([System.Uri]$script:BROKER_URL).Host

    # BroCI POST body: target app = Intune, broker = Azure Portal
    $body = @{
        grant_type       = 'refresh_token'
        client_id        = $script:INTUNE_CLIENT_ID
        scope            = 'https://graph.microsoft.com/.default'
        refresh_token    = $refreshToken
        redirect_uri     = "brk-$($script:BROKER_CLIENT_ID)://$brokerHost"
        brk_client_id    = $script:BROKER_CLIENT_ID
        brk_redirect_uri = $script:BROKER_URL
    }

    # Origin + Referer headers required to satisfy the SPA cross-origin check
    $headers = @{
        'Content-Type' = 'application/x-www-form-urlencoded'
        Origin          = $script:BROKER_URL.TrimEnd('/')
        Referer         = $script:BROKER_URL
        'User-Agent'    = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0'
    }

    $response = Invoke-RestMethod `
        -Uri "https://login.microsoftonline.com/$tenant/oauth2/v2.0/token" `
        -Method POST -Body $body -Headers $headers -ErrorAction Stop

    $accessToken = $response.access_token
    if (-not $accessToken) {
        $err = $response.error ?? 'unknown_error'
        $desc = $response.error_description ?? ''
        throw "BroCI token exchange failed: $err — $desc"
    }

    $payload = ConvertFrom-JwtPayload -Token $accessToken

    $expiresAt = if ($payload.ContainsKey('exp')) {
        [double]$payload['exp']
    } else {
        [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + ($response.expires_in ?? 3600)
    }

    $metadata = @{ expires_at = $expiresAt } | ConvertTo-Json -Compress

    Set-Secret -Name $script:SECRET_ACCESS -Secret $accessToken -Vault $script:VAULT_NAME
    Set-Secret -Name $script:SECRET_METADATA -Secret $metadata -Vault $script:VAULT_NAME

    # Store rotated refresh token (SPA tokens rotate on each use)
    if ($response.refresh_token) {
        Set-Secret -Name $script:SECRET_REFRESH -Secret $response.refresh_token -Vault $script:VAULT_NAME
    }

    return $accessToken
}

function Get-IPCValidToken {
    <#
    .SYNOPSIS
        Returns a valid Bearer access token. Auto-refreshes if expired and a
        refresh token is available.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    Initialize-IPCSecretVault

    $accessToken = $null
    try {
        $accessToken = Get-Secret -Name $script:SECRET_ACCESS -Vault $script:VAULT_NAME -AsPlainText -ErrorAction Stop
    } catch { }

    if (-not $accessToken) {
        try {
            return Update-IPCAccessTokenFromRefresh
        } catch {
            throw "No token stored. Use Set-IPCAccessToken or Set-IPCRefreshToken first."
        }
    }

    $metadataJson = $null
    try {
        $metadataJson = Get-Secret -Name $script:SECRET_METADATA -Vault $script:VAULT_NAME -AsPlainText -ErrorAction SilentlyContinue
    } catch { }

    $expiresAt = 0
    if ($metadataJson) {
        $metadata = $metadataJson | ConvertFrom-Json -AsHashtable
        $expiresAt = [double]($metadata['expires_at'] ?? 0)
    }

    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

    if ($now -lt $expiresAt) {
        return (Resolve-AccessToken -RawToken $accessToken)
    }

    try {
        return Update-IPCAccessTokenFromRefresh
    } catch {
        throw "Access token has expired. Paste a fresh token (option 1) or refresh token (option 2)."
    }
}

function Get-IPCTokenInfo {
    <#
    .SYNOPSIS
        Returns human-readable info about the stored token.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()

    Initialize-IPCSecretVault

    $accessToken = $null
    try {
        $accessToken = Get-Secret -Name $script:SECRET_ACCESS -Vault $script:VAULT_NAME -AsPlainText -ErrorAction Stop
    } catch {
        return $null
    }

    if (-not $accessToken) { return $null }

    $payload = ConvertFrom-JwtPayload -Token $accessToken
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

    $metadataJson = $null
    try {
        $metadataJson = Get-Secret -Name $script:SECRET_METADATA -Vault $script:VAULT_NAME -AsPlainText -ErrorAction SilentlyContinue
    } catch { }

    $expiresAt = 0
    if ($metadataJson) {
        $meta = $metadataJson | ConvertFrom-Json -AsHashtable
        $expiresAt = [double]($meta['expires_at'] ?? $payload['exp'] ?? 0)
    } elseif ($payload.ContainsKey('exp')) {
        $expiresAt = [double]$payload['exp']
    }

    $secondsLeft = $expiresAt - $now
    $expiryUtc = [DateTimeOffset]::FromUnixTimeSeconds([long]$expiresAt).UtcDateTime.ToString('yyyy-MM-dd HH:mm:ss UTC')

    $hasRefresh = $false
    try {
        $rt = Get-Secret -Name $script:SECRET_REFRESH -Vault $script:VAULT_NAME -AsPlainText -ErrorAction SilentlyContinue
        $hasRefresh = [bool]$rt
    } catch { }

    return @{
        User         = $payload['upn'] ?? $payload['unique_name'] ?? $payload['preferred_username'] ?? 'unknown'
        Tenant       = $payload['tid'] ?? 'unknown'
        ExpiresAt    = $expiryUtc
        ExpiresIn    = if ($secondsLeft -gt 0) { "{0}h {1}m" -f [math]::Floor($secondsLeft / 3600), [math]::Floor(($secondsLeft % 3600) / 60) } else { 'EXPIRED' }
        Expired      = $secondsLeft -le 0
        HasRefresh   = $hasRefresh
    }
}

# ── Graph Client ─────────────────────────────────────────────────────────────

function Invoke-GraphRequest {
    <#
    .SYNOPSIS
        Performs an authenticated request to the Microsoft Graph API.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [ValidateSet('GET', 'POST', 'PATCH', 'DELETE')]
        [string]$Method = 'GET',

        [hashtable]$Body,

        [hashtable]$QueryParameters
    )

    $token = Get-IPCValidToken

    $uri = $script:GRAPH_BASE_URL.TrimEnd('/') + '/' + $Path.TrimStart('/')

    if ($QueryParameters -and $QueryParameters.Count -gt 0) {
        $qsParts = foreach ($k in $QueryParameters.Keys) {
            "$k=$([System.Uri]::EscapeDataString($QueryParameters[$k]))"
        }
        $uri += '?' + ($qsParts -join '&')
    }

    $headers = @{
        Authorization  = "Bearer $token"
        'Content-Type' = 'application/json'
        Accept         = 'application/json'
    }

    $splat = @{
        Uri     = $uri
        Method  = $Method
        Headers = $headers
    }

    if ($Body) {
        $splat['Body'] = ($Body | ConvertTo-Json -Depth 20 -Compress)
    }

    try {
        $response = Invoke-RestMethod @splat -ErrorAction Stop
        return $response
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        $errorBody = $_.ErrorDetails.Message
        $msg = $errorBody
        $code = ''
        try {
            $parsed = $errorBody | ConvertFrom-Json
            $msg = $parsed.error.message ?? $errorBody
            $code = $parsed.error.code ?? ''
        } catch { }
        throw "Graph API error $statusCode [$code]: $msg"
    }
}

function Invoke-GraphBatch {
    <#
    .SYNOPSIS
        Executes multiple Graph API requests using the JSON batch endpoint.
        Requests are chunked into groups of 20 with throttle retry.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [array]$Requests,

        [scriptblock]$OnChunk
    )

    if ($Requests.Count -eq 0) { return @{} }

    $results = @{}
    $total = $Requests.Count
    $completed = 0

    for ($chunkStart = 0; $chunkStart -lt $total; $chunkStart += $script:BATCH_SIZE) {
        $chunkEnd = [math]::Min($chunkStart + $script:BATCH_SIZE, $total) - 1
        $chunk = $Requests[$chunkStart..$chunkEnd]
        $pending = @{}
        foreach ($req in $chunk) { $pending[$req.id] = $req }

        for ($attempt = 0; $attempt -lt $script:MAX_BATCH_RETRIES; $attempt++) {
            if ($pending.Count -eq 0) { break }

            $batchBody = @{ requests = @($pending.Values) }

            try {
                $envelope = Invoke-GraphRequest -Path '/$batch' -Method POST -Body $batchBody
            } catch {
                if ($_ -match '429' -and $attempt -lt ($script:MAX_BATCH_RETRIES - 1)) {
                    Write-Warning "Batch envelope throttled (429); retrying in $($script:DEFAULT_RETRY_AFTER)s..."
                    Start-Sleep -Seconds $script:DEFAULT_RETRY_AFTER
                    continue
                }
                throw
            }

            $throttledIds = @()
            $maxRetryAfter = 0

            foreach ($item in ($envelope.responses ?? @())) {
                $itemId = [string]$item.id
                $status = $item.status ?? 200
                $body = $item.body ?? @{}
                $itemHeaders = $item.headers ?? @{}

                if ($status -eq 429) {
                    $retryAfter = [double]($itemHeaders.'Retry-After' ?? $itemHeaders.'retry-after' ?? $script:DEFAULT_RETRY_AFTER)
                    $retryAfter = [math]::Max(1.0, $retryAfter)
                    $maxRetryAfter = [math]::Max($maxRetryAfter, $retryAfter)
                    $throttledIds += $itemId
                } else {
                    $results[$itemId] = @{ status = $status; body = $body }
                    $pending.Remove($itemId)
                }
            }

            if ($throttledIds.Count -gt 0) {
                $newPending = @{}
                foreach ($id in $throttledIds) {
                    if ($pending.ContainsKey($id)) { $newPending[$id] = $pending[$id] }
                }
                $pending = $newPending
                if ($attempt -lt ($script:MAX_BATCH_RETRIES - 1)) {
                    Write-Warning "$($throttledIds.Count) item(s) throttled; retrying in ${maxRetryAfter}s..."
                    Start-Sleep -Seconds $maxRetryAfter
                } else {
                    foreach ($id in $throttledIds) {
                        $results[$id] = @{
                            status = 429
                            body   = @{ error = @{ code = 'TooManyRequests'; message = 'Exceeded retry limit' } }
                        }
                    }
                }
            } else {
                break
            }
        }

        $completed += $chunk.Count
        if ($OnChunk) {
            & $OnChunk ([math]::Min($completed, $total)) $total
        }
    }

    return $results
}

# ── IPC Explorer ─────────────────────────────────────────────────────────────

function Get-IPCManagedDevices {
    <#
    .SYNOPSIS
        Lists managed devices visible to the authenticated user.
    #>
    [CmdletBinding()]
    param(
        [string]$Filter,
        [string[]]$Select,
        [int]$Top = 100
    )

    $params = @{ '$top' = $Top }
    if ($Filter) { $params['$filter'] = $Filter }
    if ($Select) { $params['$select'] = $Select -join ',' }

    $results = @()
    $response = Invoke-GraphRequest -Path '/deviceManagement/managedDevices' -QueryParameters $params
    $results += @($response.value ?? @())

    $nextLink = $response.'@odata.nextLink'
    while ($nextLink) {
        $response = Invoke-GraphRequest -Path $nextLink
        $results += @($response.value ?? @())
        $nextLink = $response.'@odata.nextLink'
    }

    return $results
}

function Get-IPCManagedDevice {
    <#
    .SYNOPSIS
        Fetches a single managed device by its Intune device ID.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$DeviceId
    )

    return Invoke-GraphRequest -Path "/deviceManagement/managedDevices/$DeviceId"
}

function Get-IPCDeviceInventoryCategories {
    <#
    .SYNOPSIS
        Returns the inventory categories available for a device.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$DeviceId
    )

    $response = Invoke-GraphRequest -Path "/deviceManagement/managedDevices('$DeviceId')/deviceInventories"
    if ($null -eq $response) { return @() }
    return @($response.value ?? @())
}

function Get-IPCDeviceInventory {
    <#
    .SYNOPSIS
        Returns cleaned inventory instances for a device and category.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$DeviceId,

        [Parameter(Mandatory)]
        [string]$Category
    )

    $instances = Get-IPCInventoryInstances -DeviceId $DeviceId -Category $Category
    $hydrated = foreach ($inst in $instances) {
        Resolve-SimpleInstance -DeviceId $DeviceId -Category $Category -Instance $inst
    }

    return @(foreach ($inst in $hydrated) {
        $ht = if ($inst -is [hashtable]) { $inst }
              elseif ($inst -is [System.Collections.IDictionary]) { [hashtable]$inst }
              else {
                  $h = @{}
                  foreach ($p in $inst.PSObject.Properties) { $h[$p.Name] = $p.Value }
                  $h
              }
        ConvertTo-CleanInstance -Instance $ht
    })
}

function Get-IPCSoftwareInventory {
    <#
    .SYNOPSIS
        Returns cleaned software (application) inventory for a device.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$DeviceId
    )

    $expandParam = 'instances($expand=Microsoft.Graph.deviceInventorySimpleItem/properties)'
    $response = Invoke-GraphRequest -Path "/deviceManagement/managedDevices('$DeviceId')/deviceInventories('ApplicationProperties')" `
        -QueryParameters @{ '$expand' = $expandParam }

    if ($null -eq $response) { return @() }
    $instances = @($response.instances ?? @())

    return @(foreach ($inst in $instances) {
        $ht = if ($inst -is [hashtable]) { $inst }
              elseif ($inst -is [System.Collections.IDictionary]) { [hashtable]$inst }
              else {
                  $h = @{}
                  foreach ($p in $inst.PSObject.Properties) { $h[$p.Name] = $p.Value }
                  $h
              }
        ConvertTo-CleanInstance -Instance $ht
    })
}

function Get-IPCInventoryBatch {
    <#
    .SYNOPSIS
        Fetches inventory for multiple devices and categories using Graph batching.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string[]]$DeviceIds,

        [Parameter(Mandatory)]
        [string[]]$Categories,

        [scriptblock]$OnChunk
    )

    if ($DeviceIds.Count -eq 0 -or $Categories.Count -eq 0) { return @{} }

    $expandParam = 'instances($expand=Microsoft.Graph.deviceInventorySimpleItem/properties)'

    $requests = @()
    foreach ($deviceId in $DeviceIds) {
        foreach ($category in $Categories) {
            $requests += @{
                id     = "$deviceId||$category"
                method = 'GET'
                url    = "/deviceManagement/managedDevices('$deviceId')/deviceInventories('$category')?`$expand=$expandParam"
            }
        }
    }

    $batchResults = Invoke-GraphBatch -Requests $requests -OnChunk $OnChunk

    $raw = @{}
    $hydrationRequests = @()

    foreach ($compositeId in $batchResults.Keys) {
        if ($compositeId -notmatch '\|\|') { continue }
        $parts = $compositeId.Split('||', 2)
        $deviceId = $parts[0]
        $category = $parts[1]
        $result = $batchResults[$compositeId]
        $status = $result.status
        $body = $result.body

        if ($status -in 400, 404) { continue }
        if ($status -lt 200 -or $status -ge 300) {
            Write-Warning "Inventory $deviceId/$category : unexpected status $status"
            continue
        }

        $instances = @($body.instances ?? @())
        if (-not $raw.ContainsKey($deviceId)) { $raw[$deviceId] = @{} }
        $raw[$deviceId][$category] = $instances

        foreach ($inst in $instances) {
            $ht = if ($inst -is [hashtable]) { $inst }
                  else {
                      $h = @{}
                      foreach ($p in $inst.PSObject.Properties) { $h[$p.Name] = $p.Value }
                      $h
                  }
            $nonMeta = @($ht.Keys | Where-Object { -not $_.StartsWith('@') })
            if ($nonMeta.Count -eq 1 -and $nonMeta[0] -eq 'id' -and $ht['id'] -is [string]) {
                $encodedId = [System.Uri]::EscapeDataString($ht['id'])
                $hydrationRequests += @{
                    id     = "$deviceId||$category||$($ht['id'])"
                    method = 'GET'
                    url    = "/deviceManagement/managedDevices('$deviceId')/deviceInventories('$category')/instances('$encodedId')"
                }
            }
        }
    }

    if ($hydrationRequests.Count -gt 0) {
        $hydrationResults = Invoke-GraphBatch -Requests $hydrationRequests
        foreach ($compositeId in $hydrationResults.Keys) {
            $parts = $compositeId.Split('||', 3)
            if ($parts.Count -ne 3) { continue }
            $deviceId = $parts[0]
            $category = $parts[1]
            $instId = $parts[2]
            $result = $hydrationResults[$compositeId]
            if ($result.status -lt 200 -or $result.status -ge 300) { continue }
            $body = $result.body
            if (-not $body -or -not $body.id) { continue }

            $instances = $raw[$deviceId][$category]
            for ($i = 0; $i -lt $instances.Count; $i++) {
                $inst = $instances[$i]
                $id = if ($inst -is [hashtable]) { $inst['id'] } else { $inst.id }
                if ($id -eq $instId) {
                    $instances[$i] = $body
                    break
                }
            }
        }
    }

    $output = @{}
    foreach ($deviceId in $raw.Keys) {
        $output[$deviceId] = @{}
        foreach ($cat in $raw[$deviceId].Keys) {
            $output[$deviceId][$cat] = @(foreach ($inst in $raw[$deviceId][$cat]) {
                $ht = if ($inst -is [hashtable]) { $inst }
                      elseif ($inst -is [System.Collections.IDictionary]) { [hashtable]$inst }
                      else {
                          $h = @{}
                          foreach ($p in $inst.PSObject.Properties) { $h[$p.Name] = $p.Value }
                          $h
                      }
                ConvertTo-CleanInstance -Instance $ht
            })
        }
    }

    return $output
}

function Get-IPCSoftwareInventoryBatch {
    <#
    .SYNOPSIS
        Fetches software inventory for multiple devices using Graph batching.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string[]]$DeviceIds,

        [scriptblock]$OnChunk
    )

    if ($DeviceIds.Count -eq 0) { return @{} }

    $expandParam = 'instances($expand=Microsoft.Graph.deviceInventorySimpleItem/properties)'

    $requests = @(foreach ($deviceId in $DeviceIds) {
        @{
            id     = $deviceId
            method = 'GET'
            url    = "/deviceManagement/managedDevices('$deviceId')/deviceInventories('ApplicationProperties')?`$expand=$expandParam"
        }
    })

    $batchResults = Invoke-GraphBatch -Requests $requests -OnChunk $OnChunk

    $output = @{}
    foreach ($deviceId in $batchResults.Keys) {
        $result = $batchResults[$deviceId]
        $status = $result.status
        $body = $result.body

        if ($status -in 400, 404) { continue }
        if ($status -lt 200 -or $status -ge 300) {
            Write-Warning "Software inventory $deviceId : unexpected status $status"
            continue
        }

        $instances = @($body.instances ?? @())
        $output[$deviceId] = @(foreach ($inst in $instances) {
            $ht = if ($inst -is [hashtable]) { $inst }
                  elseif ($inst -is [System.Collections.IDictionary]) { [hashtable]$inst }
                  else {
                      $h = @{}
                      foreach ($p in $inst.PSObject.Properties) { $h[$p.Name] = $p.Value }
                      $h
                  }
            ConvertTo-CleanInstance -Instance $ht
        })
    }

    return $output
}

# ── Internal helpers ─────────────────────────────────────────────────────────

function Get-IPCInventoryInstances {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$DeviceId,
        [Parameter(Mandatory)][string]$Category
    )

    try {
        $response = Invoke-GraphRequest -Path "/deviceManagement/managedDevices('$DeviceId')/deviceInventories('$Category')/instances"
        if ($null -ne $response -and $response.value -is [array]) {
            return @($response.value)
        }
    } catch {
        if ($_ -notmatch '40[04]') { throw }
    }

    $expandFull = 'instances($expand=Microsoft.Graph.deviceInventorySimpleItem/properties)'
    try {
        $fallback = Invoke-GraphRequest -Path "/deviceManagement/managedDevices('$DeviceId')/deviceInventories('$Category')" `
            -QueryParameters @{ '$expand' = $expandFull }
    } catch {
        $expandSimple = 'instances'
        $fallback = Invoke-GraphRequest -Path "/deviceManagement/managedDevices('$DeviceId')/deviceInventories('$Category')" `
            -QueryParameters @{ '$expand' = $expandSimple }
    }

    if ($null -eq $fallback) { return @() }
    return @($fallback.instances ?? @())
}

function Resolve-SimpleInstance {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$DeviceId,
        [Parameter(Mandatory)][string]$Category,
        [Parameter(Mandatory)]$Instance
    )

    $ht = if ($Instance -is [hashtable]) { $Instance }
          elseif ($Instance -is [System.Collections.IDictionary]) { [hashtable]$Instance }
          else {
              $h = @{}
              foreach ($p in $Instance.PSObject.Properties) { $h[$p.Name] = $p.Value }
              $h
          }

    $nonMeta = @($ht.Keys | Where-Object { -not $_.StartsWith('@') })
    if ($nonMeta.Count -ne 1 -or $nonMeta[0] -ne 'id') { return $Instance }

    $instanceId = $ht['id']
    if ($instanceId -isnot [string] -or -not $instanceId) { return $Instance }

    try {
        $encodedId = [System.Uri]::EscapeDataString($instanceId)
        $detail = Invoke-GraphRequest -Path "/deviceManagement/managedDevices('$DeviceId')/deviceInventories('$Category')/instances('$encodedId')"

        if ($null -ne $detail) {
            $detailHt = if ($detail -is [hashtable]) { $detail }
                        else {
                            $h = @{}
                            foreach ($p in $detail.PSObject.Properties) { $h[$p.Name] = $p.Value }
                            $h
                        }
            $hasExtra = @($detailHt.Keys | Where-Object { -not $_.StartsWith('@') -and $_ -ne 'id' })
            if ($detailHt.ContainsKey('id') -and $hasExtra.Count -gt 0) {
                return $detail
            }
        }
    } catch {
        # Some categories only support list-level instances
    }

    return $Instance
}

# ── High-level skill interface ────────────────────────────────────────────────

function Invoke-IPC {
    <#
    .SYNOPSIS
        Single entry-point for AI agents and scripts. Combines device lookup
        and inventory retrieval in one call.

    .DESCRIPTION
        Resolves devices by partial name, GUID, or all Windows devices, then
        fetches the requested inventory type (hardware categories, software,
        or device list). Returns structured objects ready for display or
        further processing.

    .PARAMETER Action
        What to retrieve:
          ListDevices         — Search/list managed devices
          HardwareInventory   — Fetch hardware inventory categories
          SoftwareInventory   — Fetch installed applications
          ListCategories      — List available inventory categories for a device

    .PARAMETER DeviceName
        Partial device name to search for. Mutually exclusive with DeviceId
        and AllDevices.

    .PARAMETER DeviceId
        Exact Intune device GUID. Mutually exclusive with DeviceName and
        AllDevices.

    .PARAMETER AllDevices
        Target all Windows managed devices in the tenant.

    .PARAMETER Category
        One or more hardware inventory category IDs (e.g. 'bios', 'battery',
        'diskDrive'). Use 'all' to fetch every available category. Only
        applies to HardwareInventory action.

    .PARAMETER Filter
        Text filter applied to results. For SoftwareInventory, filters
        application names/properties containing this text. For
        HardwareInventory, filters instance property values.

    .PARAMETER Top
        Maximum number of devices to return when searching (default 100).

    .EXAMPLE
        # "Show me all MSI software on computer1"
        Invoke-IPC -Action SoftwareInventory -DeviceName 'computer1' -Filter 'msi'

    .EXAMPLE
        # "Check bios information for all devices"
        Invoke-IPC -Action HardwareInventory -AllDevices -Category 'bios'

    .EXAMPLE
        # "Full software inventory of computer2"
        Invoke-IPC -Action SoftwareInventory -DeviceName 'computer2'

    .EXAMPLE
        # "List all devices matching LAPTOP"
        Invoke-IPC -Action ListDevices -DeviceName 'LAPTOP'

    .EXAMPLE
        # "What inventory categories are available for this device?"
        Invoke-IPC -Action ListCategories -DeviceId '1904d94c-d00f-42ea-abb5-e05673c61ff2'
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateSet('ListDevices', 'HardwareInventory', 'SoftwareInventory', 'ListCategories')]
        [string]$Action,

        [string]$DeviceName,

        [string]$DeviceId,

        [switch]$AllDevices,

        [string[]]$Category,

        [string]$Filter,

        [int]$Top = 100
    )

    # ── Validate parameter combinations ──────────────────────────────────
    $deviceSelectors = @(
        ($DeviceName ? 1 : 0),
        ($DeviceId   ? 1 : 0),
        ($AllDevices ? 1 : 0)
    )
    if (($deviceSelectors | Measure-Object -Sum).Sum -gt 1) {
        throw 'Specify only one of -DeviceName, -DeviceId, or -AllDevices.'
    }
    if (($deviceSelectors | Measure-Object -Sum).Sum -eq 0 -and $Action -ne 'ListDevices') {
        throw 'Specify -DeviceName, -DeviceId, or -AllDevices to identify target device(s).'
    }

    # ── Resolve devices ──────────────────────────────────────────────────
    $devices = @()
    $winFilter = "operatingSystem eq 'Windows'"

    if ($DeviceId) {
        $devices = @(Get-IPCManagedDevice -DeviceId $DeviceId)
    } elseif ($AllDevices) {
        $devices = @(Get-IPCManagedDevices -Filter $winFilter -Top $Top `
            -Select @('id', 'deviceName', 'operatingSystem', 'complianceState'))
    } elseif ($DeviceName) {
        $devices = @(Get-IPCManagedDevices `
            -Filter "startswith(deviceName,'$DeviceName') and $winFilter" `
            -Select @('id', 'deviceName', 'operatingSystem', 'complianceState') `
            -Top $Top)

        if ($devices.Count -eq 0) {
            $all = @(Get-IPCManagedDevices -Filter $winFilter -Top $Top `
                -Select @('id', 'deviceName', 'operatingSystem', 'complianceState'))
            $devices = @($all | Where-Object { $_.deviceName -like "*$DeviceName*" })
        }
    } elseif ($Action -eq 'ListDevices') {
        $devices = @(Get-IPCManagedDevices -Filter $winFilter -Top $Top `
            -Select @('id', 'deviceName', 'operatingSystem', 'complianceState'))
    }

    # ── Execute action ───────────────────────────────────────────────────
    switch ($Action) {

        'ListDevices' {
            $result = @($devices | ForEach-Object {
                @{
                    DeviceId        = $_.id ?? $_.deviceId ?? ''
                    DeviceName      = $_.deviceName ?? ''
                    OperatingSystem = $_.operatingSystem ?? ''
                    ComplianceState = $_.complianceState ?? ''
                }
            })
            if ($Filter) {
                $result = @($result | Where-Object {
                    ($_.Values -join ' ') -match [regex]::Escape($Filter)
                })
            }
            return @{
                Action      = 'ListDevices'
                DeviceCount = $result.Count
                Devices     = $result
            }
        }

        'ListCategories' {
            if ($devices.Count -eq 0) {
                return @{ Action = 'ListCategories'; DeviceCount = 0; Categories = @() }
            }
            $firstId = $devices[0].id ?? $devices[0].deviceId ?? ''
            $cats = Get-IPCDeviceInventoryCategories -DeviceId $firstId
            $catIds = @($cats | ForEach-Object { $_.id ?? $_.inventoryId ?? '' } | Where-Object { $_ })
            return @{
                Action      = 'ListCategories'
                DeviceId    = $firstId
                DeviceName  = $devices[0].deviceName ?? $firstId
                Categories  = $catIds
            }
        }

        'HardwareInventory' {
            if ($devices.Count -eq 0) {
                return @{ Action = 'HardwareInventory'; DeviceCount = 0; Results = @{} }
            }

            $deviceIdToName = @{}
            foreach ($d in $devices) {
                $did = $d.id ?? $d.deviceId ?? ''
                $deviceIdToName[$did] = $d.deviceName ?? $did
            }
            $deviceIds = @($deviceIdToName.Keys)

            # Resolve categories
            $selectedCats = @()
            if (-not $Category -or $Category -contains 'all') {
                $firstId = $deviceIds[0]
                $available = Get-IPCDeviceInventoryCategories -DeviceId $firstId
                $selectedCats = @($available | ForEach-Object { $_.id ?? $_.inventoryId ?? '' } | Where-Object { $_ })
            } else {
                $selectedCats = $Category
            }

            if ($selectedCats.Count -eq 0) {
                return @{ Action = 'HardwareInventory'; DeviceCount = $devices.Count; Results = @{} }
            }

            $batchResult = Get-IPCInventoryBatch -DeviceIds $deviceIds -Categories $selectedCats

            # Structure output by device name
            $output = @{}
            foreach ($deviceId in $batchResult.Keys) {
                $name = $deviceIdToName[$deviceId] ?? $deviceId
                $output[$name] = $batchResult[$deviceId]
            }

            # Apply filter
            if ($Filter) {
                $filtered = @{}
                foreach ($name in $output.Keys) {
                    $filteredCats = @{}
                    foreach ($cat in $output[$name].Keys) {
                        $filteredInstances = @($output[$name][$cat] | Where-Object {
                            ($_.Values -join ' ') -match [regex]::Escape($Filter)
                        })
                        if ($filteredInstances.Count -gt 0) {
                            $filteredCats[$cat] = $filteredInstances
                        }
                    }
                    if ($filteredCats.Count -gt 0) {
                        $filtered[$name] = $filteredCats
                    }
                }
                $output = $filtered
            }

            # Unwrap single device
            $unwrapped = if ($output.Count -eq 1) { $output.Values | Select-Object -First 1 } else { $output }

            return @{
                Action      = 'HardwareInventory'
                DeviceCount = $output.Count
                Categories  = $selectedCats
                Results     = $unwrapped
            }
        }

        'SoftwareInventory' {
            if ($devices.Count -eq 0) {
                return @{ Action = 'SoftwareInventory'; DeviceCount = 0; Results = @{} }
            }

            $deviceIdToName = @{}
            foreach ($d in $devices) {
                $did = $d.id ?? $d.deviceId ?? ''
                $deviceIdToName[$did] = $d.deviceName ?? $did
            }
            $deviceIds = @($deviceIdToName.Keys)

            $batchResult = Get-IPCSoftwareInventoryBatch -DeviceIds $deviceIds

            $output = @{}
            foreach ($deviceId in $batchResult.Keys) {
                $name = $deviceIdToName[$deviceId] ?? $deviceId
                $apps = @($batchResult[$deviceId])

                if ($Filter) {
                    $apps = @($apps | Where-Object {
                        ($_.Values -join ' ') -match [regex]::Escape($Filter)
                    })
                }

                $output[$name] = $apps
            }

            # Unwrap single device
            $unwrapped = if ($output.Count -eq 1) { $output.Values | Select-Object -First 1 } else { $output }
            $totalApps = if ($unwrapped -is [array]) { $unwrapped.Count } else {
                $s = 0; foreach ($v in $unwrapped.Values) { if ($v -is [array]) { $s += $v.Count } }; $s
            }

            return @{
                Action          = 'SoftwareInventory'
                DeviceCount     = $output.Count
                ApplicationCount = $totalApps
                Results         = $unwrapped
            }
        }
    }
}

# ── Token Cleanup ────────────────────────────────────────────────────────────

function Clear-IPCTokens {
    <#
    .SYNOPSIS
        Removes all stored tokens (access, refresh, metadata, tenant) from the vault.
    #>
    [CmdletBinding()]
    param()

    Initialize-IPCSecretVault

    foreach ($name in @($script:SECRET_ACCESS, $script:SECRET_REFRESH, $script:SECRET_METADATA, $script:SECRET_TENANT)) {
        try { Remove-Secret -Name $name -Vault $script:VAULT_NAME -ErrorAction Ignore } catch { }
    }
}

# ── Exported functions ───────────────────────────────────────────────────────

Export-ModuleMember -Function @(
    'Initialize-IPCSecretVault'
    'Unlock-IPCVault'
    'ConvertFrom-JwtPayload'
    'Resolve-AccessToken'
    'ConvertTo-FriendlyName'
    'ConvertTo-CleanInstance'
    'Set-IPCAccessToken'
    'Set-IPCRefreshToken'
    'Update-IPCAccessTokenFromRefresh'
    'Get-IPCValidToken'
    'Get-IPCTokenInfo'
    'Clear-IPCTokens'
    'Invoke-GraphRequest'
    'Invoke-GraphBatch'
    'Invoke-IPC'
    'Get-IPCManagedDevices'
    'Get-IPCManagedDevice'
    'Get-IPCDeviceInventoryCategories'
    'Get-IPCDeviceInventory'
    'Get-IPCSoftwareInventory'
    'Get-IPCInventoryBatch'
    'Get-IPCSoftwareInventoryBatch'
)
