"""
COM lifetime helpers.

Why this module exists
----------------------
Calling `pythoncom.CoUninitialize()` while COM interface pointers are still
alive is a deadlock. The apartment is torn down first, then Python drops the
last reference to each proxy and pywin32 calls `Release()` on it. For an
out-of-process server (Windows Update lives in `wuauserv`, WMI in `Winmgmt`)
that `Release()` marshals over RPC into an apartment that no longer exists and
blocks forever. The thread stays alive and looks healthy while never returning.

This is exactly how the "GUI stuck on searching" bug happened: the worker
logged "COM uninitialized", then hung on the way out of the function, so the
result was never posted to the UI queue.

Required pattern
----------------
    session = searcher = result = None
    pythoncom.CoInitialize()
    try:
        ...
        return plain_python_data
    finally:
        session = searcher = result = None   # drop proxies FIRST
        finalize_com("update thread")        # collect, then uninitialize

Assigning None at the call site is not optional. A helper cannot clear another
function's locals: CPython stores them in a fast-locals array that a `locals()`
mapping only copies, so writing to that mapping has no effect. The caller owns
its references, so the caller must release them.
"""

import gc

import pythoncom

from app_logger import get_logger

log = get_logger("com")


def finalize_com(label=""):
    """
    Force a collection cycle, then uninitialize COM for this thread.

    The collection is what makes this safe: it runs `Release()` on any COM
    proxy that just became unreachable while the apartment is still valid.
    Uninitializing first is what deadlocks.

    Never raises — teardown failures are logged, because an exception here
    would replace a real result with a spurious error.
    """
    where = f" ({label})" if label else ""
    try:
        collected = gc.collect()
        log.debug("COM teardown%s: gc collected %d object(s)", where, collected)
    except Exception:
        log.exception("gc.collect() failed during COM teardown%s", where)

    try:
        pythoncom.CoUninitialize()
        log.debug("COM uninitialized%s", where)
    except Exception:
        log.exception("CoUninitialize() failed%s", where)
