## Description Script

### Script Name: convert-blobs-to-block-blob-all-container.ps1

- This PowerShell script is designed to manage blob files in an Azure Storage Account. It processes all blob containers within the storage account, focusing on converting non-Block Blob types (Page blobs and Append blobs) to Block Blobs and copying existing Block Blobs to a specified target container. Additionally, it generates a CSV report detailing the operations performed and uploads this report to the target container.

### Script Name: convert-blobs-to-block-blob-law-container.ps1

- This PowerShell script is designed to manage blob files in an Azure Storage Account. It specifically targets containers whose names start with "am-", which are part of the Log Analytics Workspace (LAW) export data process. The script converts non-Block Blob types (Page blobs and Append blobs) to Block Blobs and copies existing Block Blobs to a specified target container. Additionally, it generates a CSV report detailing the operations performed and uploads this report to the target container.

## Necessary Modifications

- **Subscription ID**: Update the <mark>$desiredSubscriptionId</mark> variable with the appropriate subscription ID to ensure the script operates in the correct context. <br>
- **Resource Group and Storage Account Names**: Adjust <mark>$resourceGroupName</mark> and <mark>$storageAccountName</mark> variables to match your environment. <br>
- **Target Container Name**: Specify the name of the container in the <mark>$targetContainerName</mark> variable. If the container does not exist, it will be created automatically with the desired name. <br>
- **Report File Name**: Specify the name of the CSV files in the <mark>$reportFileName</mark> variable. If not, the file will be created as "ConversionReport.csv".

## Detailed Operation

1. **Target Container Handling**: <br>
   - **Container Check/Create**: The script checks for the existence of a container set before script execution. If it does not exist, it is created. This container will be used for storing converted and copied Block Blobs files.<br>

2. **Blob Processing**: <br>
   - **Script: `convert-blobs-to-block-blob-all-container.ps1`**: <br>
     - **Container Identification**: Processes all blob containers within the storage account. The script <mark>handles all containers without restriction</mark> . <br>

   - **Script: `convert-blobs-to-block-blob-law-container.ps1`**: <br>
     - **Container Identification**: Specifically targets containers whose names start with "am-", which are part of the Log Analytics Workspace (LAW) export data process. The script operates within these specific containers related to LAW export data. <br>

   - **Blob Conversion**: <br>
     - **Non-Block Blobs**: Blobs that are not Block Blobs are downloaded, converted to Block Blobs, and re-uploaded to the target container. <br>
        - **Breakdown**: <br>
            a. **Download**: The script first downloads the blob from the source container. This step brings the blob's data into the memory or temporary storage of the system where the script is running. <br>
            b. **Convert**: After downloading, the blob is converted from its original type (such as Page Blob or Append Blob) to a Block Blob. The conversion process involves preparing the blob data in a format suitable for Block Blob storage. <br>
            c. **Upload**: Once the conversion is complete, the script uploads the converted data to the specified target container as a Block Blob. <br>

   - **Processing Summary**: <br>
     - Logs tracks each blob’s status, detailing whether it was "Converted", "Copied", or "Failed". <br>

3. **CSV Report Generation**: <br>
   - **Report Details**: A CSV report is generated with columns for: <br>
     - **Container**: The source container name. <br>
     - **BlobName**: The blob’s name. <br>
     - **Status**: The result of the operation (e.g., "Converted", "Copied", "Failed"). <br>
   - **Report Upload**: The report is uploaded to the target container with defined name, if not, default it will be "ConversionReport.csv". <br>

4. **Summary Output**:
   - **Containers Processed**: Lists all containers that were processed during the script execution. <br>
   - **Blob Processing Statistics**: Provides a count of the total blobs processed, the number of blobs successfully converted or copied, and any failures. <br>
   - **Failure Details**: Displays details for blobs that failed during processing. <br>

## Expected results Script convert-blobs-to-block-blob-law-container

![Expected Results](https://user-images.githubusercontent.com/49751389/131340018-891f17ac-4e3b-4082-8daf-e08e1e9b17d4.png)

## Expected results Script convert-blobs-to-block-blob-all-container

> [!IMPORTANT]
> Some numbers or GUIDs that do not correspond to container names or blob metadata may appear in the results. The source of these values is unclear, but they do not impact the conversion process. They may be related to information from the Azure API, but no effect on the final outcome has been observed.

![Expected Results](https://user-images.githubusercontent.com/49751389/131340018-891f17ac-4e3b-4082-8daf-e08e1e9b17d4.png)
