import pytest
from dgm import config


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    base = tmp_path / "app-home"
    monkeypatch.setattr(config, "ROOT", base)
    config.save(config.defaults())
    return tmp_path

