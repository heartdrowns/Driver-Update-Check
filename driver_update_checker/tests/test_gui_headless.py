"""
Headless GUI construction test.

The app targets Windows, but the Tkinter/ttk layer is cross-platform.
This test stubs the Windows-only COM modules (pythoncom, win32com) so the
GUI can be fully constructed under Xvfb on Linux. It catches the class of
bug that only appears at runtime: bad ttk style names, invalid widget
options, and mismatched column/value counts.

Run:  xvfb-run -a python3 tests/test_gui_headless.py
"""

import os
import sys
import time
import types

# Make the app package importable regardless of cwd.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)


# ── Stub the Windows-only COM modules ────────────────────────────────

def install_windows_stubs():
    """Register fake pythoncom / win32com modules so imports succeed."""
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: None
    pythoncom.CoUninitialize = lambda: None
    pythoncom.com_error = type("com_error", (Exception,), {})
    sys.modules["pythoncom"] = pythoncom

    win32com = types.ModuleType("win32com")
    client = types.ModuleType("win32com.client")

    def _dispatch(name):
        raise RuntimeError(f"COM unavailable in test harness (Dispatch {name!r})")

    client.Dispatch = _dispatch
    win32com.client = client
    sys.modules["win32com"] = win32com
    sys.modules["win32com.client"] = client


install_windows_stubs()

from driver_scanner import DriverInfo, format_driver_date  # noqa: E402
from update_checker import DriverUpdate, _extract_version  # noqa: E402


# ── Sample data mirroring real WMI / WUA shapes ──────────────────────

SAMPLE_DRIVERS = [
    DriverInfo(
        device_name="NVIDIA GeForce RTX 4070",
        manufacturer="NVIDIA",
        driver_version="31.0.15.3623",
        driver_date="20230815000000.000000+000",
        provider="NVIDIA",
        inf_name="oem42.inf",
        hardware_id="PCI\\VEN_10DE&DEV_2786",
    ),
    DriverInfo(
        device_name="Intel(R) Wi-Fi 6E AX211 160MHz",
        manufacturer="Intel Corporation",
        driver_version="22.240.0.6",
        driver_date="20240102000000.000000+000",
        provider="Intel",
        inf_name="netwtw10.inf",
        hardware_id="PCI\\VEN_8086&DEV_51F0",
    ),
    # Edge case: empty/missing fields must not crash row rendering.
    DriverInfo(
        device_name="Generic PnP Monitor",
        manufacturer="",
        driver_version="",
        driver_date="",
        provider="",
        inf_name="monitor.inf",
        hardware_id="MONITOR\\Default",
    ),
]

SAMPLE_UPDATES = [
    DriverUpdate(
        title="NVIDIA - Display - 32.0.15.6094",
        description="NVIDIA display driver update.",
        driver_ver="32.0.15.6094",
        publisher="NVIDIA",
        kb_article="KB5031234",
        update_id="{a1b2c3d4-0000-0000-0000-000000000001}",
        is_downloaded=False,
    ),
    DriverUpdate(
        title="Intel Corporation - Net - 23.10.0.5",
        description="",
        driver_ver="23.10.0.5",
        publisher="Intel",
        kb_article="",
        update_id="{a1b2c3d4-0000-0000-0000-000000000002}",
        is_downloaded=True,
    ),
]


def main():
    failures = []

    def check(label, fn):
        try:
            fn()
            print(f"  PASS  {label}")
        except Exception as e:
            print(f"  FAIL  {label}: {type(e).__name__}: {e}")
            failures.append((label, e))

    print("\n=== Pure-function tests ===")

    check("format_driver_date parses WMI timestamp", lambda: _assert_eq(
        format_driver_date("20230815000000.000000+000"), "2023-08-15"))
    check("format_driver_date tolerates empty input", lambda: _assert_eq(
        format_driver_date(""), ""))
    check("format_driver_date tolerates short input", lambda: _assert_eq(
        format_driver_date("2023"), "2023"))
    check("_extract_version pulls version from title", lambda: _assert_eq(
        _extract_version("NVIDIA - Display - 32.0.15.6094"), "32.0.15.6094"))
    check("_extract_version returns empty when absent", lambda: _assert_eq(
        _extract_version("Some Driver Update"), ""))

    print("\n=== GUI construction tests ===")

    from gui import DriverUpdateApp
    from log_viewer import LogViewer as LogViewerCls

    app = None

    def build():
        nonlocal app
        app = DriverUpdateApp()

    check("DriverUpdateApp constructs (styles, menu, toolbar, tabs)", build)

    if app is None:
        _report(failures)
        return

    check("Progressbar start/stop with custom style",
          lambda: (app.progress.start(), app.progress.stop()))

    check("Populate Installed Drivers tree", lambda: app._populate_tree(
        app.tree_installed, SAMPLE_DRIVERS, app._driver_row))

    check("Installed tree row count matches input", lambda: _assert_eq(
        len(app.tree_installed.get_children()), len(SAMPLE_DRIVERS)))

    check("Populate Available Updates tree", lambda: app._populate_tree(
        app.tree_updates, SAMPLE_UPDATES, app._update_row))

    check("Updates tree row count matches input", lambda: _assert_eq(
        len(app.tree_updates.get_children()), len(SAMPLE_UPDATES)))

    check("Row value count matches declared columns (installed)",
          lambda: _assert_row_widths(app.tree_installed))
    check("Row value count matches declared columns (updates)",
          lambda: _assert_row_widths(app.tree_updates))

    check("Scan-complete handler updates UI",
          lambda: app._on_scan_complete(SAMPLE_DRIVERS))
    check("Updates-complete handler updates UI",
          lambda: app._on_updates_complete(SAMPLE_UPDATES))
    check("Updates-complete handler with zero results",
          lambda: app._on_updates_complete([]))
    check("Status bar setter", lambda: app._set_status("test status"))

    # Re-populate so the export paths have data to work with.
    app.installed_drivers = SAMPLE_DRIVERS
    app.available_updates = SAMPLE_UPDATES
    check("CSV row serialization (installed)", lambda: _assert_eq(
        len(app._driver_row_dict(SAMPLE_DRIVERS[0])), 7))
    check("CSV row serialization (updates)", lambda: _assert_eq(
        len(app._update_row_dict(SAMPLE_UPDATES[0])), 6))

    print("\n=== Hang-regression tests ===")

    # The original bug: a handler raised, the reschedule after the drain loop
    # never ran, polling stopped forever, and the UI stayed on its last status.
    def poll_loop_survives_handler_error():
        app._enter_busy("pretend work")
        original = app._on_updates_complete

        def boom(_updates):
            raise RuntimeError("simulated render failure")

        app._on_updates_complete = boom
        try:
            app._msg_queue.put(("updates_done", SAMPLE_UPDATES))
            app._poll_queue()          # must not raise
            if app._busy:
                raise AssertionError("UI left busy after handler error")
            if str(app.btn_check["state"]) == "disabled":
                raise AssertionError("buttons left disabled after handler error")
        finally:
            app._on_updates_complete = original

    check("Poll loop survives a handler exception", poll_loop_survives_handler_error)

    def poll_loop_still_alive():
        """After an error the loop must still process new messages."""
        app._msg_queue.put(("updates_done", SAMPLE_UPDATES))
        app._poll_queue()
        _assert_eq(len(app.tree_updates.get_children()), len(SAMPLE_UPDATES))

    check("Poll loop still processes messages after an error", poll_loop_still_alive)

    def watchdog_recovers_stuck_ui():
        """Worker dies without posting: watchdog must unstick the UI."""
        app._enter_busy("stuck operation")

        class DeadThread:
            @staticmethod
            def is_alive():
                return False

        app._worker = DeadThread()
        while not app._msg_queue.empty():
            app._msg_queue.get_nowait()
        app._watchdog_check()
        if app._busy:
            raise AssertionError("watchdog did not reset busy state")

    check("Watchdog recovers a stuck UI", watchdog_recovers_stuck_ui)

    def concurrent_start_rejected():
        """A second operation must not clobber one already running."""
        app._reset_busy_state()
        _assert_eq(app._enter_busy("first"), True)
        _assert_eq(app._enter_busy("second"), False)
        app._reset_busy_state()

    check("Second concurrent operation is rejected", concurrent_start_rejected)

    def worker_always_posts():
        """A failing worker function must still post an error message."""
        while not app._msg_queue.empty():
            app._msg_queue.get_nowait()

        def failing():
            raise RuntimeError("worker blew up")

        app._run_worker(failing, "updates_done", "Test op", app._generation)
        if app._msg_queue.empty():
            raise AssertionError("worker posted nothing on failure")
        item = app._msg_queue.get_nowait()
        kind, payload = item[0], item[1]
        _assert_eq(kind, "error")
        if "blew up" not in str(payload):
            raise AssertionError(f"unexpected error payload: {payload!r}")

    check("Failing worker still posts a result", worker_always_posts)

    def watchdog_catches_hung_worker():
        """
        The reported failure: worker reports alive but never posts. A liveness
        check cannot catch this, so the time budget must.
        """
        import gui as gui_mod

        app._reset_busy_state()
        app._enter_busy("hung operation", "Update check")

        class LiveThread:
            @staticmethod
            def is_alive():
                return True

        app._worker = LiveThread()
        while not app._msg_queue.empty():
            app._msg_queue.get_nowait()

        # Still inside its budget: must stay busy.
        app._watchdog_check()
        if not app._busy:
            raise AssertionError("watchdog reset too early")

        # Push the start time past the budget.
        budget = gui_mod.TIMEOUTS["Update check"]
        app._busy_started = time.monotonic() - (budget + 1)
        app._watchdog_check()
        if app._busy:
            raise AssertionError("watchdog did not catch hung worker")

    check("Watchdog catches a hung (still-alive) worker", watchdog_catches_hung_worker)

    def cancel_releases_ui():
        app._reset_busy_state()
        app._enter_busy("cancel me", "Update check")
        app._force_reset()
        if app._busy:
            raise AssertionError("cancel did not release busy state")
        if str(app.btn_check["state"]) == "disabled":
            raise AssertionError("cancel left buttons disabled")
        _assert_eq(str(app.btn_cancel["state"]), "disabled")

    check("Cancel button releases the UI", cancel_releases_ui)

    def stale_result_discarded():
        """A cancelled worker finishing late must not overwrite the UI."""
        app._reset_busy_state()
        app._enter_busy("work", "Update check")
        stale_gen = app._generation
        app._force_reset()                     # bumps generation

        app._populate_tree(app.tree_updates, [], app._update_row)
        app._msg_queue.put(("updates_done", SAMPLE_UPDATES, stale_gen))
        app._poll_queue()
        _assert_eq(len(app.tree_updates.get_children()), 0)

    check("Stale result from cancelled worker is discarded", stale_result_discarded)

    def current_result_accepted():
        """A result matching the current generation must still render."""
        app._reset_busy_state()
        app._enter_busy("work", "Update check")
        app._msg_queue.put(("updates_done", SAMPLE_UPDATES, app._generation))
        app._poll_queue()
        _assert_eq(len(app.tree_updates.get_children()), len(SAMPLE_UPDATES))

    check("Current-generation result is accepted", current_result_accepted)

    def heartbeat_logged():
        """Heartbeats must appear so a frozen UI loop is detectable."""
        import app_logger

        app._reset_busy_state()
        app._enter_busy("heartbeat test", "Update check")

        class LiveThread:
            @staticmethod
            def is_alive():
                return True

        app._worker = LiveThread()
        app._busy_started = time.monotonic() - 10   # force heartbeat due
        app._last_heartbeat = 0.0
        app._watchdog_check()
        app._reset_busy_state()

        blob = "\n".join(app_logger.get_buffered_lines())
        if "Heartbeat:" not in blob:
            raise AssertionError("no heartbeat written to log")

    check("Heartbeat is written while busy", heartbeat_logged)

    print("\n=== COM teardown ordering tests ===")

    def teardown_collects_before_uninitialize():
        """
        The actual cause of the stuck GUI: CoUninitialize ran while COM proxies
        were still alive, so their Release() calls deadlocked on the way out.
        gc.collect() MUST run first, while the apartment is still valid.
        """
        import com_utils

        calls = []
        orig_collect = com_utils.gc.collect
        orig_uninit = com_utils.pythoncom.CoUninitialize

        com_utils.gc.collect = lambda *a, **kw: (calls.append("collect"), 0)[1]
        com_utils.pythoncom.CoUninitialize = lambda *a, **kw: calls.append("uninit")
        try:
            com_utils.finalize_com("test")
        finally:
            com_utils.gc.collect = orig_collect
            com_utils.pythoncom.CoUninitialize = orig_uninit

        _assert_eq(calls, ["collect", "uninit"])

    check("COM teardown collects before uninitializing",
          teardown_collects_before_uninitialize)

    def teardown_survives_failures():
        """Teardown must never raise; an exception would mask a real result."""
        import com_utils

        orig_uninit = com_utils.pythoncom.CoUninitialize

        def boom(*a, **kw):
            raise OSError("simulated CoUninitialize failure")

        com_utils.pythoncom.CoUninitialize = boom
        try:
            com_utils.finalize_com("test")   # must not propagate
        finally:
            com_utils.pythoncom.CoUninitialize = orig_uninit

    check("COM teardown swallows its own failures", teardown_survives_failures)

    def workers_release_proxies_first():
        """
        Both COM entry points must null their proxies in the finally block
        BEFORE calling finalize_com. Guards against the fix being reverted.
        """
        import inspect
        import driver_scanner
        import update_checker

        cases = [
            (driver_scanner.scan_installed_drivers,
             ["wmi", "wmi_service", "drivers"]),
            (update_checker.check_for_driver_updates,
             ["session", "searcher", "result"]),
        ]
        for fn, names in cases:
            tail = inspect.getsource(fn).split("finally:")[-1]
            if "finalize_com" not in tail:
                raise AssertionError(fn.__name__ + " never calls finalize_com")
            positions = []
            for name in names:
                needle = name + " = None"
                if needle not in tail:
                    raise AssertionError(
                        fn.__name__ + " does not release " + repr(name)
                    )
                positions.append(tail.index(needle))
            if tail.index("finalize_com") < max(positions):
                raise AssertionError(
                    fn.__name__ + " tears down COM before releasing proxies"
                )

    check("COM workers release proxies before teardown",
          workers_release_proxies_first)

    print("\n=== Cell sanitization tests ===")

    from gui import _clean_cell

    check("Tuple cell (WMI array) is joined", lambda: _assert_eq(
        _clean_cell(("PCI\\VEN_10DE", "PCI\\VEN_8086")),
        "PCI\\VEN_10DE; PCI\\VEN_8086"))
    check("None cell becomes a dash", lambda: _assert_eq(_clean_cell(None), "\u2014"))
    check("Empty cell becomes a dash", lambda: _assert_eq(_clean_cell(""), "\u2014"))
    check("Newlines collapse to one line", lambda: _assert_eq(
        _clean_cell("line1\nline2"), "line1 line2"))
    check("NUL byte is stripped", lambda: _assert_truthy(
        "\x00" not in _clean_cell("bad\x00value")))
    check("Overlong cell is truncated", lambda: _assert_truthy(
        len(_clean_cell("x" * 5000)) <= 300))

    def hostile_rows_render():
        """Rows containing hostile text must still render without aborting."""
        hostile = [
            DriverUpdate(
                title="Driver {unbalanced",
                description="desc\x00with\nnul and newline " + "y" * 4000,
                driver_ver="1.0",
                publisher="P",
                kb_article="",
                update_id="{id}",
                is_downloaded=False,
            ),
            DriverUpdate(
                title='Quote " and [bracket] and \\backslash',
                description="",
                driver_ver="",
                publisher="",
                kb_article="",
                update_id="",
                is_downloaded=True,
            ),
        ]
        app._populate_tree(app.tree_updates, hostile, app._update_row)
        _assert_eq(len(app.tree_updates.get_children()), len(hostile))

    check("Hostile text renders without aborting the table", hostile_rows_render)

    print("\n=== Logging tests ===")

    import app_logger
    from app_logger import get_logger

    check("Logger initializes", lambda: _assert_truthy(get_logger("test")))
    check("Log records reach the memory buffer",
          lambda: _assert_logged("canary-message-12345"))
    check("Exception logging captures traceback",
          lambda: _assert_exception_logged())
    check("Log level parsing (INFO)", lambda: _assert_eq(
        LogViewerCls._parse_level(
            "2026-01-01 00:00:00 | INFO     | MainThread | x | msg"), "INFO"))
    check("Log level parsing (ERROR)", lambda: _assert_eq(
        LogViewerCls._parse_level(
            "2026-01-01 00:00:00 | ERROR    | MainThread | x | msg"), "ERROR"))
    check("Log level parsing falls back on malformed line", lambda: _assert_eq(
        LogViewerCls._parse_level("not a log line"), "INFO"))
    check("Log file is written to disk", _assert_log_file_written)

    print("\n=== Log viewer tests ===")

    viewer = None

    def open_viewer():
        nonlocal viewer
        viewer = app._show_logs() or app._log_window

    check("Logs button opens viewer window", open_viewer)

    if app._log_window is not None:
        v = app._log_window
        check("Viewer renders log lines", lambda: v._refresh(force=True))
        check("Viewer level filter (ERROR only)", lambda: (
            v._level_filter.set("ERROR"), v._refresh(force=True)))
        check("Viewer level filter (back to ALL)", lambda: (
            v._level_filter.set("ALL"), v._refresh(force=True)))
        check("Viewer search filter + highlight", lambda: (
            v._search_term.set("canary"), v._refresh(force=True)))
        check("Viewer search cleared", lambda: (
            v._search_term.set(""), v._refresh(force=True)))
        check("Viewer text widget contains content", lambda: _assert_truthy(
            v.text.get("1.0", "end").strip()))
        check("Reopening Logs reuses the same window", lambda: _assert_eq(
            (app._show_logs(), app._log_window is v)[1], True))
        check("Viewer closes cleanly", v._on_close)

    try:
        app.root.destroy()
    except Exception:
        pass

    _report(failures)


def _assert_truthy(value):
    if not value:
        raise AssertionError(f"expected truthy value, got {value!r}")


def _assert_logged(token):
    """Emit a unique message and confirm it lands in the memory buffer."""
    import app_logger
    from app_logger import get_logger

    get_logger("test").info(token)
    lines = app_logger.get_buffered_lines()
    if not any(token in line for line in lines):
        raise AssertionError(f"{token!r} not found in log buffer")


def _assert_exception_logged():
    """Confirm exception logging records the traceback."""
    import app_logger
    from app_logger import get_logger

    token = "deliberate-test-exception"
    try:
        raise ValueError(token)
    except ValueError:
        get_logger("test").exception("caught test exception")

    lines = app_logger.get_buffered_lines()
    blob = "\n".join(lines)
    if token not in blob:
        raise AssertionError("exception message missing from log")
    if "Traceback" not in blob:
        raise AssertionError("traceback missing from log")


def _assert_log_file_written():
    """Confirm the rotating file handler actually wrote to disk."""
    import app_logger

    path = app_logger.get_log_path()
    if not path:
        raise AssertionError("no log path configured")
    if not os.path.exists(path):
        raise AssertionError(f"log file not created at {path}")
    content = app_logger.read_log_file()
    if "Logging initialized" not in content:
        raise AssertionError("log file missing initialization entry")


def _assert_eq(actual, expected):
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def _assert_row_widths(tree):
    """Every row must supply exactly one value per declared column."""
    ncols = len(tree["columns"])
    for iid in tree.get_children():
        vals = tree.item(iid, "values")
        if len(vals) != ncols:
            raise AssertionError(
                f"row has {len(vals)} values but tree declares {ncols} columns")


def _report(failures):
    print()
    if failures:
        print(f"=== {len(failures)} FAILURE(S) ===")
        for label, e in failures:
            print(f"  - {label}: {e}")
        sys.exit(1)
    print("=== ALL TESTS PASSED ===")
    sys.exit(0)


if __name__ == "__main__":
    main()
