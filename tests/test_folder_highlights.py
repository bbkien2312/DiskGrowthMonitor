from dgm.columnar_compare import Comparison
from dgm.columnar_ingest import import_entries, metadata_for
from dgm.columnar_store import Repository
from dgm.disk_measurements import display_delta, display_size
from test_columnar_core import entry


def test_gui_format_uses_one_quantity_and_preserves_unit_scale():
    assert display_delta(-7_176_081_408) == '↓ −6,68 GB'
    assert display_delta(-7_176_081_408, 'decimal') == '↓ −7,18 GB'
    assert display_delta(0) == '= 0 B'
    assert display_delta(None) == '?'
    assert display_size(157_088_804_864, precise=True) == '146,3 GB'
    assert display_size(2**20) == '1 MB'
    assert display_size(2**30 - 1, precise=True) == '1 GB'


def test_highlights_rank_full_branches_and_do_not_assert_unproven_deletion(tmp_path):
    repo = Repository(tmp_path / 'repo')
    root = 'C:\\data'
    excluded = root + '\\missing-12'

    def scan(new):
        records = [entry(root, 0, 0, kind='folder')]
        for number in range(1, 13):
            shapes = [('grown', number if new else 0), ('shrunk', 0 if new else number),
                      ('new' if new else 'missing', number)]
            for group, amount in shapes:
                folder = f'{root}\\{group}-{number:02}'
                records.extend((entry(folder, 0, 0, kind='folder'),
                                entry(folder + '\\payload', amount * 10, amount * 20)))
        # Large individual file and zero-change folder must not rank.
        records.extend((entry(root + '\\large', 100_000 if new else 0, 100_000 if new else 0),
                        entry(root + '\\same', 0, 0, kind='folder')))
        meta = metadata_for([root], volume_map={'c:':'volume-A'},
            started=f'2026-11-{11 if new else 10}T08:00:00+07:00',
            scope={'roots':[root], 'excludes':[excluded] if new else [], 'implicit_excludes':[]},
            coverage={'source':'export','unknown_roots':[],'error_regions':[]})
        return import_entries(records, meta, repository=repo)

    old, new = scan(False), scan(True)
    with Comparison(new['id'], old['id'], repository=repo) as comparison:
        # A limited detail query isn't a source for the ranking.
        assert comparison.page(limit=1, root=root + '\\grown-01')['total'] == 1
        groups = comparison.folder_highlights()
        assert all(len(rows) == 10 for rows in groups.values())
        for name, rows in groups.items():
            assert [r['path'] for r in rows] == [f'{root}\\{name}-{n:02}' for n in range(12, 2, -1)]
            assert [r['delta'] for r in rows] == [(-1 if name in ('shrunk','missing') else 1) * n * 20
                                                 for n in range(12, 2, -1)]
        assert groups['new'][0]['change'] == 'new'
        assert groups['missing'][0]['change'] == 'not_scanned'
        assert groups['missing'][0]['reason'] == 'scope'
        assert groups['missing'][1]['change'] == 'missing'
        assert comparison.folder_highlights(measure='logical', limit=1)['grown'][0]['delta'] == 120
        assert comparison.tree(root=root, limit=100)['total'] == 49
    with Comparison(new['id'], None, repository=repo) as baseline:
        assert all(not rows for rows in baseline.folder_highlights().values())
