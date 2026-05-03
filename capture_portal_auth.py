"""
Extracts the Intune-scoped microsoft.graph access token from the live Edge/Chrome
session via CDP. Requires Edge running with --remote-debugging-port=9222.

Usage:
  1. Launch Edge with:
       msedge.exe --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=%TEMP%\edge-debug2 https://intune.microsoft.com
  2. Sign in and navigate to any Intune blade (e.g. Devices).
  3. Run: python capture_portal_auth.py
  4. Paste the output Bearer token into ipc-skill option 1a.
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
  var tokens = [];
  for (var i = 0; i < sessionStorage.length; i++) {
    var k = sessionStorage.key(i);
    var v = sessionStorage.getItem(k);
    try {
      var obj = JSON.parse(v);
      if (obj && obj.credentialType === 'AccessToken' && obj.secret) {
        tokens.push({target: obj.target || '', expiresOn: obj.expiresOn || 0, secret: obj.secret});
      }
    } catch(e) {}
  }
  return JSON.stringify(tokens);
})()"""

    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": js, "returnByValue": True}}))
    resp = json.loads(ws.recv())
    ws.close()

    val = resp.get("result", {}).get("result", {}).get("value", "[]")
    tokens = json.loads(val)

    # Find the Intune client token with DeviceManagement scopes
    best = None
    for t in tokens:
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
        for t in tokens:
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
    print(f"scopes  : {scp[:120]}...")
    print(f"expires : {expires_str}")
    print()
    print("Bearer token (copy this and paste into ipc-skill option 1a):")
    print()
    print(bearer[:120] + "...[truncated]")
    print()

    with open("bearer_token.txt", "w") as f:
        f.write(bearer)
    print("[+] Full token saved to bearer_token.txt")


if __name__ == "__main__":
    main()
