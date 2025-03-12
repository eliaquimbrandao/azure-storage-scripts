param(
    [Parameter(Mandatory=$true)][string]$storageAccountName,
    [Parameter(Mandatory=$true)][string]$storageAccountKey,
    [Parameter(Mandatory=$true)][string]$containerName,
    [Parameter()][string]$folder = $null
)

function RestoreBlobIfDeleted($blob)
{
    if ($blob.IsDeleted)
    {
        $container = $blob.ICloudBlob.Container.Name
        Write-Host "Undeleting $container/$($blob.Name)..."
        try
        {
            $blob.BlobBaseClient.Undelete()
            Write-Host "Successfully undeleted $container/$($blob.Name)."
        }
        catch
        {
            Write-Host "Failed to undelete $container/$($blob.Name): $_"
        }
    }
}

function PromoteVersionIfNonCurrent($blob)
{
    if ($blob.VersionId -and -not $blob.IsLatestVersion)
    {
        $container = $blob.ICloudBlob.Container.Name
        RestoreBlobIfDeleted $blob

        Write-Host "Restoring $container/$($blob.Name) version $($blob.VersionId)..."
        try
        {
            $promoted = $blob | Copy-AzStorageBlob -DestContainer $container -DestBlob $blob.Name -Context $blob.Context
            Write-Host "Successfully restored version $($blob.VersionId) of $container/$($blob.Name)."
        }
        catch
        {
            Write-Host "Failed to restore $container/$($blob.Name): $_"
        }
    }
}

function PromoteVersions([string]$storageAccountName, [string]$storageAccountKey, [string]$containerName, [string]$folder = $null)
{
    $token = $null
    $maxReturn = 10000
    $lastBlob = $null

    $context = New-AzStorageContext -StorageAccountName $storageAccountName -StorageAccountKey $storageAccountKey

    do
    {
        $blobs = @(Get-AzStorageBlob -Context $context -Container $containerName -Prefix $folder -IncludeDeleted -IncludeVersion -MaxCount $maxReturn -ContinuationToken $token)
        if ($blobs.Length -le 0)
        {
            break
        }

        foreach ($blob in $blobs)
        {
            RestoreBlobIfDeleted $blob  # Ensure soft-deleted blobs are restored
            
            if ($lastBlob -and $blob.Name -ne $lastBlob.Name)
            {
                PromoteVersionIfNonCurrent $lastBlob
            }

            $lastBlob = $blob
        }

        $token = $blobs[$blobs.Count - 1].ContinuationToken
    }
    while ($token)

    if ($lastBlob)
    {
        PromoteVersionIfNonCurrent $lastBlob
    }
}

PromoteVersions -storageAccountName $storageAccountName -storageAccountKey $storageAccountKey -containerName $containerName -folder $folder