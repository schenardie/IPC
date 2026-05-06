@{
    RootModule        = 'IPCSkill.psm1'
    ModuleVersion     = '0.2.0'
    GUID              = 'b3f7c8a1-4e2d-4f9b-a6c1-8d5e3f2a7b90'
    Author            = 'schenardie'
    Description       = 'Intune Properties Catalog Skill — query hardware and software inventory from Intune managed devices via Microsoft Graph beta API.'
    PowerShellVersion = '7.0'
    FunctionsToExport = @(
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
        'Invoke-IPCSkill'
        'Get-IPCManagedDevices'
        'Get-IPCManagedDevice'
        'Get-IPCDeviceInventoryCategories'
        'Get-IPCDeviceInventory'
        'Get-IPCSoftwareInventory'
        'Get-IPCInventoryBatch'
        'Get-IPCSoftwareInventoryBatch'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()
    PrivateData       = @{
        PSData = @{
            Tags       = @('Intune', 'Graph', 'Inventory', 'DeviceManagement')
            LicenseUri = 'https://github.com/schenardie/IPCSkill/blob/main/LICENSE'
            ProjectUri = 'https://github.com/schenardie/IPCSkill'
        }
    }
}
