# 📂 Azure File Share Snapshot Lease Breaker

This repository provides a **Python tool** to safely manage **leases on Azure File Share snapshots**.  
You can list, inspect, and selectively break leases on snapshots **older than a retention threshold you control**.

---

## ✨ Features

✅ Fully **agnostic** — works with **any storage account**, file share, or retention policy  
✅ Robust parsing for Azure snapshot timestamps with **7-digit fractions**  
✅ Shows a clear **lease status table** (status, state, lease ID)  
✅ **Prompts you interactively** before breaking leases  
✅ Handles **dynamic retention days**: pass it as a CLI flag or input interactively  
✅ Zero hardcoded secrets — you control all inputs

---

## 🚀 Use Cases

- **Automate lease cleanup:** Unlock old snapshots to enable deletion  
- **Control snapshot lifecycle:** Protect recent snapshots, clean up older ones  
- **Integrate into storage maintenance workflows**

---

## ⚙️ Requirements

- Python 3.8+
- Install dependencies:

```bash
pip install azure-storage-file-share
