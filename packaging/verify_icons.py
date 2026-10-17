"""Read-only validation: each packaged PE contains the exact multi-size icon."""
from pathlib import Path
import struct
import sys
import win32api


def verify_icon(executable, master):
    data=Path(master).read_bytes()
    count=struct.unpack_from('<H',data,4)[0]
    expected=[]
    for index in range(count):
        length,offset=struct.unpack_from('<II',data,6+16*index+8)
        expected.append(data[offset:offset+length])
    handle=win32api.LoadLibraryEx(str(Path(executable).resolve()),0,2)
    try:
        for name in win32api.EnumResourceNames(handle,14):
            group=win32api.LoadResource(handle,14,name)
            if struct.unpack_from('<H',group,4)[0]!=count:
                continue
            actual=[win32api.LoadResource(handle,3,struct.unpack_from('<H',group,6+14*index+12)[0])
                    for index in range(count)]
            if actual==expected:
                return count
    finally:
        win32api.FreeLibrary(handle)
    raise AssertionError(f'{executable}: missing or mismatched application icon')


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    for executable in sys.argv[1:]:
        count=verify_icon(executable,root/'build/icons/app.ico')
        print(f'{Path(executable).name}: {count} exact icon resolutions verified')
