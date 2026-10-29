"""M41 local, cooperative, restart-fail-closed worker control.

No subprocess launch, PID kill, network, provider, DB or environment read at
import. A foreground CLI loop holds a private O_EXCL owner record; a second
operator command creates a nonce-scoped request. Worker checks between S4
offline_fixture jobs and removes its own files only after durable work ends.

A crash leaves a claim intentionally STOPPED/IN_DOUBT; it cannot silently
reclaim a live PID or worker lease. Manual recovery checks OS liveness.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import secrets
import stat
import time

VERSION="sre-worker-control/v1"
OWNER="sre-offline-worker.owner.json"
STOP="sre-offline-worker.stop.json"
MAX_BYTES=1024


class WorkerControlBlocked(RuntimeError):
    pass


def _folder(directory):
    if not isinstance(directory,(str,Path)) or not str(directory):
        raise WorkerControlBlocked("control_dir_required")
    p=Path(directory).expanduser()
    if p.is_symlink() or not p.exists() or not p.is_dir():
        raise WorkerControlBlocked("private_existing_control_dir_required")
    if os.name=="posix":
        details=p.stat()
        if details.st_uid!=os.getuid() or details.st_mode & 0o077:
            raise WorkerControlBlocked("control_dir_must_be_owner_only_0700")
    return p


def _read(path):
    try:
        flags=os.O_RDONLY | getattr(os,"O_NOFOLLOW",0)
        fd=os.open(path,flags)
    except FileNotFoundError:
        return None
    except OSError:
        raise WorkerControlBlocked("control_record_unreadable") from None
    try:
        meta=os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size>MAX_BYTES:
            raise WorkerControlBlocked("control_record_not_regular_or_oversized")
        if os.name=="posix" and (meta.st_uid!=os.getuid() or
                                  meta.st_mode&0o077):
            raise WorkerControlBlocked("control_record_unsafe_permissions")
        with os.fdopen(fd,"rb",closefd=False) as f:
            raw=f.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:
            raise WorkerControlBlocked("control_record_oversized")
        data=json.loads(raw.decode("utf-8"))
        if (type(data) is not dict or
            set(data)!=set(("schema_version","pid","project_id","nonce")) or
            data.get("schema_version")!=VERSION or
            type(data.get("pid")) is not int or data["pid"]<=0 or
            type(data.get("nonce")) is not str or len(data["nonce"])!=32 or
            any(c not in "0123456789abcdef" for c in data["nonce"]) or
            type(data.get("project_id")) is not str or
            len(data["project_id"])!=32 or
            any(c not in "0123456789abcdef" for c in data["project_id"])):
            raise WorkerControlBlocked("control_record_invalid")
        return data
    except (UnicodeError,ValueError,TypeError):
        raise WorkerControlBlocked("control_record_invalid") from None
    finally:
        os.close(fd)


def _write_exclusive(path,data):
    body=json.dumps(data,sort_keys=True,separators=(",",":")).encode("utf-8")
    if len(body)>MAX_BYTES:raise WorkerControlBlocked("control_record_oversized")
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|
                   getattr(os,"O_NOFOLLOW",0),0o600)
    except FileExistsError:
        raise WorkerControlBlocked("control_record_already_exists") from None
    except OSError:
        raise WorkerControlBlocked("control_record_unwritable") from None
    try:
        with os.fdopen(fd,"wb",closefd=False) as out:
            out.write(body);out.flush();os.fsync(fd)
    except Exception:
        try:os.unlink(path)
        except OSError:pass
        raise WorkerControlBlocked("control_record_unwritable") from None
    finally:
        os.close(fd)


def _same(a,b):
    return a is not None and b is not None and a==b


class WorkerControl:
    def __init__(self,directory):
        self.directory=_folder(directory)
        self.owner=self.directory/OWNER
        self.stop=self.directory/STOP

    def claim(self,project):
        from .v1_contracts import project_id
        pid=project_id(project)
        if self.stop.exists():
            raise WorkerControlBlocked("orphan_stop_must_be_reconciled")
        data={"schema_version":VERSION,"pid":os.getpid(),
              "project_id":pid,"nonce":secrets.token_hex(16)}
        _write_exclusive(self.owner,data)
        return data

    def stop_requested(self,record):
        if not _same(_read(self.owner),record):
            raise WorkerControlBlocked("worker_owner_changed")
        return _same(_read(self.stop),record)

    def request_stop(self):
        owner=_read(self.owner)
        if owner is None:
            return {"status":"not_running"}
        existing=_read(self.stop)
        if existing is not None:
            if not _same(existing,owner):
                raise WorkerControlBlocked("stop_record_other_generation")
            return {"status":"stop_already_requested"}
        _write_exclusive(self.stop,owner)
        if not _same(_read(self.owner),owner):
            raise WorkerControlBlocked("worker_owner_changed_during_stop")
        return {"status":"cooperative_stop_requested"}

    def status(self):
        record=_read(self.owner)
        if record is None:
            if _read(self.stop) is not None:
                return {"status":"orphan_stop_recovery_required"}
            return {"status":"stopped"}
        return {
            "status":"stop_requested" if _same(_read(self.stop),record)
                     else "claimed_or_crashed",
            "pid":record["pid"],"project_id":record["project_id"],
            "paid_post_calls":0,
        }

    def release(self,record):
        if not _same(_read(self.owner),record):
            raise WorkerControlBlocked("owner_token_changed")
        if _same(_read(self.stop),record):
            self.stop.unlink()
        # Race with a late stop command is still possible; leave an orphan
        # stop file fail-closed, recover only by explicit operator action.
        self.owner.unlink()

    def recover_stale(self,*,acknowledged):
        if acknowledged is not True:
            raise WorkerControlBlocked("explicit_stale_recovery_ack_required")
        owner=_read(self.owner)
        if owner is None:
            if _read(self.stop) is not None:
                self.stop.unlink()
            return {"status":"orphan_stop_cleared"}
        live=pid_alive(owner["pid"])
        if live is not False:
            raise WorkerControlBlocked("owner_may_still_be_running")
        stop=_read(self.stop)
        if stop is not None and not _same(stop,owner):
            raise WorkerControlBlocked("stop_record_other_generation")
        if not _same(_read(self.owner),owner):
            raise WorkerControlBlocked("owner_changed_during_recovery")
        if stop is not None:self.stop.unlink()
        self.owner.unlink()
        return {"status":"stale_owner_cleared_manual_audit_required"}


def pid_alive(pid):
    """True alive, False definitely absent, None uncertain: NEVER auto reclaim.

    Windows uses native process query, NOT os.kill or taskkill. Permission
    errors and races fail closed. A recycled PID appears alive => no reclaim.
    """
    if type(pid) is not int or pid<=0:return None
    if os.name=="nt":
        try:
            import ctypes
            kernel=ctypes.WinDLL("kernel32",use_last_error=True)
            kernel.OpenProcess.argtypes=(ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong)
            kernel.OpenProcess.restype=ctypes.c_void_p
            handle=kernel.OpenProcess(0x1000,0,pid) # QUERY_LIMITED_INFORMATION
            if not handle:
                return False if ctypes.get_last_error()==87 else None
            try:
                exit_code=ctypes.c_ulong()
                kernel.GetExitCodeProcess.argtypes=(ctypes.c_void_p,
                                                    ctypes.POINTER(ctypes.c_ulong))
                kernel.GetExitCodeProcess.restype=ctypes.c_int
                if not kernel.GetExitCodeProcess(
                        handle,ctypes.byref(exit_code)):return None
                return exit_code.value==259 # STILL_ACTIVE
            finally:
                kernel.CloseHandle.argtypes=(ctypes.c_void_p,)
                kernel.CloseHandle(handle)
        except Exception:
            return None
    if os.name!="posix":return None
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


__all__=["VERSION","WorkerControl","WorkerControlBlocked","pid_alive"]
