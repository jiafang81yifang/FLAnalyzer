"""live.500.com 解析器：全部断言打在 2026-11-27 的真实抓包上。

夹具与出处见 `tests/fixtures/FIXTURE_SOURCES.json`：
    live500_board_20261127.html          129069B  https://live.500.com/                 39 场
    live500_zqdc_20261127.html           700459B  https://live.500.com/zqdc.php         308 行
    live500_detail_1450871_20261127.html  17447B  detail.php?fid=1450871&r=1  已开赛 2-0
    live500_detail_1503754_20261127.html  17917B  detail.php?fid=1503754&r=1  未开赛 0-0
    live500_detail_1430592_20261127.html  17362B  detail.php?fid=1430592&r=1  订单 5002

`odds.500.com` 在本机连不上（连接被拒），所以那五个分析页没有任何断言——
本模块只验证 live.500.com 真的给出的东西。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / 'tests' / 'fixtures'
board_mod = importlib.import_module('live500_board')
okooo = importlib.import_module('okooo_mobile_cs')


def _html(name: str, expected_bytes: int) -> str:
    raw = (FIXTURES / name).read_bytes()
    assert len(raw) == expected_bytes, f'{name} bytes drifted: {len(raw)}'
    return board_mod.decode_500_html(raw)


@pytest.fixture(scope='module')
def board() -> dict[str, Any]:
    return board_mod.parse_board(_html('live500_board_20261127.html', 129069))


@pytest.fixture(scope='module')
def zqdc() -> dict[str, Any]:
    return board_mod.parse_board(_html('live500_zqdc_20261127.html', 700459), source='live500_zqdc')


# --------------------------------------------------------------------------- #
# 解码
# --------------------------------------------------------------------------- #
def test_gb2312_page_decodes_through_the_shared_decoder() -> None:
    """500 声明 gb2312，字节流却含 gb2312 之外的字节。

    `gb2312` 严格解码在**盘面**（129069B）上直接 UnicodeDecodeError（0xbc 在
    offset 26588）；单场子页恰好整页都在 gb2312 内，所以那一页两种解都能过。
    结论：必须走 `decode_okooo_html`（把 gb2312/gbk 映射到 gb18030 超集），
    否则「换个页面就乱码」，而不是「一致地失败」。
    """
    board_raw = (FIXTURES / 'live500_board_20261127.html').read_bytes()
    with pytest.raises(UnicodeDecodeError):
        board_raw.decode('gb2312')
    text = board_mod.decode_500_html(board_raw)
    assert '麦克阿瑟FC' in text
    detail_raw = (FIXTURES / 'live500_detail_1503754_20261127.html').read_bytes()
    assert '帕尔梅拉斯VS拉普拉塔大学生' in board_mod.decode_500_html(detail_raw)


# --------------------------------------------------------------------------- #
# 盘面
# --------------------------------------------------------------------------- #
def test_board_has_39_matches_each_with_a_unique_order(board: dict[str, Any]) -> None:
    assert board['available'] is True
    assert board['match_count'] == 39
    assert len(board['matches']) == 39
    assert board['joinable'] == 39
    orders = [m['order'] for m in board['matches']]
    assert len(set(orders)) == 39, 'duplicate order ids would double rows in the matrix'
    assert orders[:4] == ['5001', '5002', '5003', '5004']
    assert orders[-1] == '7022'


def test_board_row_carries_both_handles_and_the_score(board: dict[str, Any]) -> None:
    first = board['matches'][0]
    assert first['order'] == '5001'
    assert first['fid'] == '1450871'
    assert first['local_no'] == '周五001'
    assert first['league'] == '澳超'
    assert first['round'] == '第6轮'
    assert first['kickoff'] == '11-27 16:40'
    assert first['home'] == '麦克阿瑟FC'
    assert first['away'] == '墨尔本城'
    assert first['home_rank'] == '05'
    assert first['away_rank'] == '07'
    assert (first['home_score'], first['away_score']) == (2, 0)
    assert first['half_time'] == '2 - 0'
    assert first['handicap_label'] == '平手'
    assert first['detail_url'] == 'https://live.500.com/detail.php?fid=1450871&r=1'
    assert first['lid'] == '312'
    assert first['gy'] == ['麦克阿瑟FC', '墨尔本城']
    # 两个站对客队的写法不同，两版都要留着
    assert first['yy'] == ['麦克阿瑟FC', '墨尔本中心']


def test_not_started_row_has_no_score_and_says_not_started(board: dict[str, Any]) -> None:
    """未开赛（status=0）的比分位是空的，这是正确状态，不是缺数据。"""
    row = board['order_to_fid']['5002']
    assert row == '1430592'
    match = next(m for m in board['matches'] if m['order'] == '5002')
    assert match['status'] == '0'
    assert match['started'] is False
    assert match['home_score'] is None and match['away_score'] is None
    assert match['handicap_label'] == '平手/半球'


def test_order_5002_is_the_users_match(board: dict[str, Any]) -> None:
    """用户报的 1338958（order 5002）在 500 上是 fid=1430592。"""
    assert board['order_to_fid']['5002'] == '1430592'
    assert board['fid_to_order']['1430592'] == '5002'


def test_zqdc_rows_have_no_order_attribute(zqdc: dict[str, Any]) -> None:
    """`zqdc.php` 的行只有序号，没有 `order`——这一页无法按竞彩编号 join。

    如实报 `order_missing=308 / joinable=0`，不拿序号冒充 order。
    """
    assert zqdc['match_count'] == 308
    assert len(zqdc['matches']) == 308
    assert zqdc['joinable'] == 0
    assert zqdc['order_missing'] == 308
    assert all(m['order'] == '' for m in zqdc['matches'])
    assert zqdc['order_to_fid'] == {}


def test_zqdc_carries_the_three_reference_sp_values(board: dict[str, Any], zqdc: dict[str, Any]) -> None:
    """`zqdc.php` 有参考SP三项，`live.500.com/` 那一列是空的。"""
    z_first = zqdc['matches'][0]
    assert z_first['fid'] == '1450871'
    assert z_first['sp_bonus'] == [3.05, 3.98, 2.37]
    b_first = board['matches'][0]
    assert b_first['sp_bonus'] == []


# --------------------------------------------------------------------------- #
# 单场子页
# --------------------------------------------------------------------------- #
def test_detail_reads_score_both_lineups_and_all_stats() -> None:
    parsed = board_mod.parse_detail(_html('live500_detail_1450871_20261127.html', 17447), fid='1450871')
    assert parsed['available'] is True
    assert parsed['stage'] == 'ok'
    assert parsed['head'] == '2026-11-27 16:40 2026-2027 澳超第6轮'
    assert parsed['score'] == '2 - 0'
    assert (parsed['home'], parsed['away']) == ('麦克阿瑟FC', '墨尔本城')
    lineups = parsed['lineups']
    assert lineups['starters_count'] == {'home': 11, 'away': 11}
    assert lineups['starters_complete'] is True
    # 两侧同名标题按出现顺序分侧：主队先
    assert lineups['starters']['home'][0].endswith('本·吉布森(前锋)')
    assert lineups['starters']['away'][0].endswith('安德鲁·纳布特(前锋)')
    assert len(lineups['subs']['home']) == 7
    assert len(parsed['stats']) == 12
    assert parsed['stat_order'] == [
        '射门', '射正', '犯规', '角球', '越位', '红牌', '黄牌', '进攻', '威胁进攻', '任意球', '救球', '控球',
    ]
    assert parsed['stats']['控球'] == {
        'home': 49.0, 'away': 51.0, 'home_text': '49%', 'away_text': '51%',
    }
    assert parsed['stats']['射门'] == {'home': 7.0, 'away': 7.0, 'home_text': '7', 'away_text': '7'}


def test_not_started_detail_does_not_fake_zero_score() -> None:
    """未开赛页面的比分是 `0 - 0` 的占位，技术统计全为 0。

    这里不断言「0 不算比分」——那是页面的真实读数，矩阵照实显示；
    但空的中场事件列必须不被当成解析失败。
    """
    parsed = board_mod.parse_detail(_html('live500_detail_1503754_20261127.html', 17917), fid='1503754')
    assert parsed['available'] is True
    assert parsed['score'] == '0 - 0'
    assert (parsed['home'], parsed['away']) == ('帕尔梅拉斯', '拉普拉塔大学生')
    assert parsed['lineups']['starters_complete'] is True
    assert len(parsed['stats']) == 12


def test_detail_without_blocks_reports_stage() -> None:
    parsed = board_mod.parse_detail('<html><body>nothing</body></html>', fid='1')
    assert parsed['available'] is False
    assert parsed['stage'] == 'no_detail_blocks'


def test_detail_requires_a_fid(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> dict[str, Any]:
        raise AssertionError('must not fetch without a fid')

    monkeypatch.setattr(board_mod, '_fetch_detail', boom)
    out = board_mod.scrape_500_detail('')
    assert out['available'] is False
    assert out['stage'] == 'no_fid'


# --------------------------------------------------------------------------- #
# 编号规范化
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('raw,expected', [
    (5002, '5002'), ('5002', '5002'), (' 05002 ', '5002'), ('5002.0', None),
    ('', ''), (None, ''), ('-', ''), ('abc', ''), ('0', '0'),
])
def test_normalize_order_collapses_every_spelling(raw: Any, expected: Any) -> None:
    """同一场不能因为 `5002` / `'5002'` / 补零写法产出两行。

    `'5002.0'` 不是合法编号（int() 会抛），归一化必须返回空串而不是 5002
    ——猜一个值会把两场不同的比赛并成一行。
    """
    got = board_mod.normalize_order(raw)
    if expected is None:
        assert got == '', f'{raw!r} must not be normalized into a key'
    else:
        assert got == expected


def test_duplicate_order_rows_are_dropped_and_counted() -> None:
    """同一 order 在盘面上重复渲染时，只留第一次，重复的进 duplicates。"""
    row = (
        '<tr id="a111" order="5009" status="0" gy="L,H,A" yy="L,H,A" lid="1" fid="111" sid="1">'
        '<td>x</td><td>L</td><td>R</td><td>t</td><td>未</td><td>H</td>'
        '<td><div class="pk"><a class="clt1"></a><a class="fgreen" data-ao="平手" data-pb="平手">平手</a>'
        '<a class="clt3"></a></div></td><td>A</td><td>-</td><td></td></tr>'
    )
    html = f'<table><tbody>{row}{row.replace("a111", "a222").replace(chr(34) + "111" + chr(34), chr(34) + "222" + chr(34))}</tbody></table>'
    parsed = board_mod.parse_board(html)
    assert len(parsed['matches']) == 1
    assert parsed['matches'][0]['fid'] == '111'
    assert parsed['duplicates'] == [{'order': '5009', 'fid': '222'}]


def test_source_reachability_table_names_the_dead_pages() -> None:
    """可达性表是报告的依据，不能靠注释口头描述。"""
    by_url = {row['url']: row for row in board_mod.SOURCE_REACHABILITY}
    assert by_url['https://live.500.com/']['ok'] is True
    assert by_url['https://live.500.com/zqdc.php']['ok'] is True
    assert by_url['https://live.500.com/jczq.php']['ok'] is False
    assert by_url['https://live.500.com/jczq.php']['status'] == 301
    tabs = {row['tab']: row for row in board_mod.ODDS_TABS_UNAVAILABLE}
    assert set(tabs) == {'亚盘', '欧指', '让球', '大小球', '盈亏'}
    assert all(row['reason'] == 'host_blocked_here' for row in tabs.values())
    assert '{fid}' in tabs['亚盘']['url']
