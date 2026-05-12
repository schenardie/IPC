---
name: IPC
description: Query hardware and software inventory from Microsoft Intune managed devices via the Graph beta API. No Azure app registration required.
version: 1.0.0
author: schenardie
tags: [intune, graph, inventory, devicemanagement]
tools:
  - powershell
---

# IPC - AI Agent Skill Manifest

## Overview

IPC queries hardware and software inventory from Microsoft Intune managed devices via the Graph beta API. It requires no Azure app registration - it uses Intune's own public client ID.

**Primary function:** `Invoke-IPC`

## Setup

> **IMPORTANT - PowerShell 7 required.** This module uses `#Requires -Version 7.0`. The default `powershell` tool often launches Windows PowerShell 5.1, which will fail. **Always run commands through `pwsh`:**
>
> ```
> pwsh -Command "Import-Module ./IPC/IPC.psm1; <your commands here>"
> ```
>
> If `pwsh` returns "Access is denied", try the full path: `& 'C:\Users\<user>\AppData\Local\Microsoft\WindowsApps\pwsh.exe' -Command "..."` or locate it with `Get-Command pwsh.exe`.

```powershell
# All commands must run inside pwsh (PowerShell 7)
pwsh -Command "Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action ListDevices"
```

> **Authentication is handled by the user, not the agent.** The user stores tokens interactively via the CLI (`./cli/Start-IPC.ps1`) or `Read-Host -AsSecureString` before invoking the agent. The agent only calls query functions (`Invoke-IPC`, `Get-IPCTokenInfo`) - it must **never** ask for, accept, or handle tokens directly.

> **If the agent gets a "No token" or "expired" error**, instruct the user to authenticate interactively:
> 1. Run `./cli/Start-IPC.ps1` and use option **1b** (refresh token) or **1a** (access token).
> 2. Then retry the agent query.

> **Vault requirement:** The vault must be **passwordless** for agent use. The agent spawns a new PowerShell process per call - vault unlock state is process-scoped and cannot be shared from the user's terminal. **If a user reports that the agent fails with a vault-locked error, instruct them to reset the vault and choose No (passwordless) at the setup prompt.**

## Invoke-IPC Parameters

| Parameter    | Type     | Required | Description |
|-------------|----------|----------|-------------|
| Action      | string   | Yes      | `ListDevices`, `HardwareInventory`, `SoftwareInventory`, `ListCategories` |
| DeviceName  | string   | No*      | Partial device name to search (e.g. `'LAPTOP'`, `'computer1'`) |
| DeviceId    | string   | No*      | Exact Intune device GUID |
| AllDevices  | switch   | No*      | Target all Windows managed devices |
| Category    | string[] | No       | Hardware inventory categories (e.g. `'bios'`, `'battery'`). Use `'all'` for everything. Only for `HardwareInventory`. |
| Filter      | string   | No       | Text filter applied to results (case-insensitive match on any property value) |
| Top         | int      | No       | Max devices to return (default 100) |

*One of `DeviceName`, `DeviceId`, or `AllDevices` is required for all actions except `ListDevices`.

## Actions

### ListDevices
Returns matching managed devices with id, name, OS, and compliance state.

### ListCategories
Returns the inventory category IDs available for a device (e.g. `bios`, `battery`, `diskDrive`, `processor`, `operatingSystem`).

### HardwareInventory
Fetches hardware inventory for specified categories. Each category returns instances with friendly-named properties (Title Case).

### SoftwareInventory
Fetches installed applications from the `ApplicationProperties` inventory category. Each application has properties like `Display Name`, `Version`, `Publisher`, `Install Date`.

## Return Format

All actions return a hashtable with:
- `Action` - the action performed
- `DeviceCount` - number of devices in results
- `Results` - the data (structure varies by action)

For single-device results, `Results` is unwrapped (no device-name wrapper).
For multi-device results, `Results` is keyed by device name.

## Common Inventory Categories

| Category ID         | Description |
|--------------------|-------------|
| `bios`             | BIOS/UEFI firmware details |
| `battery`          | Battery health, cycle count, capacity |
| `diskDrive`        | Physical disk drives |
| `logicalDisk`      | Logical disk partitions |
| `memory`           | RAM modules |
| `networkAdapter`   | Network adapters |
| `operatingSystem`  | OS name, version, build |
| `processor`        | CPU details |
| `systemEnclosure`  | Chassis type, serial number |
| `windowsQfe`       | Installed Windows updates |

> **Note:** Available categories vary by device. Use `ListCategories` to discover what's available for a specific device.

## Example Queries → Function Calls

> **Remember:** All commands must be wrapped in `pwsh -Command "Import-Module ./IPC/IPC.psm1; ..."`

| Natural Language Query | PowerShell Call (inside pwsh -Command) |
|----------------------|-----------------|
| "Show me all MSI software on computer1" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action SoftwareInventory -DeviceName 'computer1' -Filter 'msi'` |
| "Check BIOS info for all devices" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action HardwareInventory -AllDevices -Category 'bios'` |
| "Full software inventory of computer2" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action SoftwareInventory -DeviceName 'computer2'` |
| "What devices do I have?" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action ListDevices` |
| "List devices matching LAPTOP" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action ListDevices -DeviceName 'LAPTOP'` |
| "Battery health for device abc-123" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action HardwareInventory -DeviceId 'abc-123' -Category 'battery'` |
| "Show processor and memory for all devices" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action HardwareInventory -AllDevices -Category 'processor','memory'` |
| "What inventory categories exist for computer1?" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action ListCategories -DeviceName 'computer1'` |
| "Find Chrome in software inventory across all devices" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action SoftwareInventory -AllDevices -Filter 'Chrome'` |
| "Show all hardware info for computer1" | `Import-Module ./IPC/IPC.psm1; Invoke-IPC -Action HardwareInventory -DeviceName 'computer1' -Category 'all'` |

## Error Handling

- **Token expired:** If a refresh token is stored, access tokens are automatically refreshed. Otherwise throws an error suggesting the user re-authenticate.
- **Device not found:** Returns `DeviceCount = 0` with empty `Results`.
- **Category not available:** Skipped silently in batch results.
- **API throttling:** Automatically retried with `Retry-After` delays (up to 5 retries).

## Lower-Level Functions

For advanced usage, the module also exports individual functions (all must run inside `pwsh`):

```powershell
# Direct device lookup
pwsh -Command "Import-Module ./IPC/IPC.psm1; Get-IPCManagedDevices -Filter \"startswith(deviceName,'LAPTOP')\""

# Direct inventory fetch
pwsh -Command "Import-Module ./IPC/IPC.psm1; Get-IPCDeviceInventory -DeviceId '$deviceId' -Category 'battery'"

# Direct software inventory
pwsh -Command "Import-Module ./IPC/IPC.psm1; Get-IPCSoftwareInventory -DeviceId '$deviceId'"

# Batch operations
pwsh -Command "Import-Module ./IPC/IPC.psm1; Get-IPCInventoryBatch -DeviceIds @('$id1', '$id2') -Categories @('bios', 'battery')"
```
