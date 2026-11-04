import pytest
from dgm import config


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    base = tmp_path / "app-home"
    monkeypatch.setattr(config, "ROOT", base)
    cfg=config.defaults();cfg['history_format']='sqlite';cfg['source_archive_limit_mb']=512
    # Synthetic fixtures occupy only a few MB. Keep the production 1024 MB
    # default unchanged; low-space safety tests explicitly inject tiny free space.
    cfg['storage_reserve_mb']=128
    cfg['scan_backend']='legacy'  # Existing fixtures exercise the retained legacy compatibility API.
    config.save(cfg)
    return tmp_path

