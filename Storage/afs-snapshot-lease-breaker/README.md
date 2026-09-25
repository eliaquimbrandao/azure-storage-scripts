# Azure File Share Snapshot Lease Breaker

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Azure](https://img.shields.io/badge/Azure-Files-blue)
![License](https://img.shields.io/badge/License-MIT-yellowgreen)

Lists the snapshots of an Azure File Share, shows which ones are **leased**, and breaks those leases so the snapshots can be deleted. The Azure portal can't break snapshot leases, so you have to do it through the API. This script does that for you.

> [!WARNING]
> Leases on file share snapshots are usually held by **Azure Backup** to protect recovery points.
> Breaking a lease lets that snapshot be deleted, and the matching restore point may stop working.
> Check which snapshots you need first, and **always start with `--dry-run`**.
> This script is provided as-is, without warranty. See [Disclaimer](#disclaimer).

## Quick start (Azure Cloud Shell — nothing to install locally)

1. Open [Azure Cloud Shell](https://shell.azure.com) (Bash).
2. Download the script and install its dependencies:

    ```bash
    mkdir afs-lease-breaker && cd afs-lease-breaker
    BASE=https://raw.githubusercontent.com/eliaquimbrandao/azure-storage-scripts/main/Storage/afs-snapshot-lease-breaker
    curl -sSLO $BASE/afs-snapshot-break-lease.py -sSLO $BASE/requirements.txt
    python3 -m pip install --user -r requirements.txt
    ```

3. Preview what the script would do. Nothing is changed:

    ```bash
    python3 afs-snapshot-break-lease.py --dry-run --auth 4 --account <storage_account> --share <file_share> --days 30
    ```

4. Run it for real. You'll be asked to confirm before any lease is broken:

    ```bash
    python3 afs-snapshot-break-lease.py --auth 4 --account <storage_account> --share <file_share> --days 30
    ```

> `--auth 4` reuses your Cloud Shell sign-in. If that fails, use `--auth 3` (device code) or `--auth 1` (account key).

## Quick start (your own machine)

Requires Python 3.8 or later.

```bash
git clone https://github.com/eliaquimbrandao/azure-storage-scripts.git
cd azure-storage-scripts/Storage/afs-snapshot-lease-breaker
python -m pip install -r requirements.txt       # Windows: py -3 -m pip install -r requirements.txt
python afs-snapshot-break-lease.py              # Windows: py -3 afs-snapshot-break-lease.py
```

If you run it with no arguments, the script asks for everything it needs: the sign-in method, storage account, file share and cutoff in days.

## What it does

1. Lists every snapshot of the specified file share. Other shares are ignored, even if their names start the same way.
2. Shows a table with each snapshot's lease status and whether it is older than the cutoff (`--days`).
3. Selects snapshots that are **leased** and **older than the cutoff**.
   - If none are older but some newer snapshots are leased, it asks whether to break those instead.
4. Asks for confirmation, breaks the leases, and prints a summary.
5. Writes a detailed log. The log path is printed at the end of each run.

The script **does not delete snapshots**. It only breaks leases. After that, you can delete snapshots in the portal, with the CLI, or by changing your backup policy.

## Options

| Option | Description |
|---|---|
| `--auth <1-4>` | `1` Account key · `2` Entra ID browser sign-in (desktop) · `3` Entra ID device code (Cloud Shell/servers) · `4` Azure CLI sign-in (`az login` / Cloud Shell) |
| `--account <name>` | Storage account name |
| `--share <name>` | File share name |
| `--days <n>` | Retention cutoff in days. Snapshots older than this are targeted |
| `--key <key>` | Storage account key, used only with `--auth 1`. You're prompted securely if you leave it out |
| `--dry-run` | Only list the snapshots and their lease state. Makes no changes |
| `--yes`, `-y` | Skip the confirmation prompt |
| `--non-interactive` | Never prompt: fail if a required value is missing. Leases are broken only if `--yes` is also given |
| `--endpoint-suffix <suffix>` | For sovereign clouds, for example `core.usgovcloudapi.net` or `core.chinacloudapi.cn`. Default: `core.windows.net` |

### Automation example

```bash
python afs-snapshot-break-lease.py --non-interactive --yes --auth 4 --account <storage_account> --share <file_share> --days 30
```

## Authentication

| Method | When to use |
|---|---|
| `4` Azure CLI | Recommended in **Azure Cloud Shell**, or anywhere you have already run `az login` |
| `3` Device code | Cloud Shell, SSH sessions or servers without a browser. Shows a code to enter at https://microsoft.com/devicelogin |
| `2` Interactive browser | Desktop machines with a browser |
| `1` Account key | When Entra ID isn't possible. Enter the key at the secure prompt. Avoid `--key`, because it ends up in your shell history and the process list |

Entra ID (options 2–4) is recommended over account keys.

## Permissions

- **Account key (`--auth 1`):** needs the storage account key. Getting the key requires `Microsoft.Storage/storageAccounts/listKeys/action`, which is included in *Storage Account Contributor*.
- **Entra ID (`--auth 2/3/4`):** start with **Storage File Data Privileged Contributor** on the storage account.
  In testing, some lease operations weren't allowed with this role alone. If you get `AuthorizationPermissionMismatch` or `403` errors, use **Storage Account Contributor** or the account key method instead.

Assign roles at the **storage account** level (least privilege). Role assignments can take a few minutes to apply.

## Example output

```plaintext
🔐 Authentication: Entra ID (Azure CLI login) | Account: mystorageaccount | Share: myshare | Cutoff days: 30

🔍 Checking snapshots for 'myshare' older than 30 days...

Snapshot                            Status     State      Older?
2024-05-01T12:00:00.0000000Z        locked     leased     Yes
2024-05-15T12:00:00.0000000Z        unlocked   available  Yes
2024-06-20T08:30:00.0000000Z        locked     leased     No

⚠️ 1 leased snapshot(s) are older than 30 days.
   Leases on share snapshots are typically held by Azure Backup to protect restore points.
   Breaking a lease allows the snapshot to be deleted.
Break their leases? (y/n): y

Snapshot 2024-05-01T12:00:00.0000000Z — SUCCESS

=== FINAL SUMMARY ===
Snapshot                            Result
2024-05-01T12:00:00.0000000Z        SUCCESS

✅ Total succeeded: 1
❌ Total failed: 0

Detailed log: /home/user/snapshot-lease-breaker/error-log.20240622_143000.log
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `File share '<name>' was not found` | Check the storage account and share names. Both are lowercase |
| `Authentication failed` | Try a different `--auth` method. In Cloud Shell, use `4` or `3` |
| `HttpResponseError` / `403` | Check [Permissions](#permissions), the storage account firewall and private endpoints (your IP or network must be allowed) |
| `ModuleNotFoundError` | Run `python -m pip install -r requirements.txt`. On a very new Python release, use the latest version the Azure SDK supports |
| Lease break `FAILED` | See the log file for the exact error |

**Log location:** `~/snapshot-lease-breaker/` on macOS/Linux, `%APPDATA%\snapshot-lease-breaker\` on Windows.

## Disclaimer

This script is provided **"as-is"**, without warranties of any kind. Review it and test it in a non-production environment before you use it. You are responsible for validating its impact on your backups and data.

## License

[MIT](../../LICENSE) — © Eliaquim Brandao
