# Login to Azure account using Device Code
Connect-AzAccount -DeviceCode

# Define the subscription ID you want to use
$desiredSubscriptionId = "<YourSubscriptionId>"  # Replace with your subscription ID

# Set the desired subscription context
Set-AzContext -SubscriptionId $desiredSubscriptionId

# Verify the current subscription
$currentContext = Get-AzContext
Write-Host "`nCurrent Subscription Context:"
Write-Host "Subscription ID: $($currentContext.Subscription.Id)"
Write-Host "Subscription Name: $($currentContext.Subscription.Name)"

# Define variables
$resourceGroupName = "<YourResourceGroupName>"
$storageAccountName = "<YourStorageAccountName>"
$targetContainerName = "law-logs-block"  # Target container for block blobs
$reportFileName = "ConversionReport.csv"  # Name of the CSV report file

# Suppress warnings
$WarningPreference = "SilentlyContinue"

# Get the storage account context
$storageAccountKey = (Get-AzStorageAccountKey -ResourceGroupName $resourceGroupName -AccountName $storageAccountName)[0].Value
$storageAccountContext = New-AzStorageContext -StorageAccountName $storageAccountName -StorageAccountKey $storageAccountKey

# Check if the target container exists, if not, create it
$targetContainer = Get-AzStorageContainer -Context $storageAccountContext -Name $targetContainerName -ErrorAction SilentlyContinue
if (-not $targetContainer) {
    $targetContainer = New-AzStorageContainer -Context $storageAccountContext -Name $targetContainerName
}

# Get all containers in the storage account
$containers = Get-AzStorageContainer -Context $storageAccountContext

# Initialize arrays to track success, failures, and source containers
$convertedBlobs = @()
$failedBlobs = @()
$sourceContainers = @()

foreach ($container in $containers) {
    Write-Host "`nProcessing Container: $($container.Name)"
    
    # Get all blobs in the current container
    $blobs = Get-AzStorageBlob -Container $container.Name -Context $storageAccountContext

    # Skip containers that only contain block blobs
    if ($blobs | Where-Object { $_.ICloudBlob.BlobType -ne [Microsoft.WindowsAzure.Storage.Blob.BlobType]::BlockBlob }) {
        $sourceContainers += $container.Name
        
        foreach ($blob in $blobs) {
            try {
                # Check the blob type and process it accordingly
                if ($blob.ICloudBlob.BlobType -ne [Microsoft.WindowsAzure.Storage.Blob.BlobType]::BlockBlob) {
                    # Download the blob content to memory
                    $memoryStream = New-Object IO.MemoryStream
                    $blob.ICloudBlob.DownloadToStream($memoryStream)
                    $memoryStream.Position = 0

                    # Re-upload the content as a Block Blob to the target container
                    $blockBlob = $targetContainer.CloudBlobContainer.GetBlockBlobReference($blob.Name)
                    $blockBlob.UploadFromStream($memoryStream)
                    
                    # Track converted blobs
                    $convertedBlobs += [PSCustomObject]@{
                        Container = $container.Name
                        BlobName   = $blob.Name
                        Status     = "Converted"
                    }
                } else {
                    # Copy the block blob to the target container
                    $sourceBlobUri = $blob.ICloudBlob.Uri
                    $blockBlob = $targetContainer.CloudBlobContainer.GetBlockBlobReference($blob.Name)
                    $blockBlob.StartCopy($sourceBlobUri)
                    
                    # Track copied blobs
                    $convertedBlobs += [PSCustomObject]@{
                        Container = $container.Name
                        BlobName   = $blob.Name
                        Status     = "Copied"
                    }
                }
            } catch {
                # Track failed blobs
                $failedBlobs += [PSCustomObject]@{
                    Container = $container.Name
                    BlobName   = $blob.Name
                    Status     = "Failed"
                }
            }
        }
    }
}

# Restore warning preference
$WarningPreference = "Continue"

# Generate CSV report
$csvFilePath = [System.IO.Path]::Combine($env:TEMP, $reportFileName)
$convertedBlobs | Export-Csv -Path $csvFilePath -NoTypeInformation

# Upload the CSV report to the target container
$csvBlob = $targetContainer.CloudBlobContainer.GetBlockBlobReference($reportFileName)
$csvBlob.UploadFromFile($csvFilePath)

# Summary of the operation
Write-Host "`nConversion Summary:"
Write-Host "Total Source Containers Processed: $($sourceContainers.Count)"
Write-Host "Containers Processed:"
foreach ($containerName in $sourceContainers) {
    Write-Host " - $containerName"
}

Write-Host "`nTotal Blobs Processed: $($convertedBlobs.Count + $failedBlobs.Count)"
Write-Host "Blobs Successfully Converted or Copied: $($convertedBlobs.Count)"
Write-Host "Blobs Failed to Convert or Copy: $($failedBlobs.Count)"

if ($failedBlobs.Count -gt 0) {
    Write-Host "`nFailed Blobs:"
    $failedBlobs | ForEach-Object { Write-Host "$($_.Container)/$($_.BlobName) - Status: $($_.Status)" }
}

