from __future__ import annotations
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from .config import Policy, browser_path, home, inside, normalize
from .logging_utils import audit
from .platform_ops import is_admin, run, system_tool
from .scanner import ScanLock
from .store import Store, now


def rules():
    user = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", user / "AppData/Local"))
    roaming = Path(os.environ.get("APPDATA", user / "AppData/Roaming"))
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    program = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    return {
        "user-temp": {"title": "File tạm người dùng", "roots": [str(local / "Temp")], "days": 7, "risk": "Thấp; file đang khóa được bỏ qua"},
        "windows-temp": {"title": "File tạm Windows", "roots": [str(windows / "Temp")], "days": 7, "risk": "Cần quyền Admin; giữ file mới/đang dùng"},
        "crash-dumps": {"title": "Crash dump cũ", "roots": [str(local / "CrashDumps")], "days": 7, "risk": "Mất dữ liệu chẩn đoán lỗi cũ"},
        "pip-cache": {"title": "Cache pip", "roots": [str(local / "pip/Cache")], "days": 1, "risk": "Lần cài sau phải tải lại; không xóa package đã cài"},
        "npm-cache": {"title": "Cache npm", "roots": [str(local / "npm-cache")], "days": 7, "risk": "Lần cài sau phải tải lại"},
        "nuget-cache": {"title": "Cache package NuGet", "roots": [str(user / ".nuget/packages")], "days": 7, "risk": "Dừng build .NET trước; dọn cả cache bằng CLI chính thức để tránh package nửa vời; cần mạng", "tool": "nuget"},
        "gradle-cache": {"title": "Gradle cache và log daemon", "roots": [str(user / ".gradle/caches"), str(user / ".gradle/daemon")], "days": 14, "risk": "Dừng Gradle/build APK trước; cần mạng để tải lại"},
        "vscode-cache": {"title": "VS Code cache/log", "roots": [str(roaming / "Code" / name) for name in ("CachedExtensionVSIXs", "CachedData", "Cache", "GPUCache", "logs")], "days": 7, "risk": "Đóng VS Code trước; giữ nguyên User và Backups"},
        "nvidia-shader": {"title": "NVIDIA shader cache", "roots": [str(local / "NVIDIA/DXCache"), str(local / "NVIDIA/GLCache")], "days": 7, "risk": "Đóng game/app đồ họa; shader tạo lại có thể gây khựng lần đầu"},
        "nvidia-update": {"title": "NVIDIA update artifact", "roots": [str(program / "NVIDIA Corporation/NVIDIA App/UpdateFramework/ota-artifacts/crd"), str(program / "NVIDIA Corporation/NVIDIA App/UpdateFramework/ota-artifacts/nvapp")], "days": 7, "risk": "Chỉ dọn khi update đã xong; có thể mất gói rollback/tải lại", "requires_update_done": True},
        "winsxs": {"title": "Windows component cleanup", "roots": [str(windows / "WinSxS")], "days": 0, "risk": "DISM được Windows hỗ trợ; cần Administrator", "tool": "dism"},
        "windows-update": {"title": "Windows Update Cleanup", "roots": [str(windows / "SoftwareDistribution/Download")], "days": 0, "risk": "Mở công cụ Windows để chọn mục; không xóa thẳng khi đang update", "tool": "cleanmgr"},
        "recycle-bin": {"title": "Dọn Thùng rác ổ C", "roots": [r"C:\$Recycle.Bin"], "days": 0, "risk": "Xóa vĩnh viễn nội dung Thùng rác ổ C", "tool": "recycle"},
    }


def safe_file(path: Path, root: Path):
    if not inside(path, root) or browser_path(path) or inside(path, home()):
        return False
    if normalize(root.resolve(strict=True)) != normalize(root.absolute()):
        return False
    # Resolve every ancestor to reject junction/symlink substitutions after preview.
    current = path
    while True:
        st = current.lstat()
        if current.is_symlink() or getattr(st, "st_file_attributes", 0) & 0x400:
            return False
        if normalize(current) == normalize(root):
            return True
        if current.parent == current:
            return False
        current = current.parent


def candidates(root: Path, days, errors,stop=None):
    from .elevation import ElevationCancelled
    if not root.exists():
        return
    if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
        errors.append({"path": str(root), "error": "Root là reparse point; bỏ qua"})
        return
    stack = [root]
    cutoff = time.time() - days * 86400
    while stack:
        if stop is not None and stop.is_set():raise ElevationCancelled('Đã dừng tạo đề xuất; chưa xóa gì.')
        folder = stack.pop()
        try:
            with os.scandir(folder) as items:
                for entry in items:
                    if stop is not None and stop.is_set():raise ElevationCancelled('Đã dừng tạo đề xuất; chưa xóa gì.')
                    p = Path(entry.path)
                    if browser_path(p):
                        continue
                    try:
                        # DirEntry.stat may have st_ino=0 on Windows. Path.stat supplies
                        # the file identity needed to revalidate a preview accurately.
                        st = p.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(st, "st_file_attributes", 0) & 0x400:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(p)
                        elif st.st_mtime < cutoff and st.st_size:
                            yield {"path": str(p), "root": str(root), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                   "identity": f"{st.st_dev}:{st.st_ino}"}
                    except OSError as exc:
                        errors.append({"path": str(p), "error": str(exc)})
        except OSError as exc:
            errors.append({"path": str(folder), "error": str(exc)})


def plan(rule_ids, update_done=False, days=None, *, allow_uac=True, notify=None,stop=None):
    from .elevation import ElevationCancelled
    def check_stop():
        if stop is not None and stop.is_set():raise ElevationCancelled('Đã dừng tạo đề xuất; chưa xóa gì.')
    check_stop()
    known = rules()
    if any(x not in known for x in rule_ids):raise ValueError('Nhóm dọn không hợp lệ')
    needs_admin=any(x=='windows-temp' or (x=='nvidia-update' and update_done) for x in rule_ids)
    if needs_admin and not is_admin():
        if not allow_uac:raise PermissionError('Quét đề xuất hệ thống cần quyền Admin; không xin UAC từ bộ chạy nền.')
        from .elevation import request
        return request('cleanup-plan',{'rules':list(rule_ids),'update_done':bool(update_done),'days':days},notify=notify,stop=stop)
    entries, errors, blocked = [], [], []
    seen = set()
    for rule_id in rule_ids:
        check_stop()
        if rule_id not in known:
            raise ValueError(f"Rule không tồn tại: {rule_id}")
        rule = known[rule_id]
        if rule.get("requires_update_done") and not update_done:
            blocked.append({"rule": rule_id, "reason": "Cần xác nhận NVIDIA update đã hoàn tất"})
            continue
        if rule.get("tool"):
            entries.append({"rule": rule_id, "tool": rule["tool"], "path": rule["roots"][0], "size": None, "risk": rule["risk"]})
            continue
        age = rule["days"] if days is None else max(rule["days"], days)
        for root in rule["roots"]:
            for e in candidates(Path(root), age, errors,stop=stop):
                check_stop()
                if e["path"] in seen:
                    continue
                seen.add(e["path"])
                entries.append({**e, "rule": rule_id, "risk": rule["risk"]})
    check_stop()
    plan_id = uuid.uuid4().hex[:12]
    content = {"id": plan_id, "created": now(), "expires": time.time() + 3600, "rules": rule_ids, "update_done": update_done,
               "entries": entries, "bytes": sum(e.get("size") or 0 for e in entries), "errors": errors, "blocked": blocked,
               "confirmation": f"XOA {plan_id}", "note": "Plan chỉ xem trước. Xóa cache là vĩnh viễn; bytes dự kiến không phải free space chắc chắn."}
    s = Store()
    try:
        s.db.execute("INSERT INTO plans VALUES(?,?,?,?,?)", (plan_id, now(), "pending", json.dumps(content, ensure_ascii=False), ""))
        s.db.commit()
    finally:
        s.close()
    audit("cleanup-plan", id=plan_id, rules=rule_ids, bytes=content["bytes"], count=len(entries))
    return content


def get_plan(plan_id):
    s = Store()
    try:
        row = s.db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("Không tìm thấy plan")
        content = json.loads(row["content"])
        content["status"] = row["status"]
        content["result"] = json.loads(row["result"]) if row["result"] else None
        return content
    finally:
        s.close()


def select(plan_id, paths):
    p = get_plan(plan_id)
    if p["status"] != "pending":
        raise ValueError("Plan đã được xử lý")
    allowed = set(paths)
    p["entries"] = [e for e in p["entries"] if e["path"] in allowed]
    p["bytes"] = sum(e.get("size") or 0 for e in p["entries"])
    p.pop("status", None)
    p.pop("result", None)
    s = Store()
    s.db.execute("UPDATE plans SET content=? WHERE id=?", (json.dumps(p, ensure_ascii=False), plan_id))
    s.db.commit()
    s.close()
    return p


def tool_apply(e,stop=None):
    tool = e["tool"]
    if tool == "dism":
        if not is_admin():
            raise PermissionError("DISM cần Administrator; chạy launcher bằng Run as administrator")
        result = run([system_tool("Dism.exe"), "/Online", "/Cleanup-Image", "/StartComponentCleanup"], timeout=7200,maintenance=True,stop=stop)
        if result.returncode not in (0, 3010):
            raise RuntimeError(f"DISM exit={result.returncode}: {result.stdout[-1000:]}")
        return {"exit": result.returncode, "requires_reboot": result.returncode == 3010,'cancel_requested':getattr(result,'interrupted',False)}
    if tool == "cleanmgr":
        result = run([system_tool("cleanmgr.exe"), "/d", "C"], timeout=7200)
        return {"exit": result.returncode, "note": "Windows Cleanup mở; người dùng chọn mục trong công cụ Windows"}
    if tool == "recycle":
        import ctypes
        code = ctypes.windll.shell32.SHEmptyRecycleBinW(None, "C:\\", 1 | 2 | 4)
        if code not in (0, -2147467259):
            raise OSError(f"Recycle Bin HRESULT={code}")
        return {"exit": code}
    if tool == "nuget":
        executable = shutil.which("dotnet")
        if not executable:
            raise RuntimeError("Không có dotnet CLI; app không xóa package NuGet từng file để tránh hỏng cache")
        import psutil
        active = []
        for process in psutil.process_iter(["pid", "name"]):
            name = (process.info["name"] or "").casefold()
            if name in ("msbuild.exe", "dotnet.exe", "devenv.exe"):
                active.append(name)
        if active:
            raise RuntimeError("Đóng build/.NET/Visual Studio trước khi dọn NuGet")
        result = run([executable, "nuget", "locals", "global-packages", "--clear"], timeout=600)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return {"exit": result.returncode, "note": "Đã dùng dotnet nuget locals global-packages --clear; dung lượng tool không tính trước"}
    raise ValueError("Tool không được phép")


def apply(plan_id, confirmation, *, allow_uac=True, expected_digest=None, notify=None,stop=None):
    import threading
    stop=stop or threading.Event()
    p = get_plan(plan_id)
    if p["status"] != "pending" or p["expires"] < time.time():
        raise ValueError("Plan đã dùng hoặc hết hạn; tạo preview mới")
    if confirmation != p["confirmation"]:
        raise ValueError(f"Cần xác nhận đúng: {p['confirmation']}")
    from .elevation import digest,request
    if expected_digest is not None and digest(p)!=expected_digest:raise ValueError('Đề xuất đã thay đổi sau khi duyệt/UAC; không xóa. Xem và duyệt lại đề xuất.')
    known=rules()
    for entry in p['entries']:
        rule=known.get(entry.get('rule'))
        if not rule or entry.get('tool')!=rule.get('tool'):raise ValueError('Mục dọn/tool không thuộc rule đã cho phép')
        if entry.get('tool') and normalize(entry['path']) not in {normalize(r) for r in rule['roots']}:raise ValueError('Đường dẫn tool không thuộc rule')
        if rule.get('requires_update_done') and not p.get('update_done'):raise ValueError('Chưa xác nhận cập nhật đã hoàn tất')
    needs_admin=any(e.get('rule') in ('windows-temp','nvidia-update','winsxs','windows-update') for e in p['entries'])
    if needs_admin and not is_admin():
        if not allow_uac:raise PermissionError('Dọn hệ thống cần quyền Admin; không tự xin UAC lặp lại.')
        return request('cleanup-apply',{'plan_id':plan_id,'confirmation':confirmation,'plan_digest':digest(p)},stop=stop,notify=notify)
    results, deleted_bytes = [], 0
    before = shutil.disk_usage("C:\\" if os.name == "nt" else "/").free
    with ScanLock(stop):
        for e in p["entries"]:
            if stop.is_set():break
            try:
                if e.get("tool"):
                    result = {"path": e["path"], "status": "tool", **tool_apply(e,stop=stop)}
                    if result.get('cancel_requested'):stop.set()
                else:
                    path, root = Path(e["path"]), Path(e["root"])
                    actual_rule = rules().get(e["rule"])
                    if not actual_rule or normalize(root) not in {normalize(r) for r in actual_rule["roots"]}:
                        raise ValueError("Root không thuộc rule hiện tại")
                    if not safe_file(path, root):
                        raise ValueError("Path/reparse point không còn hợp lệ")
                    st = path.stat(follow_symlinks=False)
                    if st.st_size != e["size"] or st.st_mtime_ns != e["mtime_ns"] or f"{st.st_dev}:{st.st_ino}" != e["identity"]:
                        raise ValueError("File thay đổi sau preview; bỏ qua")
                    path.unlink()
                    deleted_bytes += e["size"]
                    result = {"path": str(path), "status": "deleted", "bytes": e["size"]}
                audit("cleanup-item", plan=plan_id, **result)
            except Exception as exc:
                result = {"path": e["path"], "status": "skipped", "error": str(exc)}
                audit("cleanup-item", plan=plan_id, **result)
            results.append(result)
    after = shutil.disk_usage("C:\\" if os.name == "nt" else "/").free
    result = {"items": results, "deleted_bytes": deleted_bytes, "free_before": before, "free_after": after,
              "free_delta": after - before,'cancelled':stop.is_set(),'not_processed':len(p['entries'])-len(results),
              "note": "Free delta có thể gồm tác vụ khác đang chạy đồng thời. Nếu đã hủy: các mục chưa xử lý được giữ nguyên; xem kết quả trước khi tạo đề xuất mới."}
    s = Store()
    s.db.execute("UPDATE plans SET status=?,result=? WHERE id=?", ('cancelled' if stop.is_set() else 'applied',json.dumps(result, ensure_ascii=False), plan_id))
    s.db.commit()
    s.close()
    return result

