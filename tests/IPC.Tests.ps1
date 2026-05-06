#Requires -Version 7.0
#Requires -Modules Pester
<#
.SYNOPSIS
    Pester tests for the IPC PowerShell module.
#>

BeforeAll {
    $modulePath = Join-Path $PSScriptRoot '..' 'IPC' 'IPC.psm1'
    Import-Module $modulePath -Force

    function New-FakeJwt {
        param([hashtable]$Payload)
        $header = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes('{}'))
        $payloadJson = $Payload | ConvertTo-Json -Compress
        $payloadB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($payloadJson))
        $header = $header.Replace('+', '-').Replace('/', '_').TrimEnd('=')
        $payloadB64 = $payloadB64.Replace('+', '-').Replace('/', '_').TrimEnd('=')
        return "$header.$payloadB64.fake-sig"
    }
}

Describe 'ConvertFrom-JwtPayload' {

    It 'extracts tid from a valid JWT' {
        $token = New-FakeJwt -Payload @{ tid = 'my-tenant-id'; oid = 'some-user' }
        $result = ConvertFrom-JwtPayload -Token $token
        $result['tid'] | Should -Be 'my-tenant-id'
    }

    It 'returns empty hashtable when tid is missing' {
        $token = New-FakeJwt -Payload @{ oid = 'some-user' }
        $result = ConvertFrom-JwtPayload -Token $token
        $result.ContainsKey('tid') | Should -BeFalse
    }

    It 'returns empty hashtable for garbage input' {
        (ConvertFrom-JwtPayload -Token 'not.a.jwt').Count | Should -Be 0
        (ConvertFrom-JwtPayload -Token 'garbage').Count | Should -Be 0
    }
}

Describe 'Resolve-AccessToken' {

    It 'extracts from Authorization header' {
        $jwt = New-FakeJwt -Payload @{ exp = 9999999999 }
        $result = Resolve-AccessToken -RawToken "Authorization: Bearer $jwt"
        $result | Should -Be $jwt
    }

    It 'extracts from quoted Bearer' {
        $jwt = New-FakeJwt -Payload @{ exp = 9999999999 }
        $result = Resolve-AccessToken -RawToken "`"Bearer $jwt`""
        $result | Should -Be $jwt
    }

    It 'strips Bearer prefix' {
        $jwt = New-FakeJwt -Payload @{ exp = 9999999999 }
        $result = Resolve-AccessToken -RawToken "Bearer $jwt"
        $result | Should -Be $jwt
    }

    It 'returns raw token when no prefix found' {
        $result = Resolve-AccessToken -RawToken 'my-plain-token'
        $result | Should -Be 'my-plain-token'
    }
}

Describe 'ConvertTo-FriendlyName' {
    It 'converts camelCase to Title Case' {
        ConvertTo-FriendlyName -Name 'cycleCount' | Should -Be 'Cycle Count'
    }

    It 'converts PascalCase to Title Case' {
        ConvertTo-FriendlyName -Name 'DesignedCapacity' | Should -Be 'Designed Capacity'
    }

    It 'handles single word' {
        ConvertTo-FriendlyName -Name 'manufacturer' | Should -Be 'Manufacturer'
    }
}

Describe 'ConvertTo-CleanInstance' {
    It 'strips OData fields and converts keys' {
        $instance = @{
            'id'           = 'inst1'
            '@odata.type'  = 'noise'
            'diskName'     = 'C:'
        }
        $result = ConvertTo-CleanInstance -Instance $instance
        $result.Contains('@odata.type') | Should -BeFalse
        $result['Disk Name'] | Should -Be 'C:'
        $result['Instance Name'] | Should -Be 'inst1'
    }

    It 'cleans battery-style instances' {
        $instance = @{
            'id'               = '{BFD21D0B}\SurfaceBattery'
            'cycleCount'       = 256
            'designedCapacity' = 47700
            'manufacturer'     = 'DYN'
            '@odata.type'      = '#microsoft.graph.battery'
        }
        $result = ConvertTo-CleanInstance -Instance $instance
        $result['Instance Name'] | Should -Be '{BFD21D0B}\SurfaceBattery'
        $result['Cycle Count'] | Should -Be 256
        $result['Designed Capacity'] | Should -Be 47700
        $result['Manufacturer'] | Should -Be 'DYN'
        $result.Contains('@odata.type') | Should -BeFalse
    }

    It 'parses embedded key=value pairs from instance name' {
        $instance = @{
            'id'          = 'PhysicalProcessorCount=1;ComputerName=CPC-jose-CIWHG6;HardwareModel=Virtual Machine'
            '@odata.type' = '#microsoft.graph.deviceInventorySimpleItem'
        }
        $result = ConvertTo-CleanInstance -Instance $instance
        $result['Physical Processor Count'] | Should -Be '1'
        $result['Computer Name'] | Should -Be 'CPC-jose-CIWHG6'
        $result['Hardware Model'] | Should -Be 'Virtual Machine'
    }

    It 'handles properties array (software inventory style)' {
        $instance = @{
            'id'         = 'app-1'
            'properties' = @(
                @{ displayName = 'appName'; value = 'Notepad++' }
                @{ displayName = 'version'; value = '8.6.1' }
            )
        }
        $result = ConvertTo-CleanInstance -Instance $instance
        $result['App Name'] | Should -Be 'Notepad++'
        $result['Version'] | Should -Be '8.6.1'
    }
}

Describe 'Invoke-IPCSkill' {
    It 'throws when multiple device selectors are provided' {
        { Invoke-IPCSkill -Action ListDevices -DeviceName 'test' -AllDevices } | Should -Throw '*only one*'
    }

    It 'throws when no device selector is provided for non-ListDevices action' {
        { Invoke-IPCSkill -Action SoftwareInventory } | Should -Throw '*Specify*'
    }

    It 'validates Action parameter values' {
        { Invoke-IPCSkill -Action 'InvalidAction' -DeviceName 'test' } | Should -Throw
    }

    It 'accepts valid Action parameter values' {
        $cmd = Get-Command Invoke-IPCSkill
        $validValues = $cmd.Parameters['Action'].Attributes | Where-Object { $_ -is [System.Management.Automation.ValidateSetAttribute] }
        foreach ($action in @('ListDevices', 'HardwareInventory', 'SoftwareInventory', 'ListCategories')) {
            $validValues.ValidValues | Should -Contain $action
        }
    }
}
