"""Azure File Share Snapshot Lease Breaker.

Lists the snapshots of an Azure file share and breaks their leases so they can be deleted.
Uses only the Python standard library (no pip install needed) and calls the Azure Files REST API directly.
"""
import sys

# Avoid UnicodeEncodeError for emoji on legacy Windows code pages (e.g. when output is redirected).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

MIN_VERSION = (3, 8)
if sys.version_info[:2] < MIN_VERSION:
    print(f"\n❌ Python {sys.version.split()[0]} detected. This script requires Python {MIN_VERSION[0]}.{MIN_VERSION[1]}+.\n")
    sys.exit(1)

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import RotatingFileHandler

AUTH_KEY = "1"
AUTH_BROWSER = "2"
AUTH_DEVICE = "3"
AUTH_CLI = "4"
AUTH_LABELS = {
    AUTH_KEY: "Account Key",
    AUTH_BROWSER: "Entra ID (Interactive browser)",
    AUTH_DEVICE: "Entra ID (Device code)",
    AUTH_CLI: "Entra ID (Azure CLI login)",
}

# Azure Files REST API version. 2024-11-04+ is required for Entra ID on share-level operations.
API_VERSION = "2024-11-04"
STORAGE_SCOPE = "https://storage.azure.com/.default"
# Public client ID used by Azure CLI; also the default for the Azure SDK's developer credentials.
DEFAULT_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
LOGIN_HOSTS = {
    "core.windows.net": "login.microsoftonline.com",
    "core.usgovcloudapi.net": "login.microsoftonline.us",
    "core.chinacloudapi.cn": "login.chinacloudapi.cn",
}
HTTP_TIMEOUT = 60
LOG_DIR_NAME = "snapshot-lease-breaker"
ACCT_RE = re.compile(r"^[a-z0-9]{3,24}$")
SHARE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$")


class AzureError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


# =========================
# Logging
# =========================
def setup_logging() -> str:
    base_dir = os.getenv("APPDATA", os.path.expanduser("~")) if os.name == "nt" else os.path.expanduser("~")
    log_dir = os.path.join(base_dir, LOG_DIR_NAME)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"error-log.{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    file_h = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    root = logging.getLogger()
    root.handlers = [file_h]
    root.setLevel(logging.DEBUG)
    return log_path


# =========================
# Authentication
# =========================
def _post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}")


def _token_error(resp):
    return resp.get("error_description") or resp.get("error") or "unknown error"


def get_token_device_code(login_host, tenant, client_id):
    base = f"https://{login_host}/{tenant}/oauth2/v2.0"
    resp = _post_form(f"{base}/devicecode", {"client_id": client_id, "scope": STORAGE_SCOPE})
    if "device_code" not in resp:
        raise RuntimeError(f"Device code sign-in failed: {_token_error(resp)}")
    print(f"\n🔑 {resp['message']}\n")
    interval = int(resp.get("interval", 5))
    deadline = time.time() + int(resp.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        tok = _post_form(f"{base}/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": resp["device_code"],
        })
        if "access_token" in tok:
            print("Signed in.\n")
            return tok["access_token"]
        err = tok.get("error")
        if err == "slow_down":
            interval += 5
        elif err != "authorization_pending":
            raise RuntimeError(f"Device code sign-in failed: {_token_error(tok)}")
    raise RuntimeError("Device code sign-in timed out.")


def get_token_browser(login_host, tenant, client_id):
    """Authorization code flow with PKCE and a localhost redirect (same approach as 'az login')."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h3>Sign-in complete. You can close this window and return to the script.</h3>".encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://localhost:{server.server_port}"
    base = f"https://{login_host}/{tenant}/oauth2/v2.0"
    auth_url = f"{base}/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": STORAGE_SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })

    def serve():
        while not result:
            server.handle_request()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    print("\n🌐 Opening your browser to sign in...")
    print(f"If it doesn't open, go to:\n{auth_url}\n")
    webbrowser.open(auth_url)
    t.join(timeout=300)
    server.server_close()

    if not result:
        raise RuntimeError("Browser sign-in timed out. Try --auth 3 (device code).")
    if "error" in result:
        raise RuntimeError(f"Browser sign-in failed: {result.get('error_description') or result['error']}")
    if result.get("state") != state:
        raise RuntimeError("Browser sign-in failed: state mismatch.")

    tok = _post_form(f"{base}/token", {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": result["code"],
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "scope": STORAGE_SCOPE,
    })
    if "access_token" not in tok:
        raise RuntimeError(f"Browser sign-in failed: {_token_error(tok)}")
    print("Signed in.\n")
    return tok["access_token"]


def get_token_azure_cli(tenant):
    az = shutil.which("az")
    if not az:
        raise RuntimeError("Azure CLI ('az') not found. Install it or choose another --auth method.")
    cmd = [az, "account", "get-access-token", "--resource", "https://storage.azure.com", "-o", "json"]
    if tenant and tenant not in ("organizations", "common"):
        cmd += ["--tenant", tenant]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Azure CLI could not get a token (run 'az login' first): {p.stderr.strip()}")
    return json.loads(p.stdout)["accessToken"]


# =========================
# Azure Files REST client
# =========================
class FileShareRestClient:
    def __init__(self, account, endpoint_suffix, key=None, token=None):
        self.account = account
        self.base_url = f"https://{account}.file.{endpoint_suffix}"
        self.key = base64.b64decode(key, validate=True) if key else None
        self.token = token

    def _sign(self, method, path, query, headers):
        """Shared Key signature for the File service."""
        std = [
            headers.get("Content-Encoding", ""), headers.get("Content-Language", ""),
            headers.get("Content-Length", "") if headers.get("Content-Length") not in (None, "0") else "",
            headers.get("Content-MD5", ""), headers.get("Content-Type", ""), "",  # Date (x-ms-date used instead)
            headers.get("If-Modified-Since", ""), headers.get("If-Match", ""),
            headers.get("If-None-Match", ""), headers.get("If-Unmodified-Since", ""), headers.get("Range", ""),
        ]
        ms = sorted((k.lower(), v.strip()) for k, v in headers.items() if k.lower().startswith("x-ms-"))
        canon_headers = "".join(f"{k}:{v}\n" for k, v in ms)
        canon_resource = f"/{self.account}{path}"
        for k in sorted(query, key=str.lower):
            canon_resource += f"\n{k.lower()}:{query[k]}"
        string_to_sign = method + "\n" + "\n".join(std) + "\n" + canon_headers + canon_resource
        sig = base64.b64encode(hmac.new(self.key, string_to_sign.encode("utf-8"), hashlib.sha256).digest()).decode()
        return f"SharedKey {self.account}:{sig}"

    def request(self, method, path, query, extra_headers=None):
        headers = {"x-ms-version": API_VERSION, "x-ms-date": formatdate(usegmt=True)}
        headers.update(extra_headers or {})
        if method in ("PUT", "POST"):
            headers["Content-Length"] = "0"
            # Set explicitly so urllib doesn't add its own (unsigned) form Content-Type.
            headers["Content-Type"] = "application/octet-stream"
        if self.key:
            headers["Authorization"] = self._sign(method, path, query, headers)
        else:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["x-ms-file-request-intent"] = "backup"

        url = self.base_url + urllib.parse.quote(path) + "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, method=method, headers=headers, data=b"" if method in ("PUT", "POST") else None)
        logging.debug(f"{method} {url}")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            code, message = e.headers.get("x-ms-error-code", ""), ""
            try:
                root = ET.fromstring(body)
                code = root.findtext("Code") or code
                message = (root.findtext("Message") or "").split("\n")[0]
            except ET.ParseError:
                message = body.decode(errors="replace")[:300]
            logging.error(f"{method} {url} -> {e.code} {code}: {message}")
            raise AzureError(e.code, code, message) from None

    def list_share_snapshots(self, share):
        """Yield (snapshot, lease_status, lease_state) for snapshots of exactly this share."""
        marker = None
        while True:
            q = {"comp": "list", "include": "snapshots", "prefix": share}
            if marker:
                q["marker"] = marker
            _, body = self.request("GET", "/", q)
            root = ET.fromstring(body)
            for s in root.iter("Share"):
                # prefix is a prefix match, so skip other shares (e.g. 'data' vs 'data-archive').
                if s.findtext("Name") != share or not s.findtext("Snapshot"):
                    continue
                props = s.find("Properties")
                yield (
                    s.findtext("Snapshot"),
                    (props.findtext("LeaseStatus") if props is not None else None),
                    (props.findtext("LeaseState") if props is not None else None),
                )
            marker = root.findtext("NextMarker")
            if not marker:
                break

    def break_lease(self, share, snapshot):
        self.request(
            "PUT", f"/{share}",
            {"comp": "lease", "restype": "share", "sharesnapshot": snapshot},
            {"x-ms-lease-action": "break"},
        )


# =========================
# Helpers
# =========================
def parse_snapshot_timestamp(s: str) -> datetime:
    """Convert snapshot timestamp string to UTC datetime."""
    if "." in s:
        base, frac = s.split(".")
        frac = frac.rstrip("Z")[:6]
        dt = datetime.strptime(f"{base}.{frac}Z", "%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=timezone.utc)


def validate_args(args):
    errors = []
    if args.account and not ACCT_RE.fullmatch(args.account):
        errors.append(f"- Invalid storage account name '{args.account}': must be 3–24 lowercase letters/numbers.")
    if args.share and not SHARE_RE.fullmatch(args.share):
        errors.append(f"- Invalid file share name '{args.share}': must be 3–63 chars, lowercase letters/numbers/hyphens, start/end alphanumeric.")
    if args.days is not None and args.days <= 0:
        errors.append(f"- Invalid cutoff days '{args.days}': must be a positive integer.")
    if errors:
        print("ERROR: Invalid arguments:\n")
        print("\n".join(errors))
        sys.exit(1)


def prompt_missing(auth, account, share, days):
    if not auth:
        print("Choose authentication method:")
        print("1) Account Key")
        print("2) Entra ID - Interactive browser (desktop)")
        print("3) Entra ID - Device code (Azure Cloud Shell / headless servers)")
        print("4) Entra ID - Azure CLI login (uses your existing 'az login')")
        while True:
            auth = input("Enter your choice (1-4): ").strip()
            if auth in AUTH_LABELS:
                break
            print("Invalid input. Please enter 1, 2, 3 or 4.")

    while not account:
        account = input("Enter your Storage Account name: ").strip()
        if not ACCT_RE.fullmatch(account):
            print("Invalid storage account name. Must be 3–24 lowercase letters & numbers.")
            account = None

    while not share:
        share = input("Enter the File Share name: ").strip()
        if not SHARE_RE.fullmatch(share):
            print("Invalid share name. Must be 3–63 chars, lowercase letters/numbers/hyphens, start/end alphanumeric.")
            share = None

    while days is None:
        try:
            days = int(input("Enter cutoff days: ").strip())
            if days <= 0:
                print("Cutoff days must be positive.")
                days = None
        except ValueError:
            print("Please enter an integer for cutoff days.")

    return auth, account, share, days


def build_client(args, auth, account):
    login_host = LOGIN_HOSTS.get(args.endpoint_suffix, "login.microsoftonline.com")
    if auth == AUTH_KEY:
        key = args.key
        if not key:
            if args.non_interactive:
                print("ERROR: --key is required when using --auth 1 in non-interactive mode.")
                sys.exit(1)
            key = getpass.getpass("Enter your Storage Account key: ").strip()
            if not key:
                print("\nERROR: No key was entered. Exiting.\n")
                sys.exit(1)
            print("Key received, proceeding...\n")
        try:
            return FileShareRestClient(account, args.endpoint_suffix, key=key)
        except (ValueError, base64.binascii.Error):
            print("\nERROR: The storage account key is not valid (it should be a base64 string).\n")
            sys.exit(1)

    if auth == AUTH_CLI:
        token = get_token_azure_cli(args.tenant)
    elif auth == AUTH_DEVICE:
        token = get_token_device_code(login_host, args.tenant, args.client_id)
    else:
        token = get_token_browser(login_host, args.tenant, args.client_id)
    return FileShareRestClient(account, args.endpoint_suffix, token=token)


def explain_error(e: AzureError):
    hints = {
        "AuthenticationFailed": "Authentication failed. Check the account key, or sign in again.",
        "AuthorizationPermissionMismatch": "Your identity is missing permissions. See the Permissions section in the README.",
        "AuthorizationFailure": "Access denied. The storage account firewall or private endpoint may be blocking your network.",
        "ShareNotFound": "The file share was not found.",
        "InvalidAuthenticationInfo": "The Entra ID token was rejected. Check --tenant (the storage account's tenant).",
    }
    return hints.get(e.code, f"Azure returned {e.status} {e.code}: {e.message}")


def confirm(question, assume_yes, non_interactive):
    if assume_yes:
        print(f"{question} yes (--yes)")
        return True
    if non_interactive:
        print(f"{question} no (use --yes to confirm in non-interactive mode)")
        return False
    while True:
        yn = input(f"{question} (y/n): ").strip().lower()
        if yn in ("y", "yes", "n", "no"):
            print()
            return yn.startswith("y")
        print("Invalid. Enter 'y', 'yes', 'n' or 'no'.")


def is_leased(info):
    return str(info["status"] or "").lower() == "locked" and str(info["state"] or "").lower() == "leased"


def print_snapshot_table(infos):
    if not infos:
        print("No snapshots found for this share.")
        return
    print(f"{'Snapshot':<35} {'Status':<10} {'State':<10} {'Older?':<6}")
    for i in infos:
        print(f"{i['snapshot']:<35} {str(i['status'] or '-'):<10} {str(i['state'] or '-'):<10} {'Yes' if i['older'] else 'No':<6}")


# =========================
# Main
# =========================
def main():
    parser = argparse.ArgumentParser(
        description="Azure File Share Snapshot Lease Breaker - lists snapshots of a file share and breaks their leases.",
        epilog="Tip: always start with --dry-run to review what would be changed.",
    )
    parser.add_argument("--auth", choices=list(AUTH_LABELS),
                        help="1 = Account Key, 2 = Entra ID browser, 3 = Entra ID device code (Cloud Shell/headless), 4 = Azure CLI login")
    parser.add_argument("--account", help="Storage Account name")
    parser.add_argument("--key", help="Storage Account Key (only for --auth 1; prefer the secure prompt)")
    parser.add_argument("--share", help="File Share name")
    parser.add_argument("--days", type=int, help="Retention cutoff in days")
    parser.add_argument("--non-interactive", action="store_true", help="Fail if required args are missing; never prompt")
    parser.add_argument("--dry-run", action="store_true", help="List snapshots but do not break leases")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts (required to break leases with --non-interactive)")
    parser.add_argument("--tenant", default="organizations",
                        help="Entra ID tenant ID or domain of the storage account (default: your home tenant)")
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID, help=argparse.SUPPRESS)
    parser.add_argument("--endpoint-suffix", default="core.windows.net",
                        help="Storage endpoint suffix for sovereign clouds (e.g. core.usgovcloudapi.net, core.chinacloudapi.cn)")
    args = parser.parse_args()

    log_path = setup_logging()
    validate_args(args)
    auth, account, share, days = args.auth, args.account, args.share, args.days

    missing = [n for n, v in (("--auth", auth), ("--account", account), ("--share", share), ("--days", days)) if v is None]
    if missing and args.non_interactive:
        print("ERROR: Missing required arguments in non-interactive mode:\n")
        print("\n".join(f"- {m}" for m in missing))
        sys.exit(1)
    if missing:
        auth, account, share, days = prompt_missing(auth, account, share, days)

    print(f"\n🔐 Authentication: {AUTH_LABELS[auth]} | Account: {account} | Share: {share} | Cutoff days: {days}\n")
    logging.debug(f"Auth={AUTH_LABELS[auth]}; Account={account}; Share={share}; CutoffDays={days}")

    try:
        client = build_client(args, auth, account)
    except RuntimeError as e:
        logging.error(str(e))
        print(f"\n❌ {e}")
        print(f"Detailed log: {log_path}")
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    print(f"🔍 Checking snapshots for '{share}' older than {days} days...\n")

    try:
        infos = [
            {"snapshot": snap, "status": status, "state": state, "older": parse_snapshot_timestamp(snap) < cutoff}
            for snap, status, state in client.list_share_snapshots(share)
        ]
    except AzureError as e:
        print(f"\n❌ Failed to list snapshots. {explain_error(e)}")
        print(f"Detailed log: {log_path}")
        sys.exit(1)
    except urllib.error.URLError as e:
        logging.error(f"Network error: {e}", exc_info=True)
        print(f"\n❌ Could not reach {client.base_url}: {e.reason}")
        print("Check the storage account name, your network/proxy, and the storage account firewall.")
        print(f"Detailed log: {log_path}")
        sys.exit(1)

    print_snapshot_table(infos)

    if args.dry_run:
        print("\nℹ️ Dry-run mode enabled. No leases were broken.")
        print(f"\nDetailed log: {log_path}")
        return

    older_leased = [i for i in infos if i["older"] and is_leased(i)]
    newer_leased = [i for i in infos if not i["older"] and is_leased(i)]

    if older_leased:
        print(f"\n⚠️ {len(older_leased)} leased snapshot(s) are older than {days} days.")
        print("   Leases on share snapshots are typically held by Azure Backup to protect restore points.")
        print("   Breaking a lease allows the snapshot to be deleted.")
        to_break = older_leased if confirm("Break their leases?", args.yes, args.non_interactive) else []
    elif newer_leased:
        print(f"\n⚠️ No leased snapshots older than cutoff, but {len(newer_leased)} newer snapshot(s) are leased.")
        to_break = newer_leased if confirm("Break their leases anyway?", args.yes, args.non_interactive) else []
    else:
        print("\n✅ No leased snapshots to process. Exiting.")
        print(f"\nDetailed log: {log_path}")
        return

    if not to_break:
        print("Nothing to do. Exiting.")
        print(f"\nDetailed log: {log_path}")
        return

    succ, fail = [], []
    for info in to_break:
        snap = info["snapshot"]
        try:
            client.break_lease(share, snap)
            logging.info(f"SUCCESS: {snap}")
            print(f"Snapshot {snap} — SUCCESS")
            succ.append(snap)
        except AzureError as e:
            print(f"Snapshot {snap} — FAILED ({explain_error(e)})")
            fail.append(snap)
        except Exception as e:
            logging.error(f"FAILED unexpected: {snap} — {e}", exc_info=True)
            print(f"Snapshot {snap} — FAILED (check logs)")
            fail.append(snap)

    print("\n=== FINAL SUMMARY ===")
    print(f"{'Snapshot':<35} Result")
    for s in succ:
        print(f"{s:<35} SUCCESS")
    for s in fail:
        print(f"{s:<35} FAILED")
    print(f"\n✅ Total succeeded: {len(succ)}")
    print(f"❌ Total failed: {len(fail)}")
    print(f"\nDetailed log: {log_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
