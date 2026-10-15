from pathlib import Path
import pytest
from dgm.platform_ops import dangerous_path, manual_action


def test_protect_user_home_and_system():
    assert dangerous_path(Path.home())
    assert dangerous_path(r"C:\Windows\System32\kernel32.dll")
    assert dangerous_path(r"C:\Program Files\SomeApp\app.exe")
    assert dangerous_path(r"C:\Users\ExampleUser\AppData\Local\Google\Chrome\User Data")


def test_rename_does_not_overwrite_existing(isolated):
    folder = isolated / "documents"; folder.mkdir()
    (folder / "a.txt").write_text("a")
    (folder / "b.txt").write_text("b")
    with pytest.raises(ValueError):
        manual_action(folder / "a.txt", "rename", "b.txt")
    assert (folder / "b.txt").read_text() == "b"
