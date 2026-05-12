@{
    RootModule        = 'IPC.psm1'
    ModuleVersion     = '1.0.1'
    GUID              = 'b3f7c8a1-4e2d-4f9b-a6c1-8d5e3f2a7b90'
    Author            = 'schenardie'
    CompanyName       = 'schenardie'
    Copyright         = '(c) 2026 schenardie. All rights reserved.'
    Description       = 'IPC (Intune Properties Catalog) - query hardware and software inventory from Intune managed devices via Microsoft Graph beta API. No Azure app registration required.'
    PowerShellVersion = '7.0'
    RequiredModules   = @(
        @{ ModuleName = 'Microsoft.PowerShell.SecretManagement'; ModuleVersion = '1.0.0' }
        @{ ModuleName = 'Microsoft.PowerShell.SecretStore';       ModuleVersion = '1.0.0' }
    )
    FunctionsToExport = @(
        'Initialize-IPCSecretVault'
        'Unlock-IPCVault'
        'Set-IPCAccessToken'
        'Set-IPCRefreshToken'
        'Clear-IPCTokens'
        'Get-IPCTokenInfo'
        'Get-IPCManagedDevices'
        'Get-IPCManagedDevice'
        'Get-IPCDeviceInventoryCategories'
        'Get-IPCDeviceInventory'
        'Get-IPCSoftwareInventory'
        'Get-IPCInventoryBatch'
        'Get-IPCSoftwareInventoryBatch'
        'Invoke-IPC'
    )
    AliasesToExport   = @()
    CmdletsToExport   = @()
    VariablesToExport = @()
    PrivateData       = @{
        PSData = @{
            Tags        = @('Intune', 'Graph', 'Inventory', 'DeviceManagement', 'IPC', 'MicrosoftGraph')
            LicenseUri  = 'https://github.com/schenardie/IPC/blob/main/LICENSE'
            ProjectUri  = 'https://github.com/schenardie/IPC'
            ReleaseNotes = 'v1.0.0: Renamed module to IPC for PowerShell Gallery publication. Refactored function names (Get-IPCManagedDevices, Get-IPCDeviceInventory, etc.), improved auth flow, batch support, Unlock-IPCVault vault guard, MIT license.'
        }
    }
}
