"""Checkpoint regression fixtures: no live volume scans or real history writes."""
import csv
import threading
from subprocess import CompletedProcess

import pytest

from dgm import columnar_jobs as module
from dgm.columnar_checkpoints import MIB, ExportLimitError
from dgm.columnar_gui_backend import Sessions
from dgm.columnar_store import Repository, LowSpaceError, sha256
from dgm.platform_ops import ProcessCancelled


@pytest.fixture
def captures(tmp_path, monkeypatch, isolated):
    jobs = module.Jobs(Repository(tmp_path/'repo', limits={'source_mb': 1, 'checkpoint_mb': 1}))
    exe = tmp_path/'fake-wiztree.exe'
    exe.touch()
    calls = []

    def create(**options):
        roots = options.pop('roots', ['C:\\data'])
        return jobs.create('scan', {'name':'daily',
            'profile': {'roots':roots, 'engine':'wiztree', 'mft':False},
            'engines': {'wiztree':str(exe)}, **options})

    def export(cmd, **kwargs):
        calls.append(cmd[1])
        output = next(x.split('=', 1)[1] for x in cmd if x.startswith('/export='))
        with open(output, 'w', encoding='utf-8', newline='') as stream:
            out = csv.writer(stream)
            out.writerow(['File Name','Size','Allocated','Attributes','MFTRecNo'])
            out.writerow([cmd[1]+'\\', 2000, 4096, 16, 1])
            for i in range(7000):
                out.writerow([cmd[1]+f'\\abc-{i:05d}', 1, 4096, 32, i+2])
        return CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(module, 'run', export)
    return jobs, create, calls, export


def cancel(*args, **kwargs):
    raise ProcessCancelled('Canceled after capture')


def test_two_legacy_exports_do_not_consume_the_new_job_budget(captures):
    jobs, create, calls, _ = captures
    old = []
    for _ in range(2):
        job = create()
        path = jobs.directory(job['id'])/'export-0.csv'
        path.write_bytes(b'a' * (3*MIB//4))
        proof = {'file':path.name, 'bytes':path.stat().st_size, 'sha256':sha256(path)}
        job.pop('temp_policy')
        job.update(state='failed', sources=[proof])
        jobs._save(job)
        old.append((path, proof['sha256']))
    assert jobs.source_bytes() > MIB
    result = jobs.execute(create()['id'])[0]
    assert result['summary']['files'] == 7000 and len(calls) == 1
    assert all(sha256(path)==digest for path,digest in old)
    assert jobs.source_bytes() == 3*MIB//2


def test_repeated_failures_keep_one_gzip_and_resume_without_export(captures, monkeypatch):
    jobs, create, calls, _ = captures
    real = module.import_wiztree
    monkeypatch.setattr(module, 'import_wiztree', cancel)
    original = create()
    with pytest.raises(ProcessCancelled):
        jobs.execute(original['id'])
    kept = jobs.get(original['id'])
    proof, = kept['sources']
    assert proof['file'].endswith('.csv.gz') and proof['bytes'] < proof['raw_bytes']
    stamp = kept['metadata']['started']
    for _ in range(3):
        failed = create()
        with pytest.raises(ProcessCancelled):
            jobs.execute(failed['id'])
        assert not jobs.get(failed['id'])['sources']
        assert 'Giữ điểm tiếp tục cũ' in jobs.get(failed['id'])['checkpoint_note']
        assert jobs.source_bytes() == proof['bytes']
    assert len(jobs.checkpoints()) == 1
    monkeypatch.setattr(module, 'import_wiztree', real)
    def no_export(*args, **kwargs):
        pytest.fail('Resume should stream from verified gzip')
    monkeypatch.setattr(module, 'run', no_export)
    result = jobs.execute(original['id'])[0]
    assert result['summary']['files'] == 7000 and result['started'] == stamp
    assert jobs.source_bytes() == 0 and len(calls) == 4


def test_explicit_replacement_is_conditional_on_verified_new_archive(captures, monkeypatch):
    jobs, create, _, _ = captures
    monkeypatch.setattr(module, 'import_wiztree', cancel)
    original = create()
    with pytest.raises(ProcessCancelled):
        jobs.execute(original['id'])
    real_compress = module.compress_export
    def corrupt(*args, **kwargs):
        raise OSError('simulated disk error')
    monkeypatch.setattr(module, 'compress_export', corrupt)
    refused = create(replace_checkpoint=original['id'])
    with pytest.raises(ProcessCancelled):
        jobs.execute(refused['id'])
    assert [j['id'] for j in jobs.checkpoints()] == [original['id']]
    monkeypatch.setattr(module, 'compress_export', real_compress)
    allowed = create(replace_checkpoint=original['id'])
    with pytest.raises(ProcessCancelled):
        jobs.execute(allowed['id'])
    assert [j['id'] for j in jobs.checkpoints()] == [allowed['id']]
    assert not jobs.get(original['id'])['sources']
    assert not list(jobs.directory(original['id']).glob('export-*'))


def test_incomplete_multi_root_export_is_not_resumed_as_a_mixed_capture(captures, monkeypatch):
    jobs, create, calls, export = captures
    def interrupted(cmd, **kwargs):
        if cmd[1].endswith('other'):
            raise ProcessCancelled('second root incomplete')
        return export(cmd, **kwargs)
    monkeypatch.setattr(module, 'run', interrupted)
    job = create(roots=['C:\\data','C:\\other'])
    with pytest.raises(ProcessCancelled):
        jobs.execute(job['id'])
    assert not jobs.get(job['id'])['sources'] and jobs.source_bytes() == 0
    monkeypatch.setattr(module, 'run', export)
    jobs.execute(job['id'])
    assert calls == ['C:\\data','C:\\data','C:\\other']


def test_preflight_checks_disk_before_launch_and_limits_only_active_export(captures, monkeypatch):
    jobs, create, calls, _ = captures
    real = jobs.repo.check_space
    def limited(extra=0):
        if extra:
            raise LowSpaceError('simulated low space')
        return real()
    monkeypatch.setattr(jobs.repo, 'check_space', limited)
    with pytest.raises(LowSpaceError):
        jobs.execute(create()['id'])
    assert not calls
    monkeypatch.setattr(jobs.repo, 'check_space', real)
    def huge(cmd, **kwargs):
        from pathlib import Path
        dest = Path(next(arg.split('=',1)[1] for arg in cmd if arg.startswith('/export=')))
        dest.write_bytes(b'a'*(MIB+1))
        return CompletedProcess(cmd, 0, '', '')
    monkeypatch.setattr(module, 'run', huge)
    with pytest.raises(ExportLimitError, match='hạn mức 1 MiB'):
        jobs.execute(create()['id'])
    assert jobs.source_bytes() == 0


def test_compression_failure_keeps_at_most_one_verified_raw_fallback(captures, monkeypatch):
    jobs, create, _, _ = captures
    monkeypatch.setattr(module, 'import_wiztree', cancel)
    def no_room(*args, **kwargs):
        raise ExportLimitError('compressed cap')
    monkeypatch.setattr(module, 'compress_export', no_room)
    first = create()
    with pytest.raises(ProcessCancelled):
        jobs.execute(first['id'])
    saved = jobs.source_bytes()
    assert saved > 0 and jobs.get(first['id'])['sources'][0]['file'].endswith('.csv')
    with pytest.raises(ProcessCancelled):
        jobs.execute(create()['id'])
    assert jobs.source_bytes() == saved and len(jobs.checkpoints()) == 1


def test_space_drops_after_completed_export_but_source_can_resume(captures, monkeypatch):
    jobs, create, calls, _ = captures
    real = jobs.repo.check_space
    def filled(extra=0):
        if calls:
            raise LowSpaceError('space fell after export')
        return real(extra)
    monkeypatch.setattr(jobs.repo, 'check_space', filled)
    job = create()
    with pytest.raises(LowSpaceError, match='fell after export'):
        jobs.execute(job['id'])
    assert len(jobs.get(job['id'])['sources']) == 1
    monkeypatch.setattr(jobs.repo, 'check_space', real)
    assert jobs.execute(job['id'])[0]['summary']['files'] == 7000
    assert len(calls) == 1 and jobs.source_bytes() == 0


def test_recover_incomplete_output_from_dead_worker(captures):
    jobs, create, _, _ = captures
    dead = create()
    dead.update(state='running', owner={})
    jobs._save(dead)
    partial = jobs.directory(dead['id'])/'export-0.csv'
    partial.write_bytes(b'partial')
    jobs.execute(create()['id'])
    assert jobs.get(dead['id'])['state'] == 'stopped'
    assert not partial.exists()


def test_stale_page_or_tree_does_not_raise_keyerror(tmp_path):
    sessions = Sessions(Repository(tmp_path/'repo'))
    handle = sessions.handle(threading.Event())
    for read in (lambda: handle.page((19,20)), lambda:handle.roots((19,20), [])):
        with pytest.raises(ProcessCancelled, match='Phiên so sánh'):
            read()
