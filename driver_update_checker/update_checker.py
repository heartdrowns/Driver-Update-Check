"""
Update Checker — Windows Update Driver Search

Uses the Windows Update Agent (WUA) COM API to search for available
driver updates. The WUA API is the authoritative source for driver
updates published through Microsoft's Windows Update infrastructure.

Requires pywin32.
"""

import time

import pythoncom
import win32com.client

from app_logger import get_logger
from com_utils import finalize_com

log = get_logger("updates")


class DriverUpdate:
    """Container for an available driver update from Windows Update."""

    def __init__(
        self,
        title,
        description,
        driver_ver,
        publisher,
        kb_article,
        update_id,
        is_downloaded,
    ):
        self.title = title
        self.description = description
        self.driver_ver = driver_ver
        self.publisher = publisher
        self.kb_article = kb_article
        self.update_id = update_id
        self.is_downloaded = is_downloaded

    def to_dict(self):
        return {
            "title": self.title,
            "description": self.description,
            "driver_ver": self.driver_ver,
            "publisher": self.publisher,
            "kb_article": self.kb_article,
            "update_id": self.update_id,
            "is_downloaded": self.is_downloaded,
        }

    def __repr__(self):
        return f"DriverUpdate({self.title!r}, v{self.driver_ver})"


def check_for_driver_updates(progress_callback=None):
    """
    Search Windows Update for available driver updates.

    Args:
        progress_callback: Optional callable(current, maximum) for progress.

    Returns:
        list[DriverUpdate]: Available driver updates.

    Raises:
        RuntimeError: If Windows Update is unavailable or disabled.
    """
    log.info("Starting Windows Update driver search")
    started = time.time()

    # Declared up front so the `finally` block can clear every COM proxy
    # before COM is uninitialized. Releasing a proxy after CoUninitialize
    # deadlocks the thread (see com_utils for the full explanation).
    session = None
    searcher = None
    result = None
    update = None

    pythoncom.CoInitialize()
    log.debug("COM initialized for update thread")
    try:
        log.debug("Creating Microsoft.Update.Session")
        session = win32com.client.Dispatch("Microsoft.Update.Session")
        searcher = session.CreateUpdateSearcher()
        log.debug("Update searcher created")

        # Search for driver updates that are not yet installed.
        # Type='Driver' filters to driver-class updates only.
        criteria = "IsInstalled=0 and Type='Driver'"
        log.info("Search criteria: %s", criteria)

        if progress_callback:
            # WUA search is synchronous; we can't get real progress from it,
            # but we signal start/completion via the callback.
            progress_callback(0, 1)

        log.debug("Executing search (this can take 30-60s)")
        result = searcher.Search(criteria)
        log.info(
            "Search returned %d update(s) in %.2fs",
            result.Updates.Count, time.time() - started,
        )

        if progress_callback:
            progress_callback(1, 1)

        updates = []
        for i in range(result.Updates.Count):
            update = result.Updates.Item(i)

            title = _safe(update.Title)
            description = _safe_attr(update, "Description")
            driver_ver = _extract_version(title)
            publisher = _safe_attr(update, "DriverPublisher")
            kb_article = _extract_kb(update)
            update_id = _safe(update.Identity.UpdateID)
            is_downloaded = bool(_safe_attr(update, "IsDownloaded"))

            log.debug(
                "Update available: %s | ver=%s | publisher=%s | kb=%s | downloaded=%s",
                title, driver_ver or "-", publisher or "-",
                kb_article or "-", is_downloaded,
            )

            updates.append(
                DriverUpdate(
                    title=title,
                    description=description,
                    driver_ver=driver_ver,
                    publisher=publisher,
                    kb_article=kb_article,
                    update_id=update_id,
                    is_downloaded=is_downloaded,
                )
            )

        log.info(
            "Update check complete: %d driver update(s) available (%.2fs total)",
            len(updates), time.time() - started,
        )
        # `updates` holds only plain Python values, never COM proxies, so it
        # remains valid after the apartment is gone.
        return updates

    except pythoncom.com_error as e:
        log.exception("Windows Update COM error")
        raise RuntimeError(
            f"Windows Update search failed (COM error {e}): "
            "Windows Update may be disabled by policy, offline, or blocked."
        )
    except Exception as e:
        log.exception("Update check failed")
        raise RuntimeError(f"Failed to check for driver updates: {e}")
    finally:
        # Order matters: drop proxies, then collect, then uninitialize.
        update = None
        result = None
        searcher = None
        session = None
        finalize_com("update thread")


def _safe(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_attr(obj, attr_name):
    """Safely get an attribute from a COM object, returning '' if missing."""
    try:
        val = getattr(obj, attr_name)
        return _safe(val)
    except Exception:
        return ""


def _extract_version(title):
    """
    Attempt to extract a version string from an update title.
    Many driver updates include a version like '... 1.2.3.4 ...' in the title.
    """
    import re

    if not title:
        return ""
    # Look for patterns like "v1.2.3.4" or "1.2.3.4"
    match = re.search(r"(?:v?\s*)(\d+\.\d+(?:\.\d+)*)", title)
    if match:
        return match.group(1)
    return ""


def _extract_kb(update):
    """Extract KB article number from an update if available."""
    try:
        kbs = update.KBArticleIDs
        if kbs.Count > 0:
            return f"KB{kbs.Item(0)}"
    except Exception:
        pass
    return ""
