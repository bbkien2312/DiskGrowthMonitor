import pytest
from dgm import config


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    base = tmp_path / "app-home"
    monkeypatch.setattr(config, "ROOT", base)
    cfg=config.defaults();cfg['history_format']='sqlite';cfg['source_archive_limit_mb']=512
    cfg['scan_backend']='legacy'  # Existing fixtures exercise the retained legacy compatibility API.
    config.save(cfg)
    return tmp_path

