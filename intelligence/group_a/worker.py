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


# GA20 opt-in coordinator. Legacy GA06 worker entrypoint above is deliberately
# unchanged; no QDP, paid or model integration is implicitly enabled.
from collections.abc import Mapping, Callable
from dataclasses import dataclass
from threading import Thread, Event
import os
import signal
import subprocess
import time

from .cost_resources import (
    RuntimeDenied,ResourceBudget,STEP_KINDS,RETRY_SAFE,safe_sha,
)
from .task_queue import (
    claim_next_step,heartbeat_step,finish_step,lease_is_live,
)


@dataclass(frozen=True,slots=True)
class LocalStepOutput:
    output_sha256: str
    partial: bool=False

    def __post_init__(self):
        safe_sha(self.output_sha256)
        if type(self.partial) is not bool:
            raise RuntimeDenied("invalid_stage_output_partial")


def run_bounded_local_command(argv,*,timeout_sec,env=None,cwd=None):
    """No shell, captured media or leaked output; kill the process group.

    Model/backend implementations must use this helper (or an equivalent
    host-enforced watchdog) for FFmpeg/OCR/ASR. A Python callback that hangs
    indefinitely is NOT forcibly interruptible by the Python worker.
    """
    if (type(argv) not in (list,tuple) or not 1<=len(argv)<=80 or
        any(type(x) is not str or not x or len(x)>4096 for x in argv)):
        raise RuntimeDenied("invalid_subprocess_argv")
    if type(timeout_sec) is not int or not 1<=timeout_sec<=3600:
        raise RuntimeDenied("invalid_subprocess_deadline")
    if env is not None and (
        type(env) is not dict or
        any(type(k) is not str or type(v) is not str for k,v in env.items())
    ):
        raise RuntimeDenied("invalid_subprocess_environment")
    flags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0)
    start=time.monotonic()
    process=subprocess.Popen(
        list(argv),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,cwd=cwd,env=env,shell=False,
        start_new_session=os.name!="nt",creationflags=flags,
    )
    try:
        code=process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        if os.name!="nt":
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            # Windows group child processes need stronger host Job Object
            # enforcement for full tree cancellation; taskkill /T /F.
            subprocess.run(
                ["taskkill","/PID",str(process.pid),"/T","/F"],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,timeout=10,check=False
            )
        process.kill()
        process.wait(timeout=10)
        raise RuntimeDenied("subprocess_timeout") from exc
    if code!=0:
        raise RuntimeDenied("subprocess_failed")
    return {"returncode":code,"elapsed_sec":round(time.monotonic()-start,3)}


class LocalGAOrchestrator:
    """Explicit locally-registered GA20 worker, never enabled by import.

    A registered executor(claim,should_stop) must be a real local-only
    extractor; expensive models, paid providers and network calls are not
    permitted. verify_output must authenticate immutable on-disk/DB
    provenance and current media bytes, not return True unconditionally.
    """
    def __init__(self,*,connect,executors,verify_current,verify_output,
                 budget=None,heartbeat_period_sec=10):
        if (not callable(connect) or type(executors) is not dict
            or not callable(verify_current) or
            not callable(verify_output)):
            raise RuntimeDenied("trusted_worker_ports_required")
        if (any(x not in STEP_KINDS or not callable(fn)
                for x,fn in executors.items()) or not executors):
            raise RuntimeDenied("invalid_local_executor_registry")
        if type(heartbeat_period_sec) is not int or not 1<=heartbeat_period_sec<=60:
            raise RuntimeDenied("invalid_heartbeat_period")
        self.connect=connect
        self.executors=dict(executors)
        self.verify_current=verify_current
        self.verify_output=verify_output
        self.budget=ResourceBudget() if budget is None else budget
        if not isinstance(self.budget,ResourceBudget):
            raise RuntimeDenied("invalid_resource_budget")
        if heartbeat_period_sec>=self.budget.lease_seconds:
            raise RuntimeDenied("heartbeat_cannot_outlive_lease")
        self.heartbeat_period=heartbeat_period_sec

    def once(self,*,worker_id):
        """Claim, execute outside SQL, heartbeat and atomically commit output.

        The executor should poll should_stop(), and its subprocess watchdog
        must not exceed stage.max_runtime_sec. If it ignores cancellation
        and finishes late, the token/deadline fences reject its output.
        """
        with self.connect() as conn:
            claim=claim_next_step(
                conn,worker_id=worker_id,budget=self.budget,
                verify_current=self.verify_current,
            )
        if claim is None:
            return None
        stop=Event()
        failed=Event()

        def pulse():
            while not stop.wait(self.heartbeat_period):
                try:
                    with self.connect() as c:
                        heartbeat_step(
                            c,claim,lease_seconds=self.budget.lease_seconds,
                            verify_current=self.verify_current
                        )
                except Exception:
                    failed.set()
                    return

        ticker=Thread(target=pulse,daemon=True,name="ga20-lease-heartbeat")
        ticker.start()

        def should_stop():
            if stop.is_set() or failed.is_set():
                return True
            with self.connect() as c:
                return not lease_is_live(c,claim)

        try:
            executor=self.executors.get(claim.step_kind)
            if executor is None:
                raise RuntimeDenied("local_stage_executor_missing")
            if should_stop():
                raise RuntimeDenied("step_cancelled_before_execution")
            output=executor(claim,should_stop)
            if not isinstance(output,LocalStepOutput):
                raise RuntimeDenied("invalid_extractor_result")
            if should_stop():
                raise RuntimeDenied("step_lease_revoked_after_execution")
            with self.connect() as conn:
                state=finish_step(
                    conn,claim,output_sha256=output.output_sha256,
                    partial=output.partial,verify_current=self.verify_current,
                    verify_output=self.verify_output,
                )
            return {"state":state,"run_id":claim.run_id,
                    "step_key":claim.step_key,"attempt":claim.attempt_no}
        except Exception as exc:
            code=(exc.code if isinstance(exc,RuntimeDenied) else "executor_failed")
            if code not in ("executor_failed","subprocess_timeout",
                            "subprocess_failed","local_stage_executor_missing",
                            "step_cancelled_before_execution",
                            "step_lease_revoked_after_execution",
                            "invalid_extractor_result"):
                code="executor_failed"
            try:
                with self.connect() as conn:
                    finish_step(
                        conn,claim,error_code=code,
                        verify_current=self.verify_current
                    )
            except RuntimeDenied:
                # A crashed/stale/cancelled attempt cannot overwrite the
                # state of the new worker or a revoked run.
                pass
            return {"state":"failed_or_revoked","step_key":claim.step_key,
                    "reason_code":code}
        finally:
            stop.set()
            ticker.join(timeout=self.heartbeat_period+1)

    def serve(self,*,worker_id,stop_requested,poll_seconds=2):
        """Explicit blocking loop; caller owns lifecycle and stop signal."""
        if not callable(stop_requested) or type(poll_seconds) not in (float,int):
            raise RuntimeDenied("invalid_worker_lifecycle")
        if not 0.1<=poll_seconds<=30:
            raise RuntimeDenied("invalid_worker_poll")
        processed=0
        while stop_requested() is not True:
            result=self.once(worker_id=worker_id)
            if result is None:
                time.sleep(poll_seconds)
            else:
                processed+=1
        return processed
