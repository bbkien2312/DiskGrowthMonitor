"""Opt-in, bounded read-only folder checks. Never scans a drive by default."""
import hashlib
import heapq
import os
from pathlib import Path
import time
from .cleanup import protected_file,project_folder
from .config import home,inside,browser_path
from .platform_ops import ProcessCancelled


def inspect_folder(root,mode='large',*,min_bytes=10_000_000,reference=(),limit=1000,max_files=100000,
                   hash_budget=256_000_000,max_seconds=60,stop=None):
    root=Path(root).absolute();app_root=home()
    if root==Path(root.anchor) or not root.is_dir():raise ValueError('Chọn một thư mục cụ thể, không chọn toàn ổ')
    if root.resolve()!=root or protected_file(root) or browser_path(root) or inside(root,app_root):raise ValueError('Thư mục được bảo vệ hoặc là reparse point')
    if os.name=='nt':
        windows=Path(os.environ.get('SystemRoot',r'C:\Windows'))
        if inside(root,windows) or root.name.casefold() in ('program files','program files (x86)','programdata','users'):
            raise ValueError('Chỉ kiểm tra thủ công một thư mục dữ liệu cụ thể, không chọn thư mục hệ thống/phạm vi quá rộng')
    if mode not in ('large','duplicates','empty') or not 1<=int(limit)<=1000 or min_bytes<0:raise ValueError('Tùy chọn kiểm tra không hợp lệ')
    started=time.monotonic();stack=[root];large=[];by_size={};empty=[];errors=[];files=0;sequence=0;complete=True
    def check():
        if stop is not None and stop.is_set():raise ProcessCancelled('Đã dừng kiểm tra thủ công; không xóa gì.')
        return time.monotonic()-started<=max_seconds
    while stack:
        if not check() or files>=max_files:complete=False;break
        folder=stack.pop()
        if project_folder(folder):continue
        try:
            if folder.resolve()!=folder or folder.is_symlink():complete=False;continue
            had_entries=False
            with os.scandir(folder) as entries:
                for item in entries:
                    had_entries=True
                    if not check() or files>=max_files:complete=False;break
                    path=Path(item.path)
                    if browser_path(path) or protected_file(path) or inside(path,app_root):continue
                    st=item.stat(follow_symlinks=False)
                    if item.is_symlink() or getattr(st,'st_file_attributes',0)&0x400:continue
                    if item.is_dir(follow_symlinks=False):stack.append(path);continue
                    if not item.is_file(follow_symlinks=False):continue
                    # Windows DirEntry.stat can report ino=0; Path.stat returns
                    # the identity subsequently verified against the open handle.
                    st=path.stat(follow_symlinks=False)
                    files+=1
                    if st.st_size<min_bytes:continue
                    row={'path':str(path),'size':st.st_size,'modified_ns':st.st_mtime_ns,'identity':(st.st_dev,st.st_ino),
                         'reference':any(inside(path,p) for p in reference),'note':'File lớn không đồng nghĩa file rác'}
                    if mode=='duplicates':by_size.setdefault(st.st_size,[]).append(row)
                    elif mode=='large':
                        sequence+=1;heapq.heappush(large,(st.st_size,sequence,row))
                        if len(large)>limit:heapq.heappop(large)
            if mode=='empty' and not had_entries and len(empty)<limit:
                empty.append({'path':str(folder),'size':0,'reference':any(inside(folder,p) for p in reference),'note':'Rỗng tại lúc đọc; ứng dụng có thể vẫn cần thư mục này'})
        except OSError as exc:
            complete=False
            if len(errors)<100:errors.append(str(exc))
    hashed=0;groups=[]
    if mode=='duplicates':
        seen=set()
        for size,rows in sorted(by_size.items(),reverse=True):
            if len(rows)<2:continue
            hashes={}
            for row in rows:
                if not check() or hashed+size>hash_budget:complete=False;continue
                path=Path(row['path']);identity=tuple(row['identity'])
                if identity in seen:continue  # hardlinks are one physical file, not reclaimable duplicates
                try:
                    if path.resolve()!=path:complete=False;continue
                    fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
                    with os.fdopen(fd,'rb') as stream:
                        before=os.fstat(stream.fileno())
                        if (before.st_dev,before.st_ino)!=identity or (before.st_size,before.st_mtime_ns)!=(size,row['modified_ns']):continue
                        digest=hashlib.sha256()
                        while chunk:=stream.read(1024*1024):
                            if not check() or hashed+len(chunk)>hash_budget:complete=False;break
                            hashed+=len(chunk);digest.update(chunk)
                        else:
                            after=os.fstat(stream.fileno())
                            if (after.st_size,after.st_mtime_ns)==(size,row['modified_ns']):
                                row['note']='Trùng SHA-256 + kích thước; vẫn cần quyết định giữ bản nào'
                                hashes.setdefault(digest.hexdigest(),[]).append(row);seen.add(identity)
                except OSError as exc:
                    complete=False
                    if len(errors)<100:errors.append(str(exc))
            for digest,members in hashes.items():
                if len(members)>1:groups.append({'sha256':digest,'size':size,'files':members})
            if sum(len(g['files']) for g in groups)>=limit:complete=False;break
        result=[{**row,'group':i+1} for i,g in enumerate(groups) for row in g['files']][:limit]
    else:result=empty if mode=='empty' else [item[2] for item in sorted(large,reverse=True)]
    return {'root':str(root),'mode':mode,'rows':result,'files_seen':files,'hashed_bytes':hashed,'complete':complete,
            'errors':errors,'read_only':True,'limit':limit,'note':'Không xóa gì. File trùng/lớn/rỗng không có nghĩa không cần; thư mục tham chiếu chỉ được đánh dấu bảo vệ. Giới hạn 60 giây / 100.000 file / 256 MB hash mặc định.'}
