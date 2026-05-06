# IPCSkill

**Intune Properties Catalog Skill** — an interactive CLI and PowerShell module for querying hardware and software inventory from Intune managed devices via the Microsoft Graph beta API.

No Azure app registration is required. IPCSkill uses Microsoft Intune's own well-known public client ID, so it works with any Entra ID tenant where a user holds at least the **Intune Read Only** role.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Authentication](#authentication)
  - [Option 1 — Access token (from Network tab)](#option-1--access-token-from-network-tab)
  - [Option 2 — Refresh token (from Session Storage)](#option-2--refresh-token-from-session-storage)
- [Usage — CLI](#usage--cli)
  - [Menu options](#menu-options)
  - [Device inventory](#device-inventory)
  - [Software inventory](#software-inventory)
- [Usage — PowerShell module](#usage--powershell-module)
- [Running tests](#running-tests)
- [Permissions](#permissions)

---

## Requirements

- **PowerShell 7.0** or later (cross-platform: Windows & macOS)
- An Intune-managed tenant with at least **Intune Read Only** permissions
- The following PowerShell modules (auto-installed on first run):
  - `Microsoft.PowerShell.SecretManagement`
  - `Microsoft.PowerShell.SecretStore`

> **SecretStore password:** If you already have a SecretStore configured with a password (e.g. from another tool), IPCSkill will detect it and prompt you to enter your existing password to unlock the store. If this is your first time using SecretStore, IPCSkill configures it as passwordless automatically.

---

## Installation

```powershell
git clone https://github.com/schenardie/IPCSkill.git
cd IPCSkill
```

No build step required — run the CLI directly or import the module.

---

## Authentication

IPCSkill supports two authentication methods. Both are based on tokens obtained from a browser session against Intune.

### Option 1 — Access token (from Network tab)

Short-lived token that lasts until it expires (typically ~1 hour).

1. Open [https://intune.microsoft.com](https://intune.microsoft.com) in your browser and sign in.
2. Open browser DevTools (F12) → **Network** tab.
3. Filter for requests to `graph.microsoft.com` and copy the `Authorization: Bearer <token>` value.
4. Start IPCSkill and use **option 1** to paste the token.

### Option 2 — Refresh token (from Session Storage)

Long-lived token that allows IPCSkill to automatically acquire fresh access tokens.

1. Open [https://intune.microsoft.com](https://intune.microsoft.com) in your browser and sign in.
2. Open browser DevTools (F12) → **Application** tab → **Session Storage**.
3. Look for an MSAL entry with `credentialType: "RefreshToken"`.
4. Copy the `secret` field value.
5. Start IPCSkill and use **option 2** to paste the refresh token.

Tokens are stored securely using the PowerShell `SecretStore` vault (encrypted, cross-platform).

---

## Usage — CLI

```powershell
./src/Start-IPCSkill.ps1
```

### Menu options

```
╔═══════════════════════════════════════════════╗
║          IPCSkill – Device Inventory          ║
╠═══════════════════════════════════════════════╣
║  1  Store access token  (from Network tab)    ║
║  2  Store refresh token (from Session Storage)║
║  3  Get device inventory                      ║
║  4  Get software inventory                    ║
║  q  Quit                                      ║
╚═══════════════════════════════════════════════╝
```

### Device inventory

Option **3** lets you:

1. Search for a Windows device by partial name (or paste a device GUID directly).
2. Choose one device or all matching devices.
3. Pick from the inventory categories available for that device (e.g. `battery`, `diskDrive`, `processor`, `operatingSystem`).
4. Select individual categories or `all`.

Results are printed as JSON and can optionally be copied to the clipboard.

### Software inventory

Option **4** queries the `ApplicationProperties` inventory category, which returns all installed applications on a device. It uses the Graph endpoint:

```
GET /beta/deviceManagement/managedDevices('{id}')/deviceInventories('ApplicationProperties')
    ?$expand=instances($expand=Microsoft.Graph.deviceInventorySimpleItem/properties)
```

Results are printed as JSON (one object per installed application) and can optionally be copied to the clipboard.

---

## Usage — PowerShell module

```powershell
Import-Module ./src/IPCSkill.psm1

# Store a token (retrieved from browser DevTools)
Set-IPCAccessToken -AccessToken 'eyJ...'

# Or store a refresh token for auto-refresh
Set-IPCRefreshToken -RefreshToken '<secret from Session Storage>'

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

## Running tests

Requires [Pester](https://pester.dev) (v5+):

```powershell
Install-Module Pester -Scope CurrentUser -Force
Invoke-Pester ./tests/IPCSkill.Tests.ps1 -Output Detailed
```

---

## Permissions

IPCSkill uses Microsoft Intune's own public client ID (`5926fc8e-304e-4f59-8bed-58ca97cc39a4`). No custom Azure app registration is needed.

The signed-in user must have at least the **Microsoft Intune Read Only Operator** (or equivalent) role in Entra ID to query device inventory data.

---

## License

MIT
