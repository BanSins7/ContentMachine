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


def stop_worker():
    _stop.set()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Qualified Data Pipeline local worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    print(serve(once=args.once))


if __name__ == "__main__":
    main()
