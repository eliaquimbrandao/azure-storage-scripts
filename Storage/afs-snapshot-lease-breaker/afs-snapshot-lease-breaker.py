import sys
import getpass
import os
import re
import logging
from logging.handlers import RotatingFileHandler
from azure.storage.fileshare import ShareServiceClient, ShareLeaseClient
from azure.storage.fileshare._shared.authentication import AzureSigningError
from azure.identity import InteractiveBrowserCredential
from azure.core.exceptions import HttpResponseError
from datetime import datetime, timezone, timedelta
import argparse

# === Setup Rotating Log ===
base_dir = os.getenv('APPDATA', os.path.expanduser("~")) if os.name == 'nt' else os.path.expanduser("~")
log_dir = os.path.join(base_dir, 'snapshot-lease-breaker')
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
logging.getLogger("azure").setLevel(logging.DEBUG)

def parse_snapshot_timestamp(s: str) -> datetime:
    """Convert snapshot timestamp string to UTC datetime."""
    if "." in s:
        base, frac = s.split(".")
        frac = frac.rstrip("Z")[:6]
        dt = datetime.strptime(f"{base}.{frac}Z", "%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=timezone.utc)

# === Argument Parser ===
parser = argparse.ArgumentParser(description="Azure File Share Snapshot Lease Breaker")
parser.add_argument("--auth", choices=['1', '2'], help="1 for Account Key, 2 for Entra ID")
parser.add_argument("--account", help="Storage Account name")
parser.add_argument("--key", help="Storage Account Key (only for --auth 1)")
parser.add_argument("--share", help="File Share name")
parser.add_argument("--days", type=int, help="Retention cutoff in days")
args = parser.parse_args()

# === Multi-error Validation ===
errors = []

# === Validate auth (optional, fallback to prompt) ===
auth = args.auth

# === Validate account ===
acct_re = re.compile(r'^[a-z0-9]{3,24}$')
if args.account:
    if acct_re.fullmatch(args.account):
        account = args.account
    else:
        errors.append(f"- Invalid storage account name '{args.account}': must be 3–24 lowercase letters/numbers.")
else:
    account = None  # fallback to prompt

# === Validate share ===
share_re = re.compile(r'^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$')
if args.share:
    if share_re.fullmatch(args.share):
        share = args.share
    else:
        errors.append(f"- Invalid file share name '{args.share}': must be 3–63 chars, lowercase letters/numbers/hyphens, start/end alphanumeric.")
else:
    share = None  # fallback to prompt

# === Validate days ===
if args.days is not None:
    if args.days > 0:
        days = args.days
    else:
        errors.append(f"- Invalid cutoff days '{args.days}': must be a positive integer.")
else:
    days = None  # fallback to prompt

# === If any validation errors, show all and exit ===
if errors:
    print("ERROR: Invalid arguments:\n")
    for e in errors:
        print(e)
    sys.exit(1)

# === Fallback to prompt for missing values only ===
if not auth:
    print("Choose authentication method:")
    print("1) Account Key")
    print("2) Entra ID (Interactive)")
    while True:
        auth = input("Enter your choice (1 or 2): ").strip()
        if auth in ("1", "2"):
            break
        print("Invalid input. Please enter '1' or '2'.")

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

print(
    f"\n🔐 Authentication: {'Account Key' if auth=='1' else 'Entra ID (Interactive)'}"
    f" | Account: {account} | Share: {share} | Cutoff days: {days}\n"
)
logging.debug(f"Auth={'Key' if auth=='1' else 'Interactive'}; Account={account}; Share={share}; CutoffDays={days}")

# === Client Setup ===
if auth == "1": 
    key = args.key 
    if not key: 
        try:
            key = getpass.getpass("Enter your Storage Account key: ")
            print()

            # --- START: Added Validation and Feedback ---
            if not key:
                print("\nERROR: No key was entered. Exiting.")
                print()
                sys.exit(1)
            else:
                print("Key received, proceeding...")
                print()
            # --- END: Added Validation and Feedback ---

        except Exception as e:
            logging.error(f"Could not read key securely: {e}")
            sys.exit(1)

    svc = ShareServiceClient(f"https://{account}.file.core.windows.net", credential=key) 
else:
    cred = InteractiveBrowserCredential() 
    svc = ShareServiceClient(f"https://{account}.file.core.windows.net", credential=cred, token_intent="backup") 

# === Validate Credentials and Share Existence ===
while True:
    try:
        # This single API call validates credentials, permissions, AND share existence.
        svc.get_share_client(share).get_share_properties()
        
        print(f"✅ Credentials valid and file share '{share}' found. Proceeding...")
        print()
        break  # Success, exit the loop

    except AzureSigningError:
        # This is the clear message for an invalid key format.
        print("\n❌ ERROR: Authentication failed. The provided Storage Account Key is not valid.")
        print("A valid key is a Base64 string. Please copy it directly from the Azure Portal.")
        logging.error("Authentication failed due to AzureSigningError, likely an invalid key format.")
        sys.exit(1)

    except HttpResponseError as e:
        if e.status_code == 404:
            print(f"\n❌ ERROR: The file share '{share}' was not found in the storage account '{account}'.")
            print()
            
            # Loop to ask for a new name
            while True:
                share = input("Please enter an available file share name: ").strip()
                print()
                if share_re.fullmatch(share):
                    break
                print("Invalid share name format. Must be 3–63 chars, lowercase letters/numbers/hyphens, start/end alphanumeric.")
                print()
        
        elif e.status_code == 403:
            # This is the clearer message for a permissions issue.
            print("\n❌ ERROR: Authorization failed (403 Forbidden).")
            print("The key appears valid, but it does not have the required permissions for this storage account.")
            logging.error(f"Authorization failed with 403 Forbidden: {e}")
            sys.exit(1)

        else:
            print(f"\n❌ ERROR: An unexpected Azure error occurred (HTTP {e.status_code}). See log for details.")
            logging.error(f"Azure error during validation: {e}")
            sys.exit(1)
            
    except Exception as e:
        print(f"\n❌ ERROR: An unexpected error occurred. See log for details: {e}")
        logging.error(f"Unexpected error during validation: {e}", exc_info=True)
        sys.exit(1)

# === Process Snapshots ===
cutoff = datetime.now(timezone.utc) - timedelta(days=days)
print(f"🔍 Checking snapshots for '{share}' older than {days} days...\n")

entries = svc.list_shares(name_starts_with=share, include_snapshots=True)
infos = []
for e in entries:
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
        "older": tsnap < cutoff
    })

print(f"{'Snapshot':<35} {'Status':<10} {'State':<10} {'Older?':<6}")
for i in infos:
    print(f"{i['snapshot']:<35} {i['status']:<10} {i['state']:<10} {'Yes' if i['older'] else 'No':<6}")

older = [i for i in infos if i["older"]]
locked = [i for i in infos if not i["older"] and i["status"] == "locked" and i["state"] == "leased"]

if not older:
    if locked:
        print(f"\n⚠️ No snapshots older than cutoff, but {len(locked)} are locked.")
        while True:
            yn = input("Break their leases anyway? (y/n): ").strip().lower()
            if yn in ("y", "yes", "n", "no"):
                print()
                break
            print("Invalid. Enter 'y', 'yes', 'n' or 'no'.")
        to_break = locked if yn.startswith("y") else []
        if not to_break:
            print("Nothing to do. Exiting.")
            sys.exit(0)
    else:
        print("\n✅ No snapshots to process. Exiting.")
        sys.exit(0)
else:
    to_break = older

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

print("\n=== FINAL SUMMARY ===")
print(f"{'Snapshot':<35} Result")
for s in succ:
    print(f"{s:<35} SUCCESS")
for s in fail:
    print(f"{s:<35} FAILED")

print(f"\n✅ Total succeeded: {len(succ)}")
print(f"❌ Total failed: {len(fail)}")
print(f"\nDetailed log: {log_path}")