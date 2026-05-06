@{
    RootModule        = 'IPC.psm1'
    ModuleVersion     = '1.0.0'
    GUID              = 'b3f7c8a1-4e2d-4f9b-a6c1-8d5e3f2a7b90'
    Author            = 'schenardie'
    CompanyName       = 'schenardie'
    Copyright         = '(c) 2025 schenardie. All rights reserved.'
    Description       = 'IPC (Intune Properties Catalog) — query hardware and software inventory from Intune managed devices via Microsoft Graph beta API. No Azure app registration required.'
    PowerShellVersion = '7.0'
    RequiredModules   = @(
        @{ ModuleName = 'Microsoft.PowerShell.SecretManagement'; ModuleVersion = '1.0.0' }
        @{ ModuleName = 'Microsoft.PowerShell.SecretStore';       ModuleVersion = '1.0.0' }
    )
    FunctionsToExport = @(
        'Set-IPCAccessToken'
        'Set-IPCRefreshToken'
        'Clear-IPCTokens'
        'Get-IPCTokenInfo'
        'Get-IPCDevice'
        'Get-IPCDeviceDetail'
        'Get-IPCInventoryCategory'
        'Get-IPCInventory'
        'Get-IPCSoftware'
        'Invoke-IPCSkill'
    )
    AliasesToExport   = @('Get-IPCDevices')
    CmdletsToExport   = @()
    VariablesToExport = @()
    PrivateData       = @{
        PSData = @{
            Tags        = @('Intune', 'Graph', 'Inventory', 'DeviceManagement', 'IPC', 'MicrosoftGraph')
            LicenseUri  = 'https://github.com/schenardie/IPCSkill/blob/main/LICENSE'
            ProjectUri  = 'https://github.com/schenardie/IPCSkill'
            ReleaseNotes = 'v1.0.0: Renamed module to IPC for PowerShell Gallery publication. Friendly function names, restructured for PSGallery, MIT license added.'
        }
    }
}
