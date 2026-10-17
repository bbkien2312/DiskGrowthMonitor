"""Windows allocation metadata, without reading file contents or following links."""
import ctypes
from ctypes import wintypes
from functools import lru_cache


class StandardInfo(ctypes.Structure):
    _fields_=[('allocation',ctypes.c_int64),('end',ctypes.c_int64),('links',wintypes.DWORD),
              ('pending',ctypes.c_byte),('directory',ctypes.c_byte)]


class CompressionInfo(ctypes.Structure):
    _fields_=[('allocation',ctypes.c_int64),('format',wintypes.WORD),('unit',ctypes.c_byte),
              ('chunk',ctypes.c_byte),('cluster',ctypes.c_byte),('reserved',ctypes.c_byte*3)]


@lru_cache(maxsize=1)
def api():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype=wintypes.HANDLE
    info=kernel.GetFileInformationByHandleEx
    info.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];info.restype=wintypes.BOOL
    close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
    return create,info,close


def allocation(path,attrs=0):
    if attrs & 0x400:return 0
    create,info,close=api()
    # Attributes only, share read/write/delete, OPEN_EXISTING, no reparse follow
    # or cloud recall. A failed metadata query stays unknown, not logical bytes.
    handle=create(str(path),0x80,7,None,3,0x00200000|0x02000000|0x00100000,None)
    if handle==ctypes.c_void_p(-1).value:return None
    try:
        record=CompressionInfo() if attrs & (0x800|0x200) else StandardInfo()
        kind=8 if attrs & (0x800|0x200) else 1
        if not info(handle,kind,ctypes.byref(record),ctypes.sizeof(record)):return None
        return max(0,record.allocation)
    finally:close(handle)
