# IPCSkill

**Intune Properties Catalog Skill** — an interactive CLI and Python library for querying hardware and software inventory from Intune managed devices via the Microsoft Graph beta API.

No Azure app registration is required. IPCSkill uses Microsoft Intune's own well-known public client ID, so it works with any Entra ID tenant where a user holds at least the **Intune Read Only** role.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
  - [From source (pip)](#from-source-pip)
  - [Docker](#docker)
- [Authentication](#authentication)
- [Usage — CLI](#usage--cli)
  - [Menu options](#menu-options)
  - [Device inventory](#device-inventory)
  - [Software inventory](#software-inventory)
- [Usage — Python library](#usage--python-library)
- [Running tests](#running-tests)
- [Token sharing with ExplorerSkill](#token-sharing-with-explorerskill)
- [Permissions](#permissions)

---

## Requirements

- Python 3.10 or later  
- An Intune-managed tenant with at least **Intune Read Only** permissions  
- A valid Bearer token from the Microsoft Graph (`https://graph.microsoft.com`) audience (see [Authentication](#authentication))

---

## Installation

### From source (pip)

```bash
git clone https://github.com/schenardie/IPCSkill.git
cd IPCSkill
pip install .
```

For development (includes test dependencies):

```bash
pip install -e ".[dev]"
```

After installation, the `ipc-skill` command is available on your PATH.

### Docker

Build the image:

```bash
docker build -t ipc-skill .
```

Run interactively (tokens are persisted in the `explorer-skill-tokens` named volume, shared with [ExplorerSkill](https://github.com/schenardie/IntuneExplorerSkill) if you use both):

```bash
docker run -it --rm -v explorer-skill-tokens:/root/.explorer_skill ipc-skill
```

---

## Authentication

IPCSkill does **not** perform OAuth flows itself. You supply a raw Bearer token obtained from a browser session against Intune:

1. Open [https://intune.microsoft.com](https://intune.microsoft.com) in your browser and sign in.
2. Open browser DevTools → **Network** tab.
3. Filter for requests to `graph.microsoft.com` and copy the `Authorization: Bearer <token>` value.
4. Start IPCSkill and use **option 1** to paste the token.

Tokens are encrypted and stored under `~/.explorer_skill/` (the same location as ExplorerSkill, so you only need to authenticate once if you use both tools).

---

## Usage — CLI

```bash
ipc-skill
```

### Menu options

```
╔══════════════════════════════════════════╗
║         IPCSkill – Device Inventory      ║
╠══════════════════════════════════════════╣
║  1  Store a bearer token (paste)         ║
║  2  Get device inventory                 ║
║  3  Get software inventory               ║
║  q  Quit                                 ║
╚══════════════════════════════════════════╝
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

## Usage — Python library

```python
from ipc_skill import IPCSkillConfig, IPCExplorer

config = IPCSkillConfig()
ipc = IPCExplorer(config)

# Store a token once (retrieved from browser DevTools)
ipc.token_manager.store_token(access_token="eyJ...")

# List available inventory categories for a device
categories = ipc.list_device_inventory_categories("your-device-guid")
print([c["id"] for c in categories])

# Get hardware inventory for a specific category
battery = ipc.get_device_inventory("your-device-guid", "battery")
print(battery)

# Get software (application) inventory
apps = ipc.get_software_inventory("your-device-guid")
for app in apps:
    print(app.get("Display Name"), app.get("Version"))
```

---

## Running tests

Using pytest directly:

```bash
pip install -e ".[dev]"
pytest
```

Using Docker:

```bash
docker build -f Dockerfile.test -t ipc-skill-test .
docker run --rm ipc-skill-test
```

---

## Token sharing with ExplorerSkill

IPCSkill and [ExplorerSkill](https://github.com/schenardie/IntuneExplorerSkill) share the same token store (`~/.explorer_skill/`). If you have already authenticated with ExplorerSkill, IPCSkill will reuse that token automatically — and vice versa.

When using Docker, mount the same named volume for both containers:

```bash
docker run -it --rm -v explorer-skill-tokens:/root/.explorer_skill ipc-skill
```

---

## Permissions

IPCSkill uses Microsoft Intune's own public client ID (`5926fc8e-304e-4f59-8bed-58ca97cc39a4`). No custom Azure app registration is needed.

The signed-in user must have at least the **Microsoft Intune Read Only Operator** (or equivalent) role in Entra ID to query device inventory data.

---

## License

MIT
