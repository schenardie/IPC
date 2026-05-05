"""
Extracts the Intune-scoped microsoft.graph access token AND the Azure Portal
refresh token from the live Edge/Chrome session via CDP.

Requires Edge running with --remote-debugging-port=9222.

Usage:
  1. Launch Edge with:
       msedge.exe --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=%TEMP%\\edge-debug2 https://intune.microsoft.com
  2. Sign in and navigate to any Intune blade (e.g. Devices).
  3. Run: python capture_portal_auth.py
  4. Use the output:
     - bearer_token.txt  → one-time token for ipc-skill option 1 (manual fallback)
     - broker_rt.json    → used by ipc-skill option 1c for BroCI auto-refresh (recommended)
"""
import json
import base64
import urllib.request

try:
    import websocket
except ImportError:
    print("[error] Run: pip install websocket-client")
    raise

CDP_URL = "http://localhost:9222"
INTUNE_CLIENT_ID = "5926fc8e-304e-4f59-8bed-58ca97cc39a4"
AZURE_PORTAL_CLIENT_ID = "c44b4083-3bb0-49c1-b47d-974e53cbdf3c"


def decode_jwt_payload(token):
    try:
        payload = token.split(".")[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def get_intune_page():
    with urllib.request.urlopen(f"{CDP_URL}/json") as r:
        targets = json.loads(r.read())
    for t in targets:
        if t.get("type") == "page" and "intune.microsoft.com" in t.get("url", ""):
            return t
    return None


def main():
    target = get_intune_page()
    if not target:
        print("[error] No Intune tab found. Launch Edge at intune.microsoft.com with --remote-debugging-port=9222")
        return

    ws = websocket.create_connection(target["webSocketDebuggerUrl"])

    js = """(function() {
  var result = {accessTokens: [], refreshTokens: []};
  for (var i = 0; i < sessionStorage.length; i++) {
    var k = sessionStorage.key(i);
    var v = sessionStorage.getItem(k);
    try {
      var obj = JSON.parse(v);
      if (obj && obj.credentialType === 'AccessToken' && obj.secret) {
        result.accessTokens.push({target: obj.target || '', expiresOn: obj.expiresOn || 0, secret: obj.secret, clientId: obj.clientId || ''});
      }
      if (obj && obj.credentialType === 'RefreshToken' && obj.secret) {
        result.refreshTokens.push({clientId: obj.clientId || '', secret: obj.secret, homeAccountId: obj.homeAccountId || ''});
      }
    } catch(e) {}
  }
  return JSON.stringify(result);
})()"""

    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": js, "returnByValue": True}}))
    resp = json.loads(ws.recv())
    ws.close()

    val = resp.get("result", {}).get("result", {}).get("value", '{"accessTokens":[],"refreshTokens":[]}')
    data = json.loads(val)
    access_tokens = data.get("accessTokens", [])
    refresh_tokens = data.get("refreshTokens", [])

    # --- Access token: Intune client (5926fc8e) with DeviceManagement scopes ---
    best = None
    for t in access_tokens:
        secret = t.get("secret", "")
        payload = decode_jwt_payload(secret)
        appid = payload.get("appid", "")
        scp = payload.get("scp", "")
        if appid == INTUNE_CLIENT_ID and "DeviceManagement" in scp:
            if best is None or t.get("expiresOn", 0) > best.get("expiresOn", 0):
                best = t
                best["_payload"] = payload

    if not best:
        # Fallback: any graph token with DeviceManagement scope
        for t in access_tokens:
            secret = t.get("secret", "")
            payload = decode_jwt_payload(secret)
            scp = payload.get("scp", "")
            aud = payload.get("aud", "")
            if "graph.microsoft.com" in aud and "DeviceManagement" in scp:
                best = t
                best["_payload"] = payload
                break

    if not best:
        print("[error] No Intune/DeviceManagement-scoped token found.")
        print("  - Make sure you are signed into Intune and have navigated to Devices.")
        print("  - Try refreshing the Intune page, then run this script again.")
        return

    secret = best["secret"]
    payload = best.get("_payload", {})
    scp = payload.get("scp", "")
    expires_epoch = best.get("expiresOn", 0)
    tenant_id = payload.get("tid", "")

    import datetime
    try:
        expires_dt = datetime.datetime.fromtimestamp(int(expires_epoch))
        expires_str = expires_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        expires_str = str(expires_epoch)

    bearer = f"Bearer {secret}"

    print("=" * 60)
    print("[+] Intune access token extracted!")
    print("=" * 60)
    print(f"appid   : {payload.get('appid','?')}")
    print(f"upn     : {payload.get('upn') or payload.get('unique_name','?')}")
    print(f"tenant  : {tenant_id}")
    print(f"scopes  : {scp[:120]}...")
    print(f"expires : {expires_str}")
    print()
    print("Bearer token (saved to bearer_token.txt):")
    print()
    print(bearer)
    print()

    with open("bearer_token.txt", "w") as f:
        f.write(bearer)
    print("[+] Full token also saved to bearer_token.txt")
    print("[info] Tip: use ipc-skill option 1c with broker_rt.json for automatic refresh.")
    print("[info] Option 1c authenticates you fully — no need to copy/paste the token above.")

    # --- Broker refresh token: Azure Portal (c44b4083) for BroCI auto-refresh ---
    broker_rt = None
    for rt in refresh_tokens:
        cid = rt.get("clientId", "")
        if AZURE_PORTAL_CLIENT_ID.lower() in cid.lower() or cid.lower() == AZURE_PORTAL_CLIENT_ID.lower():
            broker_rt = rt
            break

    if broker_rt:
        print()
        print("=" * 60)
        print("[+] Azure Portal refresh token found (for BroCI auto-refresh)!")
        print("=" * 60)
        broci_data = {
            "broker_refresh_token": broker_rt["secret"],
            "broker_client_id": AZURE_PORTAL_CLIENT_ID,
            "broker_url": "https://portal.azure.com/",
            "tenant_id": tenant_id,
            "home_account_id": broker_rt.get("homeAccountId", ""),
        }
        with open("broker_rt.json", "w") as f:
            json.dump(broci_data, f, indent=2)
        print("[+] Broker RT saved to broker_rt.json")
        print("[info] Use ipc-skill option 1c to enable BroCI auto-refresh with this token.")
    else:
        print()
        print("[warn] No Azure Portal refresh token found in sessionStorage.")
        print("[info] BroCI auto-refresh requires the c44b4083 RT.")
        print("[info] Try navigating to portal.azure.com in the same Edge session, then re-run.")


if __name__ == "__main__":
    main()
