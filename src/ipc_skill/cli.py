"""
IPCSkill – Interactive CLI for Intune device inventory.

Run inside Docker (tokens shared with ExplorerSkill via named volume):
  docker run -it --rm -v explorer-skill-tokens:/root/.explorer_skill ipc-skill

Tokens stored by ExplorerSkill are reused automatically.
"""
from __future__ import annotations

import getpass
import json
import subprocess
import sys


def _masked_input(prompt: str) -> str:
    """Read input one character at a time, echoing '*' for each — works on Windows and Unix."""
    print(prompt, end="", flush=True)
    chars: list[str] = []
    try:
        if sys.platform == "win32":
            import msvcrt
            while True:
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    print()
                    break
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in ("\x08", "\x7f"):
                    if chars:
                        chars.pop()
                        print("\b \b", end="", flush=True)
                else:
                    chars.append(ch)
                    print("*", end="", flush=True)
        else:
            import termios, tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ("\r", "\n"):
                        print()
                        break
                    if ch == "\x03":
                        raise KeyboardInterrupt
                    if ch in ("\x08", "\x7f"):
                        if chars:
                            chars.pop()
                            print("\b \b", end="", flush=True)
                    else:
                        chars.append(ch)
                        print("*", end="", flush=True)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        return getpass.getpass(prompt)
    return "".join(chars)


from ipc_skill import IPCSkillConfig, IPCExplorer
from ipc_skill.token_manager import TokenExpiredError


def _build_config() -> IPCSkillConfig:
    return IPCSkillConfig()


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to the OS clipboard. Returns True on success."""
    try:
        if sys.platform == "win32":
            subprocess.run("clip", input=text.encode("utf-16"), check=True)
        elif sys.platform == "darwin":
            subprocess.run("pbcopy", input=text.encode("utf-8"), check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


def _show_results(data: object, label: str = "results") -> None:
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    print(f"[ok] {len(rows)} {label} returned.")
    _print_json(data)
    if rows:
        copy = input("Copy JSON to clipboard? [y/N]: ").strip().lower()
        if copy == "y":
            text = json.dumps(data, indent=2, default=str)
            if _copy_to_clipboard(text):
                print("[ok] Copied to clipboard.")
            else:
                print("[warn] Could not access clipboard — install xclip on Linux.")


def _pick_devices(ipc: IPCExplorer) -> list[dict]:
    """Search Windows devices by partial name and let the user pick one or all."""
    name = input("Device name (partial) or GUID: ").strip()

    import re
    if re.fullmatch(r"[0-9a-fA-F\-]{36}", name):
        return [ipc.get_managed_device(name)]

    _WIN_FILTER = "operatingSystem eq 'Windows'"

    matches = ipc.list_managed_devices(
        filter_query=f"startswith(deviceName,'{name}') and {_WIN_FILTER}",
        select=["id", "deviceName", "operatingSystem", "complianceState"],
    )
    if not matches:
        all_devices = ipc.list_managed_devices(
            filter_query=_WIN_FILTER,
            select=["id", "deviceName", "operatingSystem", "complianceState"],
        )
        matches = [d for d in all_devices if name.lower() in d.get("deviceName", "").lower()]

    if not matches:
        print("[warn] No devices found.")
        return []

    print(f"\nFound {len(matches)} device(s):")
    for i, d in enumerate(matches, 1):
        print(f"  {i:>3}.  {d.get('deviceName', '?'):<30}  {d.get('operatingSystem',''):<10}  {d.get('complianceState','')}")
    print(f"  all.  All {len(matches)} devices")

    pick = input("\nPick number or 'all': ").strip().lower()
    if pick == "all":
        return matches
    try:
        idx = int(pick) - 1
        if 0 <= idx < len(matches):
            return [matches[idx]]
        print("[warn] Invalid selection.")
        return []
    except ValueError:
        print("[warn] Invalid selection.")
        return []


MENU = """
╔══════════════════════════════════════════╗
║         IPCSkill – Device Inventory      ║
╠══════════════════════════════════════════╣
║  1  Store a bearer token (paste)         ║
║  2  Get device inventory                 ║
║  q  Quit                                 ║
╚══════════════════════════════════════════╝
"""


def _print_token_status(ipc: IPCExplorer) -> None:
    info = ipc.token_manager.token_info()
    if not info:
        print("  ⚠  No token stored — use option 1 to paste a token.\n")
        return
    status = "⚠  EXPIRED" if info["expired"] else "✔  Valid"
    print(f"  Token : {status}")
    print(f"  User  : {info['user']}")
    print(f"  Tenant: {info['tenant']}")
    print(f"  Expiry: {info['expires_at']} ({info['expires_in']})\n")


def main() -> None:
    config = _build_config()
    ipc = IPCExplorer(config)
    while True:
        print(MENU)
        _print_token_status(ipc)
        choice = input("Choice: ").strip().lower()
        print()
        try:
            if choice == "q":
                break

            elif choice == "1":
                token = _masked_input("Paste bearer token (hidden): ").strip()
                print(f"  [received {len(token)} characters — {'✓ looks like a JWT' if token.startswith('eyJ') else '⚠ unexpected format'}]")
                ipc.token_manager.store_token(access_token=token)
                print("[ok] Token stored.")

            elif choice == "2":
                devices = _pick_devices(ipc)
                if not devices:
                    continue

                first_id = devices[0].get("id") or devices[0].get("deviceId", "")
                print("[info] Loading inventory categories...")
                categories_raw = ipc.list_device_inventory_categories(first_id)
                if not categories_raw:
                    print("[warn] No inventory categories found for this device.")
                    continue

                cat_names = [c.get("id") or c.get("inventoryId", "") for c in categories_raw]
                cat_names = [c for c in cat_names if c]

                print(f"\nAvailable categories ({len(cat_names)}):")
                for i, name in enumerate(cat_names, 1):
                    print(f"  {i:>3}.  {name}")
                print(f"  all.  All {len(cat_names)} categories")

                raw_pick = input("\nPick number(s) comma-separated or 'all': ").strip().lower()
                if raw_pick == "all":
                    selected_cats = cat_names
                else:
                    selected_cats = []
                    for part in raw_pick.split(","):
                        part = part.strip()
                        try:
                            idx = int(part) - 1
                            if 0 <= idx < len(cat_names):
                                selected_cats.append(cat_names[idx])
                        except ValueError:
                            if part in cat_names:
                                selected_cats.append(part)
                    if not selected_cats:
                        print("[warn] No valid categories selected.")
                        continue

                all_results = []
                for device in devices:
                    device_id = device.get("id") or device.get("deviceId", "")
                    device_name = device.get("deviceName", device_id)
                    for category in selected_cats:
                        print(f"[info] Fetching {category} for {device_name}...")
                        try:
                            inv = ipc.get_device_inventory(device_id, category)
                            inv["_deviceName"] = device_name
                            inv["_category"] = category
                            all_results.append(inv)
                        except Exception as exc:
                            from ipc_skill.graph_client import GraphAPIError
                            if isinstance(exc, GraphAPIError) and exc.status_code == 404:
                                print(f"[warn] {device_name}/{category}: not available on this device (skipped)")
                            else:
                                print(f"[error] {device_name}/{category}: {exc}")

                _show_results(all_results if len(all_results) != 1 else all_results[0], "inventory")

            else:
                print("[?] Unknown option.")

        except TokenExpiredError:
            print("[error] Your token has expired.")
            print("[info]  Go to https://intune.microsoft.com, open browser DevTools,")
            print("[info]  copy a fresh Bearer token, then use option 1 to store it.")
        except FileNotFoundError:
            print("[error] No token stored yet — use option 1 to paste a token.")
        except Exception as exc:
            print(f"[error] {exc}")


if __name__ == "__main__":
    main()
