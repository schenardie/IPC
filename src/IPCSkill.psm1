#Requires -Version 7.0
<#
.SYNOPSIS
    IPCSkill – Intune Properties Catalog Skill (PowerShell module).

.DESCRIPTION
    Interactive CLI and PowerShell module for querying hardware and software
    inventory from Intune managed devices via the Microsoft Graph beta API.

    No Azure app registration is required. IPCSkill uses Microsoft Intune's
    own well-known public client ID.

    Supports two authentication methods:
      1. Paste an access token copied from browser DevTools (Network tab)
      2. Paste a refresh token copied from browser Session Storage (auto-refreshes)
#>

# ── Constants ────────────────────────────────────────────────────────────────

$script:INTUNE_CLIENT_ID = '5926fc8e-304e-4f59-8bed-58ca97cc39a4'
$script:GRAPH_BASE_URL   = 'https://graph.microsoft.com/beta'
$script:VAULT_NAME       = 'IPCSkillVault'
$script:SECRET_ACCESS    = 'ipc-access-token'
$script:SECRET_REFRESH   = 'ipc-refresh-token'
$script:SECRET_METADATA  = 'ipc-token-metadata'
$script:BATCH_SIZE       = 20
$script:MAX_BATCH_RETRIES = 5
$script:DEFAULT_RETRY_AFTER = 30

# ── Secret Vault ─────────────────────────────────────────────────────────────

function Initialize-IPCSecretVault {
    <#
    .SYNOPSIS
        Ensures SecretManagement + SecretStore modules are installed and the
        IPCSkill vault is registered.
    #>
    [CmdletBinding()]
    param()

    foreach ($mod in @('Microsoft.PowerShell.SecretManagement', 'Microsoft.PowerShell.SecretStore')) {
        if (-not (Get-Module -ListAvailable -Name $mod)) {
            Write-Host "[info] Installing $mod ..." -ForegroundColor Cyan
            Install-Module -Name $mod -Scope CurrentUser -Force -AllowClobber
        }
        Import-Module $mod -ErrorAction Stop
    }

    if (-not (Get-SecretVault -Name $script:VAULT_NAME -ErrorAction SilentlyContinue)) {
        $storeConfig = @{ Authentication = 'None'; Interaction = 'None'; Confirm = $false }
        try {
            Set-SecretStoreConfiguration @storeConfig -Force -ErrorAction Stop
        } catch {
            # Already configured
        }
        Register-SecretVault -Name $script:VAULT_NAME -ModuleName Microsoft.PowerShell.SecretStore -DefaultVault
        Write-Host "[ok] Secret vault '$($script:VAULT_NAME)' registered." -ForegroundColor Green
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

    $expiryUtc = [DateTimeOffset]::FromUnixTimeSeconds([long]$expiresAt).UtcDateTime.ToString('yyyy-MM-dd HH:mm:ss UTC')
    Write-Host "[ok] Access token stored (expiry: $expiryUtc)." -ForegroundColor Green
}

function Set-IPCRefreshToken {
    <#
    .SYNOPSIS
        Stores a refresh token (copied from browser Session Storage).
    .DESCRIPTION
        In the browser, go to intune.microsoft.com → DevTools → Application →
        Session Storage → look for an MSAL entry with credentialType "RefreshToken"
        and copy the "secret" field value.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RefreshToken
    )

    Initialize-IPCSecretVault

    $token = $RefreshToken.Trim()

    Set-Secret -Name $script:SECRET_REFRESH -Secret $token -Vault $script:VAULT_NAME
    Write-Host "[ok] Refresh token stored." -ForegroundColor Green

    try {
        $null = Update-IPCAccessTokenFromRefresh
        Write-Host "[ok] Access token acquired from refresh token." -ForegroundColor Green
    } catch {
        Write-Warning "Could not acquire access token from refresh token: $_"
        Write-Host "[info] You may need to also store an access token (option 1) or check the refresh token." -ForegroundColor Yellow
    }
}

function Update-IPCAccessTokenFromRefresh {
    <#
    .SYNOPSIS
        Uses the stored refresh token to acquire a fresh access token.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $refreshToken = Get-Secret -Name $script:SECRET_REFRESH -Vault $script:VAULT_NAME -AsPlainText -ErrorAction Stop

    $tenant = 'common'
    try {
        $existingToken = Get-Secret -Name $script:SECRET_ACCESS -Vault $script:VAULT_NAME -AsPlainText -ErrorAction SilentlyContinue
        if ($existingToken) {
            $payload = ConvertFrom-JwtPayload -Token $existingToken
            if ($payload.ContainsKey('tid')) {
                $tenant = $payload['tid']
            }
        }
    } catch { }

    $body = @{
        client_id     = $script:INTUNE_CLIENT_ID
        grant_type    = 'refresh_token'
        refresh_token = $refreshToken
        scope         = 'https://graph.microsoft.com/.default offline_access'
    }

    $response = Invoke-RestMethod -Uri "https://login.microsoftonline.com/$tenant/oauth2/v2.0/token" `
        -Method POST -Body $body -ContentType 'application/x-www-form-urlencoded' -ErrorAction Stop

    $accessToken = $response.access_token
    $payload = ConvertFrom-JwtPayload -Token $accessToken

    $expiresAt = if ($payload.ContainsKey('exp')) {
        [double]$payload['exp']
    } else {
        [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + $response.expires_in
    }

    $metadata = @{ expires_at = $expiresAt } | ConvertTo-Json -Compress

    Set-Secret -Name $script:SECRET_ACCESS -Secret $accessToken -Vault $script:VAULT_NAME
    Set-Secret -Name $script:SECRET_METADATA -Secret $metadata -Vault $script:VAULT_NAME

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

# ── Exported functions ───────────────────────────────────────────────────────

Export-ModuleMember -Function @(
    'Initialize-IPCSecretVault'
    'ConvertFrom-JwtPayload'
    'Resolve-AccessToken'
    'ConvertTo-FriendlyName'
    'ConvertTo-CleanInstance'
    'Set-IPCAccessToken'
    'Set-IPCRefreshToken'
    'Update-IPCAccessTokenFromRefresh'
    'Get-IPCValidToken'
    'Get-IPCTokenInfo'
    'Invoke-GraphRequest'
    'Invoke-GraphBatch'
    'Get-IPCManagedDevices'
    'Get-IPCManagedDevice'
    'Get-IPCDeviceInventoryCategories'
    'Get-IPCDeviceInventory'
    'Get-IPCSoftwareInventory'
    'Get-IPCInventoryBatch'
    'Get-IPCSoftwareInventoryBatch'
)
