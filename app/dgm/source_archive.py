"""Bounded source-export retention and streaming checksums, inside app data."""
import gzip
import hashlib
import time
import uuid
from .config import home,load


def archive_folder():
    folder=home()/'data/exports/archive';folder.mkdir(parents=True,exist_ok=True)
    if folder.resolve()!=folder.absolute():raise ValueError('Không lưu/dọn CSV qua junction hoặc symlink.')
    return folder


def retain(source, *, engine, root, options, stop):
    from .platform_ops import ProcessCancelled
    folder=archive_folder()
    target=folder/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex+'.csv.gz')
    digest=hashlib.sha256()
    try:
        with source.open('rb') as src,gzip.open(target,'xb',compresslevel=1) as dest:
            while chunk:=src.read(1024*1024):
                if stop.is_set():raise ProcessCancelled()
                digest.update(chunk);dest.write(chunk)
        result={'path':str(target.relative_to(home())),'sha256':digest.hexdigest(),'source_bytes':source.stat().st_size,
                'compressed_bytes':target.stat().st_size,'engine':engine,'root':root,'options':options,
                'schema':'wiztree-csv','timezone':'UTC','sizes':'bytes','captured_at':time.time()}
        result['tool']=options[0] if options else engine
        cfg=load();limit=max(0,cfg.get('source_archive_limit_mb',512))*1_000_000
        days=max(0,cfg.get('source_archive_retention_days',90));files=sorted(folder.glob('*.csv.gz'),key=lambda p:p.stat().st_mtime,reverse=True)
        total=0
        for path in files:
            stat=path.stat();total+=stat.st_size
            if total>limit or time.time()-stat.st_mtime>days*86400:
                path.unlink(missing_ok=True)
        result['retained']=target.exists()
        return result
    except BaseException:
        target.unlink(missing_ok=True);raise


def clear():
    """Only our generated gzip exports, never a user-supplied path."""
    folder=archive_folder();count=0;total=0
    if not folder.exists():return {'files':0,'bytes':0}
    for path in folder.glob('*.csv.gz'):
        if path.is_symlink() or path.resolve().parent!=folder:continue
        total+=path.stat().st_size;path.unlink();count+=1
    return {'files':count,'bytes':total,'note':'Đã xóa nguồn CSV nén; lịch sử/chuẩn hóa giữ nguyên. Khả năng kiểm chứng lại nguồn giảm.'}
