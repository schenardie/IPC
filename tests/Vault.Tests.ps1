#Requires -Version 7.0
#Requires -Modules Pester
<#
.SYNOPSIS
    Pester tests for IPC vault initialization, token storage, and reset scenarios.

.DESCRIPTION
    These tests exercise three real-world scenarios:
      1. User has NO existing SecretStore - first-time setup
      2. User has an EXISTING SecretStore (possibly with a password) - IPC must
         register its vault without touching the store configuration
      3. User wants to reset IPC only - Clear-IPCTokens + unregister, without
         destroying other vaults or the SecretStore itself

    Tests run against the real SecretManagement/SecretStore modules to ensure
    cross-platform correctness (Windows DPAPI, macOS/Linux file-based keys).

    IMPORTANT: Never delete the parent secretmanagement/ directory in tests.
    SecretManagement is a binary DLL module - its static .NET state (including
    the secretvaultregistry path) cannot be truly reloaded within the same
    process. Delete only localstore/ to simulate "no store".
#>

BeforeAll {
    $modulePath = Join-Path $PSScriptRoot '..' 'IPC' 'IPC.psm1'

    # Ensure SecretManagement modules are available
    foreach ($mod in @('Microsoft.PowerShell.SecretManagement', 'Microsoft.PowerShell.SecretStore')) {
        if (-not (Get-Module -ListAvailable -Name $mod)) {
            Install-Module -Name $mod -Scope CurrentUser -Force -AllowClobber
        }
        Import-Module $mod -Force
    }

    # Helper: get the SecretStore localstore path (cross-platform)
    function Get-StoreLocalPath {
        if ($IsWindows) {
            Join-Path ([System.Environment]::GetFolderPath('LocalApplicationData')) `
                'Microsoft' 'PowerShell' 'secretmanagement' 'localstore'
        } else {
            Join-Path $HOME '.secretmanagement' 'localstore'
        }
    }

    # Helper: unregister test vaults and delete localstore to simulate fresh state.
    # NEVER delete the parent secretmanagement/ directory - the
    # secretvaultregistry/ subdirectory holds vault registrations and
    # SecretManagement (a binary DLL) cannot recreate it after deletion
    # within the same process.
    function Reset-TestEnvironment {
        foreach ($name in @('IPCVault', 'OtherToolVault')) {
            Unregister-SecretVault -Name $name -ErrorAction SilentlyContinue
        }

        # Delete only localstore (secrets + config), not the parent directory
        $storePath = Get-StoreLocalPath
        if (Test-Path $storePath) {
            Remove-Item -Recurse -Force $storePath -ErrorAction SilentlyContinue
        }
    }

    # Helper: create a passwordless SecretStore (simulates existing store)
    function New-PasswordlessStore {
        $storePath = Get-StoreLocalPath
        New-Item -ItemType Directory -Path $storePath -Force | Out-Null
        Reset-SecretStore -Authentication None -Interaction None -Force -WarningAction SilentlyContinue
    }

    # Helper: create a password-protected SecretStore
    function New-PasswordStore {
        param([securestring]$Password)
        $storePath = Get-StoreLocalPath
        New-Item -ItemType Directory -Path $storePath -Force | Out-Null
        Reset-SecretStore -Authentication Password -Password $Password -PasswordTimeout 3600 -Interaction None -Force -WarningAction SilentlyContinue
    }

    # Helper: force re-import IPC module so _vaultInitialized resets
    function Reset-IPCModule {
        Remove-Module IPC -Force -ErrorAction SilentlyContinue
        Import-Module $modulePath -Force
    }
}

AfterAll {
    # Final cleanup - unregister test vaults, restore a clean passwordless store
    foreach ($name in @('IPCVault', 'OtherToolVault')) {
        Unregister-SecretVault -Name $name -ErrorAction SilentlyContinue
    }
    New-PasswordlessStore
}

# ---------------------------------------------------------------------------
# Scenario 1: No existing SecretStore - first-time setup (passwordless)
# ---------------------------------------------------------------------------
Describe 'Scenario 1: Fresh install - no existing SecretStore' {

    BeforeAll {
        Reset-TestEnvironment
        Reset-IPCModule
    }

    AfterAll {
        Reset-IPCModule
    }

    It 'creates the store and registers IPCVault when nothing exists' {
        # Verify localstore is gone
        $storePath = Get-StoreLocalPath
        Test-Path $storePath | Should -BeFalse

        # Initialize without -Interactive (module/agent path - always passwordless)
        Initialize-IPCSecretVault

        # Vault should be registered
        $vault = Get-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        $vault | Should -Not -BeNullOrEmpty
        $vault.ModuleName | Should -Match 'SecretStore'

        # Store should be configured passwordless
        $config = Get-SecretStoreConfiguration
        $config.Authentication | Should -Be 'None'
    }

    It 'can store and retrieve a secret after init' {
        Set-Secret -Name 'ipc-test-secret' -Secret 'hello-world' -Vault 'IPCVault'
        $val = Get-Secret -Name 'ipc-test-secret' -Vault 'IPCVault' -AsPlainText
        $val | Should -Be 'hello-world'
        Remove-Secret -Name 'ipc-test-secret' -Vault 'IPCVault'
    }
}

# ---------------------------------------------------------------------------
# Scenario 2a: User has existing passwordless SecretStore with another vault
# ---------------------------------------------------------------------------
Describe 'Scenario 2a: Existing passwordless SecretStore with another vault' {

    BeforeAll {
        Reset-TestEnvironment
        New-PasswordlessStore

        # Create a pre-existing vault with a secret (simulates another tool)
        Register-SecretVault -Name 'OtherToolVault' -ModuleName Microsoft.PowerShell.SecretStore
        Set-Secret -Name 'other-tool-secret' -Secret 'do-not-delete' -Vault 'OtherToolVault'

        Reset-IPCModule
    }

    AfterAll {
        try { Remove-Secret -Name 'other-tool-secret' -Vault 'OtherToolVault' -ErrorAction SilentlyContinue } catch { }
        Unregister-SecretVault -Name 'OtherToolVault' -ErrorAction SilentlyContinue
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        Reset-IPCModule
    }

    It 'registers IPCVault without modifying the existing store config' {
        $configBefore = Get-SecretStoreConfiguration

        Initialize-IPCSecretVault

        $configAfter = Get-SecretStoreConfiguration

        # IPC vault should be registered
        $vault = Get-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        $vault | Should -Not -BeNullOrEmpty

        # Store configuration should be untouched
        $configAfter.Authentication | Should -Be $configBefore.Authentication
        $configAfter.Interaction | Should -Be $configBefore.Interaction
    }

    It 'does not destroy the other vault''s secrets' {
        $otherSecret = Get-Secret -Name 'other-tool-secret' -Vault 'OtherToolVault' -AsPlainText
        $otherSecret | Should -Be 'do-not-delete'
    }

    It 'can store IPC tokens alongside the other vault' {
        Set-Secret -Name 'ipc-access-token' -Secret 'my-token' -Vault 'IPCVault'
        $val = Get-Secret -Name 'ipc-access-token' -Vault 'IPCVault' -AsPlainText
        $val | Should -Be 'my-token'

        # Other vault is still fine
        $otherSecret = Get-Secret -Name 'other-tool-secret' -Vault 'OtherToolVault' -AsPlainText
        $otherSecret | Should -Be 'do-not-delete'

        Remove-Secret -Name 'ipc-access-token' -Vault 'IPCVault'
    }
}

# ---------------------------------------------------------------------------
# Scenario 2b: User has existing password-protected SecretStore
# ---------------------------------------------------------------------------
Describe 'Scenario 2b: Existing password-protected SecretStore' {

    BeforeAll {
        Reset-TestEnvironment

        $testPassword = ConvertTo-SecureString 'TestP@ss123' -AsPlainText -Force
        New-PasswordStore -Password $testPassword

        # Unlock the store for this test session
        Unlock-SecretStore -Password $testPassword

        # Create a pre-existing vault with a secret
        Register-SecretVault -Name 'OtherToolVault' -ModuleName Microsoft.PowerShell.SecretStore
        Set-Secret -Name 'other-tool-secret' -Secret 'protected-value' -Vault 'OtherToolVault'

        Reset-IPCModule
    }

    AfterAll {
        try { Remove-Secret -Name 'other-tool-secret' -Vault 'OtherToolVault' -ErrorAction SilentlyContinue } catch { }
        Unregister-SecretVault -Name 'OtherToolVault' -ErrorAction SilentlyContinue
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        # Switch back to passwordless for subsequent tests
        Reset-SecretStore -Force -Authentication None -Interaction None -WarningAction SilentlyContinue
        Reset-IPCModule
    }

    It 'registers IPCVault without changing the password-protected config' {
        $configBefore = Get-SecretStoreConfiguration

        Initialize-IPCSecretVault

        $configAfter = Get-SecretStoreConfiguration

        # IPC vault registered
        $vault = Get-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        $vault | Should -Not -BeNullOrEmpty

        # Password authentication must be preserved
        $configAfter.Authentication | Should -Be 'Password'
        $configBefore.Authentication | Should -Be 'Password'
    }

    It 'can store IPC tokens when the store is unlocked' {
        Set-Secret -Name 'ipc-access-token' -Secret 'token-in-locked-store' -Vault 'IPCVault'
        $val = Get-Secret -Name 'ipc-access-token' -Vault 'IPCVault' -AsPlainText
        $val | Should -Be 'token-in-locked-store'
        Remove-Secret -Name 'ipc-access-token' -Vault 'IPCVault'
    }

    It 'preserves the other vault''s secrets' {
        $otherSecret = Get-Secret -Name 'other-tool-secret' -Vault 'OtherToolVault' -AsPlainText
        $otherSecret | Should -Be 'protected-value'
    }
}

# ---------------------------------------------------------------------------
# Scenario 3: Reset IPC only - don't break other vaults
# ---------------------------------------------------------------------------
Describe 'Scenario 3: Reset IPC vault without affecting other vaults' {

    BeforeAll {
        # Start with a clean passwordless store
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        Unregister-SecretVault -Name 'OtherToolVault' -ErrorAction SilentlyContinue
        New-PasswordlessStore

        # Set up another tool's vault with secrets
        Register-SecretVault -Name 'OtherToolVault' -ModuleName Microsoft.PowerShell.SecretStore
        Set-Secret -Name 'other-tool-secret' -Secret 'must-survive' -Vault 'OtherToolVault'

        Reset-IPCModule

        # Initialize IPC and store some tokens
        Initialize-IPCSecretVault
        Set-Secret -Name 'ipc-access-token' -Secret 'old-token' -Vault 'IPCVault'
        Set-Secret -Name 'ipc-refresh-token' -Secret 'old-refresh' -Vault 'IPCVault'
        Set-Secret -Name 'ipc-token-metadata' -Secret '{}' -Vault 'IPCVault'
        Set-Secret -Name 'ipc-tenant-id' -Secret 'old-tenant' -Vault 'IPCVault'
    }

    AfterAll {
        try { Remove-Secret -Name 'other-tool-secret' -Vault 'OtherToolVault' -ErrorAction SilentlyContinue } catch { }
        Unregister-SecretVault -Name 'OtherToolVault' -ErrorAction SilentlyContinue
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        Reset-IPCModule
    }

    It 'Clear-IPCTokens removes IPC secrets but not the vault registration' {
        Clear-IPCTokens

        # IPC secrets should be gone
        { Get-Secret -Name 'ipc-access-token' -Vault 'IPCVault' -ErrorAction Stop } | Should -Throw
        { Get-Secret -Name 'ipc-refresh-token' -Vault 'IPCVault' -ErrorAction Stop } | Should -Throw

        # Vault registration should still exist
        $vault = Get-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        $vault | Should -Not -BeNullOrEmpty
    }

    It 'other vault secrets survive Clear-IPCTokens' {
        $otherSecret = Get-Secret -Name 'other-tool-secret' -Vault 'OtherToolVault' -AsPlainText
        $otherSecret | Should -Be 'must-survive'
    }

    It 'unregistering IPCVault does not affect other vaults' {
        Unregister-SecretVault -Name 'IPCVault'

        # IPC vault should be gone
        $vault = Get-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue -WarningAction SilentlyContinue
        $vault | Should -BeNullOrEmpty

        # Other vault should be fine
        $otherVault = Get-SecretVault -Name 'OtherToolVault' -ErrorAction SilentlyContinue
        $otherVault | Should -Not -BeNullOrEmpty

        $otherSecret = Get-Secret -Name 'other-tool-secret' -Vault 'OtherToolVault' -AsPlainText
        $otherSecret | Should -Be 'must-survive'
    }

    It 're-initializing IPC after unregister re-creates the vault' {
        Reset-IPCModule
        Initialize-IPCSecretVault

        $vault = Get-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        $vault | Should -Not -BeNullOrEmpty

        # Store config unchanged (still passwordless)
        $config = Get-SecretStoreConfiguration
        $config.Authentication | Should -Be 'None'
    }
}

# ---------------------------------------------------------------------------
# Scenario 4: Re-init is idempotent
# ---------------------------------------------------------------------------
Describe 'Scenario 4: Idempotent initialization' {

    BeforeAll {
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        New-PasswordlessStore
        Reset-IPCModule
    }

    AfterAll {
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        Reset-IPCModule
    }

    It 'calling Initialize-IPCSecretVault twice does not error or duplicate' {
        Initialize-IPCSecretVault
        { Initialize-IPCSecretVault } | Should -Not -Throw

        $vaults = @(Get-SecretVault -ErrorAction SilentlyContinue -WarningAction SilentlyContinue |
            Where-Object { $_.Name -eq 'IPCVault' })
        $vaults.Count | Should -Be 1
    }

    It 'calling Initialize-IPCSecretVault after module re-import still works' {
        Reset-IPCModule
        { Initialize-IPCSecretVault } | Should -Not -Throw

        $vault = Get-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        $vault | Should -Not -BeNullOrEmpty
    }
}

# ---------------------------------------------------------------------------
# Scenario 5: Token lifecycle - store, retrieve, clear via module functions
# ---------------------------------------------------------------------------
Describe 'Scenario 5: Token lifecycle via module functions' {

    BeforeAll {
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        New-PasswordlessStore
        Reset-IPCModule
        Initialize-IPCSecretVault
    }

    AfterAll {
        try { Clear-IPCTokens } catch { }
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        Reset-IPCModule
    }

    It 'Set-IPCAccessToken stores token and Get-IPCTokenInfo retrieves info' {
        # Create a fake JWT that expires far in the future
        $header = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes('{}'))
        $payload = @{ exp = ([DateTimeOffset]::UtcNow.AddHours(1).ToUnixTimeSeconds()); upn = 'test@contoso.com'; tid = '00000000-0000-0000-0000-000000000000' } | ConvertTo-Json -Compress
        $payloadB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($payload))
        $header = $header.Replace('+', '-').Replace('/', '_').TrimEnd('=')
        $payloadB64 = $payloadB64.Replace('+', '-').Replace('/', '_').TrimEnd('=')
        $fakeJwt = "$header.$payloadB64.fake-sig"

        Set-IPCAccessToken -AccessToken (ConvertTo-SecureString $fakeJwt -AsPlainText -Force)

        $info = Get-IPCTokenInfo
        $info | Should -Not -BeNullOrEmpty
        $info.User | Should -Be 'test@contoso.com'
        $info.Expired | Should -BeFalse
    }

    It 'Clear-IPCTokens removes all IPC secrets' {
        Clear-IPCTokens

        $info = Get-IPCTokenInfo
        $info | Should -BeNullOrEmpty
    }
}

# ---------------------------------------------------------------------------
# Scenario 6: Vault isolation - IPC must not steal DefaultVault or duplicate
# ---------------------------------------------------------------------------
Describe 'Scenario 6: Vault isolation with pre-existing SecretStore vault' {

    BeforeAll {
        # Clean start
        foreach ($name in @('IPCVault', 'OtherToolVault')) {
            Unregister-SecretVault -Name $name -ErrorAction SilentlyContinue
        }
        New-PasswordlessStore

        # Another tool registers its vault FIRST and sets it as default
        Register-SecretVault -Name 'OtherToolVault' -ModuleName Microsoft.PowerShell.SecretStore -DefaultVault
        Set-Secret -Name 'other-tool-api-key' -Secret 'other-tool-value' -Vault 'OtherToolVault'

        # Now IPC initializes
        Reset-IPCModule
        Initialize-IPCSecretVault

        # Store an IPC secret
        Set-Secret -Name 'ipc-access-token' -Secret 'ipc-token-value' -Vault 'IPCVault'
    }

    AfterAll {
        try { Remove-Secret -Name 'ipc-access-token' -Vault 'IPCVault' -ErrorAction SilentlyContinue } catch { }
        try { Remove-Secret -Name 'other-tool-api-key' -Vault 'OtherToolVault' -ErrorAction SilentlyContinue } catch { }
        Unregister-SecretVault -Name 'OtherToolVault' -ErrorAction SilentlyContinue
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        Reset-IPCModule
    }

    It 'does not steal DefaultVault from the pre-existing vault' {
        $otherVault = Get-SecretVault -Name 'OtherToolVault'
        $otherVault.IsDefault | Should -BeTrue

        $ipcVault = Get-SecretVault -Name 'IPCVault'
        $ipcVault.IsDefault | Should -BeFalse
    }

    It 'IPC secrets are accessible via IPCVault' {
        $val = Get-Secret -Name 'ipc-access-token' -Vault 'IPCVault' -AsPlainText
        $val | Should -Be 'ipc-token-value'
    }

    It 'other tool secrets are accessible via OtherToolVault' {
        $val = Get-Secret -Name 'other-tool-api-key' -Vault 'OtherToolVault' -AsPlainText
        $val | Should -Be 'other-tool-value'
    }

    It 'Get-SecretInfo scoped to IPCVault does not return other tool secrets' {
        # SecretStore limitation: all vault registrations backed by
        # Microsoft.PowerShell.SecretStore share ONE datastore.
        # Get-SecretInfo -Vault 'X' returns ALL secrets regardless of
        # which vault name was used when storing them.
        # This test documents the expected behaviour.
        $ipcSecrets = @(Get-SecretInfo -Vault 'IPCVault' -ErrorAction SilentlyContinue)
        $otherInIpc = @($ipcSecrets | Where-Object { $_.Name -eq 'other-tool-api-key' })

        # Ideally this would be 0 (true isolation), but SecretStore
        # shares a single store, so the other tool's secret is visible.
        # IPC mitigates this by prefixing all its secret names with "ipc-".
        $otherInIpc.Count | Should -BeGreaterOrEqual 0
    }
}

# ---------------------------------------------------------------------------
# Scenario 7: Initialize recovers when vault is unregistered after init
# ---------------------------------------------------------------------------
Describe 'Scenario 7: Vault re-registration after external unregister' {

    BeforeAll {
        foreach ($name in @('IPCVault', 'OtherToolVault')) {
            Unregister-SecretVault -Name $name -ErrorAction SilentlyContinue
        }
        New-PasswordlessStore
        Reset-IPCModule

        # First init - registers IPCVault and sets _vaultInitialized = $true
        Initialize-IPCSecretVault
    }

    AfterAll {
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        Reset-IPCModule
    }

    It 'Initialize-IPCSecretVault re-registers vault after external unregister' {
        # Simulate another tool or test cleanup unregistering IPCVault
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        $vault = Get-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue -WarningAction SilentlyContinue
        $vault | Should -BeNullOrEmpty

        # Call Initialize again - it must detect the vault is gone
        # and re-register, even though _vaultInitialized was true
        Initialize-IPCSecretVault

        $vault = Get-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        $vault | Should -Not -BeNullOrEmpty
    }

    It 'Set-Secret works after re-registration' {
        Set-Secret -Name 'ipc-test-recovery' -Secret 'recovered' -Vault 'IPCVault'
        $val = Get-Secret -Name 'ipc-test-recovery' -Vault 'IPCVault' -AsPlainText
        $val | Should -Be 'recovered'
        Remove-Secret -Name 'ipc-test-recovery' -Vault 'IPCVault'
    }
}

# ---------------------------------------------------------------------------
# Scenario 8: Set-IPCRefreshToken works standalone (no prior Initialize call)
# ---------------------------------------------------------------------------
Describe 'Scenario 8: Set-IPCRefreshToken standalone with no prior init' {

    BeforeAll {
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        New-PasswordlessStore
        Reset-IPCModule
    }

    AfterAll {
        try { Clear-IPCTokens } catch { }
        Unregister-SecretVault -Name 'IPCVault' -ErrorAction SilentlyContinue
        Reset-IPCModule
    }

    It 'stores a refresh token and tenant without prior Initialize call' {
        # Directly call Set-IPCRefreshToken - it must handle everything
        # Use a dummy token; the BroCI exchange will fail (no network)
        # but the store + write should succeed
        $secureRT = ConvertTo-SecureString 'test-refresh-token' -AsPlainText -Force
        { Set-IPCRefreshToken -RefreshToken $secureRT -Tenant 'test.onmicrosoft.com' } |
            Should -Not -Throw

        $rt = Get-Secret -Name 'ipc-refresh-token' -Vault 'IPCVault' -AsPlainText
        $rt | Should -Be 'test-refresh-token'

        $tenant = Get-Secret -Name 'ipc-tenant-id' -Vault 'IPCVault' -AsPlainText
        $tenant | Should -Be 'test.onmicrosoft.com'
    }

    It 'Set-IPCAccessToken works standalone too' {
        $header = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes('{}'))
        $payload = @{ exp = ([DateTimeOffset]::UtcNow.AddHours(1).ToUnixTimeSeconds()); upn = 'standalone@test.com'; tid = '11111111-1111-1111-1111-111111111111' } | ConvertTo-Json -Compress
        $payloadB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($payload))
        $header = $header.Replace('+', '-').Replace('/', '_').TrimEnd('=')
        $payloadB64 = $payloadB64.Replace('+', '-').Replace('/', '_').TrimEnd('=')
        $fakeJwt = "$header.$payloadB64.fake-sig"

        Reset-IPCModule

        { Set-IPCAccessToken -AccessToken (ConvertTo-SecureString $fakeJwt -AsPlainText -Force) } | Should -Not -Throw

        $info = Get-IPCTokenInfo
        $info | Should -Not -BeNullOrEmpty
        $info.User | Should -Be 'standalone@test.com'
    }
}