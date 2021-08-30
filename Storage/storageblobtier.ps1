# Enter Data and Declare Variables
$subID = Read-Host "`n Enter your Subscription ID: "
$rg = Read-Host "`n Enter the name of your Resource Group: "
$sa = Read-Host "`n Enter the name of your Storage Account: "

# Connect to Az  Account
Connect-AzAccount

# Choose subscription
Select-AzSubscription -SubscriptionId "$subID"

# Create Azure Storage Context
$stCtx = New-AzStorageContext -StorageAccountName $sa -StorageAccountKey ((Get-AzStorageAccountKey -ResourceGroupName $rg -Name $sa).Value[0])

# Fetch Containers
$containers = Get-AzStorageContainer -Context $stCtx

# Hold File List
$array = @();

# Zero out our total
$length = 0

# Loops through the list of blobs and retrieves the length for each blob and adds it to the total
$listOfBlobs | ForEach-Object {$length = $length + $_.Length}

# Loop
foreach($container in $containers)
{
    # fetch blobs in current container
    $blobs = Get-AzStorageBlob -Container $container.Name -Context $stCtx
    $array += ($blobs | Select-Object @{N='Container'; E={$_.ICloudBlob.Container.Name}}, Name, BlobType, AccessTier, Length);
}
    
$totalarray = @($array).Count  

# Get total tier/size/percentage
$array | Group-Object AccessTier | %{
     New-Object psobject -Property @{
        AccessTier = $_.Name
        TotalSize = ($_.Group | Measure-Object Length -Sum).Sum
        TotalTier = ($_.Group | Measure-Object AccessTier).Count
        Percentage = (($_.Group | Measure-Object AccessTier).Count * 100)/$totalarray
    }
}
