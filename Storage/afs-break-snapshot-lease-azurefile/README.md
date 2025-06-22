# Azure File Share Snapshot Lease Breaker

![Python](https://img.shields.io/badge/Python-3.6%2B-blue)
![Azure](https://img.shields.io/badge/Azure-Storage-blue)
![Azure SDK](https://img.shields.io/badge/Azure%20SDK-File%20Share-brightgreen)

![Status](https://img.shields.io/badge/Status-Active%20Development-orange)
![License](https://img.shields.io/badge/License-MIT-yellowgreen)
![Contributions](https://img.shields.io/badge/Contributions-Welcome-brightgreen)

## Overview

Welcome to the **Azure File Share Snapshot Lease Breaker**! 🎉 This Python script simplifies the management of Azure File Share snapshots by identifying and breaking leases on snapshots that are either older than a specified retention period or locked and leased. This tool is perfect for automating cleanup tasks, ensuring your storage account remains tidy and manageable, and ultimately contributing to **cost-effective** Azure storage management.

Built with **flexibility** and **ease of use** in mind, the script supports both Azure Storage Account Key and Entra ID (Azure AD) authentication, interactive prompts, and clear output to keep you informed every step of the way. Whether you're a cloud admin or a developer, this script makes snapshot lease management a breeze! 🚀

## Table of Contents

- [Features](#features)
- [Technologies Used](#technologies-used)
- [Getting Started - Prerequisites](#getting-started---prerequisites)
- [Installation](#installation)
- [Usage](#usage)
- [Authentication Methods](#authentication-methods)
- [Permissions](#permissions)
- [Example Output](#example-output)
- [Troubleshooting](#troubleshooting)
- [Future Improvements](#future-improvements)
- [Collaboration & Contribution](#collaboration--contribution)
- [Contact](#contact)
- [License](#license)

## Features

- 🔒 **Dual Authentication**: Choose between Azure Storage Account Key or Entra ID (Azure AD) with InteractiveBrowserCredential.
- 🕰️ **Retention-Based Cleanup**: Automatically targets snapshots older than a user-defined retention period, helping to reduce unnecessary storage costs.
- 🔧 **Lease Management**: Optionally breaks leases on newer snapshots that are locked and leased, with user confirmation.
- 📥 **Interactive Prompts**: User-friendly input collection for account details, file share name, and retention period if not provided via command-line arguments.
- 📊 **Clear Reporting**: Displays a table of snapshots with lease status and a summary of lease-breaking results.
- 🛡️ **Robust Error Handling & Logging**: Gracefully handles Azure service errors and unexpected issues, logging detailed information to a rotating file.

## Technologies Used

- **[Python](https://www.python.org/)**: Core language for the script (version 3.6+).
- **[Azure Storage File Share SDK](https://docs.microsoft.com/en-us/python/api/azure-storage-file-share/)**: Manages Azure File Shares and snapshots.
- **[Azure Identity SDK](https://docs.microsoft.com/en-us/python/api/azure-identity/)**: Secure authentication via Entra ID.
- **[Azure APIs](https://learn.microsoft.com/en-us/rest/api/storageservices/)**: RESTful APIs for interacting with Azure Storage.

## Getting Started - Prerequisites

Before you dive in, ensure you have the following:

1. **Python 3.6+**: [Install Python](https://www.python.org/downloads/) if not already set up.
2. **Azure Subscription**: An active Azure account with a Storage Account and File Shares containing snapshots.
3. **Git**: [Install Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) to clone the repository.
4. **Azure Permissions**: Ensure your account or identity has the necessary permissions as detailed in the [Permissions](#permissions) section.

## Installation

1. **Clone the Repository**:

    ```bash
    git clone https://github.com/your-username/afs-snapshot-lease-breaker.git
    cd afs-snapshot-lease-breaker
    ```

2. **Install Python Dependencies**:

    ```bash
    pip install azure-storage-file-share azure-identity
    ```

3. **Verify Installation**: Ensure Python and pip are installed:

    ```bash
    python --version
    pip --version
    ```

## Usage

Run the script in interactive mode or provide arguments directly via the command line.

### Interactive Mode

```bash
python afs-snapshot-break-lease.py
```

The script will prompt for any missing required information:

- Authentication method (1 for Account Key, 2 for Entra ID)
- Storage Account name
- Storage Account Key (if using Account Key)
- File Share name
- Retention cutoff (in days)

### Command-line Arguments

You can provide all necessary parameters directly as command-line arguments for non-interactive execution:

```bash
python afs-snapshot-break-lease.py \
  --account <storage_account_name> \
  --auth <1|2> \
  --key <account_key> \
  --share <file_share_name> \
  --days <retention_days>
```

*Note*:

- `--auth`: Use `1` for Account Key, `2` for Entra ID.
- `--key`: This argument is only required if `--auth` is set to `1`.

## Authentication Methods

1. **Account Key**:
    - Simple but less secure; ideal for testing or specific automated scenarios where Entra ID is not feasible.
    - Select `1` in interactive mode or use `--auth 1`.
    - Requires providing the Storage Account name and key.

2. **Entra ID (Azure AD)**:
    - Secure, identity-based authentication using `InteractiveBrowserCredential`.
    - Select `2` in interactive mode or use `--auth 2`.
    - Opens a browser for login if needed, providing a secure way to authenticate without handling sensitive keys directly.

## Permissions

The account or identity used to run this script requires permissions to list Azure File Share snapshots and break their leases. The **Storage File Data Privileged Contributor** role is generally considered the most granular role for data plane operations on Azure Files.

However, during testing, it has been observed that the **Storage File Data Privileged Contributor** role might not always provide sufficient permissions for all lease-breaking operations performed by the script. While listing snapshots typically works, there is no option in the Azure Portal to break leases manually — this must be done programmatically. In such cases, the **Storage Account Contributor** role has been found to work. Please be aware that **Storage Account Contributor** grants broader permissions, including management plane access to the storage account, so use it with caution and only if strictly necessary.

To follow the principle of least privilege, assign these roles only at the **specific storage account** or **resource group** level, and avoid broader scopes whenever possible.

## Example Output

Here’s what you can expect when running the script:

```plaintext
🔐 Authentication: Entra ID (Interactive) | Account: mystorageaccount | Share: myshare | Cutoff days: 30

🔍 Checking snapshots for 'myshare' older than 30 days...

Snapshot                            Status     State      Older?
2023-10-01T12:00:00.000000Z         locked     leased     Yes
2023-11-15T12:00:00.000000Z         unlocked   available  No
2024-01-20T08:30:00.000000Z         locked     leased     No

⚠️ No snapshots older than cutoff, but 1 are locked.
Break their leases anyway? (y/n): y

Snapshot 2024-01-20T08:30:00.000000Z — SUCCESS

=== FINAL SUMMARY ===
Snapshot                            Result
2023-10-01T12:00:00.000000Z         SUCCESS
2024-01-20T08:30:00.000000Z         SUCCESS

✅ Total succeeded: 2
❌ Total failed: 0

Detailed log: C:\Users\username\snapshot-lease-breaker\error-log.20240622_143000.log
```

## Troubleshooting

- **ResourceNotFoundError**: Verify the Storage Account name, File Share name, or that the specified snapshot exists.
- **HttpResponseError**: Check your credentials, ensure the identity has the correct permissions (see [Permissions](#permissions)), or verify the Azure service status.
- **ModuleNotFoundError**: Ensure `azure-storage-file-share` and `azure-identity` are installed as per the [Installation](#installation) steps.
- **Timestamp Parsing Issues**: The script handles various timestamp formats, but if issues persist, please report them.
**Detailed Errors:** For any failures, check the detailed log file specified at the end of the script’s output (e.g., `Detailed log: /Users/username/snapshot-lease-breaker/error-log.YYYYMMDD_HHMMSS.log` on macOS/Linux, or `C:\Users\username\snapshot-lease-breaker\error-log.YYYYMMDD_HHMMSS.log` on Windows).

## Future Improvements

This section can be updated or expanded in the future as needed or upon request.

## Collaboration & Contribution

We love community input! 🌟 Whether you’re a Python pro or just getting started with Azure, your contributions can make this tool even better. Here’s how you can help:

- **Report Issues**: Found a bug? [Open an issue](https://github.com/eliaquimbrandao/azure-storage-scripts/issues).
- **Suggest Features**: Have an idea? Share it with us!
- **Submit Pull Requests**: Fork the repo and contribute code.

Let’s build a robust tool for Azure snapshot management together!

## Contact

Questions or feedback? Reach out:

- **GitHub**: [Your GitHub Profile](https://github.com/eliaquimbrandao)
- **LinkedIn**: [Your LinkedIn Profile](https://www.linkedin.com/in/eliaquim/)

## License

Made with ❤️ by [Eliaquim Brandao](https://github.com/eliaquimbrandao)

This project is licensed under the [MIT License](https://choosealicense.com/licenses/mit/).
