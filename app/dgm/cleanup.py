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
    base = {
        "user-temp": {"title": "File tạm người dùng", "roots": [str(local / "Temp")], "days": 7, "risk": "Thấp; file đang khóa được bỏ qua"},
        "windows-temp": {"title": "File tạm Windows", "roots": [str(windows / "Temp")], "days": 7, "risk": "Cần quyền Admin; giữ file mới/đang dùng"},
        "crash-dumps": {"title": "Crash dump cũ", "roots": [str(local / "CrashDumps")], "days": 7, "risk": "Mất dữ liệu chẩn đoán lỗi cũ"},
        "pip-cache": {"title": "Cache pip", "roots": [str(local / "pip/Cache")], "days": 1, "risk": "Lần cài sau phải tải lại; không xóa package đã cài"},
        "npm-cache": {"title": "Cache npm", "roots": [str(local / "npm-cache")], "days": 7, "risk": "Lần cài sau phải tải lại"},
        "uv-cache": {"title": "Cache uv", "roots": [str(local / "uv/cache")], "days": 7, "risk": "Dừng uv/build; cần tải lại; không xóa môi trường .venv"},
        "nuget-cache": {"title": "Cache package NuGet", "roots": [str(user / ".nuget/packages")], "days": 7, "risk": "Dừng build .NET trước; dọn cả cache bằng CLI chính thức để tránh package nửa vời; cần mạng", "tool": "nuget"},
        "gradle-cache": {"title": "Gradle cache và log daemon", "roots": [str(user / ".gradle/caches"), str(user / ".gradle/daemon")], "days": 14, "risk": "Dừng Gradle/build APK trước; cần mạng để tải lại"},
        "vscode-cache": {"title": "VS Code cache/log", "roots": [str(roaming / "Code" / name) for name in ("CachedExtensionVSIXs", "CachedData", "Cache", "GPUCache", "logs")], "days": 7, "risk": "Đóng VS Code trước; giữ nguyên User và Backups"},
        "nvidia-shader": {"title": "NVIDIA shader cache", "roots": [str(local / "NVIDIA/DXCache"), str(local / "NVIDIA/GLCache")], "days": 7, "risk": "Đóng game/app đồ họa; shader tạo lại có thể gây khựng lần đầu"},
        "directx-shader": {"title": "DirectX shader cache", "roots": [str(local / "D3DSCache")], "days": 7, "risk": "Đóng app đồ họa; shader sẽ tạo lại"},
        "wer-reports": {"title": "Báo cáo lỗi Windows đã lưu", "roots": [str(local / "Microsoft/Windows/WER/ReportArchive")], "days": 14, "risk": "Mất báo cáo chẩn đoán cũ; giữ ReportQueue và file mới"},
        "nvidia-update": {"title": "NVIDIA update artifact", "roots": [str(program / "NVIDIA Corporation/NVIDIA App/UpdateFramework/ota-artifacts/crd"), str(program / "NVIDIA Corporation/NVIDIA App/UpdateFramework/ota-artifacts/nvapp")], "days": 7, "risk": "Chỉ dọn khi update đã xong; có thể mất gói rollback/tải lại", "requires_update_done": True},
        "winsxs": {"title": "Windows component cleanup", "roots": [str(windows / "WinSxS")], "days": 0, "risk": "DISM được Windows hỗ trợ; cần Administrator", "tool": "dism"},
        "windows-update": {"title": "Windows Update Cleanup", "roots": [str(windows / "SoftwareDistribution/Download")], "days": 0, "risk": "Mở công cụ Windows để chọn mục; không xóa thẳng khi đang update", "tool": "cleanmgr"},
        "recycle-bin": {"title": "Dọn Thùng rác ổ C", "roots": [r"C:\$Recycle.Bin"], "days": 0, "risk": "Xóa vĩnh viễn nội dung Thùng rác ổ C", "tool": "recycle"},
    }
    from .cleanup_catalog import enrich
    return enrich(base)


def safe_file(path: Path, root: Path):
    if not inside(path, root) or browser_path(path) or inside(path, home()):
        return False
    if protected_file(path):return False
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


def protected_file(path):
    parts={x.casefold() for x in Path(path).parts}
    name=Path(path).name.casefold()
    return bool(parts & {'.venv','.git','node_modules','driverstore','winsxs','installer','package cache','system volume information','models','checkpoints','android-sdk'}) or name.startswith(('ntuser.','$mft','pytorch_model')) or name.endswith(('.sqlite','.sqlite3','.db','.sqlite-wal','.sqlite-shm','.db-wal','.db-shm','.safetensors','.gguf','.ckpt','.onnx','.pt','.pth'))


def project_folder(folder):
    return any((folder/name).exists() for name in ('.git','pyproject.toml','package.json','gradlew','pom.xml'))


def candidates(root: Path, days, errors,stop=None):
    from .elevation import ElevationCancelled
    if not root.exists():
        return
    if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
        errors.append({"path": str(root), "error": "Root là reparse point; bỏ qua"})
        return
    stack = [root];app_root=home()
    cutoff = time.time() - days * 86400
    while stack:
        if stop is not None and stop.is_set():raise ElevationCancelled('Đã dừng tạo đề xuất; chưa xóa gì.')
        folder = stack.pop()
        if project_folder(folder):continue
        try:
            with os.scandir(folder) as items:
                for entry in items:
                    if stop is not None and stop.is_set():raise ElevationCancelled('Đã dừng tạo đề xuất; chưa xóa gì.')
                    p = Path(entry.path)
                    if browser_path(p) or protected_file(p) or inside(p,app_root):
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


def plan(rule_ids, update_done=False, days=None, *, allow_uac=True, notify=None,stop=None,compact=False):
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
        result=request('cleanup-plan',{'rules':list(rule_ids),'update_done':bool(update_done),'days':days},notify=notify,stop=stop)
        return get_plan(result['id'],compact=compact)
    if not rule_ids:raise ValueError('Chọn ít nhất một nhóm kiểm tra')
    from .progress import TaskProgress
    progress=TaskProgress('cleanup-plan',notify or (lambda *a:None),[('rules','Kiểm tra điều kiện nhóm',10),('files','Đọc metadata và lập đề xuất',85),('save','Lưu đề xuất nén',100)])
    progress.stage('rules');progress.complete('Đã kiểm tra nhóm và quyền cần thiết');progress.stage('files')
    errors, blocked = [], []
    class Errors:
        count=0
        def append(self,value):
            self.count+=1
            if len(errors)<1000:errors.append(value)
    collector=Errors()
    def generate():
        read=0;last=time.monotonic()
        for rule_id in dict.fromkeys(rule_ids):
            check_stop();rule=known[rule_id]
            if rule.get('requires_update_done') and not update_done:
                blocked.append({'rule':rule_id,'reason':'Cần xác nhận NVIDIA update đã hoàn tất'});continue
            if rule.get('tool'):
                yield {'rule':rule_id,'tool':rule['tool'],'path':rule['roots'][0],'size':None,'risk':rule['risk']};continue
            age=rule['days'] if days is None else max(rule['days'],days)
            for root in rule['roots']:
                for entry in candidates(Path(root),age,collector,stop=stop):
                    check_stop();read+=1
                    if time.monotonic()-last>=.5:
                        progress.advance(read,detail=f"{read:,} file đủ điều kiện; đang đọc {rule['title']}");last=time.monotonic()
                    yield {**entry,'rule':rule_id,'risk':rule['risk']}
    check_stop()
    plan_id = uuid.uuid4().hex[:12]
    from .cleanup_storage import write_entries,prune
    s=Store()
    try:prune(s,reserve_slot=True)
    finally:s.close()
    storage=write_entries(plan_id,generate(),stop=stop)
    progress.complete(f"Đã đọc metadata: {storage['entry_count']:,} mục, {collector.count:,} lỗi đọc");progress.stage('save')
    content = {"id": plan_id, "created": now(), "expires": time.time() + 3600, "rules": list(rule_ids), "update_done": update_done,
               **storage,"errors":errors,"error_count":collector.count,"blocked":blocked,
               "confirmation": f"XOA {plan_id}", "note": "Plan chỉ xem trước. Xóa cache là vĩnh viễn; bytes dự kiến không phải free space chắc chắn."}
    s = Store()
    try:
        s.db.execute("INSERT INTO plans VALUES(?,?,?,?,?)", (plan_id, now(), "pending", json.dumps(content, ensure_ascii=False), ""))
        s.db.commit()
    finally:
        s.close()
    audit("cleanup-plan", id=plan_id, rules=rule_ids, bytes=content["bytes"], count=content['entry_count'])
    progress.finish(f"Đã lưu đề xuất nén {storage['entry_count']:,} mục; chưa xóa gì")
    return get_plan(plan_id,compact=compact)


def get_plan(plan_id,*,compact=False):
    s = Store()
    try:
        row = s.db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("Không tìm thấy plan")
        content = json.loads(row["content"])
        content["status"] = row["status"]
        content["result"] = json.loads(row["result"]) if row["result"] else None
        if not compact and content.get('entry_archive'):
            from .cleanup_storage import entries
            content['entries']=list(entries(content))
        return content
    finally:
        s.close()


def select(plan_id, paths,*,compact=False,expected_digest=None):
    p = get_plan(plan_id,compact=True)
    if expected_digest is not None:
        from .elevation import digest
        if digest(p)!=expected_digest:raise ValueError('Đề xuất đã thay đổi sau khi hiển thị; mở và duyệt lại')
    if p["status"] != "pending" or p['expires']<time.time():
        raise ValueError("Plan đã được xử lý")
    allowed = set(paths)
    if not allowed:raise ValueError('Chưa chọn mục; không xử lý đề xuất rỗng')
    from .cleanup_storage import entries,write_entries,remove_artifacts
    old=dict(p)
    selected=write_entries(plan_id,(e for e in entries(p) if e['path'] in allowed))
    if selected['entry_count']!=len(allowed):
        remove_artifacts(selected);raise ValueError('Lựa chọn có đường dẫn không thuộc đề xuất')
    p.update(selected)
    p.pop("status", None)
    p.pop("result", None)
    s = Store()
    try:
        s.db.execute('BEGIN IMMEDIATE');row=s.db.execute('SELECT * FROM plans WHERE id=?',(plan_id,)).fetchone()
        actual=json.loads(row['content']);actual.update(status=row['status'],result=json.loads(row['result']) if row['result'] else None)
        from .elevation import digest
        if actual.get('status')!='pending' or digest(actual)!=digest(old):raise ValueError('Đề xuất đã thay đổi; xem lại lựa chọn')
        s.db.execute("UPDATE plans SET content=? WHERE id=?", (json.dumps(p, ensure_ascii=False), plan_id));s.db.commit()
    except BaseException:
        remove_artifacts(selected);raise
    finally:s.close()
    try:remove_artifacts(old)
    except OSError:pass  # another reader holds the old disposable index; expiry can remove it later
    return get_plan(plan_id,compact=compact)


def plan_page(plan_id,**options):
    from .cleanup_storage import page
    p=get_plan(plan_id,compact=True)
    if p.get('entries_purged'):return {'entries':[],'total':0,'offset':0,'limit':options.get('limit',20)}
    return page(p,**options)


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
    p = get_plan(plan_id,compact=True)
    if p["status"] != "pending" or p["expires"] < time.time():
        raise ValueError("Plan đã dùng hoặc hết hạn; tạo preview mới")
    if confirmation != p["confirmation"]:
        raise ValueError(f"Cần xác nhận đúng: {p['confirmation']}")
    from .elevation import digest,request
    if expected_digest is not None and digest(p)!=expected_digest:raise ValueError('Đề xuất đã thay đổi sau khi duyệt/UAC; không xóa. Xem và duyệt lại đề xuất.')
    known=rules()
    from .cleanup_storage import entries
    for entry in entries(p):
        rule=known.get(entry.get('rule'))
        if not rule or entry.get('tool')!=rule.get('tool'):raise ValueError('Mục dọn/tool không thuộc rule đã cho phép')
        if entry.get('tool') and normalize(entry['path']) not in {normalize(r) for r in rule['roots']}:raise ValueError('Đường dẫn tool không thuộc rule')
        if rule.get('requires_update_done') and not p.get('update_done'):raise ValueError('Chưa xác nhận cập nhật đã hoàn tất')
    needs_admin=any(e.get('rule') in ('windows-temp','nvidia-update','winsxs','windows-update') for e in entries(p))
    if needs_admin and not is_admin():
        if not allow_uac:raise PermissionError('Dọn hệ thống cần quyền Admin; không tự xin UAC lặp lại.')
        return request('cleanup-apply',{'plan_id':plan_id,'confirmation':confirmation,'plan_digest':digest(p)},stop=stop,notify=notify)
    results, deleted_bytes, processed = [], 0, 0
    counts={'deleted':0,'skipped':0,'tool':0}
    from .progress import TaskProgress
    progress=TaskProgress('cleanup-apply',notify or (lambda *a:None),[('check','Kiểm tra lại đề xuất đã duyệt',10),('items','Xử lý từng mục đã chọn',95),('summary','Lưu biên bản dọn',100)])
    progress.stage('check');progress.complete('Đã kiểm tra xác nhận, hạn và rule');progress.stage('items')
    before = shutil.disk_usage("C:\\" if os.name == "nt" else "/").free
    from .cleanup_storage import claim
    with ScanLock(stop),claim(p):
        for e in entries(p):
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
            processed+=1;counts[result['status']]+=1
            if processed%100==0:progress.advance(processed,p.get('entry_count',len(p['entries'])),detail=f"Đã xử lý {processed:,} mục; bỏ qua {counts['skipped']:,}")
            if len(results)<1000:results.append(result)
    after = shutil.disk_usage("C:\\" if os.name == "nt" else "/").free
    if not stop.is_set():
        progress.complete(f"Đã xử lý {processed:,} mục; xóa {counts['deleted']:,}, bỏ qua {counts['skipped']:,}");progress.stage('summary')
    result = {"items": results, "deleted_bytes": deleted_bytes, "free_before": before, "free_after": after,
              "free_delta": after - before,'cancelled':stop.is_set(),'not_processed':p.get('entry_count',len(p['entries']))-processed,
              'processed':processed,'counts':counts,'items_truncated':processed>len(results),
              "note": "Free delta có thể gồm tác vụ khác đang chạy đồng thời. Nếu đã hủy: các mục chưa xử lý được giữ nguyên; xem kết quả trước khi tạo đề xuất mới."}
    s = Store()
    s.db.execute("UPDATE plans SET status=?,result=? WHERE id=?", ('cancelled' if stop.is_set() else 'applied',json.dumps(result, ensure_ascii=False), plan_id))
    s.db.commit()
    s.close()
    if stop.is_set():progress.emit('Đã dừng; các mục còn lại giữ nguyên',label='Đã dừng xử lý đề xuất')
    else:progress.finish(f"Đã lưu biên bản: {counts['deleted']:,} file xóa, {counts['skipped']:,} bỏ qua, {counts['tool']:,} công cụ")
    return result

