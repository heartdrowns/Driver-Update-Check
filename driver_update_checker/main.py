"""
Driver Update Checker — Entry Point

A Windows desktop application that scans installed drivers and checks
for available updates via Windows Update.

Usage:
    python main.py

Requirements:
    pip install pywin32
"""

import platform
import sys
import threading


def check_windows():
    """Ensure we're running on Windows."""
    if platform.system() != "Windows":
        print("ERROR: This application only runs on Windows.")
        print(f"Detected OS: {platform.system()}")
        sys.exit(1)


def install_exception_hooks(log):
    """
    Route uncaught exceptions — on the main thread and on worker threads —
    into the log so crashes are never silent.
    """

    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = handle_exception

    def handle_thread_exception(args):
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        log.critical(
            "Uncaught exception in thread %s",
            args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    # threading.excepthook requires Python 3.8+
    if hasattr(threading, "excepthook"):
        threading.excepthook = handle_thread_exception


def main():
    check_windows()

    # Imports are deferred so non-Windows systems get a clean error message
    # before any Windows-only modules are loaded.
    from app_logger import setup_logging, get_log_path

    log = setup_logging()
    install_exception_hooks(log)

    log.info("Driver Update Checker starting")
    print(f"Logging to: {get_log_path()}")

    try:
        from gui import DriverUpdateApp

        app = DriverUpdateApp()
        app.run()
    except Exception:
        log.exception("Fatal error during application startup")
        raise
    finally:
        log.info("Driver Update Checker exited")


if __name__ == "__main__":
    main()
