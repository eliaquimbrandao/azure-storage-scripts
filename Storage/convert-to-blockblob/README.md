## Description Script

### Script Name: convert-blobs-to-block-blob-all-container.ps1

- This PowerShell script is designed to manage blob files in an Azure Storage Account. It processes all blob containers within the storage account, focusing on converting non-Block Blob types (Page blobs and Append blobs) to Block Blobs and copying existing Block Blobs to a specified target container. Additionally, it generates a CSV report detailing the operations performed and uploads this report to the target container.

### Script Name: convert-blobs-to-block-blob-law-container.ps1

- This PowerShell script is designed to manage blob files in an Azure Storage Account. It specifically targets containers whose names start with "am-", which are part of the Log Analytics Workspace (LAW) export data process. The script converts non-Block Blob types (Page blobs and Append blobs) to Block Blobs and copies existing Block Blobs to a specified target container. Additionally, it generates a CSV report detailing the operations performed and uploads this report to the target container.

> [!IMPORTANT]
> All testing of this code has been performed in a small environment with a low workload. As a result, the current implementation may not be optimized for handling high-speed or heavy workloads. Please keep this in mind if you plan to use it in larger environments.
> 
> If you have suggestions for improvements or encounter any issues, feel free to contribute or open a discussion. I will update the code as needed, and once optimized for higher workloads, this note will be removed.

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
            - **Download**: The script first downloads the blob from the source container. This step brings the blob's data into the memory or temporary storage of the system where the script is running. <br>
            - **Convert**: After downloading, the blob is converted from its original type (such as Page Blob or Append Blob) to a Block Blob. The conversion process involves preparing the blob data in a format suitable for Block Blob storage. <br>
            - **Upload**: Once the conversion is complete, the script uploads the converted data to the specified target container as a Block Blob. <br>

   - **Processing Summary**: <br>
     - Logs tracks each blob’s status, detailing whether it was "Converted", "Copied", or "Failed". <br>

3. **CSV Report Generation**: <br>
   - **Report Details**: A CSV report is generated with columns for: <br>
     - **Container**: The source container name. <br>
     - **BlobName**: The blob’s name. <br>
     - **Status**: The result of the operation (e.g., "Converted", "Copied", "Failed"). <br>
   - **Report Upload**: The report is uploaded to the target container with defined name, otherwise the default will be "ConversionReport.csv". <br>

4. **Summary Output**:
   - **Containers Processed**: Lists all containers that were processed during the script execution. <br>
   - **Blob Processing Statistics**: Provides a count of the total blobs processed, the number of blobs successfully converted or copied, and any failures. <br>
   - **Failure Details**: Displays details for blobs that failed during processing. <br>

## Expected results Script convert-blobs-to-block-blob-law-container

![Expected Results](https://github.com/user-attachments/assets/73bf0771-8dd5-45d7-b00f-fc7a9ecbcf94)

## Expected results Script convert-blobs-to-block-blob-all-container

![Expected Results](https://github.com/user-attachments/assets/8b88330c-8784-4f3a-90a8-ee3d30616c84)

> [!NOTE]
> Some numbers or GUIDs that do not correspond to container names may appear in the results. The source of these values is unclear, but they do not impact the conversion process. They may be related to information from the Azure API or blob metadata, but no effect on the final outcome has been observed.

![Expected Results](https://github.com/user-attachments/assets/a67d1932-4b5a-4d9e-8c9b-e0ddac2567b7)

## Expected results CSV report
![Expected Results](https://github.com/user-attachments/assets/3252456c-4d50-41ac-b203-b1b731e0c5d2)

> [!WARNING]
> This code was developed with the assistance of artificial intelligence and is provided "as-is" without any warranties or guarantees. Although extensive testing has been performed with successful results, the author and any associated parties are not responsible for any direct or indirect damages, losses, or issues that may arise from the use or misuse of this code. It is the responsibility of the user to thoroughly review, test, and validate the code in their own environment before deploying it in a production setting. By using this code, you acknowledge that you assume all risks associated with its use.
