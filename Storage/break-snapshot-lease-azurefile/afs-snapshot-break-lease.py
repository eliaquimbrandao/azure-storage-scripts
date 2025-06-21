import sys
import logging
from azure.storage.fileshare import ShareServiceClient, ShareLeaseClient
from azure.core.exceptions import HttpResponseError
from azure.identity import InteractiveBrowserCredential
from datetime import datetime, timezone, timedelta

# --- Clean logging ---
logging.basicConfig(level=logging.INFO, format="%(message)s")
for noisy in ["azure.core.pipeline.policies.http_logging_policy"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)

def parse_snapshot_timestamp(s):
    if "." in s:
        base, frac = s.split(".")
        frac = frac.rstrip("Z")
        if len(frac) > 6:
            frac = frac[:6]
        dt = datetime.strptime(f"{base}.{frac}Z", "%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=timezone.utc)

print("Choose authentication method:")
print("1) Account Key")
print("2) Entra ID (Azure AD, Interactive)")
choice = input("Enter your choice (1 or 2): ").strip()

account_name = input("Enter your Storage Account name: ").strip()

if choice == "1":
    account_key = input("Enter your Storage Account key: ").strip()
    share_service_client = ShareServiceClient(
        f"https://{account_name}.file.core.windows.net",
        credential=account_key
    )
elif choice == "2":
    credential = InteractiveBrowserCredential()
    share_service_client = ShareServiceClient(
        f"https://{account_name}.file.core.windows.net",
        credential=credential,
        token_intent="backup"
    )
else:
    print("Invalid choice.")
    sys.exit(1)

share_name = input("Enter the File Share name: ").strip()
cutoff_days = int(input("Enter cutoff days: ").strip())
cutoff_date = datetime.now(timezone.utc) - timedelta(days=cutoff_days)

print(f"\n🔍 Checking snapshots for '{share_name}' older than {cutoff_days} days...\n")

shares = share_service_client.list_shares(name_starts_with=share_name, include_snapshots=True)

snapshot_info = []

for share in shares:
    snapshot_ts = share.get("snapshot")
    if snapshot_ts:
        parsed_ts = parse_snapshot_timestamp(snapshot_ts)
        share_client = share_service_client.get_share_client(share_name, snapshot=snapshot_ts)
        props = share_client.get_share_properties()
        lease = props.get("lease", {})
        info = {
            "snapshot": snapshot_ts,
            "timestamp": parsed_ts,
            "lease_status": lease.get("status"),
            "lease_state": lease.get("state"),
            "older_than_cutoff": parsed_ts < cutoff_date
        }
        snapshot_info.append(info)

if not snapshot_info:
    print("No snapshots found.")
    sys.exit(0)

# --- Show table ---
print(f"{'Snapshot':<35} {'Status':<10} {'State':<10} {'Older?':<6}")
for info in snapshot_info:
    print(f"{info['snapshot']:<35} {info['lease_status']:<10} {info['lease_state']:<10} "
          f"{'Yes' if info['older_than_cutoff'] else 'No'}")

# --- Determine targets ---
to_break_due_to_cutoff = [s for s in snapshot_info if s['older_than_cutoff']]
to_break_locked_newer = [
    s for s in snapshot_info 
    if not s['older_than_cutoff'] and s['lease_status'] == 'locked' and s['lease_state'] == 'leased'
]

# --- Decide what to do ---
if not to_break_due_to_cutoff:
    if to_break_locked_newer:
        print(f"\n⚠️ No snapshots older than cutoff, but found {len(to_break_locked_newer)} locked & leased snapshots.")
        confirm = input(f"Do you want to break their leases anyway? (y/n): ").strip().lower()
        if confirm == "y":
            to_break = to_break_locked_newer
        else:
            print("Nothing to do. Exiting.")
            sys.exit(0)
    else:
        print("\n✅ No snapshots to process. Exiting.")
        sys.exit(0)
else:
    to_break = to_break_due_to_cutoff

# --- Break leases ---
successes = []
failures = []

for info in to_break:
    try:
        share_client = share_service_client.get_share_client(share_name, snapshot=info['snapshot'])
        lease_client = ShareLeaseClient(share_client)
        lease_client.break_lease()
        successes.append(info)
    except HttpResponseError as e:
        failures.append((info, str(e)))

# --- Final clean summary ---
print("\n=== FINAL SUMMARY ===")
print(f"{'Snapshot':<35} {'Result':<10}")

for s in successes:
    print(f"{s['snapshot']:<35} SUCCESS")

for s, err in failures:
    print(f"{s['snapshot']:<35} FAILED")

print(f"\n✅ Total succeeded: {len(successes)}")
print(f"❌ Total failed: {len(failures)}")