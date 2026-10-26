"""Dedicated Group A worker; does not claim QDP or Discovery jobs."""
from __future__ import annotations
import argparse
import threading
from .repository import work_once
from .capabilities import BOOTSTRAP_REASON, live_handoff_ready

_stop=threading.Event()
_lock=threading.Lock()
_thread=None


def serve(*,once=False):
    if not live_handoff_ready():
        return {"status":"unavailable","reason":BOOTSTRAP_REASON}
    while not _stop.is_set():
        try:
            result=work_once()
        except Exception as exc:
            print("Group A local worker:",type(exc).__name__,flush=True)
            result=None
        if once:
            return result
        if result is None:
            _stop.wait(2.0)


def start_worker():
    if not live_handoff_ready():
        return False
    from intelligence.config import enabled, dsn
    global _thread
    if not enabled() or not dsn():
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop.clear()
        _thread=threading.Thread(target=serve,daemon=True,name="group-a-local-worker")
        _thread.start()
        return True


def stop_worker():
    _stop.set()


def main(argv=None):
    parser=argparse.ArgumentParser(description="Group A technical extraction worker")
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args(argv)
    print(serve(once=args.once))


if __name__=="__main__":
    main()
