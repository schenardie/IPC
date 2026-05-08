# IPC

[![PSGallery](https://img.shields.io/powershellgallery/v/IPC?label=PSGallery&logo=powershell)](https://www.powershellgallery.com/packages/IPC)

**IPC (Intune Properties Catalog)** — an interactive CLI and PowerShell module for querying hardware and software inventory from Intune managed devices via the Microsoft Graph beta API.

No Azure app registration is required. IPC uses Microsoft Intune's own well-known public client ID, so it works with any Entra ID tenant where a user holds at least the **Intune Read Only** role.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Vault setup](#vault-setup)
- [Vault security](#vault-security)
- [Authentication](#authentication)
  - [Option 1a — Access token (from Network tab)](#option-1a--access-token-from-network-tab)
  - [Option 1b — Refresh token (from Session Storage)](#option-1b--refresh-token-from-session-storage)
  - [Option 1c — Clear all tokens](#option-1c--clear-all-tokens)
- [Usage — CLI](#usage--cli)
  - [Menu options](#menu-options)
  - [Device inventory](#device-inventory)
  - [Software inventory](#software-inventory)
- [Usage — PowerShell module](#usage--powershell-module)
- [Usage — AI agent (Invoke-IPC)](#usage--ai-agent-invoke-ipc)
- [Running tests](#running-tests)
- [Permissions](#permissions)

---

## Requirements

- **PowerShell 7.0** or later (cross-platform: Windows & macOS)
- An Intune-managed tenant with at least **Intune Read Only** permissions
- The following PowerShell modules (auto-installed on first run):
  - `Microsoft.PowerShell.SecretManagement`
  - `Microsoft.PowerShell.SecretStore`

> **SecretStore:** On first run IPC will ask whether to protect the vault with a password. Choose **No** for seamless, agent-friendly operation. Choose **Yes** for encrypted-at-rest storage — you must run `Unlock-IPCVault` before each agent session.

---

## Installation

```powershell
git clone https://github.com/schenardie/IPC.git
cd IPC
```

No build step required — run the CLI directly or import the module.

---

## Vault setup

The first time you run `./cli/Start-IPC.ps1`, IPC sets up a **SecretStore vault** to hold your tokens securely. You will see a one-time prompt:

```
  ┌─ IPC Vault Setup ───────────────────────────────────┐
  │                                                      │
  │  Protect the secret vault with a password?           │
  │                                                      │
  │  [N] No  — passwordless, always seamless,            │
  │           works with AI agents/skills out of the box │
  │                                                      │
  │  [y] Yes — encrypted vault; you must run             │
  │           Unlock-IPCVault before each agent session  │
  │                                                      │
  └──────────────────────────────────────────────────────┘
```

| Choice | Behaviour | Best for |
|--------|-----------|----------|
| **No (default)** | Vault is never locked — tokens are always accessible without a prompt | Most users, AI agent / Copilot skill use |
| **Yes** | Vault is encrypted with a password you set now. Run `Unlock-IPCVault` in your terminal once before using the IPC agent or skill each session. The vault stays unlocked for 8 hours. | High-security environments where you want stored tokens encrypted at rest |

> This prompt appears **once only**. The choice is persisted by SecretStore. If you later want to change it, see [Resetting the vault](#resetting-the-vault) below.

### Unlocking a password-protected vault

If you chose a password, unlock the vault before starting an agent or skill session:

```powershell
Import-Module ./IPC/IPC.psm1
Unlock-IPCVault    # prompts for your password, stays unlocked for 8 hours
```

### Resetting the vault

To wipe the vault and start over (e.g. to change the password setting):

```powershell
# PowerShell 7
Import-Module Microsoft.PowerShell.SecretStore
Remove-SecretStore -Force
```

Or delete the store files directly (macOS/Linux):

```bash
rm -rf ~/.secretmanagement
```

> ⚠ This removes all stored tokens. Re-enter them via options `1a` or `1b` after the reset.

---

## Vault security

### The short answer

**Your tokens are encrypted on disk regardless of whether you set a vault password or not.** Choosing "no password" does not mean "no encryption" — it means the encryption key is managed by your operating system rather than by a separate password you type.

### How SecretStore encrypts your data

`Microsoft.PowerShell.SecretStore` is an open-source module published by Microsoft. It always encrypts vault contents using **AES-256** before writing anything to disk. What differs between the two modes is how the AES key itself is protected:

| Mode | How the AES key is protected |
|------|------------------------------|
| **Passwordless** | Windows: key is wrapped by **DPAPI** (Data Protection API), tied to your Windows user account and machine. macOS/Linux: key file stored at `~/.secretmanagement/` with **`600` permissions** (owner read/write only). |
| **Password-protected** | The AES key is derived from your vault password using a key-derivation function. Without the password the key cannot be reconstructed. |

In both cases the token data on disk is ciphertext — opening the files in a hex editor reveals nothing useful.

### What passwordless protects against

- ✅ **Other users on the same machine** — DPAPI (Windows) and file permissions (macOS/Linux) prevent other OS accounts from reading the store files
- ✅ **Plain-text exposure** — tokens are never written to `.env` files, config files, shell history, or environment variables
- ✅ **Accidental leaks** — no risk of committing a secrets file to source control
- ✅ **Log/output scraping** — tokens stored in the vault are never echoed to the terminal

### What passwordless does not protect against

- ❌ **Processes running as your own user** — malware or a rogue script running in your user session can call the same SecretStore APIs and read the vault
- ❌ **Root/Administrator access** — a system administrator can read the key files on macOS/Linux (same limitation as macOS Keychain when unlocked)
- ❌ **Physical disk access** — an attacker with the raw disk and knowledge of the key file location could reconstruct the secrets (this is also true of most OS-level credential stores)

### Is passwordless appropriate for IPC tokens?

Yes, for the vast majority of users. The tokens IPC stores are **short-lived OAuth bearer tokens** (access tokens expire in ~1 hour; refresh tokens used by IPC expire in ~24 hours). Even in the worst case where a token is obtained, the attacker has a narrow window before it expires and Intune Read Only permissions are the blast radius.

Passwordless SecretStore is equivalent in security to your browser's saved-password store, macOS Keychain in an unlocked session, or Windows Credential Manager — all of which are standard practice for credential storage.

Use the **password-protected** option if you are on a shared or managed machine where other administrators may have access to your user profile, or if your security policy explicitly requires credentials to be encrypted with a secret not derived from your OS login.

---

## Authentication

IPC supports two authentication methods. Only one is active at a time — storing a new token clears the other to prevent cross-tenant issues.

### Option 1a — Access token (from Network tab)

Short-lived token that lasts until it expires (typically ~1 hour). No auto-refresh.

1. Open [https://intune.microsoft.com](https://intune.microsoft.com) in your browser and sign in.
2. Open browser DevTools (F12) → **Network** tab.
3. Filter for requests to `graph.microsoft.com` and copy the `Authorization: Bearer <token>` value.
4. Start IPC and use **option 1a** to paste the token.

### Option 1b — Refresh token (from Session Storage)

Long-lived token that allows IPC to automatically acquire fresh access tokens via the BroCI (Nested App Authentication) flow. As long as you refresh at least once every 24 hours, the session stays alive indefinitely.

1. Open [https://intune.microsoft.com](https://intune.microsoft.com) in your browser and sign in.
2. Open browser DevTools (F12) → **Application** tab → **Session Storage**.
3. Look for an MSAL entry with `credentialType: "RefreshToken"`.
4. Copy the `secret` field value.
5. Start IPC and use **option 1b**.
6. Enter your tenant domain (e.g. `contoso.onmicrosoft.com`) or tenant GUID.
7. Paste the refresh token secret.

IPC exchanges the refresh token for a fresh Intune access token using the Azure Portal as a broker. The refresh token is rotated on each exchange, so the stored token is always up to date.

### Option 1c — Clear all tokens

Removes all stored tokens (access, refresh, metadata, tenant) from the vault. Use this when switching tenants or accounts.

Tokens are stored securely using the PowerShell `SecretStore` vault (encrypted, cross-platform).

---

## Usage — CLI

```powershell
./src/Start-IPC.ps1
```

### Menu options

```
╔══════════════════════════════════════════════════╗
║           IPC – Device Inventory            ║
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
Import-Module ./src/IPC.psm1

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

## Usage — AI agent (Invoke-IPC)

### Quick start — importing the skill into your agent

```powershell
# 1. Import the module
Import-Module ./src/IPC.psm1

# 2. Authenticate (choose one method)

# Method A — Refresh token (recommended for agents, auto-refreshes for 24h)
Set-IPCRefreshToken -RefreshToken '<secret from Session Storage>' -Tenant 'contoso.onmicrosoft.com'

# Method B — Access token (quick but expires in ~1 hour, no auto-refresh)
Set-IPCAccessToken -AccessToken 'eyJ...'

# 3. You're ready — call Invoke-IPC
Invoke-IPC -Action ListDevices
```

### How authentication works for agents

| Scenario | What happens | What you need to do |
|----------|-------------|-------------------|
| **First time** | No token stored | Authenticate with Method A or B above |
| **Vault is password-protected** | Vault locked — agent calls will fail | Run `Unlock-IPCVault` in your terminal first |
| **Within 1 hour** | Access token is valid | Nothing — calls work automatically |
| **After 1 hour (with refresh token)** | Access token expired | Nothing — auto-refreshes silently via BroCI |
| **After 24 hours (with refresh token)** | Refresh token expired | Re-authenticate: get a fresh refresh token from Session Storage |
| **After 1 hour (access token only)** | Access token expired, no refresh | Re-authenticate: paste a new access token |
| **Switching tenants** | Tokens from wrong tenant | Run `Clear-IPCTokens` then re-authenticate |

### Re-authenticating when tokens expire

If you're using a **refresh token** (Method A) and it's been more than 24 hours:

```powershell
# Clear the expired tokens
Clear-IPCTokens

# Get a fresh refresh token from Session Storage and store it
Set-IPCRefreshToken -RefreshToken '<new secret>' -Tenant 'contoso.onmicrosoft.com'
```

If you're using an **access token** (Method B) and it expired:

```powershell
# Just paste a new one — it automatically replaces the old one
Set-IPCAccessToken -AccessToken 'eyJ...'
```

### Checking token status programmatically

```powershell
$info = Get-IPCTokenInfo
if (-not $info) {
    Write-Host "No token — need to authenticate"
} elseif ($info.Expired -and -not $info.HasRefresh) {
    Write-Host "Token expired — need a fresh access token"
} elseif ($info.Expired -and $info.HasRefresh) {
    Write-Host "Token expired — will auto-refresh on next call"
} else {
    Write-Host "Token valid for $($info.ExpiresIn)"
}
```

### Example queries

```powershell
# "Show me all MSI software on computer1"
Invoke-IPC -Action SoftwareInventory -DeviceName 'computer1' -Filter 'msi'

# "Check BIOS info for all devices"
Invoke-IPC -Action HardwareInventory -AllDevices -Category 'bios'

# "Full software inventory of computer2"
Invoke-IPC -Action SoftwareInventory -DeviceName 'computer2'

# "List all devices matching LAPTOP"
Invoke-IPC -Action ListDevices -DeviceName 'LAPTOP'

# "What inventory categories are available?"
Invoke-IPC -Action ListCategories -DeviceName 'computer1'

# "Show processor and memory for all devices"
Invoke-IPC -Action HardwareInventory -AllDevices -Category 'processor','memory'

# "Find Chrome across all devices"
Invoke-IPC -Action SoftwareInventory -AllDevices -Filter 'Chrome'
```

See [SKILL.md](SKILL.md) for the full AI agent manifest with parameter reference, category list, and natural language → function call mappings.

---

## Running tests

Requires [Pester](https://pester.dev) (v5+):

```powershell
Install-Module Pester -Scope CurrentUser -Force
Invoke-Pester ./tests/IPC.Tests.ps1 -Output Detailed
```

---

## Permissions

IPC uses Microsoft Intune's own public client ID (`5926fc8e-304e-4f59-8bed-58ca97cc39a4`). No custom Azure app registration is needed.

The refresh token flow uses the Azure Portal application (`c44b4083-3bb0-49c1-b47d-974e53cbdf3c`) as a broker via the BroCI (Nested App Authentication) exchange.

The signed-in user must have at least the **Microsoft Intune Read Only Operator** (or equivalent) role in Entra ID to query device inventory data.

---

## License

MIT
