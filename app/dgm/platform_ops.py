from __future__ import annotations
import ctypes
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

_measurement = threading.local()


def reset_measurements():
    _measurement.runs = []


def measurements():
    return getattr(_measurement, "runs", [])


class ProcessCancelled(Exception):
    """A cooperative cancellation, not an engine failure."""


def run(argv, timeout=None, stop=None, *, maintenance=False):
    logging.getLogger("dgm").debug("EXEC cwd=%s argv=%s", Path.cwd(), json.dumps([str(x) for x in argv], ensure_ascii=False))
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    if stop is not None and stop.is_set():
        raise ProcessCancelled()
    job = None
    interrupted=False
    flags = 0x08000000 if os.name == "nt" else 0
    # Suspend cancellable Windows tools until they belong to our private Job.
    # This prevents child processes from escaping before cancellation is armed.
    if os.name == "nt" and stop is not None:
        flags |= 0x00000004  # CREATE_SUSPENDED
    with subprocess.Popen([str(x) for x in argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          errors="replace", startupinfo=startup, creationflags=flags,
                          start_new_session=os.name != "nt" and stop is not None) as process:
        try:
            if os.name == "nt" and stop is not None:
                import psutil, win32api, win32job, win32process
                import uuid
                job = win32job.CreateJobObject(None, "Local\\DiskGrowthMonitor-" + uuid.uuid4().hex)
                limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
                limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
                win32job.AssignProcessToJobObject(job, int(process._handle))
                for thread_info in psutil.Process(process.pid).threads():
                    handle = win32api.OpenThread(0x0002, False, thread_info.id)
                    try:
                        win32process.ResumeThread(handle)
                    finally:
                        handle.Close()
            deadline = time.monotonic() + timeout if timeout is not None else None
            while True:
                if stop is not None and stop.is_set():
                    if not maintenance:raise ProcessCancelled()
                    interrupted=True
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    if not maintenance:raise subprocess.TimeoutExpired(argv, timeout)
                    logging.getLogger('dgm').warning('Windows maintenance exceeded timeout; waiting for safe completion, not terminating the process')
                    interrupted=True;deadline=None;remaining=None
                try:
                    stdout, stderr = process.communicate(timeout=min(0.15, remaining) if remaining is not None else 0.15)
                    break
                except subprocess.TimeoutExpired:
                    continue
                except KeyboardInterrupt:
                    if not maintenance:raise
                    interrupted=True
                    logging.getLogger('dgm').warning('Stop requested; waiting for Windows maintenance to finish safely')
        except BaseException:
            if job is not None:
                import win32job
                win32job.TerminateJobObject(job, 130)
            elif os.name != "nt" and stop is not None:
                import signal
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.communicate()
            raise
        finally:
            if job is not None:
                job.Close()
        if os.name == "nt":
            try:
                import win32process
                times = win32process.GetProcessTimes(int(process._handle))
                memory = win32process.GetProcessMemoryInfo(int(process._handle))
                io = win32process.GetProcessIoCounters(int(process._handle))
                item = {"pid": process.pid, "cpu_seconds": (times["UserTime"] + times["KernelTime"]) / 10_000_000,
                        "peak_rss": memory["PeakWorkingSetSize"], "read_bytes": io["ReadTransferCount"], "write_bytes": io["WriteTransferCount"]}
                if hasattr(_measurement, "runs"):
                    _measurement.runs.append(item)
            except Exception as exc:
                logging.getLogger("dgm").debug("Subprocess metrics unavailable: %s", exc)
        result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        result.interrupted=interrupted
    logging.getLogger("dgm").debug("EXIT=%s stdout=%s stderr=%s", result.returncode, result.stdout, result.stderr)
    return result


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == "nt" else os.geteuid() == 0


def system_tool(name):
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name)


def open_item(path, action="open"):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if os.name != "nt":
        raise RuntimeError("Thao tác Explorer chỉ có trên Windows")
    from .logging_utils import audit
    audit("shell", path=str(p), operation=action)
    if action == "reveal":
        subprocess.Popen([system_tool("explorer.exe"), "/select,", str(p)])
    elif action == "properties":
        from win32com.shell import shell, shellcon
        shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_INVOKEIDLIST, lpVerb="properties", lpFile=str(p), nShow=1)
    else:
        os.startfile(str(p))


def dangerous_path(path):
    from .config import browser_path, inside, ROOT, normalize
    n = normalize(path)
    if browser_path(path) or inside(path, ROOT):
        return True
    parts = n.split("/")
    user = Path.home()
    if any(normalize(path) == normalize(p) for p in (user, user / "AppData", user / "AppData/Local", user / "AppData/Roaming")):
        return True
    if inside(path, user / "AppData/Local/Programs"):
        return True
    if any(x in {".venv", "venv", ".runtime", ".git", ".ssh"} for x in parts):
        return True
    if Path(path).name.casefold() in {"$mft", "pagefile.sys", "swapfile.sys", "hiberfil.sys"}:
        return True
    base = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    if inside(path, base) or inside(path, r"C:\Program Files") or inside(path, r"C:\Program Files (x86)"):
        return True
    return len(parts) <= 2


def manual_action(path, action, new_name=None):
    p = Path(path).absolute()
    if action in ("open", "reveal", "properties"):
        return open_item(p, action)
    if dangerous_path(p):
        raise ValueError("Mục được bảo vệ; hãy dùng quy tắc dọn phù hợp hoặc công cụ Windows.")
    for ancestor in [*reversed(list(p.parents)), p]:
        st = ancestor.lstat()
        if ancestor.is_symlink() or getattr(st, "st_file_attributes", 0) & 0x400:
            raise ValueError("Path đi qua junction/symlink; không thao tác xóa/đổi tên qua alias.")
    from .logging_utils import audit
    if action == "trash":
        from send2trash import send2trash
        send2trash(str(p))
        audit("manual-trash", path=str(p))
    elif action == "rename":
        if not new_name or new_name in (".", "..") or any(c in new_name for c in '/\\<>:"|?*'):
            raise ValueError("Tên mới không hợp lệ")
        target = p.with_name(new_name)
        if target.exists():
            raise ValueError("Tên mới đã tồn tại")
        p.rename(target)
        audit("rename", path=str(p), target=str(target))
    else:
        raise ValueError("Thao tác không hợp lệ")
