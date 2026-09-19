"""
Log Viewer Window

A Toplevel window that displays application logs with level filtering,
live auto-refresh, search, and export. Reads from the in-memory ring
buffer maintained by app_logger.
"""

import os
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import app_logger
from app_logger import get_logger

log = get_logger("logviewer")

LEVELS = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"]

# Level ordering for threshold filtering
LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

# Colors for each level
LEVEL_COLORS = {
    "DEBUG": "#6b7280",
    "INFO": "#1f2937",
    "WARNING": "#b45309",
    "ERROR": "#b91c1c",
    "CRITICAL": "#7f1d1d",
}

REFRESH_MS = 1000


class LogViewer(tk.Toplevel):
    """Log viewer window. Only one instance should be open at a time."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Application Logs")
        self.geometry("980x600")
        self.minsize(700, 400)

        self._auto_refresh = tk.BooleanVar(value=True)
        self._level_filter = tk.StringVar(value="ALL")
        self._search_term = tk.StringVar(value="")
        self._last_line_count = -1
        self._refresh_job = None

        self._build_toolbar()
        self._build_text_area()
        self._build_statusbar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        log.info("Log viewer opened")
        self._refresh(force=True)
        self._schedule_refresh()

    # ── Toolbar ─────────────────────────────────────────────────────

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill=tk.X)

        ttk.Label(bar, text="Level:").pack(side=tk.LEFT, padx=(0, 4))
        combo = ttk.Combobox(
            bar, textvariable=self._level_filter, values=LEVELS,
            state="readonly", width=10,
        )
        combo.pack(side=tk.LEFT, padx=(0, 12))
        combo.bind("<<ComboboxSelected>>", lambda e: self._refresh(force=True))

        ttk.Label(bar, text="Find:").pack(side=tk.LEFT, padx=(0, 4))
        entry = ttk.Entry(bar, textvariable=self._search_term, width=24)
        entry.pack(side=tk.LEFT, padx=(0, 12))
        entry.bind("<KeyRelease>", lambda e: self._refresh(force=True))

        ttk.Checkbutton(
            bar, text="Auto-refresh", variable=self._auto_refresh,
        ).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(bar, text="Refresh", command=lambda: self._refresh(force=True)).pack(
            side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="Copy All", command=self._copy_all).pack(
            side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="Save As...", command=self._save_as).pack(
            side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="Open Log Folder", command=self._open_folder).pack(
            side=tk.LEFT, padx=(0, 6))
        ttk.Button(bar, text="Clear View", command=self._clear_view).pack(
            side=tk.LEFT)

    # ── Text Area ───────────────────────────────────────────────────

    def _build_text_area(self):
        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        self.text = tk.Text(
            frame, wrap=tk.NONE, font=("Consolas", 9),
            background="#ffffff", foreground="#1f2937",
            state=tk.DISABLED, borderwidth=1, relief=tk.SOLID,
        )
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for level, color in LEVEL_COLORS.items():
            self.text.tag_configure(level, foreground=color)
        self.text.tag_configure("ERROR", foreground=LEVEL_COLORS["ERROR"])
        self.text.tag_configure("match", background="#fef08a")

    # ── Status Bar ──────────────────────────────────────────────────

    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.status = ttk.Label(bar, text="")
        self.status.pack(side=tk.LEFT)

        path = app_logger.get_log_path() or "(file logging disabled)"
        self.path_label = ttk.Label(bar, text=path, foreground="#6b7280")
        self.path_label.pack(side=tk.RIGHT)

    # ── Refresh Logic ───────────────────────────────────────────────

    def _schedule_refresh(self):
        self._refresh_job = self.after(REFRESH_MS, self._tick)

    def _tick(self):
        if self._auto_refresh.get():
            self._refresh(force=False)
        self._schedule_refresh()

    def _refresh(self, force=False):
        """Redraw the log view. Skips redraw if nothing changed and not forced."""
        lines = app_logger.get_buffered_lines()

        if not force and len(lines) == self._last_line_count:
            return
        self._last_line_count = len(lines)

        threshold = self._level_filter.get()
        term = self._search_term.get().strip().lower()

        # Preserve scroll position unless pinned to the bottom.
        try:
            at_bottom = self.text.yview()[1] >= 0.999
        except Exception:
            at_bottom = True

        visible = []
        for line in lines:
            level = self._parse_level(line)
            if threshold != "ALL":
                if LEVEL_ORDER.get(level, 0) < LEVEL_ORDER.get(threshold, 0):
                    continue
            if term and term not in line.lower():
                continue
            visible.append((line, level))

        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        for line, level in visible:
            self.text.insert(tk.END, line + "\n", (level,))

        # Highlight search matches
        if term:
            self._highlight(term)

        self.text.configure(state=tk.DISABLED)

        if at_bottom:
            self.text.see(tk.END)

        total = len(lines)
        shown = len(visible)
        if shown == total:
            self.status.config(text=f"{total} log entries")
        else:
            self.status.config(text=f"Showing {shown} of {total} entries")

    def _highlight(self, term):
        """Tag every occurrence of term in the text widget."""
        start = "1.0"
        while True:
            pos = self.text.search(term, start, stopindex=tk.END, nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(term)}c"
            self.text.tag_add("match", pos, end)
            start = end

    @staticmethod
    def _parse_level(line):
        """
        Extract the level from a formatted log line.
        Format: 'timestamp | LEVEL    | thread | name | message'
        """
        parts = line.split("|", 2)
        if len(parts) >= 2:
            candidate = parts[1].strip()
            if candidate in LEVEL_ORDER:
                return candidate
        return "INFO"

    # ── Actions ─────────────────────────────────────────────────────

    def _copy_all(self):
        content = self.text.get("1.0", tk.END)
        try:
            self.clipboard_clear()
            self.clipboard_append(content)
            self.status.config(text="Copied to clipboard")
            log.info("Logs copied to clipboard (%d chars)", len(content))
        except Exception as e:
            log.exception("Clipboard copy failed")
            messagebox.showerror("Copy Failed", str(e), parent=self)

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"),
                       ("All files", "*.*")],
            initialfile="driver_update_checker.log",
            title="Save logs",
        )
        if not path:
            return
        try:
            content = self.text.get("1.0", tk.END)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            log.info("Logs exported to %s", path)
            messagebox.showinfo("Saved", f"Logs saved to:\n{path}", parent=self)
        except Exception as e:
            log.exception("Log export failed")
            messagebox.showerror("Save Failed", str(e), parent=self)

    def _open_folder(self):
        log_path = app_logger.get_log_path()
        if not log_path:
            messagebox.showinfo(
                "No Log File",
                "File logging is disabled, so there is no folder to open.",
                parent=self,
            )
            return
        folder = os.path.dirname(log_path)
        log.info("Opening log folder: %s", folder)
        try:
            if os.name == "nt":
                os.startfile(folder)  # noqa: S606 - Windows-only API
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            log.exception("Could not open log folder")
            messagebox.showerror("Error", f"Could not open folder:\n{e}", parent=self)

    def _clear_view(self):
        """Clear the in-memory buffer. The log file on disk is preserved."""
        if not messagebox.askyesno(
            "Clear View",
            "Clear the log view?\n\nThe log file on disk is not deleted.",
            parent=self,
        ):
            return
        app_logger.clear_buffer()
        log.info("Log view cleared by user")
        self._refresh(force=True)

    def _on_close(self):
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None
        log.info("Log viewer closed")
        self.destroy()
