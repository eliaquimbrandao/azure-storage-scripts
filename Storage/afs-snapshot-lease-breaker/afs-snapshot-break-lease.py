"""Azure File Share Snapshot Lease Breaker.

Lists the snapshots of Azure file shares, breaks their leases so they can be deleted, and
optionally deletes them. Uses only the Python standard library (no pip install needed) and
calls the Azure Files REST API directly.
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
import csv
import getpass
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import shutil
import socket
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

__version__ = "2.0.0"

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
MAX_ATTEMPTS = 5
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
LOG_DIR_NAME = "snapshot-lease-breaker"
ACCT_RE = re.compile(r"^[a-z0-9]{3,24}$")
SHARE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2


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
    for h in root.handlers:
        h.close()
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
    def __init__(self, account, endpoint_suffix, key=None, token=None, account_url=None, sleep=None):
        self.account = account
        self.base_url = (account_url or f"https://{account}.file.{endpoint_suffix}").rstrip("/")
        self.key = base64.b64decode(key, validate=True) if key else None
        self.token = token
        self._sleep = sleep or (lambda seconds: time.sleep(seconds))

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

    def _send_once(self, method, path, query, extra_headers):
        headers = {"x-ms-version": API_VERSION, "x-ms-date": formatdate(usegmt=True)}
        headers.update(extra_headers or {})
        if method in ("PUT", "POST", "DELETE"):
            headers["Content-Length"] = "0"
            # Set explicitly so urllib doesn't add its own (unsigned) form Content-Type.
            headers["Content-Type"] = "application/octet-stream"
        if self.key:
            headers["Authorization"] = self._sign(method, path, query, headers)
        else:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["x-ms-file-request-intent"] = "backup"

        url = self.base_url + urllib.parse.quote(path) + "?" + urllib.parse.urlencode(query)
        data = b"" if method in ("PUT", "POST", "DELETE") else None
        req = urllib.request.Request(url, method=method, headers=headers, data=data)
        logging.debug(f"{method} {url}")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read()

    def request(self, method, path, query, extra_headers=None):
        """Send a request, retrying transient failures (throttling, server busy, network) with backoff."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            retry_after = None
            try:
                return self._send_once(method, path, query, extra_headers)
            except urllib.error.HTTPError as e:
                body = e.read()
                e.close()
                code, message = e.headers.get("x-ms-error-code", "") or "", ""
                try:
                    root = ET.fromstring(body)
                    code = root.findtext("Code") or code
                    message = (root.findtext("Message") or "").split("\n")[0]
                except ET.ParseError:
                    message = body.decode(errors="replace")[:300]
                logging.error(f"{method} {path} {query} -> {e.code} {code}: {message} (attempt {attempt})")
                if e.code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    raise AzureError(e.code, code, message) from None
                retry_after = e.headers.get("Retry-After")
            except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
                logging.error(f"{method} {path} {query} -> network error: {e} (attempt {attempt})")
                if attempt == MAX_ATTEMPTS:
                    raise
            delay = min(30.0, 2 ** attempt) + random.uniform(0, 1)
            if retry_after and retry_after.isdigit():
                delay = min(60.0, float(retry_after))
            print(f"   ⏳ Azure is busy or the network failed; retrying in {delay:.0f}s ({attempt}/{MAX_ATTEMPTS - 1})...")
            self._sleep(delay)

    def list_snapshots(self, share=None):
        """Yield snapshot dicts for one share (exact name match) or for all shares when share is None.

        Also yields base-share entries (snapshot=None) so callers can read base-share metadata.
        """
        marker = None
        while True:
            q = {"comp": "list", "include": "snapshots,metadata"}
            if share:
                q["prefix"] = share
            if marker:
                q["marker"] = marker
            _, body = self.request("GET", "/", q)
            root = ET.fromstring(body)
            for s in root.iter("Share"):
                name = s.findtext("Name")
                # prefix is a prefix match, so skip other shares (e.g. 'data' vs 'data-archive').
                if share and name != share:
                    continue
                props = s.find("Properties")
                meta_el = s.find("Metadata")
                yield {
                    "share": name,
                    "snapshot": s.findtext("Snapshot") or None,
                    "status": props.findtext("LeaseStatus") if props is not None else None,
                    "state": props.findtext("LeaseState") if props is not None else None,
                    "duration": props.findtext("LeaseDuration") if props is not None else None,
                    "metadata": {m.tag: (m.text or "") for m in meta_el} if meta_el is not None else {},
                }
            marker = root.findtext("NextMarker")
            if not marker:
                break

    def break_lease(self, share, snapshot):
        self.request(
            "PUT", f"/{share}",
            {"comp": "lease", "restype": "share", "sharesnapshot": snapshot},
            {"x-ms-lease-action": "break"},
        )

    def delete_snapshot(self, share, snapshot):
        self.request("DELETE", f"/{share}", {"restype": "share", "sharesnapshot": snapshot})


# =========================
# Helpers
# =========================
def read_secret(prompt):
    """Read a secret, echoing '*' per character so the user can see a paste landed."""
    if not sys.stdin.isatty():
        return getpass.getpass(prompt)

    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars = []

    if os.name == "nt":
        import msvcrt
        while True:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                break
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch in ("\x00", "\xe0"):  # arrow/function key prefix: skip the key code
                msvcrt.getwch()
                continue
            if ch == "\x08":
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
            elif ch.isprintable():
                chars.append(ch)
                sys.stdout.write("*")
            sys.stdout.flush()
    else:
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~(termios.ECHO | termios.ICANON)  # no echo, char-by-char; Ctrl+C still works
        new[6][termios.VMIN], new[6][termios.VTIME] = 1, 0
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            while True:
                ch = os.read(fd, 1).decode(errors="ignore")
                if ch in ("\r", "\n", ""):
                    break
                if ch in ("\x7f", "\x08"):
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b")
                elif ch.isprintable():
                    chars.append(ch)
                    sys.stdout.write("*")
                sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(chars)


def parse_snapshot_timestamp(s: str) -> datetime:
    """Convert snapshot timestamp string to UTC datetime."""
    s = s.strip()
    if "." in s:
        base, frac = s.split(".")
        frac = frac.rstrip("Z")[:6]
        dt = datetime.strptime(f"{base}.{frac}Z", "%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=timezone.utc)


def has_backup_marker(metadata):
    return any("azurebackup" in k.lower() for k in metadata)


def backup_hint(info, share_protected):
    """Best-effort guess whether Azure Backup holds this snapshot's lease (Azure doesn't expose the lease holder)."""
    if has_backup_marker(info["metadata"]):
        return "Yes"
    if is_leased(info) and share_protected and str(info["duration"] or "").lower() == "infinite":
        return "Likely"
    return "-"


def is_leased(info):
    return str(info["status"] or "").lower() == "locked" and str(info["state"] or "").lower() == "leased"


def validate_args(args):
    errors = []
    if args.account and not ACCT_RE.fullmatch(args.account):
        errors.append(f"- Invalid storage account name '{args.account}': must be 3–24 lowercase letters/numbers.")
    if args.share and not SHARE_RE.fullmatch(args.share):
        errors.append(f"- Invalid file share name '{args.share}': must be 3–63 chars, lowercase letters/numbers/hyphens, start/end alphanumeric.")
    if args.share and args.all_shares:
        errors.append("- Use either --share or --all-shares, not both.")
    if args.days is not None and args.days <= 0:
        errors.append(f"- Invalid cutoff days '{args.days}': must be a positive integer.")
    if args.snapshot and args.days is not None:
        errors.append("- Use either --snapshot or --days, not both.")
    for snap in args.snapshot or []:
        try:
            parse_snapshot_timestamp(snap)
        except ValueError:
            errors.append(f"- Invalid --snapshot '{snap}': expected a timestamp like 2024-05-01T12:00:00.0000000Z.")
    if args.report and not args.report.lower().endswith((".csv", ".json")):
        errors.append("- --report must end in .csv or .json.")
    if errors:
        print("ERROR: Invalid arguments:\n")
        print("\n".join(errors))
        sys.exit(EXIT_ERROR)


def prompt_missing(args):
    if not args.auth:
        print("Choose authentication method:")
        print("1) Account Key")
        print("2) Entra ID - Interactive browser (desktop)")
        print("3) Entra ID - Device code (Azure Cloud Shell / headless servers)")
        print("4) Entra ID - Azure CLI login (uses your existing 'az login')")
        while True:
            args.auth = input("Enter your choice (1-4): ").strip()
            if args.auth in AUTH_LABELS:
                break
            print("Invalid input. Please enter 1, 2, 3 or 4.")

    while not args.account:
        args.account = input("Enter your Storage Account name: ").strip()
        if not ACCT_RE.fullmatch(args.account):
            print("Invalid storage account name. Must be 3–24 lowercase letters & numbers.")
            args.account = None

    while not args.share and not args.all_shares:
        value = input("Enter the File Share name (or * for all shares): ").strip()
        if value == "*":
            args.all_shares = True
        elif SHARE_RE.fullmatch(value):
            args.share = value
        else:
            print("Invalid share name. Must be 3–63 chars, lowercase letters/numbers/hyphens, start/end alphanumeric.")

    while args.days is None and not args.snapshot:
        try:
            args.days = int(input("Enter cutoff days: ").strip())
            if args.days <= 0:
                print("Cutoff days must be positive.")
                args.days = None
        except ValueError:
            print("Please enter an integer for cutoff days.")


def build_client(args):
    login_host = LOGIN_HOSTS.get(args.endpoint_suffix, "login.microsoftonline.com")
    if args.auth == AUTH_KEY:
        key = args.key or os.environ.get("AZURE_STORAGE_KEY")
        if not key:
            if args.non_interactive:
                print("ERROR: --key (or AZURE_STORAGE_KEY) is required when using --auth 1 in non-interactive mode.")
                sys.exit(EXIT_ERROR)
            key = read_secret("Enter your Storage Account key: ").strip()
            if not key:
                print("\nERROR: No key was entered. Exiting.\n")
                sys.exit(EXIT_ERROR)
            note = "" if len(key) == 88 else " (Azure account keys are usually 88 characters, please double-check)"
            print(f"Key received: {len(key)} characters{note}.\n")
        try:
            return FileShareRestClient(args.account, args.endpoint_suffix, key=key, account_url=args.account_url)
        except (ValueError, base64.binascii.Error):
            print("\nERROR: The storage account key is not valid (it should be a base64 string).\n")
            sys.exit(EXIT_ERROR)

    if args.auth == AUTH_CLI:
        token = get_token_azure_cli(args.tenant)
    elif args.auth == AUTH_DEVICE:
        token = get_token_device_code(login_host, args.tenant, args.client_id)
    else:
        token = get_token_browser(login_host, args.tenant, args.client_id)
    return FileShareRestClient(args.account, args.endpoint_suffix, token=token, account_url=args.account_url)


def explain_error(e: AzureError):
    hints = {
        "AuthenticationFailed": "Authentication failed. Check the account key, or sign in again.",
        "AuthorizationPermissionMismatch": "Your identity is missing permissions. See the Permissions section in the README.",
        "AuthorizationFailure": "Access denied. The storage account firewall or private endpoint may be blocking your network.",
        "ShareNotFound": "The file share was not found.",
        "InvalidAuthenticationInfo": "The Entra ID token was rejected. Check --tenant (the storage account's tenant).",
        "LeaseNotPresentWithShareOperation": "The snapshot has no lease (it may have been released already).",
        "LeaseIdMissing": "The snapshot is still leased. Break its lease first.",
        "ShareSnapshotNotFound": "The snapshot no longer exists.",
    }
    return hints.get(e.code, f"Azure returned {e.status} {e.code}: {e.message}")


def confirm(question, assume_yes, non_interactive, typed_word=None):
    if assume_yes:
        print(f"{question} yes (--yes)")
        return True
    if non_interactive:
        print(f"{question} no (use --yes to confirm in non-interactive mode)")
        return False
    if typed_word:
        answer = input(f"{question} Type '{typed_word}' to confirm: ").strip().lower()
        print()
        return answer == typed_word
    while True:
        yn = input(f"{question} (y/n): ").strip().lower()
        if yn in ("y", "yes", "n", "no"):
            print()
            return yn.startswith("y")
        print("Invalid. Enter 'y', 'yes', 'n' or 'no'.")


def print_snapshot_table(infos, show_share):
    if not infos:
        print("No snapshots found.")
        return
    share_w = max([5] + [len(i["share"]) for i in infos]) + 2 if show_share else 0
    header = (f"{'Share':<{share_w}}" if show_share else "") + f"{'Snapshot':<31} {'Status':<9} {'State':<10} {'Older?':<7} {'Backup?':<7}"
    print(header)
    for i in infos:
        row = f"{i['share']:<{share_w}}" if show_share else ""
        row += (f"{i['snapshot']:<31} {str(i['status'] or '-'):<9} {str(i['state'] or '-'):<10} "
                f"{i['older_label']:<7} {i['backup']:<7}")
        print(row)


def write_report(path, infos, meta):
    fields = ["share", "snapshot", "lease_status", "lease_state", "lease_duration", "selected", "backup", "action", "result", "error"]
    rows = [{
        "share": i["share"], "snapshot": i["snapshot"], "lease_status": i["status"] or "", "lease_state": i["state"] or "",
        "lease_duration": i["duration"] or "", "selected": i["selected"], "backup": i["backup"],
        "action": i.get("action", ""), "result": i.get("result", ""), "error": i.get("error", ""),
    } for i in infos]
    if path.lower().endswith(".json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({**meta, "snapshots": rows}, f, indent=2)
    else:
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    print(f"📝 Report written to: {os.path.abspath(path)}")


def run_action(label, infos, fn):
    """Run fn(share, snapshot) for each info, recording the result on the info dict."""
    ok = failed = 0
    for info in infos:
        name = f"{info['share']}@{info['snapshot']}"
        try:
            fn(info["share"], info["snapshot"])
            logging.info(f"{label} SUCCESS: {name}")
            print(f"{label:<13} {name} — SUCCESS")
            info["result"] = "SUCCESS"
            ok += 1
        except AzureError as e:
            print(f"{label:<13} {name} — FAILED ({explain_error(e)})")
            info["result"], info["error"] = "FAILED", f"{e.status} {e.code}: {e.message}"
            failed += 1
        except Exception as e:
            logging.error(f"{label} FAILED unexpected: {name} — {e}", exc_info=True)
            print(f"{label:<13} {name} — FAILED (check logs)")
            info["result"], info["error"] = "FAILED", str(e)
            failed += 1
    return ok, failed


# =========================
# Main
# =========================
def build_parser():
    parser = argparse.ArgumentParser(
        description="Azure File Share Snapshot Lease Breaker - lists file share snapshots, breaks their leases, and optionally deletes them.",
        epilog="Tip: always start with --dry-run to review what would be changed.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--auth", choices=list(AUTH_LABELS),
                        help="1 = Account Key, 2 = Entra ID browser, 3 = Entra ID device code (Cloud Shell/headless), 4 = Azure CLI login")
    parser.add_argument("--account", help="Storage Account name")
    parser.add_argument("--key", help="Storage Account Key (only for --auth 1; prefer the secure prompt or AZURE_STORAGE_KEY)")
    target = parser.add_argument_group("what to process")
    target.add_argument("--share", help="File Share name")
    target.add_argument("--all-shares", action="store_true", help="Process every file share in the storage account")
    target.add_argument("--days", type=int, help="Select snapshots older than this many days")
    target.add_argument("--snapshot", action="append", metavar="TIMESTAMP",
                        help="Select a specific snapshot (repeatable), e.g. 2024-05-01T12:00:00.0000000Z")
    action = parser.add_argument_group("actions and safety")
    action.add_argument("--dry-run", action="store_true", help="List snapshots but make no changes")
    action.add_argument("--delete", action="store_true", help="Also DELETE the selected snapshots after breaking their leases")
    action.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts (required to make changes with --non-interactive)")
    action.add_argument("--non-interactive", action="store_true", help="Fail if required args are missing; never prompt")
    action.add_argument("--report", metavar="FILE", help="Write a report of all snapshots and results to FILE (.csv or .json)")
    conn = parser.add_argument_group("connection")
    conn.add_argument("--tenant", default="organizations",
                      help="Entra ID tenant ID or domain of the storage account (default: your home tenant)")
    conn.add_argument("--endpoint-suffix", default="core.windows.net",
                      help="Storage endpoint suffix for sovereign clouds (e.g. core.usgovcloudapi.net, core.chinacloudapi.cn)")
    conn.add_argument("--client-id", default=DEFAULT_CLIENT_ID, help=argparse.SUPPRESS)
    conn.add_argument("--account-url", help=argparse.SUPPRESS)  # testing / custom endpoints
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    log_path = setup_logging()
    validate_args(args)

    missing = [n for n, v in (("--auth", args.auth), ("--account", args.account),
                              ("--share or --all-shares", args.share or args.all_shares),
                              ("--days or --snapshot", args.days is not None or args.snapshot)) if not v]
    if missing and args.non_interactive:
        print("ERROR: Missing required arguments in non-interactive mode:\n")
        print("\n".join(f"- {m}" for m in missing))
        return EXIT_ERROR
    if missing:
        prompt_missing(args)

    scope = "all shares" if args.all_shares else args.share
    selector = f"snapshots: {', '.join(args.snapshot)}" if args.snapshot else f"cutoff days: {args.days}"
    print(f"\n🔐 Authentication: {AUTH_LABELS[args.auth]} | Account: {args.account} | Share: {scope} | {selector}"
          f"{' | DELETE enabled' if args.delete else ''}\n")
    logging.debug(f"v{__version__} Auth={AUTH_LABELS[args.auth]}; Account={args.account}; Share={scope}; {selector}; Delete={args.delete}")

    try:
        client = build_client(args)
    except RuntimeError as e:
        logging.error(str(e))
        print(f"\n❌ {e}")
        print(f"Detailed log: {log_path}")
        return EXIT_ERROR

    print(f"🔍 Listing snapshots of {'all shares' if args.all_shares else repr(args.share)}...\n")
    try:
        entries = list(client.list_snapshots(None if args.all_shares else args.share))
    except AzureError as e:
        print(f"\n❌ Failed to list snapshots. {explain_error(e)}")
        print(f"Detailed log: {log_path}")
        return EXIT_ERROR
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        logging.error(f"Network error: {e}", exc_info=True)
        print(f"\n❌ Could not reach {client.base_url}: {getattr(e, 'reason', e)}")
        print("Check the storage account name, your network/proxy, and the storage account firewall.")
        print(f"Detailed log: {log_path}")
        return EXIT_ERROR

    protected_shares = {e["share"] for e in entries if e["snapshot"] is None and has_backup_marker(e["metadata"])}
    infos = [e for e in entries if e["snapshot"]]
    if not args.all_shares and not any(e["share"] == args.share for e in entries):
        print(f"❌ File share '{args.share}' was not found in this storage account.")
        return EXIT_ERROR

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days) if args.days else None
    wanted = {parse_snapshot_timestamp(s) for s in (args.snapshot or [])}
    for i in infos:
        ts = parse_snapshot_timestamp(i["snapshot"])
        i["selected"] = (ts in wanted) if args.snapshot else (ts < cutoff)
        i["older_label"] = ("Picked" if i["selected"] else "-") if args.snapshot else ("Yes" if i["selected"] else "No")
        i["backup"] = backup_hint(i, i["share"] in protected_shares)

    print_snapshot_table(infos, show_share=args.all_shares)
    for share in sorted(protected_shares):
        print(f"\n🛡️  Share '{share}' has Azure Backup metadata. To remove backup snapshots, the recommended way is to change")
        print("    the retention in the backup policy, or use 'Stop protection and delete data' in the Recovery Services vault.")

    if args.snapshot:
        found = {parse_snapshot_timestamp(i["snapshot"]) for i in infos}
        for s in args.snapshot:
            if parse_snapshot_timestamp(s) not in found:
                print(f"\n⚠️ Snapshot {s} was not found.")

    meta = {"tool": "afs-snapshot-lease-breaker", "version": __version__, "account": args.account,
            "share": scope, "days": args.days, "snapshots_requested": args.snapshot or [],
            "delete": args.delete, "dry_run": args.dry_run, "run_at": datetime.now(timezone.utc).isoformat()}

    def finish(code):
        if args.report:
            write_report(args.report, infos, meta)
        print(f"\nDetailed log: {log_path}")
        return code

    selected = [i for i in infos if i["selected"]]
    to_break = [i for i in selected if is_leased(i)]
    to_delete = list(selected) if args.delete else []

    # Keep the original behaviour: if nothing old is leased, offer to break newer leased snapshots.
    if not args.snapshot and not args.delete and not to_break:
        newer = [i for i in infos if not i["selected"] and is_leased(i)]
        if newer and not args.dry_run:
            print(f"\n⚠️ No leased snapshots older than cutoff, but {len(newer)} newer snapshot(s) are leased.")
            if confirm("Break their leases anyway?", args.yes, args.non_interactive):
                for i in newer:
                    i["selected"] = True
                to_break = newer

    for i in to_break:
        i["action"] = "break-lease"
    for i in to_delete:
        i["action"] = "break-lease+delete" if is_leased(i) else "delete"

    if args.dry_run:
        for i in to_break + to_delete:
            i["result"] = "DRY-RUN"
        print(f"\nℹ️ Dry-run: would break {len(to_break)} lease(s)"
              f"{f' and delete {len(to_delete)} snapshot(s)' if args.delete else ''}. No changes were made.")
        return finish(EXIT_OK)

    if not to_break and not to_delete:
        print("\n✅ Nothing to do.")
        return finish(EXIT_OK)

    if to_break:
        print(f"\n⚠️ {len(to_break)} leased snapshot(s) selected.")
        print("   Leases on share snapshots are typically held by Azure Backup to protect restore points.")
        print("   Breaking a lease allows the snapshot to be deleted.")
        if not confirm("Break their leases?", args.yes, args.non_interactive):
            for i in to_break + to_delete:
                i["result"] = "SKIPPED"
            print("Nothing changed.")
            return finish(EXIT_OK)

    if to_delete:
        print(f"\n🗑️  {len(to_delete)} snapshot(s) will be PERMANENTLY DELETED. This cannot be undone.")
        if not confirm("Delete them?", args.yes, args.non_interactive, typed_word="delete"):
            args.delete, to_delete = False, []
            for i in selected:
                if i.get("action", "").endswith("delete"):
                    i["action"] = "break-lease" if is_leased(i) else ""
            print("Deletion cancelled; leases will still be broken." if to_break else "Nothing changed.")

    total_failed = 0
    if to_break:
        _, failed = run_action("Break lease", to_break, client.break_lease)
        total_failed += failed
    if to_delete:
        # Don't try to delete snapshots whose lease break failed.
        deletable = [i for i in to_delete if i.get("result") != "FAILED"]
        for i in to_delete:
            if i not in deletable:
                i["error"] = (i.get("error", "") + " | delete skipped because the lease break failed").strip(" |")
        for i in deletable:
            i.pop("result", None)
        _, failed = run_action("Delete", deletable, client.delete_snapshot)
        total_failed += failed

    done = [i for i in infos if i.get("result")]
    print("\n=== FINAL SUMMARY ===")
    for i in done:
        print(f"{i['share'] + '@' + i['snapshot']:<60} {i['action']:<20} {i['result']}")
    print(f"\n✅ Succeeded: {sum(1 for i in done if i['result'] == 'SUCCESS')}")
    print(f"❌ Failed: {sum(1 for i in done if i['result'] == 'FAILED')}")
    return finish(EXIT_PARTIAL if total_failed else EXIT_OK)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
