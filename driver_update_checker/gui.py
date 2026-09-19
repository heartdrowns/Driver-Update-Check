"""
GUI — Driver Update Checker Application

A Tkinter/ttk-based desktop GUI with two panels:
1. Installed Drivers — local driver inventory from WMI
2. Available Updates — driver updates from Windows Update Agent

Features:
- Threaded scanning (UI stays responsive)
- Progress indicators
- Export to CSV
- Open Windows Optional Updates settings
"""

import csv
import os
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from queue import Empty, Queue

import app_logger
from app_logger import get_logger
from driver_scanner import scan_installed_drivers, format_driver_date
from log_viewer import LogViewer
from update_checker import check_for_driver_updates

log = get_logger("gui")

# How often the UI drains the worker message queue.
POLL_INTERVAL_MS = 200

# Cap cell text so a pathological description can't stall table rendering.
MAX_CELL_CHARS = 300

# While an operation is running, log a heartbeat this often. This proves
# whether the UI event loop is still alive when something appears frozen.
HEARTBEAT_SECONDS = 5

# Hard timeouts. A worker that exceeds these is considered hung and the UI is
# released so the app stays usable. Windows Update searches are legitimately
# slow, so its budget is generous.
# It is no longer 15 minutes: the COM teardown deadlock that made such a long
# budget necessary is fixed in com_utils.
TIMEOUTS = {
    "Driver scan": 180,
    "Update check": 300,
}
DEFAULT_TIMEOUT = 300


def _clean_cell(value):
    """
    Normalize a value for display in a Treeview cell.

    WMI array fields arrive as tuples, and WUA descriptions can contain
    newlines, control characters, or run to thousands of characters. Collapse
    all of that into a single safe line.
    """
    if value is None:
        return "—"
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value if v is not None)
    else:
        value = str(value)

    # Strip control characters (including NUL) and collapse whitespace.
    value = "".join(ch if ch >= " " or ch == "\t" else " " for ch in value)
    value = " ".join(value.split())

    if len(value) > MAX_CELL_CHARS:
        value = value[:MAX_CELL_CHARS - 3] + "..."
    return value or "—"


class DriverUpdateApp:
    """Main application window and controller."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Driver Update Checker")
        self.root.geometry("1100x700")
        self.root.minsize(900, 550)

        self.installed_drivers = []
        self.available_updates = []
        self._msg_queue = Queue()
        self._log_window = None

        # Busy-state tracking. _worker holds the active background thread so a
        # watchdog can detect the case where the thread died without ever
        # posting a result, which would otherwise leave the UI stuck forever.
        self._worker = None
        self._stale_worker = None
        self._busy = False
        self._busy_label = ""
        self._busy_op = ""
        self._busy_started = 0.0
        self._last_heartbeat = 0.0
        # Incremented whenever an operation starts or is abandoned. Results
        # tagged with an older generation are discarded, so a cancelled worker
        # that finishes late cannot overwrite current UI state.
        self._generation = 0

        log.info("Application window initializing")

        self._build_styles()
        self._build_menu()
        self._build_toolbar()
        self._build_notebook()
        self._build_statusbar()
        self._poll_queue()

        # Route Tkinter callback exceptions into the log. By default Tkinter
        # prints these to stderr and continues, which hides real failures when
        # the app is launched by double-clicking rather than from a terminal.
        self.root.report_callback_exception = self._on_tk_exception

        self.root.protocol("WM_DELETE_WINDOW", self._on_app_close)
        log.info("Application window ready")

    # ── Styles ──────────────────────────────────────────────────────

    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass  # fall back to default theme

        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TB.TButton", padding=(12, 6))
        style.configure("Status.TLabel", font=("Segoe UI", 9))
        # Custom progressbar styles MUST be named <Name>.Horizontal.TProgressbar.
        # ttk prepends the orientation when resolving the layout, so a style
        # named "Status.TProgressbar" makes it look for a nonexistent
        # "Horizontal.Status.TProgressbar" layout and raises TclError.
        style.configure("Status.Horizontal.TProgressbar", thickness=6)

    # ── Menu Bar ────────────────────────────────────────────────────

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Export Installed Drivers (CSV)...", command=self._export_installed)
        file_menu.add_command(label="Export Available Updates (CSV)...", command=self._export_updates)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="View Logs", command=self._show_logs)
        tools_menu.add_separator()
        tools_menu.add_command(label="Open Windows Optional Updates", command=self._open_optional_updates)
        tools_menu.add_command(label="Open Device Manager", command=self._open_device_manager)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    # ── Toolbar ─────────────────────────────────────────────────────

    def _build_toolbar(self):
        toolbar = ttk.Frame(self.root, padding=(8, 6))
        toolbar.pack(fill=tk.X)

        self.btn_scan = ttk.Button(
            toolbar, text="Scan Installed Drivers", style="TB.TButton",
            command=self._start_scan
        )
        self.btn_scan.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_check = ttk.Button(
            toolbar, text="Check for Updates", style="TB.TButton",
            command=self._start_update_check
        )
        self.btn_check.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_open_updates = ttk.Button(
            toolbar, text="Open Optional Updates", style="TB.TButton",
            command=self._open_optional_updates
        )
        self.btn_open_updates.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_logs = ttk.Button(
            toolbar, text="Logs", style="TB.TButton",
            command=self._show_logs
        )
        self.btn_logs.pack(side=tk.LEFT, padx=(0, 8))

        # Escape hatch: releases the UI if an operation never returns.
        self.btn_cancel = ttk.Button(
            toolbar, text="Cancel", style="TB.TButton",
            command=self._force_reset, state=tk.DISABLED
        )
        self.btn_cancel.pack(side=tk.LEFT, padx=(0, 8))

        self.progress = ttk.Progressbar(
            toolbar, mode="indeterminate",
            style="Status.Horizontal.TProgressbar", length=200
        )
        self.progress.pack(side=tk.RIGHT, padx=(8, 0))

    # ── Notebook (Tabs) ──────────────────────────────────────────────

    def _build_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        # ── Tab 1: Installed Drivers ──
        tab_installed = ttk.Frame(self.notebook)
        self.notebook.add(tab_installed, text="Installed Drivers")

        installed_cols = (
            "device", "manufacturer", "version", "date",
            "provider", "inf", "hwid"
        )
        installed_headers = {
            "device": "Device Name",
            "manufacturer": "Manufacturer",
            "version": "Driver Version",
            "date": "Driver Date",
            "provider": "Provider",
            "inf": "INF Name",
            "hwid": "Hardware ID",
        }
        self.tree_installed = self._build_treeview(
            tab_installed, installed_cols, installed_headers
        )
        self._build_tree_scrollbar(tab_installed, self.tree_installed)

        # ── Tab 2: Available Updates ──
        tab_updates = ttk.Frame(self.notebook)
        self.notebook.add(tab_updates, text="Available Updates")

        update_cols = (
            "title", "version", "publisher", "kb", "downloaded", "description"
        )
        update_headers = {
            "title": "Update Title",
            "version": "Version",
            "publisher": "Publisher",
            "kb": "KB Article",
            "downloaded": "Downloaded",
            "description": "Description",
        }
        self.tree_updates = self._build_treeview(
            tab_updates, update_cols, update_headers
        )
        self._build_tree_scrollbar(tab_updates, self.tree_updates)

        # Empty-state labels
        self.lbl_installed_count = ttk.Label(tab_installed, text="", style="Status.TLabel")
        self.lbl_installed_count.pack(anchor=tk.W, padx=8, pady=4)

        self.lbl_updates_count = ttk.Label(tab_updates, text="", style="Status.TLabel")
        self.lbl_updates_count.pack(anchor=tk.W, padx=8, pady=4)

    def _build_treeview(self, parent, columns, headers):
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        for col in columns:
            tree.heading(col, text=headers[col])
            tree.column(col, width=160, minwidth=80)
        tree.pack(fill=tk.BOTH, expand=True)
        # Alternating row colors
        tree.tag_configure("odd", background="#f5f5f5")
        tree.tag_configure("even", background="#ffffff")
        return tree

    def _build_tree_scrollbar(self, parent, tree):
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)

    # ── Status Bar ──────────────────────────────────────────────────

    def _build_statusbar(self):
        statusbar = ttk.Frame(self.root, padding=(8, 4))
        statusbar.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_label = ttk.Label(statusbar, text="Ready.", style="Status.TLabel")
        self.status_label.pack(side=tk.LEFT)

        self.scan_time_label = ttk.Label(statusbar, text="", style="Status.TLabel")
        self.scan_time_label.pack(side=tk.RIGHT)

    # ── Busy State ──────────────────────────────────────────────────

    def _enter_busy(self, status, op=""):
        """
        Disable the action buttons and start the progress bar.

        Returns False if an operation is already running, which prevents a
        second worker from overwriting the first one's result.
        """
        if self._busy:
            log.warning("Ignoring request: '%s' already running", self._busy_op)
            return False
        self._busy = True
        self._busy_label = status
        self._busy_op = op or status
        self._busy_started = time.monotonic()
        self._last_heartbeat = 0.0
        self._generation += 1
        self.btn_scan.config(state=tk.DISABLED)
        self.btn_check.config(state=tk.DISABLED)
        try:
            self.btn_cancel.config(state=tk.NORMAL)
        except Exception:
            pass
        try:
            self.progress.start()
        except Exception:
            log.exception("Could not start progress bar")
        self._set_status(status)
        log.info("Operation started: %s", self._busy_op)
        return True

    def _reset_busy_state(self, status=None):
        """Return the UI to idle. Safe to call even if already idle."""
        if self._busy:
            log.info(
                "Operation finished: %s (%.1fs)",
                self._busy_op, time.monotonic() - self._busy_started,
            )
        self._busy = False
        self._busy_label = ""
        self._busy_op = ""
        self._worker = None
        try:
            self.progress.stop()
        except Exception:
            log.exception("Could not stop progress bar")
        try:
            self.btn_scan.config(state=tk.NORMAL)
            self.btn_check.config(state=tk.NORMAL)
            self.btn_cancel.config(state=tk.DISABLED)
        except Exception:
            log.exception("Could not re-enable buttons")
        if status:
            self._set_status(status)

    def _force_reset(self):
        """
        User-triggered escape hatch.

        The background thread cannot be safely killed (it may be blocked inside
        a COM call), but it is a daemon and its result is ignored once the UI is
        released, so abandoning it is safe.
        """
        log.warning(
            "User action: Cancel / Reset (was: %s, %.1fs elapsed, alive=%s)",
            self._busy_op or "idle",
            time.monotonic() - self._busy_started if self._busy else 0.0,
            bool(self._worker is not None and self._worker.is_alive()),
        )
        self._stale_worker = self._worker
        self._generation += 1  # invalidate any result the old worker may post
        self._reset_busy_state("Cancelled by user. Background work abandoned.")

    def _watchdog_check(self):
        """
        Detect and recover from a stuck operation.

        Handles three distinct failure modes:

        1. Worker died without posting  -> thread is not alive, queue is empty.
        2. Worker hung                  -> thread reports alive but never
                                           finishes. Only a time budget can
                                           catch this; a liveness check cannot.
        3. UI event loop stalled        -> heartbeats stop appearing in the log,
                                           which tells us the freeze is on the
                                           main thread rather than the worker.
        """
        if not self._busy:
            return

        if not self._msg_queue.empty():
            return  # a result is queued and will be handled on the next tick

        worker = self._worker
        elapsed = time.monotonic() - self._busy_started
        alive = bool(worker is not None and worker.is_alive())

        # Heartbeat: proves the UI loop is running and records worker state.
        if elapsed - self._last_heartbeat >= HEARTBEAT_SECONDS:
            self._last_heartbeat = elapsed
            log.info(
                "Heartbeat: '%s' running %.0fs | worker_alive=%s | queue=%d",
                self._busy_op, elapsed, alive, self._msg_queue.qsize(),
            )

        # Case 1: worker is gone but posted nothing.
        if worker is not None and not alive:
            log.error(
                "Watchdog: '%s' worker ended without posting a result "
                "after %.1fs. Resetting UI.",
                self._busy_op, elapsed,
            )
            self._reset_busy_state(
                "Operation ended without returning a result — see Logs."
            )
            return

        # Case 2: worker still alive but past its time budget.
        budget = TIMEOUTS.get(self._busy_op, DEFAULT_TIMEOUT)
        if elapsed > budget:
            log.error(
                "Watchdog: '%s' exceeded %ds budget (%.0fs elapsed, "
                "worker_alive=%s). Releasing UI; the worker thread is a daemon "
                "and will not block exit.",
                self._busy_op, budget, elapsed, alive,
            )
            self._reset_busy_state(
                f"{self._busy_op} timed out after {int(elapsed)}s — see Logs."
            )

    # ── Threading: Scan Installed Drivers ────────────────────────────

    def _start_scan(self):
        log.info("User action: Scan Installed Drivers")
        if not self._enter_busy("Scanning installed drivers...", "Driver scan"):
            return
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(scan_installed_drivers, "scan_done", "Driver scan",
                  self._generation),
            name="ScanWorker",
            daemon=True,
        )
        self._worker.start()

    # ── Threading: Check for Updates ────────────────────────────────

    def _start_update_check(self):
        log.info("User action: Check for Updates")
        if not self._enter_busy(
            "Checking Windows Update for driver updates...", "Update check"
        ):
            return
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(check_for_driver_updates, "updates_done", "Update check",
                  self._generation),
            name="UpdateWorker",
            daemon=True,
        )
        self._worker.start()

    # ── Generic Worker Runner ────────────────────────────────────────

    def _run_worker(self, fn, success_msg, label, generation=0):
        """
        Run `fn` on a background thread and post exactly one queue message.

        Catching BaseException matters here: if the worker died on something
        outside the Exception hierarchy it would post nothing at all, and the
        UI would wait on a result that never arrives.

        The result is posted before it is logged. An earlier version logged
        first, which meant a failure inside logging could strand the result and
        freeze the UI.
        """
        log.debug("%s worker thread started (gen=%d)", label, generation)
        posted = False
        try:
            result = fn()
            self._msg_queue.put((success_msg, result, generation))
            posted = True
            log.debug("%s posted %r to UI queue", label, success_msg)
        except Exception as e:
            log.exception("%s worker failed", label)
            self._post_error_safely(f"{label} failed: {e}", generation)
            posted = True
        except BaseException as e:
            self._post_error_safely(f"{label} aborted: {e!r}", generation)
            posted = True
            log.critical("%s worker aborted: %r", label, e, exc_info=True)
            raise
        finally:
            if not posted:
                # Should be unreachable, but a silent hang is worse than a
                # spurious error message.
                self._post_error_safely(
                    f"{label} ended unexpectedly.", generation)
                log.error("%s worker posted no result; forced error", label)
            log.debug("%s worker thread finished", label)

    def _post_error_safely(self, message, generation):
        """Queue an error without letting a queue failure escape."""
        try:
            self._msg_queue.put(("error", message, generation))
        except Exception:
            log.exception("Could not post error to UI queue: %s", message)

    # ── Message Queue Polling ────────────────────────────────────────

    def _poll_queue(self):
        """
        Drain the worker->UI message queue.

        The reschedule lives in `finally` so that an exception raised by any
        handler can never kill the polling loop. Previously the reschedule sat
        after the drain loop, so a single handler error stopped polling forever
        and the UI stayed frozen on its last status message.
        """
        try:
            while True:
                try:
                    item = self._msg_queue.get_nowait()
                except Empty:
                    break

                # Messages are (type, data, generation); the generation is
                # optional so tests and older callers still work.
                if len(item) == 3:
                    msg_type, data, gen = item
                else:
                    msg_type, data = item
                    gen = self._generation

                if gen != self._generation:
                    log.warning(
                        "Discarding stale %r result (gen %d, current %d)",
                        msg_type, gen, self._generation,
                    )
                    continue

                log.debug("Queue message received: %s", msg_type)
                try:
                    if msg_type == "scan_done":
                        self._on_scan_complete(data)
                    elif msg_type == "updates_done":
                        self._on_updates_complete(data)
                    elif msg_type == "error":
                        self._on_error(data)
                    else:
                        log.warning("Unknown queue message type: %r", msg_type)
                except Exception:
                    # A handler failed. Log it, reset the UI so the app stays
                    # usable, and keep draining.
                    log.exception("Handler for %r raised; resetting UI", msg_type)
                    self._reset_busy_state(
                        "An error occurred while displaying results — see Logs."
                    )
        except Exception:
            log.exception("Unexpected error in queue polling loop")
        finally:
            self._watchdog_check()
            try:
                self.root.after(POLL_INTERVAL_MS, self._poll_queue)
            except Exception:
                # Root window is being destroyed; stop polling.
                pass

    def _on_scan_complete(self, drivers):
        count = len(drivers)
        log.info("Rendering %d installed driver row(s)", count)
        self.installed_drivers = drivers
        # Reset the busy state FIRST so the UI unlocks even if rendering fails.
        self._reset_busy_state(f"Scan complete — {count} drivers found.")
        self._populate_tree(self.tree_installed, drivers, self._driver_row)
        self.lbl_installed_count.config(
            text=f"{count} driver{'s' if count != 1 else ''} found")
        now = datetime.now().strftime("%H:%M:%S")
        self.scan_time_label.config(text=f"Last scan: {now}")
        log.info("Installed driver table rendered")

    def _on_updates_complete(self, updates):
        count = len(updates)
        log.info("Rendering %d available update row(s)", count)
        self.available_updates = updates

        if count == 0:
            status = "No driver updates available. Your drivers are up to date."
        else:
            status = f"{count} driver update{'s' if count != 1 else ''} available."

        # Reset the busy state FIRST so the UI unlocks even if rendering fails.
        self._reset_busy_state(status)
        self._populate_tree(self.tree_updates, updates, self._update_row)
        self.lbl_updates_count.config(
            text=f"{count} update{'s' if count != 1 else ''} available")
        now = datetime.now().strftime("%H:%M:%S")
        self.scan_time_label.config(text=f"Last check: {now}")
        log.info("Available updates table rendered")

    def _on_error(self, message):
        log.error("Error surfaced to user: %s", message)
        self._reset_busy_state("Error: " + message)
        messagebox.showerror("Error", message)

    # ── Treeview Population ─────────────────────────────────────────

    def _driver_row(self, driver, index):
        tag = "even" if index % 2 == 0 else "odd"
        return (
            driver.device_name or "(unknown)",
            driver.manufacturer or "(unknown)",
            driver.driver_version or "—",
            format_driver_date(driver.driver_date) or "—",
            driver.provider or "—",
            driver.inf_name or "—",
            driver.hardware_id or "—",
        ), tag

    def _update_row(self, update, index):
        tag = "even" if index % 2 == 0 else "odd"
        return (
            update.title or "(unknown)",
            update.driver_ver or "—",
            update.publisher or "—",
            update.kb_article or "—",
            "Yes" if update.is_downloaded else "No",
            update.description or "—",
        ), tag

    def _populate_tree(self, tree, items, row_fn):
        """
        Replace the contents of a tree. Each row is inserted independently so a
        single malformed record cannot abort the whole table.
        """
        try:
            tree.delete(*tree.get_children())
        except Exception:
            log.exception("Could not clear tree before repopulating")

        failed = 0
        for i, item in enumerate(items):
            try:
                values, tag = row_fn(item, i)
                values = tuple(_clean_cell(v) for v in values)
                tree.insert("", tk.END, values=values, tags=(tag,))
            except Exception:
                failed += 1
                log.exception("Failed to render row %d (%r)", i, item)

        if failed:
            log.error("%d of %d row(s) could not be rendered", failed, len(items))

    # ── Export ──────────────────────────────────────────────────────

    def _export_installed(self):
        log.info("User action: Export Installed Drivers")
        if not self.installed_drivers:
            log.warning("Export aborted: no driver data loaded")
            messagebox.showinfo("Export", "No driver data to export. Run a scan first.")
            return
        self._export_csv(
            "installed_drivers.csv",
            ["Device Name", "Manufacturer", "Driver Version", "Driver Date",
             "Provider", "INF Name", "Hardware ID"],
            self.installed_drivers,
            self._driver_row_dict,
        )

    def _export_updates(self):
        log.info("User action: Export Available Updates")
        if not self.available_updates:
            log.warning("Export aborted: no update data loaded")
            messagebox.showinfo("Export", "No update data to export. Check for updates first.")
            return
        self._export_csv(
            "available_updates.csv",
            ["Title", "Version", "Publisher", "KB Article", "Downloaded", "Description"],
            self.available_updates,
            self._update_row_dict,
        )

    def _driver_row_dict(self, driver):
        return [
            driver.device_name, driver.manufacturer, driver.driver_version,
            format_driver_date(driver.driver_date), driver.provider,
            driver.inf_name, driver.hardware_id,
        ]

    def _update_row_dict(self, update):
        return [
            update.title, update.driver_ver, update.publisher,
            update.kb_article, "Yes" if update.is_downloaded else "No",
            update.description,
        ]

    def _export_csv(self, default_name, headers, items, row_fn):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=default_name,
            title="Save report",
        )
        if not filepath:
            log.info("Export cancelled by user")
            return
        try:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for item in items:
                    writer.writerow(row_fn(item))
            log.info("Exported %d row(s) to %s", len(items), filepath)
            messagebox.showinfo("Export", f"Report saved to:\n{filepath}")
        except Exception as e:
            log.exception("CSV export failed")
            messagebox.showerror("Export Error", f"Failed to save file:\n{e}")

    # ── Tools ───────────────────────────────────────────────────────

    def _open_optional_updates(self):
        """Open the Windows Optional Updates settings page."""
        log.info("User action: Open Windows Optional Updates")
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "ms-settings:windowsupdate-optionalupdates"],
                shell=False,
            )
        except Exception as e:
            log.exception("Could not open Optional Updates settings")
            messagebox.showerror("Error", f"Could not open settings:\n{e}")

    def _open_device_manager(self):
        """Open Windows Device Manager."""
        log.info("User action: Open Device Manager")
        try:
            subprocess.Popen(["devmgmt.msc"], shell=True)
        except Exception as e:
            log.exception("Could not open Device Manager")
            messagebox.showerror("Error", f"Could not open Device Manager:\n{e}")

    # ── Misc ────────────────────────────────────────────────────────

    def _show_logs(self):
        """Open the log viewer, reusing the existing window if already open."""
        log.info("User action: View Logs")
        if self._log_window is not None and self._log_window.winfo_exists():
            self._log_window.lift()
            self._log_window.focus_force()
            return
        try:
            self._log_window = LogViewer(self.root)
        except Exception as e:
            log.exception("Failed to open log viewer")
            messagebox.showerror("Error", f"Could not open log viewer:\n{e}")

    def _on_tk_exception(self, exc_type, exc_value, exc_tb):
        """
        Tkinter callback exception handler.

        Tkinter's default prints to stderr, which is invisible when the app is
        started by double-clicking. Routing it here guarantees widget callback
        failures land in the log file.
        """
        log.critical(
            "Unhandled exception in Tk callback",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        if self._busy:
            self._reset_busy_state("Recovered from an internal error — see Logs.")

    def _on_app_close(self):
        log.info("Application closing")
        try:
            self.root.destroy()
        except Exception:
            pass

    def _set_status(self, text):
        log.debug("Status: %s", text)
        self.status_label.config(text=text)

    def _show_about(self):
        messagebox.showinfo(
            "About",
            "Driver Update Checker\n\n"
            "Scans installed drivers via WMI and checks for\n"
            "available updates through Windows Update.\n\n"
            "Built with Python, Tkinter, and pywin32.",
        )

    def run(self):
        self.root.mainloop()
