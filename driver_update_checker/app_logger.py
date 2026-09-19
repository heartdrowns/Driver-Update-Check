"""
Application Logging

Central logging setup for Driver Update Checker. Writes to a rotating
log file on disk and keeps a bounded in-memory ring buffer so the Logs
window can display recent activity without re-reading the file.

Log location (Windows):  %LOCALAPPDATA%\\DriverUpdateChecker\\logs\\app.log
Fallback:                <app dir>/logs/app.log
"""

import logging
import os
import platform
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler

APP_NAME = "DriverUpdateChecker"
LOG_FILENAME = "app.log"
MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file
BACKUP_COUNT = 3
MEMORY_BUFFER_SIZE = 5000

_logger = None
_log_path = None
_memory_handler = None
_lock = threading.Lock()


class MemoryRingHandler(logging.Handler):
    """Keeps the most recent N formatted records in memory, thread-safely."""

    def __init__(self, capacity=MEMORY_BUFFER_SIZE):
        super().__init__()
        self._buffer = deque(maxlen=capacity)
        self._buf_lock = threading.Lock()

    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:
            return
        with self._buf_lock:
            self._buffer.append(msg)

    def get_lines(self):
        with self._buf_lock:
            return list(self._buffer)

    def clear(self):
        with self._buf_lock:
            self._buffer.clear()


def get_log_dir():
    """Resolve a writable directory for log files."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return os.path.join(base, APP_NAME, "logs")
    # Non-Windows or LOCALAPPDATA missing: fall back beside the app.
    app_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(app_dir, "logs")


def get_log_path():
    """Full path to the active log file."""
    return _log_path


def setup_logging(level=logging.DEBUG):
    """
    Initialize logging. Safe to call more than once — subsequent calls
    return the already-configured logger.
    """
    global _logger, _log_path, _memory_handler

    with _lock:
        if _logger is not None:
            return _logger

        logger = logging.getLogger(APP_NAME)
        logger.setLevel(level)
        logger.propagate = False

        fmt = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(threadName)-14s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # ── In-memory buffer (drives the Logs window) ──
        _memory_handler = MemoryRingHandler()
        _memory_handler.setFormatter(fmt)
        _memory_handler.setLevel(level)
        logger.addHandler(_memory_handler)

        # ── Rotating file handler ──
        try:
            log_dir = get_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            _log_path = os.path.join(log_dir, LOG_FILENAME)

            fh = RotatingFileHandler(
                _log_path,
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            fh.setLevel(level)
            logger.addHandler(fh)
        except Exception as e:
            # Logging must never take the app down. Record the failure in
            # the memory buffer and continue without file output.
            _log_path = None
            logger.warning("File logging unavailable: %s", e)

        # ── Console handler (visible when run from a terminal) ──
        try:
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(fmt)
            ch.setLevel(logging.INFO)
            logger.addHandler(ch)
        except Exception:
            pass

        _logger = logger

        logger.info("=" * 60)
        logger.info("Logging initialized")
        logger.info("Log file: %s", _log_path or "(file logging disabled)")
        logger.info(
            "Python %s | %s %s",
            platform.python_version(),
            platform.system(),
            platform.release(),
        )
        logger.info("=" * 60)

        return logger


def get_logger(name=None):
    """
    Get a namespaced child logger. Initializes logging on first use.

    Example:
        log = get_logger("scanner")
    """
    root = setup_logging()
    if name:
        return root.getChild(name)
    return root


def get_buffered_lines():
    """Recent log lines from the in-memory buffer."""
    if _memory_handler is None:
        return []
    return _memory_handler.get_lines()


def clear_buffer():
    """Clear the in-memory buffer. Does not touch the log file."""
    if _memory_handler is not None:
        _memory_handler.clear()


def read_log_file(max_bytes=1024 * 1024):
    """
    Read the tail of the log file, up to max_bytes.

    Returns:
        str: File contents, or an explanatory message on failure.
    """
    if not _log_path:
        return "File logging is disabled; no log file available."
    try:
        size = os.path.getsize(_log_path)
        with open(_log_path, "r", encoding="utf-8", errors="replace") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # discard the partial first line
                return "... (truncated) ...\n" + f.read()
            return f.read()
    except FileNotFoundError:
        return "Log file has not been created yet."
    except Exception as e:
        return f"Could not read log file: {e}"


def log_exception(logger, message):
    """Log an exception with its full traceback."""
    logger.exception(message)
