# Suppress warnings 
$WarningPreference = "SilentlyContinue"

# Define functions for clarity and reusability
function Get-AzureStorageBlobDetails {
    param(
        [string]$subscriptionId,
        [string]$resourceGroupName,
        [string]$storageAccountName
    )

    # Check if resource group name is empty or null
    if (!$resourceGroupName) {
        Write-Error "Resource group name cannot be empty or null."
        exit 1
    }

    # Connect to Azure account and select subscription
    try {
        Write-Host "Attempting to connect to Azure. Please select your account..."
        Connect-AzAccount -ErrorAction Stop
        Write-Host "Successfully logged in."

        # Retrieve subscriptions
        $subscriptions = Get-AzSubscription
        if ($subscriptions.Count -eq 0) {
            Write-Error "No subscriptions found."
            exit 1
        }

        # Select subscription
        $selectedSubscription = $subscriptions | Where-Object { $_.Id -eq $subscriptionId }
        if ($null -eq $selectedSubscription) {
            Write-Error "Subscription ID '$subscriptionId' not found."
            exit 1
        }

        Select-AzSubscription -SubscriptionId $subscriptionId -ErrorAction Stop
        Write-Host "Successfully selected the subscription: '$($selectedSubscription.Name)' (ID: $subscriptionId)"
    }
    catch {
        Write-Error "Failed to connect to Azure or select subscription: $_"
        exit 1
    }

    # Create Azure Storage Context
    try {
        $storageAccountKey = (Get-AzStorageAccountKey -ResourceGroupName $resourceGroupName -Name $storageAccountName).Value[0]
        $stCtx = New-AzStorageContext -StorageAccountName $storageAccountName -StorageAccountKey $storageAccountKey
    }
    catch {
        Write-Error "Failed to create Azure Storage Context: $_"
        exit 1
    }

    # Fetch Containers
    try {
        $containers = Get-AzStorageContainer -Context $stCtx
    }
    catch {
        Write-Error "Failed to fetch containers: $_"
        exit 1
    }

    # Fetch blobs and return details
    try {
        $blobs = $containers | ForEach-Object {
            $containerName = $_.Name
            Get-AzStorageBlob -Container $containerName -Context $stCtx | 
            Select-Object @{Name='Container'; Expression = { $containerName }}, Name, BlobType, AccessTier, Length
        }
    }
    catch {
        Write-Error "Failed to fetch blob details: $_"
        exit 1
    }

    return $blobs
}

function Get-AzureStorageBlobSummary {
    param(
        [object[]]$blobs
    )

    # Group blobs by access tier and calculate summary statistics
    $summary = $blobs | Group-Object AccessTier | ForEach-Object {
        New-Object psobject -Property @{
            TotalTier      = ($_.Group | Measure-Object AccessTier).Count
            AccessTier     = $_.Name
            Percentage     = (($_.Group | Measure-Object AccessTier).Count * 100) / $blobs.Count
            TotalSizeBytes = ($_.Group | Measure-Object Length -Sum).Sum
            TotalSizeKB    = [math]::Round(($_.Group | Measure-Object Length -Sum).Sum / 1024, 2)
            TotalSizeMB    = [math]::Round(($_.Group | Measure-Object Length -Sum).Sum / 1024 / 1024, 2)
            TotalSizeGB    = [math]::Round(($_.Group | Measure-Object Length -Sum).Sum / 1024 / 1024 / 1024, 2)
        }
    }

    # Sort results in the specified order
    $summary | Select-Object TotalTier, AccessTier, Percentage, TotalSizeBytes, TotalSizeKB, TotalSizeMB, TotalSizeGB
}

function Export-AzureBlobReport {
    param(
        [object[]]$blobs,
        [string]$reportPath
    )

    # Ensure the directory exists
    $directory = Split-Path -Path $reportPath -Parent
    if (-not (Test-Path -Path $directory)) {
        $createDirectory = Read-Host "The directory '$directory' does not exist. Would you like to create it? (Y/N)"
        if ($createDirectory -eq 'Y') {
            New-Item -Path $directory -ItemType Directory -Force
            Write-Host "Directory '$directory' created."
        } else {
            Write-Error "Directory must exist to export the report."
            return
        }
    }

    # Prepare data for export including Container name
    $reportData = $blobs | Select-Object @{Name='Container'; Expression={ $_.Container }}, Name, BlobType, AccessTier, Length

    # Export blob details to CSV report
    try {
        $reportData | Export-Csv -Path $reportPath -NoTypeInformation -Force
        Write-Host "Report exported to '$reportPath'"
    }
    catch {
        Write-Error "Failed to export report: $_"
    }
}

# Get user input
$subscriptionId = Read-Host "`n Enter your Subscription ID: "
$resourceGroupName = Read-Host "`n Enter the name of your Resource Group: "
$storageAccountName = Read-Host "`n Enter the name of your Storage Account: "
$reportPath = Read-Host "`n Enter the path for the CSV report (e.g., C:\path\to\report.csv): "

# Get blob details
try {
    $blobs = Get-AzureStorageBlobDetails -subscriptionId $subscriptionId -resourceGroupName $resourceGroupName -storageAccountName $storageAccountName
}
catch {
    Write-Error "Failed to get blob details: $_"
    exit 1
}

# Get summary statistics
$summary = Get-AzureStorageBlobSummary -blobs $blobs

# Output results
$summary | Format-Table -AutoSize

# Export blob details to CSV report
Export-AzureBlobReport -blobs $blobs -reportPath $reportPath

# Restore warning preference
$WarningPreference = "Continue"
