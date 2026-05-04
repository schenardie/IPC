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
from ipc_skill.token_manager import TokenExpiredError, TokenRefreshError


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
╔═══════════════════════════════════════════════════════════════════╗
║                   IPCSkill – Device Inventory                     ║
╠═══════════════════════════════════════════════════════════════════╣
║  1   Store a bearer token (manual paste)                          ║
║  1b  Enable WAM auto-refresh (Windows only, uses OS sign-in)      ║
║  1c  Enable BroCI auto-refresh (cross-platform, needs broker RT)  ║
║  2   Get device inventory                                         ║
║  3   Get software inventory                                       ║
║  q   Quit                                                         ║
╚═══════════════════════════════════════════════════════════════════╝
"""


def _print_token_status(ipc: IPCExplorer) -> None:
    info = ipc.token_manager.token_info()
    if not info:
        print("  ⚠  No token stored — use option 1 or 1b to authenticate.\n")
        return
    status = "⚠  EXPIRED" if info["expired"] else "✔  Valid"
    print(f"  Token        : {status}")
    print(f"  User         : {info['user']}")
    print(f"  Tenant       : {info['tenant']}")
    print(f"  Expiry       : {info['expires_at']} ({info['expires_in']})")
    print(f"  Auto-refresh : {info['auto_refresh']}\n")


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

            elif choice in ("1", "1a"):
                print("[info] Tip: run 'python capture_portal_auth.py' to extract a token from")
                print("[info] a live Edge/Chrome session (requires --remote-debugging-port=9222).")
                print()
                token = _masked_input("Paste bearer token (hidden): ").strip()
                print(f"  [received {len(token)} characters — {'✓ looks like a JWT' if token.startswith('eyJ') else '⚠ unexpected format'}]")
                ipc.token_manager.store_token(access_token=token)
                print("[ok] Token stored.")

            elif choice == "1b":
                print("[info] WAM auto-refresh uses the Windows Account Manager (WAM) broker.")
                print("[info] It silently refreshes your token using your Windows sign-in —")
                print("[info] the same mechanism the Intune portal uses to stay signed in.")
                print()
                tenant_id = input("Tenant ID (GUID, or press Enter for 'organizations'): ").strip()
                if not tenant_id:
                    tenant_id = "organizations"
                username = input("UPN hint (e.g. user@contoso.com, or Enter to skip): ").strip() or None
                print("[info] Contacting Windows WAM broker — a sign-in dialog may appear...")
                try:
                    resolved_user = ipc.token_manager.store_wam_auth(
                        tenant_id=tenant_id, username=username
                    )
                    print(f"[ok] WAM auto-refresh enabled for {resolved_user}.")
                    print("[ok] Tokens will now refresh silently whenever they expire.")
                except Exception as exc:
                    print(f"[error] WAM setup failed: {exc}")

            elif choice == "1c":
                import os, json as _json
                print("[info] BroCI auto-refresh exchanges your Azure Portal refresh token")
                print("[info] for a fresh Intune token — works on Windows, Mac, and Linux.")
                print("[info] Run 'python capture_portal_auth.py' first to generate broker_rt.json.")
                print()
                default_rt_file = "broker_rt.json"
                rt_file = input(f"Path to broker_rt.json [default: {default_rt_file}]: ").strip() or default_rt_file
                if not os.path.isfile(rt_file):
                    print(f"[error] File not found: {rt_file}")
                    print("[info] Run: python capture_portal_auth.py")
                else:
                    try:
                        with open(rt_file) as f:
                            broci_data = _json.load(f)
                        tenant_id = broci_data.get("tenant_id") or input("Tenant ID (GUID): ").strip()
                        broker_rt = broci_data["broker_refresh_token"]
                        print("[info] Testing BroCI exchange...")
                        resolved_user = ipc.token_manager.store_broci_auth(
                            tenant_id=tenant_id,
                            broker_refresh_token=broker_rt,
                        )
                        print(f"[ok] BroCI auto-refresh enabled for {resolved_user}.")
                        print("[ok] Tokens will now refresh automatically whenever they expire.")
                    except Exception as exc:
                        print(f"[error] BroCI setup failed: {exc}")

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

                grouped: dict = {}
                device_id_to_name = {
                    (d.get("id") or d.get("deviceId", "")): d.get("deviceName", d.get("id", ""))
                    for d in devices
                }
                device_ids = list(device_id_to_name.keys())
                total_reqs = len(device_ids) * len(selected_cats)
                total_chunks = (total_reqs + 19) // 20
                print(f"[info] Batching {total_reqs} request(s) in {total_chunks} Graph batch call(s)...")

                def _inv_progress(done: int, total: int) -> None:
                    print(f"\r  {done}/{total} requests complete...", end="", flush=True)

                try:
                    batch_result = ipc.get_inventory_batch(
                        device_ids, selected_cats, on_chunk=_inv_progress
                    )
                    print()  # end progress line
                except Exception as exc:
                    print(f"\n[error] Batch failed: {exc}")
                    continue

                for device_id, cats_data in batch_result.items():
                    device_name = device_id_to_name.get(device_id, device_id)
                    grouped[device_name] = cats_data

                for device_id, device_name in device_id_to_name.items():
                    device_cats = batch_result.get(device_id, {})
                    for cat in selected_cats:
                        if cat not in device_cats:
                            print(f"[warn] {device_name}/{cat}: not available (skipped)")

                # Unwrap single-device output for cleaner JSON
                output = grouped if len(grouped) > 1 else next(iter(grouped.values()), {})
                total = sum(len(v) for v in output.values() if isinstance(v, list))
                print(f"[ok] {len(grouped)} device(s), {total} total instance(s).")
                _print_json(output)
                copy = input("Copy JSON to clipboard? [y/N]: ").strip().lower()
                if copy == "y":
                    if _copy_to_clipboard(json.dumps(output, indent=2, default=str)):
                        print("[ok] Copied to clipboard.")
                    else:
                        print("[warn] Could not access clipboard — install xclip on Linux.")

            elif choice == "3":
                devices = _pick_devices(ipc)
                if not devices:
                    continue

                all_apps: dict = {}
                device_id_to_name = {
                    (d.get("id") or d.get("deviceId", "")): d.get("deviceName", d.get("id", ""))
                    for d in devices
                }
                device_ids = list(device_id_to_name.keys())
                total_chunks = (len(device_ids) + 19) // 20
                print(f"[info] Batching {len(device_ids)} request(s) in {total_chunks} Graph batch call(s)...")

                def _sw_progress(done: int, total: int) -> None:
                    print(f"\r  {done}/{total} requests complete...", end="", flush=True)

                try:
                    batch_result = ipc.get_software_inventory_batch(
                        device_ids, on_chunk=_sw_progress
                    )
                    print()  # end progress line
                except Exception as exc:
                    print(f"\n[error] Batch failed: {exc}")
                    continue

                for device_id, apps in batch_result.items():
                    device_name = device_id_to_name.get(device_id, device_id)
                    all_apps[device_name] = apps

                for device_id, device_name in device_id_to_name.items():
                    if device_id not in batch_result:
                        print(f"[warn] {device_name}: software inventory not available (skipped)")

                output = all_apps if len(all_apps) > 1 else next(iter(all_apps.values()), [])
                total = len(output) if isinstance(output, list) else sum(len(v) for v in output.values() if isinstance(v, list))
                print(f"[ok] {len(all_apps)} device(s), {total} total application(s).")
                _show_results(output, label="applications")

            else:
                print("[?] Unknown option.")

        except TokenExpiredError:
            print("[error] Your token has expired.")
            print("[info]  Option 1b: WAM auto-refresh (Windows, uses OS sign-in)")
            print("[info]  Option 1c: BroCI auto-refresh (cross-platform, run capture_portal_auth.py first)")
            print("[info]  Option 1:  Paste a fresh Bearer token manually")
        except TokenRefreshError as exc:
            print(f"[error] Auto-refresh failed: {exc}")
            print("[info]  Re-run option 1b (WAM) or 1c (BroCI) to re-authenticate.")
        except FileNotFoundError:
            print("[error] No token stored yet — use option 1, 1b, or 1c to authenticate.")
        except Exception as exc:
            print(f"[error] {exc}")


if __name__ == "__main__":
    main()
