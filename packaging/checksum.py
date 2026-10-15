"""Generate public release checksums, no private files."""
import hashlib
from pathlib import Path
root=Path(__file__).resolve().parent.parent
installer=root/'dist/installer.exe'
with installer.open('rb') as file:checksum=hashlib.file_digest(file,'sha256').hexdigest()
(root/'dist/SHA256SUMS.txt').write_text(f'{checksum}  installer.exe\n',encoding='ascii')
print(f'{installer} ({installer.stat().st_size/1_000_000:.1f} MB) SHA256 {checksum}')
