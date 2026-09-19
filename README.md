
# Driver Update Checker

A Windows desktop application that scans your computer for installed drivers and checks for available updates through Windows Update.

## Features

* **Installed Driver Inventory**: Scans all installed drivers using WMI (`Win32_PnPSignedDriver`), showing device name, manufacturer, driver version, driver date, provider, INF name, and hardware ID.


* **Update Detection**: Searches Windows Update for available driver updates using the Windows Update Agent (WUA) COM API.


* **Clean GUI**: Two-tab interface (Installed Drivers / Available Updates) built with Tkinter/ttk.


* **Export**: Export driver inventory and update reports to CSV.


* **Full Logging**: Every action, scan result, and error is logged to a rotating file on disk. A built-in Logs viewer shows live output with level filtering and search.


* **Quick Access**: Open Windows Optional Updates settings or Device Manager directly from the app.


* **Non-blocking**: All scans run in background threads so the UI stays responsive.



## Prerequisites & Installation

* **Windows 10 or later** (Windows 11 recommended).


* **Python 3.8+** installed from python.org (ensure "Add python.exe to PATH" is checked).


* **pywin32** package.



To install dependencies, run the following command in your terminal:

```bash
py -m pip install -r requirements.txt

```

## Usage

Double-click `run.bat` to automatically locate a working Python install, install missing dependencies, and launch the application.

Alternatively, run it manually via the command line:

```bash
py main.py

```

> **Note:** Use the `py` launcher rather than `python` to avoid colliding with the Microsoft Store alias stub, which runs sandboxed and can interfere with WMI and COM calls.
> 
> 

### Basic Workflow

1. Click **Scan Installed Drivers** to populate the Installed Drivers tab with your system's driver inventory.


2. Click **Check for Updates** to search Windows Update for available driver updates.


3. Review results in either tab.


4. Click **Open Optional Updates** to open the Windows Optional Updates settings page to install available updates.


5. Use **File > Export** to save a CSV report of installed drivers or available updates.



## Architecture & Implementation

* **Driver Scanning**: The application queries WMI's `Win32_PnPSignedDriver` class to extract detailed metadata about every installed Plug-and-Play driver on the system.


* **Update Checking**: Utilizes the Windows Update Agent (WUA) API via COM (`Microsoft.Update.Session`) to search for driver-class updates that are not yet installed (search criteria: `IsInstalled=0 and Type='Driver'`).


* **Threading Model**: Background scanning relies on `threading.Thread` workers, communicating results back to the main Tkinter thread via a `queue.Queue`. COM objects are safely initialized and uninitialized per thread using `pythoncom.CoInitialize()` and `pythoncom.CoUninitialize()`, with garbage collection triggered beforehand to prevent deadlocks.


* **Exception Handling**: Uncaught exceptions on both the main thread and worker threads are routed directly into the log file via `sys.excepthook` and `threading.excepthook`.



## Logging and Privacy

All application events, scan results, and error tracebacks are logged to a rotating file on disk.

**Log Location:**
`%LOCALAPPDATA%\DriverUpdateChecker\logs\app.log`

**Privacy Note:**
Log files and CSV exports write a full inventory of your machine's hardware to disk, including device names, driver versions, INF filenames, and hardware IDs. None of this data belongs in a public repository, which is why `logs/` and `*.csv` files are strictly excluded via `.gitignore`. Review any exported logs or CSVs before attaching them to a public bug report.

## License

This project is released under the MIT License. Copyright (c) 2026 Bedirhan Fidan.
