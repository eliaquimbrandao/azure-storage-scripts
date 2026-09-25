# Integration test for Break-AfsSnapshotLease.ps1 against tests/mock_server.py.
# Requires Az.Storage and Python 3. Run:  pwsh ./tests/test_powershell.ps1
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$script = Join-Path (Split-Path $here) 'Break-AfsSnapshotLease.ps1'
$python = if (Get-Command python3 -ErrorAction SilentlyContinue) { 'python3' } else { 'python' }
$key = [Convert]::ToBase64String([byte[]](0..63))
$port = Get-Random -Minimum 20000 -Maximum 60000
$failures = 0

function Start-Mock {
    $log = Join-Path ([IO.Path]::GetTempPath()) "afs-mock-$([guid]::NewGuid()).jsonl"
    $p = Start-Process $python -ArgumentList @("$here/mock_server.py", '--port', $port, '--key', $key, '--requests-log', $log) -PassThru -NoNewWindow
    for ($i = 0; $i -lt 50; $i++) {
        try { $c = [Net.Sockets.TcpClient]::new('127.0.0.1', $port); $c.Close(); break } catch { Start-Sleep -Milliseconds 100 }
    }
    [pscustomobject]@{ Process = $p; Log = $log }
}
function Stop-Mock($m) { Stop-Process -Id $m.Process.Id -Force; Remove-Item $m.Log -ErrorAction SilentlyContinue }
function Get-Calls($m, $method) {
    if (-not (Test-Path $m.Log)) { return @() }
    # Parse with a regex: ConvertFrom-Json would turn the timestamps into [datetime].
    @(Get-Content $m.Log | Where-Object { $_ -match "`"method`": `"$method`"" } | ForEach-Object {
        $null = $_ -match '"path": "([^"]*)".*"sharesnapshot": "([^"]*)"'
        "$($Matches[1].Split('/')[-1])@$($Matches[2])"
    })
}
function Assert($condition, $message) {
    if ($condition) { Write-Host "  PASS $message" -ForegroundColor Green }
    else { Write-Host "  FAIL $message" -ForegroundColor Red; $script:failures++ }
}
function Invoke-Case($name, [hashtable]$params, [scriptblock]$check) {
    Write-Host "`n== $name"
    $m = Start-Mock
    try {
        $ctx = New-AzStorageContext -ConnectionString "DefaultEndpointsProtocol=http;AccountName=devacct;AccountKey=$key;FileEndpoint=http://127.0.0.1:$port/devacct;"
        $global:LASTEXITCODE = 0
        try {
            $out = & $script -StorageContext $ctx @params 6>&1 3>&1 2>&1 | Out-String
            $code = $global:LASTEXITCODE
        } catch {
            $out = "$_"; $code = 1
        }
        & $check $m $out $code
    } finally { Stop-Mock $m }
}

Import-Module Az.Storage
$old = '2020-01-01T00:00:00.0000000Z'; $free = '2020-02-01T00:00:00.0000000Z'
$bk = '2020-03-01T00:00:00.0000000Z'; $new = '2099-01-01T00:00:00.0000000Z'

Invoke-Case 'WhatIf changes nothing' @{ Share = 'data'; WhatIf = $true } {
    param($m, $out)
    Assert ((Get-Calls $m 'PUT').Count -eq 0) 'no lease breaks'
    Assert ($out -match 'Likely') 'shows Backup=Likely'
    Assert ($out -match 'Azure Backup metadata') 'warns about protected share'
}
Invoke-Case 'Older than 30 days breaks only exact share' @{ Share = 'data'; OlderThanDays = 30; Confirm = $false } {
    param($m, $out, $code)
    $puts = Get-Calls $m 'PUT' | Sort-Object
    Assert (($puts -join ',') -eq "data@$old,data@$bk") "broke $($puts -join ',')"
    Assert ($code -eq 0) 'exit code 0'
}
Invoke-Case 'Specific snapshot' @{ Share = 'data'; Snapshot = $new; Confirm = $false } {
    param($m)
    Assert (((Get-Calls $m 'PUT') -join ',') -eq "data@$new") 'broke only the requested snapshot'
}
Invoke-Case 'All shares' @{ AllShares = $true; OlderThanDays = 30; Confirm = $false } {
    param($m)
    Assert ((Get-Calls $m 'PUT').Count -eq 3) 'broke 3 leases across shares'
}
Invoke-Case 'Delete' @{ Share = 'data'; OlderThanDays = 30; Delete = $true; Confirm = $false } {
    param($m)
    $dels = Get-Calls $m 'DELETE' | Sort-Object
    Assert (($dels -join ',') -eq "data@$old,data@$free,data@$bk") "deleted $($dels -join ',')"
}
$report = Join-Path ([IO.Path]::GetTempPath()) "afs-report-$port.csv"
Invoke-Case 'CSV report' @{ Share = 'data'; OlderThanDays = 30; Confirm = $false; ReportPath = $report } {
    $rows = Import-Csv $report
    Assert (($rows | Where-Object Snapshot -eq $old).Result -eq 'SUCCESS') 'report shows SUCCESS'
    Assert (($rows | Where-Object Snapshot -eq $bk).Backup -eq 'Yes') 'report shows Backup=Yes'
    Remove-Item $report
}
Invoke-Case '-Delete needs an explicit selection' @{ Share = 'data'; Delete = $true; Confirm = $false } {
    param($m, $out, $code)
    Assert ((Get-Calls $m 'DELETE').Count -eq 0 -and $code -eq 1) 'refused to delete without a selection'
}
Invoke-Case 'Share not found' @{ Share = 'nope'; Confirm = $false } {
    param($m, $out, $code)
    Assert ($out -match 'was not found') 'reports missing share'
    Assert ($code -eq 1) 'exit code 1'
}

Write-Host ""
if ($failures) { Write-Host "$failures check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "All PowerShell checks passed" -ForegroundColor Green
