import threading
import pytest

from dgm.columnar_store import Repository
from dgm.columnar_ingest import import_entries
from dgm.columnar_compare import Comparison
from test_columnar_core import entry, meta


def captured(*, when, trusted=True, engine='wiztree', **extra):
    result = meta(engine=engine, started=when, **extra)
    result['coverage'] = {'source': 'filesystem' if trusted else 'export',
                          'unknown_roots': [] if trusted else ['C:\\data'], 'error_regions': []}
    return result


def pair(repo, old, new, *, a=None, b=None):
    a = a or captured(when='2026-01-01T00:00:00+00:00')
    b = b or captured(when='2026-01-02T00:00:00+00:00')
    old_scan = import_entries(old, a, repository=repo)
    new_scan = import_entries(new, b, repository=repo)
    return Comparison(new_scan['id'], old_scan['id'], repository=repo)


def test_fast_full_join_presence_and_totals(tmp_path):
    repo = Repository(tmp_path / 'repo')
    with pair(repo, [entry('C:\\data', kind='folder', size=0, allocated=0), entry('C:\\data\\a', 10), entry('C:\\data\\gone', 30)],
              [entry('C:\\data', kind='folder', size=0, allocated=0), entry('C:\\data\\a', 20), entry('C:\\data\\added', 40)]) as compare:
        rows = {r['path'].rstrip('\\').split('\\')[-1]: r for r in compare.page(minimum=0, limit=100, measure='logical')['rows']}
        assert rows['a']['presence'] == 'both' and rows['a']['delta'] == 10
        assert rows['added']['change'] == 'new' and rows['added']['delta'] == 40
        assert rows['gone']['change'] == 'missing' and rows['gone']['delta'] == -30
        assert rows['data']['delta'] == 20
        assert compare.summary()['common_old']['logical'] == 10
        assert compare.summary()['common_new']['logical'] == 20
        assert compare.summary()['private_old']['logical'] == 30
        assert compare.summary()['private_new']['logical'] == 40
        assert compare.summary()['verified_common_delta'] == 10
        assert compare.summary()['verified_private_delta'] == 10
        assert compare.summary()['partition_valid']
        assert len(compare.tree()['rows']) == 1
    assert not list(repo.root.rglob('*.sqlite*'))


def test_fast_global_sort_then_load_20_plus_100_and_scoped_sort(tmp_path):
    repo = Repository(tmp_path / 'repo')
    folders = [entry('C:\\data', kind='folder', size=0, allocated=0), entry('C:\\data\\sub', kind='folder', size=0, allocated=0)]
    old = folders + [entry(f'C:\\data\\sub\\f{i:03d}', 1) for i in range(150)]
    new = folders + [entry(f'C:\\data\\sub\\f{i:03d}', i + 1) for i in range(150)] + [entry('C:\\data\\outside', 9999)]
    with pair(repo, old, new) as compare:
        first = compare.page(kind='file', sort='new', limit=20, measure='logical')
        assert first['rows'][0]['after'] == 9999
        next_page = compare.page(kind='file', sort='new', limit=100, cursor=first['cursor'], measure='logical')
        assert next_page['loaded'] == 120
        assert len({r['item_id'] for r in first['rows'] + next_page['rows']}) == 120
        scoped = compare.page(root='C:\\data\\sub', kind='file', sort='new', limit=20, measure='logical')
        assert scoped['total'] == 150 and scoped['rows'][0]['after'] == 150
        with pytest.raises(ValueError, match='Con trỏ'):
            compare.page(kind='file', sort='new', direction='asc', cursor=first['cursor'])
        children = compare.tree(compare.scope_new.keys.key('C:\\data'))
        assert [r['path'] for r in children['rows']] == ['C:\\data\\sub']


def test_fast_unknown_coverage_is_not_deleted_and_does_not_pollute_delta(tmp_path):
    repo = Repository(tmp_path / 'repo')
    folders = [entry('C:\\data', kind='folder', size=0, allocated=0)]
    with pair(repo, folders + [entry('C:\\data\\gone', 100), entry('C:\\data\\a', 10)],
              folders + [entry('C:\\data\\a', 30)], b=captured(when='2026-01-02T00:00:00+00:00', trusted=False)) as compare:
        rows = {r['path']: r for r in compare.page(limit=100, measure='logical')['rows']}
        assert rows['C:\\data\\gone']['change'] == 'unknown'
        assert rows['C:\\data\\gone']['new_issue'] == 'coverage'
        assert rows['C:\\data\\gone']['delta'] is None
        assert rows['C:\\data']['delta'] == 20
        assert rows['C:\\data']['unknown_files'] == 1


def test_fast_folder_replacement_and_null_allocation(tmp_path):
    repo = Repository(tmp_path / 'repo')
    root = entry('C:\\data', kind='folder', size=0, allocated=0)
    with pair(repo, [root, entry('C:\\data\\x', 50, None)],
              [root, entry('C:\\data\\x', kind='folder', size=0, allocated=0), entry('C:\\data\\x\\child', 20)]) as compare:
        rows = {r['path']: r for r in compare.page(limit=100, measure='logical')['rows']}
        assert rows['C:\\data\\x']['change'] == 'replaced'
        assert rows['C:\\data\\x']['delta'] == -30
        assert rows['C:\\data']['delta'] == -30
        assert rows['C:\\data']['unknown_disk_files'] >= 1


def test_fast_empty_folders_and_baseline(tmp_path):
    repo = Repository(tmp_path / 'repo')
    scan = import_entries([entry('C:\\data', kind='folder', size=0, allocated=0)], captured(when='2026-01-01T00:00:00+00:00'), repository=repo)
    with Comparison(scan['id'], repository=repo) as compare:
        rows = compare.page()['rows']
        assert len(rows) == 1
        assert rows[0]['change'] == 'first_scan' and rows[0]['delta'] is None
        assert compare.summary()['common_old'] is None


def test_fast_ambiguous_case_keys_not_many_to_many_join(tmp_path):
    repo = Repository(tmp_path / 'repo')
    with pair(repo, [entry('C:\\data\\A', 10)], [entry('C:\\data\\A', 20), entry('C:\\data\\a', 30)]) as compare:
        rows = compare.page()['rows']
        assert len(rows) == 3
        assert all(row['presence'] != 'both' and row['delta'] is None and row['change'] == 'unknown' for row in rows)


def test_fast_captured_exclusions_not_false_missing(tmp_path):
    repo = Repository(tmp_path / 'repo')
    new_meta = captured(when='2026-01-02T00:00:00+00:00')
    new_meta['scope']['excludes'] = ['C:\\data\\ignored']
    with pair(repo, [entry('C:\\data\\ignored\\a', 20)], [], b=new_meta) as compare:
        row = compare.page()['rows'][0]
        assert row['change'] == 'not_scanned' and row['new_issue'] == 'scope'
        assert row['delta'] is None


def test_fast_refuses_reversed_chronology_and_releases_session(tmp_path):
    repo = Repository(tmp_path / 'repo')
    a = import_entries([], captured(when='2026-01-01T00:00:00+00:00'), repository=repo)
    b = import_entries([], captured(when='2026-01-02T00:00:00+00:00'), repository=repo)
    with pytest.raises(ValueError, match='Mốc cũ'):
        Comparison(a['id'], b['id'], repository=repo)
    assert not list(repo.work.iterdir())
