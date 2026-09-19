# Driver Update Checker

A Windows desktop application that scans your computer for installed drivers and checks for available updates through Windows Update.

## Features

- **Installed Driver Inventory** — Scans all installed drivers using WMI (`Win32_PnPSignedDriver`), showing device name, manufacturer, driver version, driver date, provider, INF name, and hardware ID.
- **Update Detection** — Searches Windows Update for available driver updates using the Windows Update Agent (WUA) COM API.
- **Clean GUI** — Two-tab interface (Installed Drivers / Available Updates) built with Tkinter/ttk.
- **Export** — Export driver inventory and update reports to CSV.
- **Full Logging** — Every action, scan result, and error is logged to a rotating file on disk. A built-in **Logs** viewer shows live output with level filtering and search.
- **Quick Access** — Open Windows Optional Updates settings or Device Manager directly from the app.
- **Non-blocking** — All scans run in background threads so the UI stays responsive.

## Prerequisites

- **Windows 10 or later** (Windows 11 recommended)
- **Python 3.8+**
- **pywin32** package

## Installation

Python must be installed from [python.org](https://www.python.org/downloads/) with **"Add python.exe to PATH"** checked during setup. The `python` command that ships with Windows by default is only a placeholder that redirects to the Microsoft Store.

```bash
cd driver_update_checker
py -m pip install -r requirements.txt
```

## Usage

Easiest option — double-click **`run.bat`**. It locates a working Python install, installs missing dependencies, and launches the app.

Or run it manually:

```bash
py main.py
```

Use `py` rather than `python`. The `py` launcher resolves to a real interpreter and never collides with the Microsoft Store alias stub.

### Troubleshooting: "Python was not found"

This means no real Python interpreter is installed, or the Store alias is shadowing the one you have.

- **Not installed:** get it from [python.org/downloads](https://www.python.org/downloads/), check **"Add python.exe to PATH"**, then open a **new** terminal (PATH changes don't reach already-open windows).
- **Already installed:** go to **Settings → Apps → Advanced app settings → App execution aliases** and toggle **off** `python.exe` and `python3.exe`.

Prefer the python.org installer over the Microsoft Store build — the Store version runs sandboxed, which can interfere with the WMI and COM calls this app depends on.

### How to use

1. Click **Scan Installed Drivers** to populate the Installed Drivers tab with your system's driver inventory.
2. Click **Check for Updates** to search Windows Update for available driver updates.
3. Review results in either tab. Use the horizontal/vertical scrollbars as needed.
4. Click **Open Optional Updates** to open the Windows Optional Updates settings page where you can install available driver updates.
5. Use **File > Export** to save a CSV report of installed drivers or available updates.

## How It Works

### Driver Scanning

The app queries WMI's `Win32_PnPSignedDriver` class, which provides detailed metadata about every installed Plug-and-Play driver on the system, including:

| Field | Description |
|---|---|
| Device Name | Human-readable device name |
| Manufacturer | Device manufacturer |
| Driver Version | Installed driver version string |
| Driver Date | Driver release date |
| Provider | Driver provider (e.g., NVIDIA, Intel) |
| INF Name | INF file that installed the driver |
| Hardware ID | Hardware identifier for the device |

### Update Checking

The app uses the **Windows Update Agent (WUA) API** via COM (`Microsoft.Update.Session`) to search for driver-class updates that are not yet installed. This is the same mechanism Windows uses internally for optional driver updates.

The search criteria used is: `IsInstalled=0 and Type='Driver'`

### Threading Model

Scans run in background `threading.Thread` workers. Results are communicated back to the main Tkinter thread via a `queue.Queue`, with `root.after()` polling to safely update the UI. COM objects are properly initialized/uninitialized per thread using `pythoncom.CoInitialize()` / `CoUninitialize()`.

## Project Structure

```
driver_update_checker/
├── run.bat              # Windows launcher (finds Python, installs deps)
├── main.py              # Entry point, OS check, exception hooks
├── app_logger.py        # Rotating file logging + in-memory buffer
├── driver_scanner.py    # WMI-based driver inventory scanning
├── LICENSE              # MIT
├── .gitignore           # excludes logs/ and CSV exports (contain hardware IDs)
├── update_checker.py    # Windows Update Agent driver update search
├── com_utils.py         # COM teardown; releases proxies before CoUninitialize
├── log_viewer.py        # Logs window (filter, search, export)
├── gui.py               # Tkinter/ttk GUI application
├── tests/
│   └── test_gui_headless.py   # Headless GUI + logging test suite
├── requirements.txt     # Python dependencies
└── README.md            # This file
```

## Logging

Everything the app does is logged. Click the **Logs** button in the toolbar (or **Tools → View Logs**) to open the viewer.

**Log file location:**

```
%LOCALAPPDATA%\DriverUpdateChecker\logs\app.log
```

Files rotate at 2 MB with 3 backups kept, so logs never grow without bound.

**What gets logged:**

| Category | Examples |
|---|---|
| Lifecycle | App start/exit, window init, Python and OS version |
| User actions | Every button click and menu selection |
| WMI scanning | Connection, query text, each driver found, timing, skipped entries |
| Update checks | Session creation, search criteria, each update found, timing |
| Exports | Destination path, row count, cancellations |
| Errors | Full tracebacks for every failure, including uncaught exceptions on worker threads |

**Viewer features:**

- **Level filter** — ALL / DEBUG / INFO / WARNING / ERROR, color-coded by severity
- **Search** — live substring filter with match highlighting
- **Auto-refresh** — updates once per second while a scan runs; toggleable
- **Copy All / Save As** — grab logs for sharing or bug reports
- **Open Log Folder** — jump straight to the file in Explorer

Uncaught exceptions on both the main thread and worker threads are routed into the log via `sys.excepthook` and `threading.excepthook`, so a crash always leaves a record.

## Testing

A headless test suite covers GUI construction, table rendering, logging, and the log viewer. It stubs the Windows-only COM modules so it can run anywhere:

```bash
py tests\test_gui_headless.py
```

55 checks, including edge cases like drivers with empty fields and zero-result update searches.

## Limitations

- The Windows Update Agent may not return a clean per-device version match for every installed driver. Update titles and descriptions are shown as-is from WUA metadata.
- The app is a **checker/reporter** — it does not automatically install updates. Use the "Open Optional Updates" button to install available updates through Windows Settings.
- Windows Update must be enabled and accessible. If disabled by group policy or offline, the update check will show an error.
- Running as administrator is recommended for the most complete driver inventory.

## Future Enhancements

- Direct driver installation through the WUA API (with explicit user confirmation)
- Vendor-specific update checking (Intel, NVIDIA, AMD websites)
- Driver backup and restore before updates
- Scheduled automatic scans
- Dark mode theme

## License

Released under the MIT License — see [LICENSE](LICENSE).

## Privacy note

Log files and CSV exports contain a full inventory of the machine they ran on,
including device names, driver versions, INF filenames, and hardware IDs. The
included `.gitignore` excludes `logs/` and `*.csv` for that reason. Review any
log before attaching it to a public bug report.
