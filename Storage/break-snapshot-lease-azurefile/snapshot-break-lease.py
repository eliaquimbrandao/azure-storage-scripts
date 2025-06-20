import argparse
from azure.storage.fileshare import ShareServiceClient, ShareLeaseClient
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
from datetime import datetime, timezone, timedelta

# -------------------------------------
# timestamp parser for 7-digit fraction handling
# -------------------------------------
def parse_snapshot_timestamp(s):
    if '.' in s:
        base, frac = s.split('.')
        frac = frac.rstrip('Z')
        if len(frac) > 6:
            frac = frac[:6]
        return datetime.strptime(f"{base}.{frac}Z", "%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")

# -------------------------------------
# arguments & interactive prompts
# -------------------------------------
parser = argparse.ArgumentParser(description="Azure File Share Snapshot Lease Breaker")

parser.add_argument('--account-name', help='Storage account name')
parser.add_argument('--account-key', help='Storage account key')
parser.add_argument('--file-share', help='File share name')
parser.add_argument('--retention-days', type=int, help='Retention in days (snapshots older than this may have lease broken)')

args = parser.parse_args()

# Prompt for missing inputs
if not args.account_name:
    args.account_name = input("Enter storage account name: ").strip()

if not args.account_key:
    args.account_key = input("Enter storage account key: ").strip()

if not args.file_share:
    args.file_share = input("Enter file share name: ").strip()

if args.retention_days is None:
    args.retention_days = int(input("Enter retention in days (snapshots older than this may have lease broken): ").strip())

account_name = args.account_name
account_key = args.account_key
file_share_name = args.file_share
retention_days = args.retention_days

# -------------------------------------
# Connect to the service
# -------------------------------------
service_client = ShareServiceClient(
    f"https://{account_name}.file.core.windows.net",
    credential=account_key
)

# -------------------------------------
# List and sort snapshots
# -------------------------------------
shares = service_client.list_shares(include_snapshots=True)

snapshots = []
for share in shares:
    if share['name'] == file_share_name and 'snapshot' in share and share['snapshot'] is not None:
        snapshots.append(share['snapshot'])

if not snapshots:
    print("\nℹ️ No snapshots found for this file share.")
    exit()

snapshots.sort(key=parse_snapshot_timestamp)

print(f"\n✅ Found {len(snapshots)} snapshots (oldest first):")
for s in snapshots:
    print(f"  {s}")

# -------------------------------------
# Lease status summary
# -------------------------------------
print(f"\n🔑 Snapshot lease status summary:")
print(f"{'Snapshot':<35} {'Status':<10} {'State':<10} {'Lease ID':<36}")

snapshot_info = []
for s in snapshots:
    share_client = service_client.get_share_client(file_share_name, snapshot=s)
    try:
        props = share_client.get_share_properties()
        lease_status = props['lease']['status']
        lease_state = props['lease']['state']
        lease_id = props['lease'].get('lease_id', None)
        print(f"{s:<35} {lease_status:<10} {lease_state:<10} {lease_id}")
        snapshot_info.append({
            'snapshot': s,
            'lease_status': lease_status,
            'lease_state': lease_state,
            'lease_id': lease_id
        })
    except Exception as e:
        print(f"{s:<35} ERROR: {e}")

# -------------------------------------
# Process snapshots older than retention period
# -------------------------------------
cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

print(f"\n📅 Checking snapshots older than {retention_days} days (cutoff: {cutoff_date.isoformat()}):")

matches = []
for info in snapshot_info:
    snap_dt = parse_snapshot_timestamp(info['snapshot']).replace(tzinfo=timezone.utc)
    if snap_dt < cutoff_date:
        matches.append(info)

if not matches:
    print("✅ No snapshots older than retention period.")
else:
    for info in matches:
        s = info['snapshot']
        lease_status = info['lease_status']
        lease_state = info['lease_state']
        lease_id = info['lease_id']

        print(f"\n🔎 Snapshot: {s}")
        print(f"Lease Status: {lease_status} | Lease State: {lease_state} | Lease ID: {lease_id}")

        if lease_status.lower() == "locked":
            resp = input(f"❓ Break lease for this snapshot? (Y/N): ").strip().lower()
            if resp == 'y':
                share_client = service_client.get_share_client(file_share_name, snapshot=s)
                lease_client = ShareLeaseClient(share_client)
                lease_client.break_lease()
                print(f"✅ Lease broken for snapshot: {s}")
            else:
                print(f"🚫 Skipped breaking lease for snapshot: {s}")
        else:
            print(f"ℹ️ No lease to break.")

print("\n🎉 Finished processing snapshots!")
