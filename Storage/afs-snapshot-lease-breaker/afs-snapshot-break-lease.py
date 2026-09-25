import sys

# =========================
# Python Version Compatibility
# =========================
# Goal: keep the script runnable on "latest Python" *as long as* the Azure SDK
# you install supports that Python version.
#
# - We enforce only a minimum version (3.8+) for language features used here.
# - For newer Python versions, we warn (instead of hard failing).
MIN_VERSION = (3, 8)

current_major_minor = (sys.version_info.major, sys.version_info.minor)

if current_major_minor < MIN_VERSION:
    print(
        f"\n❌ Python {sys.version.split()[0]} detected. This script requires Python {MIN_VERSION[0]}.{MIN_VERSION[1]}+.\n"
    )
    sys.exit(1)

if current_major_minor >= (3, 13):
    print(
        f"\n⚠️  You are running Python {sys.version.split()[0]}.\n"
        "This script is designed to be forward-compatible, but the Azure SDK wheels may lag behind brand-new Python releases.\n"
        "If you hit install/import errors, use the newest Python version supported by the Azure SDK (often N-1), or try pre-release wheels.\n"
    )

import getpass
import os
import re
import logging
from logging.handlers import RotatingFileHandler

# =========================
# Dependency Guard (friendly errors)
# =========================
try:
    from azure.storage.fileshare import ShareServiceClient, ShareLeaseClient
    from azure.identity import InteractiveBrowserCredential, DeviceCodeCredential, AzureCliCredential
    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError, ClientAuthenticationError
except ModuleNotFoundError as e:
    missing = str(e).split("No module named ")[-1].strip("'\"")
    print(
        "\n❌ Missing Python dependency.\n"
        f"Missing module: {missing}\n\n"
        "Install dependencies with:\n"
        "  python -m pip install -r requirements.txt\n\n"
        "If you are on a very new Python release and installation fails, try:\n"
        "  python -m pip install --upgrade pip\n"
        "  python -m pip install --pre -r requirements.txt\n\n"
        "Or run the script using a supported Python version (example on Windows):\n"
        "  py -3.12 -m pip install -r requirements.txt\n"
        "  py -3.12 afs-snapshot-break-lease.py\n"
    )
    sys.exit(1)
from datetime import datetime, timezone, timedelta
import argparse

AUTH_KEY = "1"
AUTH_ENTRA = "2"
AUTH_DEVICE = "3"
AUTH_CLI = "4"
AUTH_LABELS = {
    AUTH_KEY: "Account Key",
    AUTH_ENTRA: "Entra ID (Interactive browser)",
    AUTH_DEVICE: "Entra ID (Device code)",
    AUTH_CLI: "Entra ID (Azure CLI login)",
}
LOG_DIR_NAME = "snapshot-lease-breaker"

def setup_logging() -> str:
    base_dir = os.getenv('APPDATA', os.path.expanduser("~")) if os.name == 'nt' else os.path.expanduser("~")
    log_dir = os.path.join(base_dir, LOG_DIR_NAME)
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"error-log.{ts}.log")

    console_h = logging.StreamHandler()
    console_h.setFormatter(logging.Formatter("%(message)s"))
    console_h.setLevel(logging.CRITICAL)

    file_h = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5)
    file_h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    file_h.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.handlers = [console_h, file_h]
    root.setLevel(logging.DEBUG)
    logging.getLogger("azure").setLevel(logging.WARNING)

    return log_path

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

    acct_re = re.compile(r'^[a-z0-9]{3,24}$')
    share_re = re.compile(r'^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$')

    auth = args.auth

    if args.account:
        if acct_re.fullmatch(args.account):
            account = args.account
        else:
            errors.append(f"- Invalid storage account name '{args.account}': must be 3–24 lowercase letters/numbers.")
            account = None
    else:
        account = None

    if args.share:
        if share_re.fullmatch(args.share):
            share = args.share
        else:
            errors.append(f"- Invalid file share name '{args.share}': must be 3–63 chars, lowercase letters/numbers/hyphens, start/end alphanumeric.")
            share = None
    else:
        share = None

    if args.days is not None:
        if args.days > 0:
            days = args.days
        else:
            errors.append(f"- Invalid cutoff days '{args.days}': must be a positive integer.")
            days = None
    else:
        days = None

    if errors:
        print("ERROR: Invalid arguments:\n")
        for e in errors:
            print(e)
        sys.exit(1)

    return auth, account, share, days


def prompt_missing(auth, account, share, days):
    acct_re = re.compile(r'^[a-z0-9]{3,24}$')
    share_re = re.compile(r'^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$')

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

    if not account:
        while True:
            account = input("Enter your Storage Account name: ").strip()
            if acct_re.fullmatch(account):
                break
            print("Invalid storage account name. Must be 3–24 lowercase letters & numbers.")

    if not share:
        while True:
            share = input("Enter the File Share name: ").strip()
            if share_re.fullmatch(share):
                break
            print("Invalid share name. Must be 3–63 chars, lowercase letters/numbers/hyphens, start/end alphanumeric.")

    if days is None:
        while True:
            try:
                days = int(input("Enter cutoff days: ").strip())
                if days > 0:
                    break
                else:
                    print("Cutoff days must be positive.")
            except ValueError:
                print("Please enter an integer for cutoff days.")

    return auth, account, share, days


def build_service_client(auth, account, key, non_interactive, endpoint_suffix):
    account_url = f"https://{account}.file.{endpoint_suffix}"

    if auth == AUTH_KEY:
        if not key:
            if non_interactive:
                print("ERROR: --key is required when using --auth 1 in non-interactive mode.")
                sys.exit(1)
            try:
                key = getpass.getpass("Enter your Storage Account key: ")
                print()
                if not key:
                    print("\nERROR: No key was entered. Exiting.\n")
                    sys.exit(1)
                else:
                    print("Key received, proceeding...\n")
            except Exception as e:
                logging.error(f"Could not read key securely: {e}")
                sys.exit(1)

        return ShareServiceClient(account_url, credential=key)

    if auth == AUTH_DEVICE:
        cred = DeviceCodeCredential()
    elif auth == AUTH_CLI:
        cred = AzureCliCredential()
    else:
        cred = InteractiveBrowserCredential()

    # token_intent was added to support "backup" scenarios for some data-plane operations.
    # To remain compatible across Azure SDK versions, fall back if the installed SDK doesn't support it.
    try:
        return ShareServiceClient(account_url, credential=cred, token_intent="backup")
    except TypeError:
        return ShareServiceClient(account_url, credential=cred)


def list_snapshots(svc, share, cutoff, log_path):
    infos = []
    try:
        entries = svc.list_shares(name_starts_with=share, include_snapshots=True)
        for e in entries:
            # name_starts_with is a prefix match, so skip other shares (e.g. 'data' vs 'data-archive').
            if e.get("name") != share:
                continue
            snap = e.get("snapshot")
            if not snap:
                continue
            tsnap = parse_snapshot_timestamp(snap)
            client = svc.get_share_client(share, snapshot=snap)
            props = client.get_share_properties()
            lease = props.get("lease", {})
            infos.append({
                "snapshot": snap,
                "ts": tsnap,
                "status": lease.get("status"),
                "state": lease.get("state"),
                "older": tsnap < cutoff,
            })
    except ResourceNotFoundError as e:
        logging.error(f"Share not found: {e.message or str(e)}", exc_info=True)
        print(f"\n❌ File share '{share}' was not found in this storage account.")
        print(f"Detailed log: {log_path}")
        sys.exit(1)
    except ClientAuthenticationError as e:
        logging.error(f"Authentication failed: {e}", exc_info=True)
        print("\n❌ Authentication failed. Try another --auth method (e.g. 3 = device code in Cloud Shell).")
        print(f"Detailed log: {log_path}")
        sys.exit(1)
    except HttpResponseError as e:
        logging.error(f"Failed to list or inspect snapshots: {e.message or str(e)}", exc_info=True)
        print("\n❌ Failed to list or inspect snapshots. Check credentials, permissions, and network connectivity.")
        print(f"Detailed log: {log_path}")
        sys.exit(1)

    return infos


def print_snapshot_table(infos):
    if not infos:
        print("No snapshots found for this share.")
        return
    print(f"{'Snapshot':<35} {'Status':<10} {'State':<10} {'Older?':<6}")
    for i in infos:
        status = str(i["status"] or "-")
        state = str(i["state"] or "-")
        print(f"{i['snapshot']:<35} {status:<10} {state:<10} {'Yes' if i['older'] else 'No':<6}")


def is_leased(info):
    return str(info["status"] or "").lower() == "locked" and str(info["state"] or "").lower() == "leased"


def confirm(question, assume_yes, non_interactive):
    if assume_yes:
        print(f"{question} yes (--yes)")
        return True
    if non_interactive:
        print(f"{question} no (use --yes to confirm in non-interactive mode)")
        return False
    while True:
        yn = input(f"{question} (y/n): ").strip().lower()
        if yn in ("y", "yes"):
            print()
            return True
        if yn in ("n", "no"):
            print()
            return False
        print("Invalid. Enter 'y', 'yes', 'n' or 'no'.")


def break_leases(svc, share, to_break):
    succ, fail = [], []
    for info in to_break:
        snap = info["snapshot"]
        try:
            client = svc.get_share_client(share, snapshot=snap)
            lease = ShareLeaseClient(client)
            lease.break_lease()
            logging.info(f"✅ SUCCESS: {snap}")
            print(f"Snapshot {snap} — SUCCESS")
            succ.append(snap)
        except HttpResponseError as e:
            logging.error(f"❌ FAILED: {snap} — {e.message or str(e)}")
            print(f"Snapshot {snap} — FAILED (check logs)")
            fail.append(snap)
        except Exception as e:
            logging.error(f"❌ FAILED unexpected: {snap} — {str(e)}", exc_info=True)
            print(f"Snapshot {snap} — FAILED (check logs)")
            fail.append(snap)

    return succ, fail


def main():
    # === Argument Parser ===
    parser = argparse.ArgumentParser(
        description="Azure File Share Snapshot Lease Breaker - lists snapshots of a file share and breaks their leases.",
        epilog="Tip: always start with --dry-run to review what would be changed.",
    )
    parser.add_argument(
        "--auth", choices=list(AUTH_LABELS),
        help="1 = Account Key, 2 = Entra ID browser, 3 = Entra ID device code (Cloud Shell/headless), 4 = Azure CLI login",
    )
    parser.add_argument("--account", help="Storage Account name")
    parser.add_argument("--key", help="Storage Account Key (only for --auth 1)")
    parser.add_argument("--share", help="File Share name")
    parser.add_argument("--days", type=int, help="Retention cutoff in days")
    parser.add_argument("--non-interactive", action="store_true", help="Fail if required args are missing")
    parser.add_argument("--dry-run", action="store_true", help="List snapshots but do not break leases")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts (required to break leases with --non-interactive)")
    parser.add_argument("--endpoint-suffix", default="core.windows.net",
                        help="Storage endpoint suffix for sovereign clouds (e.g. core.usgovcloudapi.net, core.chinacloudapi.cn)")
    args = parser.parse_args()

    log_path = setup_logging()

    auth, account, share, days = validate_args(args)

    missing = []
    if not auth:
        missing.append("--auth")
    if not account:
        missing.append("--account")
    if not share:
        missing.append("--share")
    if days is None:
        missing.append("--days")

    if missing and args.non_interactive:
        print("ERROR: Missing required arguments in non-interactive mode:\n")
        for m in missing:
            print(f"- {m}")
        sys.exit(1)

    if missing:
        auth, account, share, days = prompt_missing(auth, account, share, days)

    print(
        f"\n🔐 Authentication: {AUTH_LABELS[auth]}"
        f" | Account: {account} | Share: {share} | Cutoff days: {days}\n"
    )
    logging.debug(f"Auth={AUTH_LABELS[auth]}; Account={account}; Share={share}; CutoffDays={days}")

    svc = build_service_client(auth, account, args.key, args.non_interactive, args.endpoint_suffix)

    # === Process Snapshots ===
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    print(f"🔍 Checking snapshots for '{share}' older than {days} days...\n")

    infos = list_snapshots(svc, share, cutoff, log_path)
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

    succ, fail = break_leases(svc, share, to_break)

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
    main()