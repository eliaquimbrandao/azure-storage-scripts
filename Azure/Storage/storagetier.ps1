# Connect to Az  Account
Connect-AzAccount

# Declare variable
$subID = "MySubID"

# Choose subscription
Select-AzSubscription -SubscriptionId "$subID"

# Declare variable
$strAccountName = "MyStorageAccounName"
$strAccountRG = "MyRG"

# create context
$stCtx = New-AzStorageContext -StorageAccountName $strAccountName -StorageAccountKey ((Get-AzStorageAccountKey -ResourceGroupName $strAccountRG -Name $strAccountName).Value[0])

# fetch containers
$containers = Get-AzStorageContainer -Context $stCtx

# placeholder to hold file list
$array = @();

# zero out our total
$length = 0

# this loops through the list of blobs and retrieves the length for each blob and adds it to the total
$listOfBlobs | ForEach-Object {$length = $length + $_.Length}

# outer loop
foreach($container in $containers)
{
    # fetch blobs in current container
    $blobs = Get-AzStorageBlob -Container $container.Name -Context $stCtx
    $array += ($blobs | Select-Object @{N='Container'; E={$_.ICloudBlob.Container.Name}}, Name, BlobType, AccessTier, Length);
}
    
$totalarray = @($array).Count  

#get total tier/size/percentage
$array | Group-Object AccessTier | %{
     New-Object psobject -Property @{
        AccessTier = $_.Name
        TotalSize = ($_.Group | Measure-Object Length -Sum).Sum
        TotalTier = ($_.Group | Measure-Object AccessTier).Count
        Percentage = (($_.Group | Measure-Object AccessTier).Count * 100)/$totalarray
    }
}
