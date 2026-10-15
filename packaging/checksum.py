"""Generate public release checksums, no private files."""
import hashlib
import sys
from pathlib import Path
root=Path(__file__).resolve().parent.parent
directory=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'dist'
installer=directory/'installer.exe'
with installer.open('rb') as file:checksum=hashlib.file_digest(file,'sha256').hexdigest()
(directory/'SHA256SUMS.txt').write_text(f'{checksum}  installer.exe\n',encoding='ascii')
print(f'{installer} ({installer.stat().st_size/1_000_000:.1f} MB) SHA256 {checksum}')
