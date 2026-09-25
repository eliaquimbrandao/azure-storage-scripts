<#
.SYNOPSIS
    Breaks leases on Azure File Share snapshots so they can be deleted, and optionally deletes them.

.DESCRIPTION
    Lists the snapshots of one Azure file share (or every share with -AllShares), shows their lease
    state, and breaks the lease on the selected leased snapshots. With -Delete it also deletes the
    selected snapshots.

    Select snapshots with -OlderThanDays (0 = all, the default) or with -Snapshot <timestamp>.

    WARNING: snapshot leases are usually held by Azure Backup to protect recovery points.
    Breaking a lease allows the snapshot (and its restore point) to be deleted. For backed-up
    shares, Microsoft recommends changing the backup policy retention or using
    "Stop protection and delete data" in the Recovery Services vault instead.
    Always run with -WhatIf first.

.PARAMETER StorageAccount
    Storage account name.

.PARAMETER Share
    File share name.

.PARAMETER AllShares
    Process every file share in the storage account.

.PARAMETER OlderThanDays
    Only select snapshots older than this many days. 0 (default) = all snapshots.

.PARAMETER Snapshot
    Select specific snapshots by timestamp, e.g. 2024-05-01T12:00:00.0000000Z. Can't be combined with -OlderThanDays.

.PARAMETER Delete
    Also delete the selected snapshots (after breaking their leases). This cannot be undone.

.PARAMETER ReportPath
    Write a report of all snapshots and results to a .csv or .json file.

.PARAMETER UseEntraId
    Sign in with Microsoft Entra ID instead of the storage account key.

.PARAMETER TenantId
    Entra ID tenant of the storage account (used with -UseEntraId when you're not signed in yet).

.PARAMETER Environment
    Azure environment (e.g. AzureCloud, AzureUSGovernment, AzureChinaCloud). Default: AzureCloud.

.EXAMPLE
    ./Break-AfsSnapshotLease.ps1 -StorageAccount mystorage -Share myshare -WhatIf
    Lists what would be changed without breaking any lease.

.EXAMPLE
    ./Break-AfsSnapshotLease.ps1 -StorageAccount mystorage -Share myshare -OlderThanDays 30 -ReportPath report.csv
    Breaks leases on leased snapshots older than 30 days, asking for confirmation, and writes a report.

.EXAMPLE
    ./Break-AfsSnapshotLease.ps1 -StorageAccount mystorage -Share myshare -Snapshot 2024-05-01T12:00:00.0000000Z -Delete
    Breaks the lease on one snapshot and deletes it.

.EXAMPLE
    ./Break-AfsSnapshotLease.ps1 -StorageAccount mystorage -AllShares -UseEntraId -Confirm:$false
    Uses Entra ID and breaks the leases of all snapshots of all shares without prompting.

.NOTES
    Version 2.0.0. Exit codes: 0 = success, 1 = error, 2 = some operations failed.
    The account key can also be supplied with the AZURE_STORAGE_KEY environment variable.
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High', DefaultParameterSetName = 'Account')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Account', Position = 0)]
    [ValidatePattern('^[a-z0-9]{3,24}$')] [string]$StorageAccount,

    [Parameter(Position = 1)]
    [ValidatePattern('^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$')] [string]$Share,

    [switch]$AllShares,
    [ValidateRange(0, 36500)] [int]$OlderThanDays = 0,
    [string[]]$Snapshot,
    [switch]$Delete,
    [ValidatePattern('\.(csv|json)$')] [string]$ReportPath,

    [Parameter(ParameterSetName = 'Account')] [switch]$UseEntraId,
    [Parameter(ParameterSetName = 'Account')] [string]$TenantId,
    [Parameter(ParameterSetName = 'Account')] [string]$Environment = 'AzureCloud',

    # For testing or advanced use: an existing context from New-AzStorageContext.
    [Parameter(Mandatory, ParameterSetName = 'Context')] [object]$StorageContext
)

$ErrorActionPreference = 'Stop'
$version = '2.0.0'

if ($Share -and $AllShares) { throw "Use either -Share or -AllShares, not both." }
if (-not $Share -and -not $AllShares) { throw "Specify -Share <name> or -AllShares." }
if ($Snapshot -and $PSBoundParameters.ContainsKey('OlderThanDays')) { throw "Use either -Snapshot or -OlderThanDays, not both." }
if ($Delete -and -not $Snapshot -and -not $PSBoundParameters.ContainsKey('OlderThanDays')) {
    throw "-Delete requires -OlderThanDays <n> or -Snapshot <timestamp> so snapshots aren't deleted by accident. Use -OlderThanDays 0 to delete all."
}

function ConvertTo-SnapshotTime([string]$Value) {
    [DateTimeOffset]::Parse($Value, [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal)
}
$wanted = @(foreach ($s in $Snapshot) {
    try { ConvertTo-SnapshotTime $s } catch { throw "Invalid -Snapshot '$s'. Expected a timestamp like 2024-05-01T12:00:00.0000000Z." }
})

if (-not (Get-Module -ListAvailable -Name Az.Storage)) {
    throw "Az.Storage module not found. Install it with: Install-Module Az.Storage -Scope CurrentUser"
}
Import-Module Az.Storage

# --- Authentication ---
if ($StorageContext) {
    $ctx = $StorageContext
    $StorageAccount = $ctx.StorageAccountName
} elseif ($UseEntraId) {
    if (-not (Get-AzContext -ErrorAction SilentlyContinue)) {
        $connect = @{ Environment = $Environment }
        if ($TenantId) { $connect.Tenant = $TenantId }
        Connect-AzAccount @connect | Out-Null
    }
    $ctx = New-AzStorageContext -StorageAccountName $StorageAccount -Environment $Environment `
        -UseConnectedAccount -EnableFileBackupRequestIntent
} else {
    $key = $env:AZURE_STORAGE_KEY
    if (-not $key) {
        $secureKey = Read-Host "Storage account key for '$StorageAccount'" -AsSecureString
        $key = [System.Net.NetworkCredential]::new('', $secureKey).Password
    }
    if (-not $key) { throw "No key entered." }
    $ctx = New-AzStorageContext -StorageAccountName $StorageAccount -StorageAccountKey $key -Environment $Environment
}

# --- Find snapshots ---
$scope = if ($AllShares) { 'all shares' } else { "'$Share'" }
$selector = if ($Snapshot) { "snapshot(s) $($Snapshot -join ', ')" } elseif ($OlderThanDays) { "older than $OlderThanDays days" } else { 'all snapshots' }
Write-Host "`nChecking snapshots of $scope in '$StorageAccount' ($selector)...`n"

$cutoff = [DateTimeOffset]::UtcNow.AddDays(-$OlderThanDays)
$listArgs = @{ Context = $ctx }
if ($Share) { $listArgs.Prefix = $Share }

# -Prefix is a prefix match; keep only the exact share. Results include base shares and snapshots.
$items = @(Get-AzStorageShare @listArgs | Where-Object { $AllShares -or $_.Name -eq $Share })
if (-not $AllShares -and -not $items) { throw "File share '$Share' was not found in storage account '$StorageAccount'." }

function Test-BackupMarker($Metadata) {
    [bool]($Metadata -and @($Metadata.Keys | Where-Object { $_ -match 'azurebackup' }).Count)
}
$protectedShares = @($items | Where-Object { -not $_.IsSnapshot -and (Test-BackupMarker $_.ListShareProperties.Properties.Metadata) } |
    ForEach-Object { $_.Name })  # not 'ForEach-Object Name': that honours -WhatIf and would return nothing

$snapshots = @($items | Where-Object IsSnapshot | ForEach-Object {
    $props = $_.ListShareProperties.Properties
    $time = ConvertTo-SnapshotTime $_.ListShareProperties.Snapshot
    $leased = "$($props.LeaseState)" -eq 'Leased'
    $selected = if ($Snapshot) { [bool]($wanted | Where-Object { $_ -eq $time }) } else { $OlderThanDays -eq 0 -or $time -lt $cutoff }
    $backup = if (Test-BackupMarker $props.Metadata) { 'Yes' }
              elseif ($leased -and "$($props.LeaseDuration)" -eq 'Infinite' -and $protectedShares -contains $_.Name) { 'Likely' }
              else { '-' }
    [pscustomobject]@{
        Share         = $_.Name
        Snapshot      = $_.ListShareProperties.Snapshot
        LeaseStatus   = "$($props.LeaseStatus)"
        LeaseState    = "$($props.LeaseState)"
        LeaseDuration = "$($props.LeaseDuration)"
        Selected      = $selected
        Backup        = $backup
        Action        = if (-not $selected) { '' } elseif ($Delete) { if ($leased) { 'break-lease+delete' } else { 'delete' } } elseif ($leased) { 'break-lease' } else { '' }
        Result        = ''
        Error         = ''
        Leased        = $leased
        Client        = $_.ShareClient
    }
})

$columns = @('Share', 'Snapshot', 'LeaseState', 'LeaseStatus', 'Backup', @{ n = 'Selected'; e = { if ($_.Selected) { 'Yes' } else { 'No' } } })
if ($snapshots) { $snapshots | Format-Table $columns -AutoSize | Out-String -Width 200 | Write-Host } else { Write-Host "No snapshots found." }

foreach ($p in $protectedShares) {
    Write-Warning ("Share '$p' has Azure Backup metadata. To remove backup snapshots, the recommended way is to change the " +
        "backup policy retention, or use 'Stop protection and delete data' in the Recovery Services vault.")
}
foreach ($i in 0..($wanted.Count - 1)) {
    if ($wanted.Count -and -not ($snapshots | Where-Object { (ConvertTo-SnapshotTime $_.Snapshot) -eq $wanted[$i] })) {
        Write-Warning "Snapshot $($Snapshot[$i]) was not found."
    }
}

function Write-Report {
    if (-not $ReportPath) { return }
    $rows = $snapshots | Select-Object Share, Snapshot, LeaseStatus, LeaseState, LeaseDuration, Selected, Backup, Action, Result, Error
    if ($ReportPath -match '\.json$') {
        [ordered]@{
            tool = 'afs-snapshot-lease-breaker (PowerShell)'; version = $version; account = $StorageAccount
            share = if ($AllShares) { '*' } else { $Share }; olderThanDays = $OlderThanDays; snapshotsRequested = @($Snapshot)
            delete = [bool]$Delete; whatIf = [bool]$WhatIfPreference; runAt = [DateTimeOffset]::UtcNow.ToString('o')
            snapshots = @($rows)
        } | ConvertTo-Json -Depth 4 | Set-Content -Path $ReportPath -Encoding utf8
    } else {
        $rows | Export-Csv -Path $ReportPath -NoTypeInformation -Encoding utf8
    }
    Write-Host "Report written to: $((Resolve-Path $ReportPath).Path)"
}

$targets = @($snapshots | Where-Object { $_.Action })
if (-not $targets) { Write-Host "Nothing to do."; Write-Report; return }

if (@($targets | Where-Object Leased).Count) {
    Write-Warning "Snapshot leases are usually held by Azure Backup. Breaking them allows the snapshots to be deleted."
}
if ($Delete) { Write-Warning "-Delete is set: selected snapshots will be PERMANENTLY deleted." }

# --- Break leases / delete ---
$failed = 0
foreach ($s in $targets) {
    $name = "$($s.Share)@$($s.Snapshot)"
    try {
        if ($s.Leased) {
            if (-not $PSCmdlet.ShouldProcess("Snapshot $name", 'Break lease')) {
                $s.Result = if ($WhatIfPreference) { 'WHATIF' } else { 'SKIPPED' }; continue
            }
            $lease = [Azure.Storage.Files.Shares.Specialized.ShareLeaseClient]::new($s.Client, $null)
            $null = $lease.Break([System.Threading.CancellationToken]::None)
            Write-Host "Break lease  $name - SUCCESS" -ForegroundColor Green
        }
        if ($Delete) {
            if (-not $PSCmdlet.ShouldProcess("Snapshot $name", 'DELETE snapshot (cannot be undone)')) {
                $s.Result = if ($WhatIfPreference) { 'WHATIF' } elseif ($s.Leased) { 'SUCCESS (lease broken, delete skipped)' } else { 'SKIPPED' }; continue
            }
            $null = $s.Client.Delete([Azure.Storage.Files.Shares.Models.ShareDeleteOptions]::new(), [System.Threading.CancellationToken]::None)
            Write-Host "Delete       $name - SUCCESS" -ForegroundColor Green
        }
        $s.Result = 'SUCCESS'
    } catch {
        $msg = $_.Exception.Message -split "`n" | Select-Object -First 1
        Write-Host "$name - FAILED: $msg" -ForegroundColor Red
        $s.Result = 'FAILED'; $s.Error = $msg
        $failed++
    }
}

if (-not $WhatIfPreference) {
    Write-Host "`nSucceeded: $(@($targets | Where-Object { $_.Result -like 'SUCCESS*' }).Count)   Failed: $failed"
}
Write-Report
if ($failed) { exit 2 }
