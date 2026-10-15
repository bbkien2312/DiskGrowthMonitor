"""Only writes a fresh, explicitly named directory of synthetic benchmark data."""
from pathlib import Path

root = Path(r"D:\DiskGrowthMonitor-benchmark-owned")
if root.exists():
    raise SystemExit("Fixture exists; refuse to overwrite it")
root.mkdir()
(root / "DGM_SYNTHETIC_FIXTURE.txt").write_text("Generated only for DiskGrowthMonitor tests; contains no user data.", encoding="utf-8")
for folder_index in range(40):
    folder = root / f"folder-{folder_index:02d}"
    folder.mkdir()
    for index in range(50):
        (folder / f"file-{index:03d}.txt").write_bytes(b"x" * (1024 + index))
for index in range(3):
    (root / f"large-{index}.dat").write_bytes(b"x" * 11_000_000)
print(root)

