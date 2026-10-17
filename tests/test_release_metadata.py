"""A release must not mix versions in its runtime, installer and lockfile."""
import importlib.metadata
import json
from pathlib import Path
import tomllib
from dgm import __version__


def test_release_versions_agree():
    root=Path(__file__).resolve().parents[1]
    project=tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))
    assert project['project']['version']==__version__==importlib.metadata.version('disk-growth-monitor')
    assert json.loads((root/'packaging/installed-app.json').read_text(encoding='utf-8'))['version']==__version__
    assert f'#define AppVersion "{__version__}"' in (root/'packaging/installer.iss').read_text(encoding='utf-8')
    assert (root/'packaging/INSTALL-NOTES.txt').read_text(encoding='utf-8').startswith(f'Disk Growth Monitor {__version__} - ')
    lock=tomllib.loads((root/'uv.lock').read_text(encoding='utf-8'))
    package=next(p for p in lock['package'] if p['name']=='disk-growth-monitor')
    assert package['version']==__version__
    assert (root/f'packaging/RELEASE-v{__version__}.md').is_file()
