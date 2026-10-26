"""Dedicated local-only worker. QDP never claims unrelated Discovery/Collector jobs."""
from __future__ import annotations

import argparse
import threading
import time
import uuid

from intelligence.config import enabled, dsn
from .repository import work_once, purge_revoked

_thread = None
_lock = threading.Lock()
_stop = threading.Event()


def serve(*, sleep_seconds: float = 2.0, once: bool = False):
    worker_id = "qdp-" + uuid.uuid4().hex[:12]
    cycles = 0
    while not _stop.is_set():
        try:
            result = work_once(worker_id=worker_id)
            if cycles % 30 == 0:
                purge_revoked()
            cycles += 1
        except Exception as exc:
            # Do not expose source/metadata content in logs.
            print("QDP worker:", type(exc).__name__, flush=True)
            result = None
        if once:
            return result
        if result is None:
            _stop.wait(sleep_seconds)


def start_worker():
    global _thread
    if not enabled() or not dsn():
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(target=serve, name="qdp-local-worker", daemon=True)
        _thread.start()
        return True


def stop_worker(*, timeout_seconds: float = 5.0):
    """Request worker termination and join without waiting indefinitely.

    The current database transaction cannot be forcefully interrupted; a
    caller may observe still_running=True and should not report shutdown done.
    """
    if not isinstance(timeout_seconds,(float,int)) or isinstance(timeout_seconds,bool) or (
            not 0 <= timeout_seconds <= 30):
        raise ValueError("Worker stop timeout must be 0..30 seconds")
    _stop.set()
    with _lock:
        thread=_thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout_seconds)
    return {"stop_requested":True,"still_running":bool(thread and thread.is_alive())}


def worker_status():
    """Local lifecycle evidence only; no claims about future scheduling."""
    with _lock:
        alive=bool(_thread and _thread.is_alive())
    return {"worker_thread_alive":alive,"shutdown_requested":_stop.is_set(),
            "database_enabled":bool(enabled() and dsn()),
            "provider_calls":0}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Qualified Data Pipeline local worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    print(serve(once=args.once))


if __name__ == "__main__":
    main()
