import json
import pytest
from dgm.presentation import size,set_units,whole,change_text,display_json


@pytest.mark.parametrize('value,expected',[(None,'—'),(0,'0 B'),(999,'999 B'),(1000,'1 KB'),
    (1499,'1 KB'),(1500,'2 KB'),(-1500,'-2 KB'),(2_500_000,'3 MB'),(138_644_837_019,'139 GB'),
    (999_500,'1 MB'),(-999_500,'-1 MB')])
def test_sizes_are_integer_rounded_half_up(value,expected):
    set_units(False)
    assert size(value)==expected


def test_binary_units_delta_and_small_change_keep_significance():
    try:
        set_units(True);assert size(161290907648)=='150 GiB'
        assert size(1048064)=='1 MiB'
        set_units(False)
        assert change_text(-3684741)=='↓ −4 MB'  # Not rounded to 0 GB.
        assert change_text(1500)=='↑ +2 KB'
        assert change_text(0)=='→ 0 B'
        assert change_text(None)=='—'
    finally:set_units(False)


@pytest.mark.parametrize('value,expected',[(2.5,3),(1.5,2),(-2.5,-3),(.49,0),(.5,1)])
def test_standard_rounding_not_bankers(value,expected):assert whole(value)==expected


def test_diagnostic_display_is_a_copy_and_keeps_versions_dates_and_byte_counts():
    data={'seconds':2.75,'cpu_percent':12.5,'bytes':161290907648,'version':'1.2.0',
          'date':'2026-10-17T12:34:56.123+07:00','nested':[{'seconds':.5}]}
    original=json.dumps(data)
    shown=json.loads(display_json(data))
    assert shown['seconds']==3 and shown['cpu_percent']==13 and shown['nested'][0]['seconds']==1
    for k in ('version','date','bytes'):assert shown[k]==data[k]
    assert json.dumps(data)==original


def test_view_formatter_does_not_change_underlying_comparison_rows(isolated):
    from dgm.store import Store
    from dgm.comparison import Comparisons
    from test_v2 import snapshot,entry
    s=Store();root=isolated/'virtual'
    a=snapshot(s,root,[entry(root/'f',value=138_644_837_019)])
    b=snapshot(s,root,[entry(root/'f',value=138_641_152_278)])
    c=Comparisons();pair=c.prepare(a,b);row=c.page(pair,kind='file')['rows'][0]
    assert size(row['before'])==size(row['after'])=='139 GB'
    assert row['delta']==-3684741 and change_text(row['delta'])=='↓ −4 MB'
    c.close();s.close()
