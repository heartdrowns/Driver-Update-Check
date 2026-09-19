"""
Driver Scanner — Installed Driver Inventory

Uses WMI (Win32_PnPSignedDriver) to enumerate installed drivers and
collect their metadata: device name, manufacturer, driver version,
driver date, provider, INF name, and hardware ID.

Requires pywin32 (for COM/WMI access via win32com).
"""

import time

import pythoncom
import win32com.client

from app_logger import get_logger
from com_utils import finalize_com

log = get_logger("scanner")


class DriverInfo:
    """Container for a single installed driver's metadata."""

    def __init__(
        self,
        device_name,
        manufacturer,
        driver_version,
        driver_date,
        provider,
        inf_name,
        hardware_id,
    ):
        self.device_name = device_name
        self.manufacturer = manufacturer
        self.driver_version = driver_version
        self.driver_date = driver_date
        self.provider = provider
        self.inf_name = inf_name
        self.hardware_id = hardware_id

    def to_dict(self):
        return {
            "device_name": self.device_name,
            "manufacturer": self.manufacturer,
            "driver_version": self.driver_version,
            "driver_date": self.driver_date,
            "provider": self.provider,
            "inf_name": self.inf_name,
            "hardware_id": self.hardware_id,
        }

    def __repr__(self):
        return f"DriverInfo({self.device_name!r}, v{self.driver_version})"


def scan_installed_drivers():
    """
    Scan installed drivers using WMI.

    Returns:
        list[DriverInfo]: Installed driver entries with metadata.
    """
    log.info("Starting installed driver scan")
    started = time.time()

    # Declared up front so the `finally` block can clear every COM proxy
    # before COM is uninitialized. This path had the same latent deadlock as
    # the update checker; it survived by luck rather than by design.
    wmi = None
    wmi_service = None
    drivers = None
    d = None

    # CoInitialize is required when calling COM from a non-main thread.
    pythoncom.CoInitialize()
    log.debug("COM initialized for scan thread")
    try:
        log.debug("Connecting to WMI service (root\\cimv2)")
        wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        wmi_service = wmi.ConnectServer(".", r"root\cimv2")
        log.debug("WMI connection established")

        query = "SELECT * FROM Win32_PnPSignedDriver"
        log.debug("Executing WMI query: %s", query)
        drivers = wmi_service.ExecQuery(query)

        results = []
        skipped = 0
        for d in drivers:
            device_name = _safe(d.DeviceName)
            manufacturer = _safe(d.Manufacturer)
            driver_version = _safe(d.DriverVersion)
            driver_date = _safe(d.DriverDate)
            provider = _safe(d.DriverProviderName)
            inf_name = _safe(d.InfName)
            hardware_id = _safe(d.HardwareID)

            # Skip entries that are just empty placeholders
            if not device_name and not hardware_id:
                skipped += 1
                continue

            log.debug(
                "Driver found: %s | ver=%s | provider=%s | inf=%s",
                device_name or "(unnamed)",
                driver_version or "-",
                provider or "-",
                inf_name or "-",
            )

            results.append(
                DriverInfo(
                    device_name=device_name,
                    manufacturer=manufacturer,
                    driver_version=driver_version,
                    driver_date=driver_date,
                    provider=provider,
                    inf_name=inf_name,
                    hardware_id=hardware_id,
                )
            )

        elapsed = time.time() - started
        log.info(
            "Scan complete: %d drivers found, %d empty entries skipped (%.2fs)",
            len(results), skipped, elapsed,
        )
        # `results` holds only plain Python values, never COM proxies.
        return results
    except Exception:
        log.exception("Driver scan failed")
        raise
    finally:
        # Order matters: drop proxies, then collect, then uninitialize.
        d = None
        drivers = None
        wmi_service = None
        wmi = None
        finalize_com("scan thread")


def _safe(value):
    """
    Safely extract a string from a COM variant.

    Some WMI properties (notably HardwareID) come back as arrays rather than
    scalars. Joining them keeps the display readable instead of leaking a
    Python tuple repr into the UI.
    """
    if value is None:
        return ""
    try:
        if isinstance(value, (list, tuple)):
            return "; ".join(str(v) for v in value if v is not None)
        return str(value)
    except Exception:
        return ""


def format_driver_date(raw_date):
    """
    Convert a WMI datetime string (e.g. '20230115000000.000000+000')
    into a readable format: '2023-01-15'.

    Returns the original string if parsing fails.
    """
    if not raw_date or len(raw_date) < 8:
        return raw_date
    try:
        return f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    except Exception:
        return raw_date
