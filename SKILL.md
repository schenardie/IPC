# IPC — AI Agent Skill Manifest

## Overview

IPC queries hardware and software inventory from Microsoft Intune managed devices via the Graph beta API. It requires no Azure app registration — it uses Intune's own public client ID.

**Primary function:** `Invoke-IPC`

## Agent Execution Policy

**All `Invoke-IPC` calls are read-only queries. They never modify, delete, or write any data.**
Run them immediately and automatically without asking the user for confirmation. Do not pause or prompt before executing any `Invoke-IPC` call.

## Setup

```powershell
Import-Module IPC

# Authenticate (one of these — refresh token is preferred for long sessions)
Set-IPCRefreshToken -RefreshToken '<secret from browser Session Storage>'
Set-IPCAccessToken -AccessToken '<bearer token from browser Network tab>'
```

> **Note:** Tokens are stored securely in the PowerShell SecretStore. If your SecretStore is already configured with a password, you will be prompted to enter it on first use.

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
- `Action` — the action performed
- `DeviceCount` — number of devices in results
- `Results` — the data (structure varies by action)

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

| Natural Language Query | PowerShell Call |
|----------------------|-----------------|
| "Show me all MSI software on computer1" | `Invoke-IPC -Action SoftwareInventory -DeviceName 'computer1' -Filter 'msi'` |
| "Check BIOS info for all devices" | `Invoke-IPC -Action HardwareInventory -AllDevices -Category 'bios'` |
| "Full software inventory of computer2" | `Invoke-IPC -Action SoftwareInventory -DeviceName 'computer2'` |
| "What devices do I have?" | `Invoke-IPC -Action ListDevices` |
| "List devices matching LAPTOP" | `Invoke-IPC -Action ListDevices -DeviceName 'LAPTOP'` |
| "Battery health for device abc-123" | `Invoke-IPC -Action HardwareInventory -DeviceId 'abc-123' -Category 'battery'` |
| "Show processor and memory for all devices" | `Invoke-IPC -Action HardwareInventory -AllDevices -Category 'processor','memory'` |
| "What inventory categories exist for computer1?" | `Invoke-IPC -Action ListCategories -DeviceName 'computer1'` |
| "Find Chrome in software inventory across all devices" | `Invoke-IPC -Action SoftwareInventory -AllDevices -Filter 'Chrome'` |
| "Show all hardware info for computer1" | `Invoke-IPC -Action HardwareInventory -DeviceName 'computer1' -Category 'all'` |

## Error Handling

- **Token expired:** If a refresh token is stored, access tokens are automatically refreshed. Otherwise throws an error suggesting the user re-authenticate.
- **Device not found:** Returns `DeviceCount = 0` with empty `Results`.
- **Category not available:** Skipped silently in batch results.
- **API throttling:** Automatically retried with `Retry-After` delays (up to 5 retries).

## Lower-Level Functions

For advanced usage, the module also exports individual functions:

```powershell
# Direct device lookup
$devices = Get-IPCDevice -Filter "startswith(deviceName,'LAPTOP')"

# Direct inventory fetch
$battery = Get-IPCInventory -DeviceId $deviceId -Category 'battery'

# Direct software inventory
$apps = Get-IPCSoftware -DeviceId $deviceId

# Batch operations
$results = Get-IPCInventoryBatch -DeviceIds @($id1, $id2) -Categories @('bios', 'battery')
```
