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

Two versions are included. Both do the same job, so pick whichever suits you:

| | PowerShell ([`Break-AfsSnapshotLease.ps1`](Break-AfsSnapshotLease.ps1)) | Python ([`afs-snapshot-break-lease.py`](afs-snapshot-break-lease.py)) |
|---|---|---|
| Best for | Windows, or Azure Cloud Shell (PowerShell) | Any OS, including Windows on ARM, macOS, Linux and Cloud Shell |
| Requires | Az.Storage module (already in Cloud Shell) | **Only Python 3.8+.** No packages to install |

## PowerShell quick start

1. Open [Azure Cloud Shell](https://shell.azure.com) in **PowerShell** mode. On your own machine, run `Install-Module Az.Storage -Scope CurrentUser` once instead.
2. Download the script:

    ```powershell
    Invoke-WebRequest https://raw.githubusercontent.com/eliaquimbrandao/azure-storage-scripts/main/Storage/afs-snapshot-lease-breaker/Break-AfsSnapshotLease.ps1 -OutFile Break-AfsSnapshotLease.ps1
    ```

3. Preview what would change. `-WhatIf` breaks nothing:

    ```powershell
    ./Break-AfsSnapshotLease.ps1 -StorageAccount <storage_account> -Share <file_share> -OlderThanDays 30 -WhatIf
    ```

4. Run it. You're asked to confirm each lease; answer `A` for *Yes to All*:

    ```powershell
    ./Break-AfsSnapshotLease.ps1 -StorageAccount <storage_account> -Share <file_share> -OlderThanDays 30
    ```

| Parameter | Description |
|---|---|
| `-StorageAccount`, `-Share` | Storage account and file share names (required) |
| `-OlderThanDays <n>` | Only snapshots older than *n* days. Default `0` targets all leased snapshots |
| `-UseEntraId` | Sign in with Entra ID instead of being prompted for the account key. Needs the roles in [Permissions](#permissions) |
| `-Environment` | Sovereign cloud, e.g. `AzureUSGovernment` or `AzureChinaCloud` |
| `-WhatIf` / `-Confirm:$false` | Dry run / skip the prompts |

> On Windows, if you get *"running scripts is disabled"*, run `Set-ExecutionPolicy -Scope Process Bypass` first, then run the script again.
> Full help: `Get-Help ./Break-AfsSnapshotLease.ps1 -Full`.

## Python quick start

The Python script has **no dependencies**. It uses only the Python standard library, so there's nothing to `pip install`. Download the single `.py` file and run it with Python 3.8 or later. It works on Windows (x64, ARM64 and 32-bit), macOS, Linux and Azure Cloud Shell.

**Azure Cloud Shell (Bash)** — open [shell.azure.com](https://shell.azure.com):

```bash
curl -sSLO https://raw.githubusercontent.com/eliaquimbrandao/azure-storage-scripts/main/Storage/afs-snapshot-lease-breaker/afs-snapshot-break-lease.py
python3 afs-snapshot-break-lease.py --dry-run --auth 4 --account <storage_account> --share <file_share> --days 30
```

`--auth 4` reuses your Cloud Shell sign-in. If the storage account blocks public network access or uses a firewall, Cloud Shell can't reach it. In that case, run the script from a machine on an allowed network.

**Windows (PowerShell)**

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/eliaquimbrandao/azure-storage-scripts/main/Storage/afs-snapshot-lease-breaker/afs-snapshot-break-lease.py -OutFile afs-snapshot-break-lease.py
py afs-snapshot-break-lease.py --dry-run
```

**macOS / Linux**

```bash
curl -sSLO https://raw.githubusercontent.com/eliaquimbrandao/azure-storage-scripts/main/Storage/afs-snapshot-lease-breaker/afs-snapshot-break-lease.py
python3 afs-snapshot-break-lease.py --dry-run
```

When you're happy with the dry-run output, run the same command without `--dry-run`. You'll be asked to confirm before any lease is broken.

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
| `--tenant <id or domain>` | Entra ID tenant of the storage account. Needed if your account belongs to several tenants, or you're a guest user |
| `--endpoint-suffix <suffix>` | For sovereign clouds, for example `core.usgovcloudapi.net` or `core.chinacloudapi.cn`. Default: `core.windows.net` |

### Automation example

```bash
python afs-snapshot-break-lease.py --non-interactive --yes --auth 4 --account <storage_account> --share <file_share> --days 30
```

## Authentication

| Method | When to use |
|---|---|
| `4` Azure CLI | Recommended in **Azure Cloud Shell**, or anywhere you have already run `az login`. Requires the Azure CLI |
| `3` Device code | Servers without a browser or SSH sessions. Shows a code to enter at https://microsoft.com/devicelogin. Some organisations block device code sign-in with Conditional Access |
| `2` Interactive browser | Desktop machines. Opens your browser to sign in, like `az login` does |
| `1` Account key | When Entra ID isn't possible. Enter the key at the secure prompt. Avoid `--key`, because it ends up in your shell history and the process list |

Entra ID (options 2–4) is recommended over account keys.

## Permissions

The script uses two Azure Files REST operations: *List Shares* (including snapshots and their lease state) and *Lease Share* (break).

**Account key (`--auth 1`)** — the key grants full access to the storage account. To read the key in the portal or CLI, you need `Microsoft.Storage/storageAccounts/listKeys/action`, which is included in **Storage Account Contributor**.

**Entra ID (`--auth 2`, `3`, `4`)** — the identity needs these permissions on the storage account:

| Permission | Used for |
|---|---|
| `Microsoft.Storage/storageAccounts/fileServices/shares/read` | List Shares |
| `Microsoft.Storage/storageAccounts/fileServices/shares/lease/action` | Break the lease |

These are **management (control-plane) actions**, not data actions. Data roles such as *Storage File Data Privileged Contributor* don't include them, so that role alone isn't enough. Assign one of:

- **Storage Account Contributor**. This built-in role includes both actions. It is broad, so scope it to the single storage account.
- A **custom role** with only the two actions above. This is the least-privilege option.

Also assign **Storage File Data Privileged Contributor** on the same storage account. Microsoft's guidance for Azure Files OAuth over REST asks for a data role alongside the management permissions, and having it avoids `403` errors.

Entra ID access to share-level operations requires Azure Files REST API version `2024-11-04` or later. The script uses that version.
Role assignments can take a few minutes to apply.

References: [Permissions for calling Azure Files operations](https://learn.microsoft.com/rest/api/storageservices/authorize-with-azure-active-directory#permissions-for-calling-data-operations) · [Azure Files OAuth over REST](https://learn.microsoft.com/azure/storage/files/authorize-oauth-rest)

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
| `AuthorizationPermissionMismatch` / `403` | The identity is missing a permission. See [Permissions](#permissions). A firewall or private endpoint can also block you: your IP or network must be allowed |
| `Could not reach https://<account>.file...` | Check the account name and your network. If you're behind a proxy, set `HTTPS_PROXY` |
| `CERTIFICATE_VERIFY_FAILED` (macOS, python.org installer) | Run *Install Certificates.command* from your Python folder in Applications |
| `Azure CLI ('az') not found` | Install the Azure CLI, or use `--auth 2` or `3` |
| `InvalidAuthenticationInfo` with Entra ID | The storage account is in another tenant. Add `--tenant <tenant-id>` |
| Lease break `FAILED` | See the log file for the exact error |

**Log location:** `~/snapshot-lease-breaker/` on macOS/Linux, `%APPDATA%\snapshot-lease-breaker\` on Windows.

## Disclaimer

This script is provided **"as-is"**, without warranties of any kind. Review it and test it in a non-production environment before you use it. You are responsible for validating its impact on your backups and data.

## License

[MIT](../../LICENSE) — © Eliaquim Brandao
