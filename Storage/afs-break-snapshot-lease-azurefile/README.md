# Azure File Share Snapshot Lease Breaker

![Python](https://img.shields.io/badge/Python-3.6%2B-blue)
![Azure](https://img.shields.io/badge/Azure-Storage-blue)
![Azure SDK](https://img.shields.io/badge/Azure%20SDK-File%20Share-brightgreen)

![Status](https://img.shields.io/badge/Status-Active%20Development-orange)
![License](https://img.shields.io/badge/License-MIT-yellowgreen)
![Contributions](https://img.shields.io/badge/Contributions-Welcome-brightgreen)

## Overview

Welcome to the **Azure File Share Snapshot Lease Breaker**! 🎉 This Python script simplifies the management of Azure File Share snapshots by identifying and breaking leases on snapshots that are either older than a specified retention period or locked and leased. This tool is perfect for automating cleanup tasks, ensuring your storage account remains tidy and manageable.

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
- 🕰️ **Retention-Based Cleanup**: Automatically target snapshots older than a user-defined retention period.
- 🔧 **Lease Management**: Optionally break leases on newer snapshots that are locked and leased.
- 📥 **Interactive Prompts**: User-friendly input collection for account details, file share name, and retention period.
- 📊 **Clear Reporting**: Displays a table of snapshots with lease status and a summary of lease-breaking results.
- 🛡️ **Robust Error Handling**: Gracefully handles Azure service errors with detailed feedback.

## Technologies Used

- **[Python](https://www.python.org/)**: Core language for the script (version 3.6+).
- **[Azure Storage File Share SDK](https://docs.microsoft.com/en-us/python/api/azure-storage-file-share/)**: Manage Azure File Shares and snapshots.
- **[Azure Identity SDK](https://docs.microsoft.com/en-us/python/api/azure-identity/)**: Secure authentication via Entra ID.
- **[Azure APIs](https://learn.microsoft.com/en-us/rest/api/storageservices/)**: RESTful APIs for interacting with Azure Storage.

## Getting Started - Prerequisites

Before you dive in, ensure you have the following:

1. **Python 3.6+**: [Install Python](https://www.python.org/downloads/) if not already set up.
2. **Azure Subscription**: An active Azure account with a Storage Account and File Shares containing snapshots.
3. **Git**: [Install Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) to clone the repository.
4. **Azure Permissions**: Ensure your account or identity has the **Storage File Data Contributor** role.

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

Run the script in one of three modes: interactive, with command-line arguments, or using environment variables.

### Interactive Mode

```bash
python afs-snapshot-break-lease.py
```

The script will prompt for:

- Authentication method (1 for Account Key, 2 for Entra ID)
- Storage Account name
- Account Key (if using Account Key)
- File Share name
- Retention period (in days)

### Command-line Arguments

```bash
python afs-snapshot-break-lease.py \
  --account-name <storage_account_name> \
  --auth-method <key|entra_id> \
  --account-key <account_key> \
  --file-share <file_share_name> \
  --retention-days <days>
```

*Note*: `--account-key` is required only for `--auth-method key`.

### Environment Variables

Set these variables to reduce prompts:

- `AZURE_STORAGE_ACCOUNT_NAME`
- `AZURE_STORAGE_ACCOUNT_KEY` (for Account Key authentication)
- `AZURE_FILE_SHARE_NAME`

Then run:

```bash
python afs-snapshot-break-lease.py
```

## Authentication Methods

1. **Account Key**:
   - Simple but less secure; ideal for testing.
   - Select `1` in interactive mode or use `--auth-method key`.
   - Provide the Storage Account name and key.

2. **Entra ID (Azure AD)**:
   - Secure, identity-based authentication using InteractiveBrowserCredential.
   - Select `2` in interactive mode or use `--auth-method entra_id`.
   - Opens a browser for login if needed.

## Permissions

The account or identity must have the **Storage File Data Contributor** role to manage snapshots and break leases. Assign this role at the Storage Account or resource group level via the Azure Portal or CLI.

## Example Output

Here’s what you can expect when running the script:

```plaintext
🔍 Checking snapshots for 'myshare' older than 30 days...

Snapshot                             Status     State      Older?
2023-10-01T12:00:00.000000Z          locked     leased     Yes
2023-11-15T12:00:00.000000Z          unlocked   available  No

=== FINAL SUMMARY ===
Snapshot                             Result
2023-10-01T12:00:00.000000Z          SUCCESS

✅ Total succeeded: 1
❌ Total failed: 0
```

## Troubleshooting

- **ResourceNotFoundError**: Verify Storage Account, File Share, or snapshot names.
- **HttpResponseError**: Check credentials, permissions, or Azure service status.
- **ModuleNotFoundError**: Ensure `azure-storage-file-share` and `azure-identity` are installed.
- **Timestamp Parsing Issues**: The script handles variable timestamp formats, but contact us if issues persist.

## Future Improvements

- 🚀 Add non-interactive mode for automated workflows.
- 📄 Support JSON/CSV output for integration with other tools.
- 🔍 Enable advanced snapshot filtering by tags or date ranges.
- ⚡ Implement concurrent processing for large snapshot lists.
- 📜 Add configurable logging levels for verbosity control.

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
