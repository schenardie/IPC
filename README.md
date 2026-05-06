# IPCSkill

**Intune Properties Catalog Skill** — an interactive CLI and PowerShell module for querying hardware and software inventory from Intune managed devices via the Microsoft Graph beta API.

No Azure app registration is required. IPCSkill uses Microsoft Intune's own well-known public client ID, so it works with any Entra ID tenant where a user holds at least the **Intune Read Only** role.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Authentication](#authentication)
  - [Option 1a — Access token (from Network tab)](#option-1a--access-token-from-network-tab)
  - [Option 1b — Refresh token (from Session Storage)](#option-1b--refresh-token-from-session-storage)
  - [Option 1c — Clear all tokens](#option-1c--clear-all-tokens)
- [Usage — CLI](#usage--cli)
  - [Menu options](#menu-options)
  - [Device inventory](#device-inventory)
  - [Software inventory](#software-inventory)
- [Usage — PowerShell module](#usage--powershell-module)
- [Usage — AI agent (Invoke-IPCSkill)](#usage--ai-agent-invoke-ipcskill)
- [Running tests](#running-tests)
- [Permissions](#permissions)

---

## Requirements

- **PowerShell 7.0** or later (cross-platform: Windows & macOS)
- An Intune-managed tenant with at least **Intune Read Only** permissions
- The following PowerShell modules (auto-installed on first run):
  - `Microsoft.PowerShell.SecretManagement`
  - `Microsoft.PowerShell.SecretStore`

> **SecretStore password:** If you already have a SecretStore configured with a password (e.g. from another tool), SecretStore will prompt you to enter your existing password once per session. If this is your first time using SecretStore, IPCSkill configures it as passwordless automatically.

---

## Installation

```powershell
git clone https://github.com/schenardie/IPCSkill.git
cd IPCSkill
```

No build step required — run the CLI directly or import the module.

---

## Authentication

IPCSkill supports two authentication methods. Only one is active at a time — storing a new token clears the other to prevent cross-tenant issues.

### Option 1a — Access token (from Network tab)

Short-lived token that lasts until it expires (typically ~1 hour). No auto-refresh.

1. Open [https://intune.microsoft.com](https://intune.microsoft.com) in your browser and sign in.
2. Open browser DevTools (F12) → **Network** tab.
3. Filter for requests to `graph.microsoft.com` and copy the `Authorization: Bearer <token>` value.
4. Start IPCSkill and use **option 1a** to paste the token.

### Option 1b — Refresh token (from Session Storage)

Long-lived token that allows IPCSkill to automatically acquire fresh access tokens via the BroCI (Nested App Authentication) flow. As long as you refresh at least once every 24 hours, the session stays alive indefinitely.

1. Open [https://intune.microsoft.com](https://intune.microsoft.com) in your browser and sign in.
2. Open browser DevTools (F12) → **Application** tab → **Session Storage**.
3. Look for an MSAL entry with `credentialType: "RefreshToken"`.
4. Copy the `secret` field value.
5. Start IPCSkill and use **option 1b**.
6. Enter your tenant domain (e.g. `contoso.onmicrosoft.com`) or tenant GUID.
7. Paste the refresh token secret.

IPCSkill exchanges the refresh token for a fresh Intune access token using the Azure Portal as a broker. The refresh token is rotated on each exchange, so the stored token is always up to date.

### Option 1c — Clear all tokens

Removes all stored tokens (access, refresh, metadata, tenant) from the vault. Use this when switching tenants or accounts.

Tokens are stored securely using the PowerShell `SecretStore` vault (encrypted, cross-platform).

---

## Usage — CLI

```powershell
./src/Start-IPCSkill.ps1
```

### Menu options

```
╔══════════════════════════════════════════════════╗
║           IPCSkill – Device Inventory            ║
╠══════════════════════════════════════════════════╣
║  1a  Store access token  (from Network tab)      ║
║  1b  Store refresh token (from Session Storage)  ║
║  1c  Clear all tokens                            ║
║  2   Get device inventory                        ║
║  3   Get software inventory                      ║
║  q   Quit                                        ║
╚══════════════════════════════════════════════════╝
```

The status display shows the current token state:

```
  Status : ✔  Valid
  Type   : Refresh (auto-refresh enabled)
  User   : admin@contoso.onmicrosoft.com
  Tenant : 73925f60-2387-41d1-a78c-d27c42472f24
  Expiry : 2026-05-06 02:10:01 UTC (1h 10m)
```

### Device inventory

Option **2** lets you:

1. Search for a Windows device by partial name (or paste a device GUID directly).
2. Choose one device or all matching devices.
3. Pick from the inventory categories available for that device (e.g. `battery`, `diskDrive`, `processor`, `operatingSystem`).
4. Select individual categories or `all`.

Results are printed as JSON and can optionally be copied to the clipboard.

### Software inventory

Option **3** queries the `ApplicationProperties` inventory category, which returns all installed applications on a device. It uses the Graph endpoint:

```
GET /beta/deviceManagement/managedDevices('{id}')/deviceInventories('ApplicationProperties')
    ?$expand=instances($expand=Microsoft.Graph.deviceInventorySimpleItem/properties)
```

Results are printed as JSON (one object per installed application) and can optionally be copied to the clipboard.

---

## Usage — PowerShell module

```powershell
Import-Module ./src/IPCSkill.psm1

# Store an access token (retrieved from browser DevTools Network tab)
Set-IPCAccessToken -AccessToken 'eyJ...'

# Or store a refresh token for auto-refresh (from Session Storage)
Set-IPCRefreshToken -RefreshToken '<secret from Session Storage>' -Tenant 'contoso.onmicrosoft.com'

# Clear all tokens (e.g. when switching tenants)
Clear-IPCTokens

# List available inventory categories for a device
$categories = Get-IPCDeviceInventoryCategories -DeviceId 'your-device-guid'
$categories | ForEach-Object { $_.id }

# Get hardware inventory for a specific category
$battery = Get-IPCDeviceInventory -DeviceId 'your-device-guid' -Category 'battery'
$battery | ConvertTo-Json -Depth 10

# Get software (application) inventory
$apps = Get-IPCSoftwareInventory -DeviceId 'your-device-guid'
$apps | ForEach-Object { "$($_.'Display Name') v$($_.'Version')" }
```

---

## Usage — AI agent (Invoke-IPCSkill)

`Invoke-IPCSkill` is a single entry-point function designed for AI agents. It combines device lookup and inventory retrieval in one call:

```powershell
# "Show me all MSI software on computer1"
Invoke-IPCSkill -Action SoftwareInventory -DeviceName 'computer1' -Filter 'msi'

# "Check BIOS info for all devices"
Invoke-IPCSkill -Action HardwareInventory -AllDevices -Category 'bios'

# "Full software inventory of computer2"
Invoke-IPCSkill -Action SoftwareInventory -DeviceName 'computer2'

# "List all devices matching LAPTOP"
Invoke-IPCSkill -Action ListDevices -DeviceName 'LAPTOP'

# "What inventory categories are available?"
Invoke-IPCSkill -Action ListCategories -DeviceName 'computer1'
```

See [SKILL.md](SKILL.md) for the full AI agent manifest with parameter reference, category list, and example query mappings.

---

## Running tests

Requires [Pester](https://pester.dev) (v5+):

```powershell
Install-Module Pester -Scope CurrentUser -Force
Invoke-Pester ./tests/IPCSkill.Tests.ps1 -Output Detailed
```

---

## Permissions

IPCSkill uses Microsoft Intune's own public client ID (`5926fc8e-304e-4f59-8bed-58ca97cc39a4`). No custom Azure app registration is needed.

The refresh token flow uses the Azure Portal application (`c44b4083-3bb0-49c1-b47d-974e53cbdf3c`) as a broker via the BroCI (Nested App Authentication) exchange.

The signed-in user must have at least the **Microsoft Intune Read Only Operator** (or equivalent) role in Entra ID to query device inventory data.

---

## License

MIT
