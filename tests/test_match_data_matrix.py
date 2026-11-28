"""合并矩阵：join key、逐字段优先级、provenance、缺格原因码。

夹具全部是真实抓包（见 `tests/fixtures/FIXTURE_SOURCES.json`），
而且刻意让**两侧都能拿到数**（order 5002 同时有奥客 MatchID 1338958 与 500 的
fid 1430592），否则「优先级规则」这条断言在缺源的情况下会假通过。
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
mdm = importlib.import_module('match_data_matrix')
board_mod = importlib.import_module('live500_board')
tabs_mod = importlib.import_module('okooo_analysis_tabs')
okooo = importlib.import_module('okooo_mobile_cs')


def _html(name: str, size: int) -> str:
    raw = (FIXTURES / name).read_bytes()
    assert len(raw) == size, f'{name} drifted: {len(raw)}'
    return okooo.decode_okooo_html(raw)


@pytest.fixture(scope='module')
def board() -> dict[str, Any]:
    parsed = board_mod.parse_board(_html('live500_board_20261127.html', 129069))
    parsed['report'] = {'url': 'https://live.500.com/', 'status': 200, 'bytes': 129069}
    return parsed


@pytest.fixture(scope='module')
def details() -> dict[str, Any]:
    out = {}
    for fid, size in (('1450871', 17447), ('1430592', 17362)):
        parsed = board_mod.parse_detail(_html(f'live500_detail_{fid}_20261127.html', size), fid=fid)
        parsed['report'] = {'url': f'https://live.500.com/detail.php?fid={fid}&r=1',
                            'status': 200, 'bytes': size}
        out[fid] = parsed
    return out


@pytest.fixture(scope='module')
def okooo_tabs() -> dict[str, Any]:
    return tabs_mod.scrape_okooo_analysis_tabs('1338958', html_by_tab={
        'ah': _html('okooo_mobile_ah.html', 95392),
        'ou': _html('okooo_mobile_ou.html', 13148),
        'eu': _html('okooo_mobile_1x2.html', 90907),
        'rq': _html('okooo_mobile_1x2.html', 90907),
        'pnl': _html('okooo_mobile_exchanges.html', 15324),
    })


@pytest.fixture()
def merged(board: dict[str, Any], details: dict[str, Any], okooo_tabs: dict[str, Any]) -> dict[str, Any]:
    matrix = mdm.build_matrix(
        five_hundred_board=board,
        five_hundred_details=details,
        okooo_by_order={'5002': {'match_id': '1338958', 'tabs': okooo_tabs}},
    )
    return next(r for r in matrix['rows'] if r['order'] == '5002')


# --------------------------------------------------------------------------- #
# join 与句柄
# --------------------------------------------------------------------------- #
def test_row_joins_both_sources_on_the_order_key(merged: dict[str, Any]) -> None:
    """`order` 是唯一主键；三个句柄各留一列，绝不合并。"""
    assert merged['order'] == '5002'
    assert merged['fid'] == '1430592'      # 500 的场次号
    assert merged['okooo_match_id'] == '1338958'  # 澳客 MatchID
    assert len({merged['order'], merged['fid'], merged['okooo_match_id']}) == 3
    assert merged['present_sources'] == ['live500', 'okooo_mobile']
    assert merged['missing_sources'] == []


def test_matrix_keeps_match_absent_from_one_source(board: dict[str, Any], okooo_tabs: dict[str, Any]) -> None:
    """一侧缺场是常态：行照出，缺的那一源被点名，不补造。"""
    matrix = mdm.build_matrix(
        five_hundred_board=board,
        okooo_by_order={'5002': {'match_id': '1338958', 'tabs': okooo_tabs}},
    )
    row_5001 = next(r for r in matrix['rows'] if r['order'] == '5001')
    assert row_5001['present_sources'] == ['live500']
    assert row_5001['missing_sources'] == ['okooo_mobile']
    assert row_5001['fields']['asian_handicap']['value'] is None
    assert row_5001['fields']['asian_handicap']['reason'] == 'no_such_match_in_source'


def test_okooo_only_match_still_gets_a_row() -> None:
    matrix = mdm.build_matrix(okooo_by_order={'5011': {'match_id': '1350996'}})
    assert matrix['count'] == 1
    row = matrix['rows'][0]
    assert row['order'] == '5011'
    assert row['present_sources'] == ['okooo_mobile']
    assert row['missing_sources'] == ['live500']


# --------------------------------------------------------------------------- #
# 优先级
# --------------------------------------------------------------------------- #
def test_odds_fields_come_from_okooo_and_never_from_500(merged: dict[str, Any]) -> None:
    """盘口只认澳客手机页：500 没有赔率页（odds.500.com 不可达）。"""
    for field in ('asian_handicap', 'ou_line', 'odds_1x2', 'pnl_saved'):
        cell = merged['fields'][field]
        assert cell['reason'] == 'ok', (field, cell)
        assert cell['source'] == 'okooo_mobile', (field, cell['source'])
        assert 'm.okooo.com' in cell['url'], (field, cell['url'])
        assert cell['status'] == 0 or cell['status'] == 200
    assert merged['fields']['asian_handicap']['value'] == -1.0


def test_score_fields_come_from_500_and_never_from_okooo(merged: dict[str, Any]) -> None:
    """比分/半场/让球标签是 500 原生，澳客手机分析页根本没有这些字段。"""
    for field in ('home_score', 'away_score', 'half_time', 'handicap_label'):
        cell = merged['fields'][field]
        assert cell['source'] in ('', 'live500'), (field, cell['source'])
        assert cell['url'].startswith('https://live.500.com/'), (field, cell['url'])
    assert merged['fields']['handicap_label']['value'] == '平手/半球'


def test_odds_precedence_wins_when_both_sources_offer_the_field() -> None:
    """两源**都有**这个字段时，盘口仍然只取澳客。

    真实数据里 500 的盘口主机不可达，所以「澳客优先」在缺源情况下会自然成立——
    那样断言测不到规则本身。这里给 500 侧（`odds.500.com` 解析结果）一份
    同名同义的值，规则必须仍然选澳客并保持澳客的出处。
    """
    row = mdm.build_match_matrix(
        order='5002', okooo_match_id='1338958',
        five_hundred={
            'fid': '1430592', 'home': 'A', 'away': 'B', 'league': 'L',
            'handicap_label': '平手/半球', 'started': False,
            'report': {'url': 'https://live.500.com/', 'status': 200, 'bytes': 129069},
        },
        five_hundred_odds={
            'fid': '1430592', 'asian_handicap': 0.5, 'ou_line': 2.5,
            'report': {'url': 'https://odds.500.com/fenxi/yazhi-1430592.shtml',
                       'status': 200, 'bytes': 40000},
        },
        okooo_tabs={'match_id': '1338958', 'tabs': {
            'ah': {'url': 'https://m.okooo.com/match/handicap.php?MatchID=1338958',
                   'status': 200, 'bytes': 95392, 'parse_stage': 'ok', 'available': True,
                   'companies': [{'name': 'x'}],
                   'avg': {'latest': {'line': -0.75, 'line_text': '受半/一',
                                      'home_water': 1.9, 'away_water': 2.55},
                           'start': {'line': -0.5}}},
            'ou': {'url': 'https://m.okooo.com/match/ah.php?type=daxiao&matchId=1338958',
                   'status': 200, 'bytes': 13148, 'parse_stage': 'ok', 'available': True,
                   'companies': [{'latest': {'over_water': 1.93, 'under_water': 1.77}}],
                   'summary': {'line_mode': '3.25'}},
        }},
    )
    cell = row['fields']['asian_handicap']
    assert cell['value'] == -0.75, '500 的 0.5 不能盖住澳客的平均指数盘口'
    assert cell['source'] == 'okooo_mobile'
    assert cell['url'].startswith('https://m.okooo.com/')
    ou = row['fields']['ou_line']
    assert ou['value'] == '3.25' and ou['source'] == 'okooo_mobile'


def test_500_odds_fill_in_when_okooo_is_absent() -> None:
    """澳客缺场时，500 的盘口结构（住宅 IP 抓到时）必须能顶上，而不是留空。

    这条同时证明 `five_hundred_odds` 这个口子是通的：数据一到位就进候选，
    不需要改取源代码。
    """
    row = mdm.build_match_matrix(
        order='5003',
        five_hundred={'fid': '1416075', 'home': '格罗宁根', 'away': '威廉二世',
                      'report': {'url': 'https://live.500.com/', 'status': 200, 'bytes': 129069}},
        five_hundred_odds={
            'fid': '1416075', 'asian_handicap': 1.75,
            'report': {'url': 'https://odds.500.com/fenxi/yazhi-1416075.shtml',
                       'status': 200, 'bytes': 40000},
        },
    )
    cell = row['fields']['asian_handicap']
    assert cell['value'] == 1.75
    assert cell['source'] == 'live500'
    assert 'odds.500.com' in cell['url']


def test_score_precedence_wins_when_both_sources_offer_the_field() -> None:
    """反过来：比分两侧都可能有时，必须取 500。"""
    row = mdm.build_match_matrix(
        order='5002', okooo_match_id='1338958',
        five_hundred={'fid': '1430592', 'home_score': 3, 'away_score': 1, 'started': True,
                      'report': {'url': 'https://live.500.com/detail.php?fid=1430592&r=1',
                                 'status': 200, 'bytes': 17362}},
        okooo_tabs={'match_id': '1338958', 'tabs': {
            'pnl': {'url': 'https://m.okooo.com/match/exchanges.php?MatchID=1338958',
                    'status': 200, 'bytes': 15324, 'parse_stage': 'ok', 'available': True,
                    'sections': {'竞足保存盈亏': {'rows': [{'label': '胜', 'cells': ['4.55', '1', '+2']}]}}},
        }},
    )
    cell = row['fields']['home_score']
    assert cell['value'] == 3
    assert cell['source'] == 'live500'
    assert cell['reason'] == 'ok'
    assert cell['url'] == 'https://live.500.com/detail.php?fid=1430592&r=1'
    assert cell['status'] == 200
    assert cell['bytes'] == 17362
    assert row['fields']['away_score']['value'] == 1
def test_precedence_table_is_the_single_source_of_truth() -> None:
    """规则只写一处：任何字段的取源都从 FIELD_PRECEDENCE 读，不在调用点硬编码。"""
    # 盘口：澳客先，500 补缺（住宅 IP 上 500 盘口页可用时必须能顶上）
    assert mdm.FIELD_PRECEDENCE['asian_handicap'] == ('okooo_mobile', 'live500')
    # 比分：500 是唯一来源，澳客没有这个字段
    assert mdm.FIELD_PRECEDENCE['home_score'] == ('live500',)
    assert mdm.FIELD_PRECEDENCE['league'] == ('live500', 'okooo_mobile')
    assert mdm.DEFAULT_PRECEDENCE == ('okooo_mobile', 'live500')
    # 未被特判的字段走默认顺序
    assert 'unknown_field' not in mdm.FIELD_PRECEDENCE
    cell = mdm._pick('unknown_field', {
        'okooo_mobile': mdm._Cell('from_okooo', source='okooo_mobile'),
        'live500': mdm._Cell('from_500', source='live500'),
    })
    assert cell.source == 'okooo_mobile'


def test_team_names_prefer_the_500_simplified_spelling(merged: dict[str, Any]) -> None:
    assert merged['fields']['home']['value'] == '海登海姆'
    assert merged['fields']['home']['source'] == 'live500'


# --------------------------------------------------------------------------- #
# 逐格 provenance
# --------------------------------------------------------------------------- #
def test_every_filled_cell_carries_a_traceable_url(merged: dict[str, Any]) -> None:
    """每个有值的格子都必须能指到一个 URL —— 这就是「可追溯」的定义。"""
    for field, cell in merged['fields'].items():
        if cell['reason'] != 'ok':
            continue
        assert cell['url'], f'{field} filled without a url: {cell}'
        assert cell['url'].startswith('https://'), f'{field} url not absolute: {cell["url"]}'


def test_gap_reasons_are_enumerated(merged: dict[str, Any]) -> None:
    for gap in merged['gaps']:
        assert gap['reason'] in mdm.REASON_CODES, gap
    # 未开赛场的比分位是 `not_started`，不是 `not_in_source`；但它是**赛程状态**，
    # 不是缺口：留在 `fields` 里（矩阵段显示「未开赛」），不进 `gaps`。
    # 以前这里断言它出现在 `gaps` 里，于是每份赛前报告都印出一条假缺口。
    assert [g for g in merged['gaps'] if g['reason'] == 'not_started'] == []
    for field in ('home_score', 'away_score', 'half_time'):
        assert merged['fields'][field]['reason'] == 'not_started', merged['fields'][field]


def test_zero_handicap_counts_as_present() -> None:
    """0.0 是真实盘口（平手盘）。用 `not value` 判空会把平手盘判成缺。"""
    cell = mdm._Cell(0.0, source='okooo_mobile', reason='ok')
    assert cell.filled is True
    assert cell.as_dict()['reason'] == 'ok'
    for empty in (None, '', [], {}):
        assert mdm._Cell(empty).filled is False


def test_js_rendered_panel_reason_survives_into_the_gap_list(
    board: dict[str, Any], details: dict[str, Any], okooo_tabs: dict[str, Any],
) -> None:
    """让球面板由 JS 渲染时，那一格的原因码必须是 `panel_js_rendered`，
    不能和「解析失败」混成同一个码。"""
    row = mdm.build_match_matrix(
        order='5002', okooo_match_id='1338958',
        five_hundred=board['matches'][1], five_hundred_detail=details['1430592'],
        okooo_tabs=okooo_tabs,
    )
    cell = row['fields']['handicap_1x2']
    assert cell['value'] is None
    assert cell['reason'] == 'panel_js_rendered'
    assert cell.get('optional') is True


# --------------------------------------------------------------------------- #
# 去重
# --------------------------------------------------------------------------- #
def test_dedupe_merges_rows_by_normalized_order() -> None:
    left = {'order': '5002', 'fields': {'a': {'reason': 'ok', 'value': 1}, 'b': {'reason': 'not_in_source'}}}
    right = {'order': 5002, 'fields': {'a': {'reason': 'not_in_source'}, 'b': {'reason': 'ok', 'value': 2}}}
    out = mdm.dedupe_rows([left, right])
    assert out['count'] == 1
    merged = out['rows'][0]['fields']
    assert merged['a']['value'] == 1 and merged['b']['value'] == 2
    assert out['duplicates'][0]['order'] == '5002'


def test_dedupe_drops_rows_without_an_order() -> None:
    out = mdm.dedupe_rows([{'order': ''}, {'order': 'abc'}, {'order': '5001', 'fields': {}}])
    assert out['count'] == 1
    assert len(out['duplicates']) == 2
    assert {d['reason'] for d in out['duplicates']} == {'no_order'}


def test_build_matrix_never_emits_two_rows_for_one_order(
    board: dict[str, Any], okooo_tabs: dict[str, Any],
) -> None:
    """盘面与奥客都给 5002 时，矩阵必须只有一行。"""
    matrix = mdm.build_matrix(
        five_hundred_board=board,
        okooo_by_order={'5002': {'match_id': '1338958', 'tabs': okooo_tabs},
                        '05002': {'match_id': '1338958', 'tabs': okooo_tabs}},
    )
    orders = [r['order'] for r in matrix['rows']]
    assert orders.count('5002') == 1, orders


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def test_tg_lines_show_numbers_and_say_what_is_missing(
    board: dict[str, Any], details: dict[str, Any], okooo_tabs: dict[str, Any],
) -> None:
    matrix = mdm.build_matrix(
        five_hundred_board=board, five_hundred_details=details,
        okooo_by_order={'5002': {'match_id': '1338958', 'tabs': okooo_tabs}},
    )
    lines = mdm.format_matrix_tg_lines(matrix, max_rows=2)
    text = '\n'.join(lines)
    assert '两源合并数据矩阵' in text
    # 正文只说中文：原来是「join 键」，现在「匹配键 = 竞彩编号（order）」。
    assert '匹配键' in text
    assert 'join 键' not in text, text
    # 5002 这一行必须同时出现两侧的实数字
    assert '-1' in text            # 亚盘（澳客）
    assert '3.25' in text          # 大小球（澳客）
    assert '平手/半球' in text      # 让球标签（500）
    assert '未开赛' in text         # 未开赛比分位
    assert 'm.okooo.com' not in text, 'URL 只在证据字段里，不进主表'
    assert 'HTTP 200/129069B' in text


def test_tg_lines_with_no_rows_say_not_formed() -> None:
    lines = mdm.format_matrix_tg_lines({'rows': []})
    assert any('未形成' in line for line in lines)


def test_tg_lines_never_fabricate_a_dash_as_a_number() -> None:
    """缺格只写原因，「-」不是数字，不能被读成 0。"""
    row = mdm.build_match_matrix(order='5999')  # 两源都没有
    lines = mdm.format_matrix_tg_lines({'rows': [row], 'duplicates': []})
    text = '\n'.join(lines)
    assert '对方源无此场' in text
    assert '500 缺' in text and '澳客 缺' in text


def test_matrix_provenance_carries_the_board_elapsed_time() -> None:
    """耗时必须进到矩阵出处里，报告里那一行才有真实数字可读。"""
    row = mdm.build_match_matrix(
        order='5002',
        five_hundred={
            'fid': '1430592', 'home': 'A', 'away': 'B',
            'report': {'url': 'https://live.500.com/', 'status': 200, 'bytes': 129080},
            'elapsed_ms': 1704,
        },
    )
    assert row['provenance']['live500']['board']['elapsed_ms'] == 1704
    assert row['fields']['home']['elapsed_ms'] == 1704
    lines = mdm.format_matrix_tg_lines({'rows': [row], 'duplicates': []})
    assert '1704ms' in '\n'.join(lines), '报告里看不到真实耗时'


def test_matrix_elapsed_is_zero_not_invented_when_unknown() -> None:
    """没有耗时就写 0，不编一个好看的数。"""
    row = mdm.build_match_matrix(
        order='5002',
        five_hundred={'fid': '1', 'home': 'A', 'away': 'B',
                      'report': {'url': 'https://live.500.com/', 'status': 200}},
    )
    assert row['provenance']['live500']['board']['elapsed_ms'] == 0
    text = '\n'.join(mdm.format_matrix_tg_lines({'rows': [row], 'duplicates': []}))
    assert 'ms' not in text.split('500 场次号=')[1].split('｜')[0], '没有耗时却印了毫秒'
