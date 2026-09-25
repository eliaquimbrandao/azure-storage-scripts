<#
.SYNOPSIS
    Breaks leases on Azure File Share snapshots so they can be deleted.

.DESCRIPTION
    Lists the snapshots of one Azure file share, shows their lease state, and breaks the
    lease on leased snapshots (optionally only those older than -OlderThanDays).
    The script does NOT delete snapshots.

    WARNING: snapshot leases are usually held by Azure Backup to protect recovery points.
    Breaking a lease allows the snapshot (and its restore point) to be deleted.
    Always run with -WhatIf first.

.PARAMETER StorageAccount
    Storage account name.

.PARAMETER Share
    File share name.

.PARAMETER OlderThanDays
    Only target snapshots older than this many days. 0 (default) = all leased snapshots.

.PARAMETER UseEntraId
    Sign in with Microsoft Entra ID instead of the storage account key.

.PARAMETER Environment
    Azure environment (e.g. AzureCloud, AzureUSGovernment, AzureChinaCloud). Default: AzureCloud.

.EXAMPLE
    ./Break-AfsSnapshotLease.ps1 -StorageAccount mystorage -Share myshare -WhatIf
    Lists what would be changed without breaking any lease.

.EXAMPLE
    ./Break-AfsSnapshotLease.ps1 -StorageAccount mystorage -Share myshare -OlderThanDays 30
    Breaks leases on leased snapshots older than 30 days, asking for confirmation.

.EXAMPLE
    ./Break-AfsSnapshotLease.ps1 -StorageAccount mystorage -Share myshare -UseEntraId -Confirm:$false
    Uses Entra ID and breaks all snapshot leases without prompting.
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[a-z0-9]{3,24}$')] [string]$StorageAccount,
    [Parameter(Mandatory)] [ValidatePattern('^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$')] [string]$Share,
    [ValidateRange(0, 36500)] [int]$OlderThanDays = 0,
    [switch]$UseEntraId,
    [string]$Environment = 'AzureCloud'
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Module -ListAvailable -Name Az.Storage)) {
    throw "Az.Storage module not found. Install it with: Install-Module Az.Storage -Scope CurrentUser"
}
Import-Module Az.Storage

# --- Authentication ---
if ($UseEntraId) {
    if (-not (Get-AzContext -ErrorAction SilentlyContinue)) {
        Connect-AzAccount -Environment $Environment | Out-Null
    }
    $ctx = New-AzStorageContext -StorageAccountName $StorageAccount -Environment $Environment `
        -UseConnectedAccount -EnableFileBackupRequestIntent
} else {
    $secureKey = Read-Host "Storage account key for '$StorageAccount'" -AsSecureString
    $key = [System.Net.NetworkCredential]::new('', $secureKey).Password
    if (-not $key) { throw "No key entered." }
    $ctx = New-AzStorageContext -StorageAccountName $StorageAccount -StorageAccountKey $key -Environment $Environment
}

# --- Find leased snapshots ---
$cutoff = [DateTimeOffset]::UtcNow.AddDays(-$OlderThanDays)
Write-Host "`nChecking snapshots of '$Share' in '$StorageAccount'$(if ($OlderThanDays) { " older than $OlderThanDays days" })...`n"

# -Prefix is a prefix match and includes snapshots; keep only snapshots of this exact share.
$snapshots = @(Get-AzStorageShare -Context $ctx -Prefix $Share |
    Where-Object { $_.Name -eq $Share -and $_.IsSnapshot } |
    ForEach-Object {
        $props = $_.ListShareProperties.Properties
        [pscustomobject]@{
            Snapshot    = $_.ListShareProperties.Snapshot
            Created     = $_.SnapshotTime
            LeaseState  = "$($props.LeaseState)"
            LeaseStatus = "$($props.LeaseStatus)"
            Target      = ($OlderThanDays -eq 0 -or $_.SnapshotTime -lt $cutoff) -and ("$($props.LeaseState)" -eq 'Leased')
            Client      = $_.ShareClient
        }
    })

if (-not $snapshots) { Write-Host "No snapshots found for share '$Share'."; return }

$snapshots | Format-Table Snapshot, LeaseState, LeaseStatus, @{ n = 'WillBreak'; e = { if ($_.Target) { 'Yes' } else { 'No' } } } -AutoSize

$targets = @($snapshots | Where-Object Target)
if (-not $targets) { Write-Host "No leased snapshots to process."; return }

Write-Warning "Snapshot leases are usually held by Azure Backup. Breaking them allows the snapshots to be deleted."

# --- Break leases ---
$ok = 0; $failed = 0
foreach ($s in $targets) {
    if (-not $PSCmdlet.ShouldProcess("Snapshot $($s.Snapshot) of share '$Share'", 'Break lease')) { continue }
    try {
        $lease = [Azure.Storage.Files.Shares.Specialized.ShareLeaseClient]::new($s.Client, $null)
        $null = $lease.Break([System.Threading.CancellationToken]::None)
        Write-Host "$($s.Snapshot) - SUCCESS" -ForegroundColor Green
        $ok++
    } catch {
        Write-Host "$($s.Snapshot) - FAILED: $($_.Exception.Message)" -ForegroundColor Red
        $failed++
    }
}

if (-not $WhatIfPreference) {
    Write-Host "`nSucceeded: $ok   Failed: $failed"
}
