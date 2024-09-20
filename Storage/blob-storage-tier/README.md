## Description Script

- This PowerShell script retrieves information about BlockBlob tiers, PageBlobs, and Append Blobs in a specified Azure Storage Blob Service. It calculates the percentage each BlockBlob tier occupies and generates summary statistics, including total sizes in bytes, kilobytes, megabytes, and gigabytes, with the possibility of exporting to csv for file validation. <br>

> [!NOTE]
> PageBlobs and Append Blobs do not support access tiers, so their access tier results will be blank.

> [!IMPORTANT]
> All testing of this code has been performed in a small environment with a low workload. As a result, the current implementation may not be optimized for handling high-speed or heavy workloads. Please keep this in mind if you plan to use it in larger environments.
> 
> If you have suggestions for improvements or encounter any issues, feel free to contribute or open a discussion. I will update the code as needed, and once optimized for higher workloads, this note will be removed.

## Detailed Operation

- **Connects to Azure:** Uses the Subscription ID to connect and select the appropriate Azure subscription. <br>
- **Fetches Blob Details:** Gathers information about BlockBlobs, PageBlobs, and Append Blobs, calculating the percentage each tier occupies in storage. <br>
- **Handles Access Tiers:**  For PageBlobs and Append Blobs, the access tier field will be blank since they do not support tiers. For BlockBlobs, the script identifies and categorizes them into access tiers, including Hot, Cool, Cold, and Archive, allowing for better management and optimization of storage costs based. <br>
- **Generates Summary Statistics:** Groups blobs by their access tier and calculates various statistics, including total size in bytes, kilobytes, megabytes, and gigabytes, as well as the percentage of each access tier occupying the total storage. <br>
- **CSV Report Generation**: The script collects and organizes blob details, including the container name, blob name, blob type, access tier, and size to a csv file. <br>

The script consists of three main functions: <br>
- `Get-AzureStorageBlobDetails`: Connects to Azure and fetches blob details from the specified storage account. <br>
- `Get-AzureStorageBlobSummary`: Computes summary statistics for blobs, grouped by access tier. <br>
- `Export-AzureBlobReport`: The function performs several key operations to ensure a smooth export of blob details to a CSV file. <br>


## Description Results

- The script produces a table of summary statistics for blobs, organized by access tier. The output includes the following columns: <br>
  - **TotalTier:** The number of blobs in each access tier. <br>
  - **AccessTier:** The access tier of the blobs (e.g., Cool, Hot). <br>
  - **Percentage:** The percentage of blobs in each access tier relative to the total number of blobs. <br>
  - **TotalSizeBytes:** The total size of blobs in bytes for each access tier. <br>
  - **TotalSizeKB:** The total size of blobs in kilobytes for each access tier. <br>
  - **TotalSizeMB:** The total size of blobs in megabytes for each access tier. <br>
  - **TotalSizeGB:** The total size of blobs in gigabytes for each access tier. <br>

```plaintext
TotalTier AccessTier       Percentage TotalSizeBytes TotalSizeKB TotalSizeMB TotalSizeGB
--------- ----------       ---------- -------------- ----------- ----------- -----------
      177            59.5959595959596    16696180933 16304864.19    15922.72       15.55
        5 Archive    1.68350168350168         478992      467.77        0.46           0
       90 Hot        30.3030303030303       28527155    27858.55       27.21        0.03
       12 Cool       4.04040404040404         382968      373.99        0.37           0
       11 Cold        3.7037037037037       25325394    24731.83       24.15        0.02
```

## Expected results in terminal
![Expected Results](https://github.com/user-attachments/assets/bf3b3cc0-7e84-4bc9-ac54-aa5016feaae2)

## Expected results for csv report
![Expected Results](https://github.com/user-attachments/assets/38ed0709-309a-43f1-b32a-9d0988f46d6c)

> [!WARNING]
> This code was developed with the assistance of artificial intelligence and is provided "as-is" without any warranties or guarantees. Although extensive testing has been performed with successful results, the author and any associated parties are not responsible for any direct or indirect damages, losses, or issues that may arise from the use or misuse of this code. It is the responsibility of the user to thoroughly review, test, and validate the code in their own environment before deploying it in a production setting. By using this code, you acknowledge that you assume all risks associated with its use.
