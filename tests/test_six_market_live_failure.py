"""六盘口 live 失败现场（match 1338958）的回归守卫。

用户在 Windows 机器上跑出来的日志里，六盘口缺三个、31 表空、日志停在一句半截话上。
这里每条测试对应报告里一个已定位的根因，删掉修复必须变红。

夹具全部来自仓库自己的抓包，不用手搓 dict：
  tests/fixtures/okooo_jczq_list_20261126.html   m.okooo.com/jczq/ 整页（23 个 orderid）
  tests/fixtures/okooo_mobile_ah.html            手机亚指页（GB18030，26 家公司）
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import okooo_mobile_cs as okooo  # noqa: E402

app = importlib.import_module('app_v84_hermes')

LIST_FIXTURE = FIXTURES / 'okooo_jczq_list_20261126.html'
AH_FIXTURE = FIXTURES / 'okooo_mobile_ah.html'

# 用户那场：match 1338958 → orderid 5002（夹具里 matchIds 的第 14 个）。
USER_MATCH_ID = '1338958'
USER_ORDERID = '5002'


def _list_text() -> str:
    return okooo.decode_okooo_html(LIST_FIXTURE.read_bytes())


def _slot() -> dict:
    text = _list_text()
    data = okooo._decode_js_object(text)
    assert data is not None
    return data[USER_ORDERID]


# ---------------------------------------------------------------------------
# 1. oddsData 里那四个没人读的块
# ---------------------------------------------------------------------------

def test_list_page_has_five_odds_blocks_and_four_were_unread() -> None:
    """同一份 oddsData 的五个块，项数与页面 optionConfig 完全一致。"""
    slot = _slot()
    odds = slot['OddsList']
    assert sorted(odds) == [
        'SportteryHalfFull', 'SportteryNWDL', 'SportteryScore',
        'SportteryTotalGoals', 'SportteryWDL',
    ]
    assert len(odds['SportteryWDL']) == 3
    assert len(odds['SportteryNWDL']) == 3
    assert len(odds['SportteryHalfFull']) == 9
    assert len(odds['SportteryTotalGoals']) == 8
    assert len(odds['SportteryScore']) == 31


def test_list_boards_parse_all_four_from_user_match() -> None:
    """用户那场（orderid 5002）四块全部解析出来，且值就是夹具里的原始赔率。"""
    res = okooo.parse_jczq_list_boards(_list_text(), USER_MATCH_ID)
    assert res['orderid'] == USER_ORDERID
    assert set(res['hit']) == {'胜平负', '竞彩让球', '总进球', '半全场'}

    wdl = res['blocks']['胜平负']
    assert wdl['stage'] == 'ok'
    # 胜平负 = SportteryNWDL 16/15/14（betway="0" 平手块），不是 WDL。
    assert (wdl['odds_1x2']['home'], wdl['odds_1x2']['draw'], wdl['odds_1x2']['away']) == (1.91, 3.86, 2.8)
    assert wdl['source_block'] == 'SportteryNWDL'

    jc = res['blocks']['竞彩让球']
    assert jc['stage'] == 'ok'
    assert jc['line'] == -1.0            # Boundary.SportteryWDL == '-1'
    # 竞彩让球 = SportteryWDL 13/11/10 + Boundary，不是 NWDL。
    assert (jc['odds']['home'], jc['odds']['draw'], jc['odds']['away']) == (3.55, 4.03, 1.64)
    assert jc['source_block'] == 'SportteryWDL+Boundary.SportteryWDL'

    tg = res['blocks']['总进球']
    assert tg['stage'] == 'ok'
    assert list(tg['odds']) == ['0', '1', '2', '3', '4', '5', '6', '7+']
    assert tg['odds']['0'] == 30.0
    assert tg['odds']['7+'] == 10.0

    hf = res['blocks']['半全场']
    assert hf['stage'] == 'ok'
    assert len(hf['odds']) == 9
    assert hf['odds']['胜胜'] == 2.95    # code 20
    assert hf['odds']['平平'] == 7.5     # code 24
    assert hf['odds']['负负'] == 4.75    # code 28


def test_wdl_codes_are_not_home_first() -> None:
    """10 是客胜、13 是主胜：与比分块「先主队」相反，不能类推。

    证据来自清单页自己渲染的行：每行先显示竞彩让球「胜/平/负」，再显示胜平负
    「胜/平/负」。21/21 场里 WDL 的 13/11/10 与行内胜/平/负逐位相等。
    """
    text = _list_text()
    data = okooo._decode_js_object(text)
    checked = 0
    for block in re.split(r'<div[^>]*class="[^"]*jsMatchItem[^"]*"', text)[1:]:
        oid_m = re.search(r'orderid="(\d+)"', block)
        if not oid_m:
            continue
        slot = data.get(oid_m.group(1)) or {}
        wdl = (slot.get('OddsList') or {}).get('SportteryWDL') or {}
        if len(wdl) != 3:
            continue
        plain = re.sub(r'<[^>]+>', ' ', block)
        trips = re.findall(r'胜\s*([\d.]+)\s*平\s*([\d.]+)\s*负\s*([\d.]+)', plain)
        if len(trips) < 2:
            continue
        straight = trips[1]
        assert (wdl['13'], wdl['11'], wdl['10']) == straight, oid_m.group(1)
        checked += 1
    assert checked >= 20, f'只用 {checked} 场核对，样本太少不足以断言编码顺序'
    # 解析器必须按证据映射：胜平负取 NWDL(16/15/14)，竞彩让球取 WDL(13/11/10)。
    # 块与市场的关系见下方 test_render_dom_pins_market_assignment。
    blocks = okooo.parse_jczq_list_boards(text, USER_MATCH_ID)['blocks']
    slot = _slot()['OddsList']
    assert blocks['胜平负']['odds_1x2']['home'] == float(slot['SportteryNWDL']['16'])
    assert blocks['竞彩让球']['odds']['home'] == float(slot['SportteryWDL']['13'])


def test_half_full_group_one_is_strictly_ordered_by_price() -> None:
    """半全场 20-22 = 胜胜/胜平/胜负：23 场里 20 恒最低、22 恒最高，无一例外。

    这条是 20..28 顺序的价格证据（另一半来自页面 optionConfig 的同序 cn 表）。
    """
    data = okooo._decode_js_object(_list_text())
    seen = 0
    for slot in data.values():
        hf = (slot.get('OddsList') or {}).get('SportteryHalfFull') or {}
        if len(hf) != 9:
            continue
        low, mid, high = float(hf['20']), float(hf['21']), float(hf['22'])
        assert low <= mid <= high, (hf['20'], hf['21'], hf['22'])
        seen += 1
    assert seen == 23


def test_block_missing_is_reported_not_invented() -> None:
    """某场缺一个块：如实报 block_absent，且**不得**凭空补出赔率。

    4008 / 4012 的 `SportteryNWDL` 是空数组。映射纠正后 NWDL 就是**胜平负**，
    所以这两场缺的是胜平负，不是竞彩让球——它们的 WDL（让球盘）反而是齐的。
    """
    text = _list_text()
    data = okooo._decode_js_object(text)
    empty = [oid for oid, s in data.items()
             if not (s.get('OddsList') or {}).get('SportteryNWDL')]
    assert empty, '夹具里应当存在缺 NWDL 的场次'
    for oid, mid in (('4008', '1347783'), ('4012', '1347663')):
        assert oid in empty
        blocks = okooo.parse_jczq_list_boards(text, mid)['blocks']
        # NWDL 空 => 胜平负缺
        assert blocks['胜平负']['stage'] == 'block_absent', oid
        assert blocks['胜平负']['odds_1x2'] == {}
        # 让球盘（WDL + Boundary）齐 => 竞彩让球 ok
        assert blocks['竞彩让球']['stage'] == 'ok', oid
        assert len(blocks['竞彩让球']['odds']) >= 6


def test_partial_block_is_incomplete_not_ok() -> None:
    """块在但项数不够 → stage=odds_incomplete_N_of_M，绝不冒充 ok。"""
    html = (
        '<div class="jsMatchItem" matchid="999" orderid="7001"></div>'
        '<script>var oddsData = {"7001":{"Boundary":{"SportteryWDL":"+1"},'
        '"OddsList":{"SportteryHalfFull":{"20":"2.0"},'
        '"SportteryTotalGoals":{"00":"9.0","01":"5.0"}},"Result":{}}};</script>'
    )
    res = okooo.parse_jczq_list_boards(html, '999')
    assert res['blocks']['半全场']['stage'] == 'odds_incomplete_1_of_9'
    assert res['blocks']['总进球']['stage'] == 'odds_incomplete_2_of_8'
    assert res['blocks']['胜平负']['stage'] == 'block_absent'
    assert res['hit'] == []


def test_handicap_line_formats_are_signed_integers() -> None:
    """Boundary 的 '+1' / '-1' / '-2' / '0' 都要认，非整数一律不收。"""
    assert okooo._jc_line_of_boundary('+1') == 1.0
    assert okooo._jc_line_of_boundary('-1') == -1.0
    assert okooo._jc_line_of_boundary('-2') == -2.0
    assert okooo._jc_line_of_boundary('0') == 0.0
    assert okooo._jc_line_of_boundary('+3') == 3.0
    assert okooo._jc_line_of_boundary('+0.5') is None
    assert okooo._jc_line_of_boundary('') is None
    assert okooo._jc_line_of_boundary(None) is None


# ---------------------------------------------------------------------------
# 2. 亚盘词表（defect B）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('text,expected', [
    ('平手', 0.0), ('平/半', 0.25), ('半球', 0.5), ('半/一', 0.75),
    ('一球', 1.0), ('一/球半', 1.25), ('球半', 1.5), ('球半/两球', 1.75),
    ('两球', 2.0), ('两球/两球半', 2.25), ('两球半', 2.5),
    ('两球半/三球', 2.75), ('三球', 3.0), ('三半', 3.5), ('三球半', 3.5),
    ('三/三半', 3.25), ('四球', 4.0), ('四半', 4.5), ('四球半', 4.5),
    ('五球', 5.0),
    # 繁体「兩」
    ('兩球', 2.0), ('兩球半', 2.5),
    # 受让 / 让球前缀
    ('受半球', -0.5), ('受半/一', -0.75), ('受一球', -1.0),
    ('受三球', -3.0), ('受三半', -3.5), ('受三/三半', -3.25),
    ('受兩球', -2.0), ('受让一球', -1.0), ('让一球', 1.0),
])
def test_ah_vocabulary_is_compositional(text: str, expected: float) -> None:
    assert okooo.ah_text_to_line(text) == expected


def test_ah_long_line_is_not_truncated_to_a_short_one() -> None:
    """历史实现用 `key in body` 模糊匹配，「三球半」先命中「球半」得到 1.5。

    一个更长的盘口被更短的词静默截断，是最危险的一类错误：给出来的是一个
    看起来合法、却整整差两球的数。
    """
    assert okooo.ah_text_to_line('三球半') == 3.5
    assert okooo.ah_text_to_line('四球半') == 4.5
    assert okooo.ah_text_to_line('三球半') != 1.5


def test_ah_unknown_text_returns_none() -> None:
    assert okooo.ah_text_to_line('x') is None
    assert okooo.ah_text_to_line('') is None
    assert okooo.ah_text_to_line('待定') is None


def test_full_ah_fixture_vocabulary_all_maps() -> None:
    """夹具里出现的每一条盘口文本都必须能映射，一条不剩。"""
    text = okooo.decode_okooo_html(AH_FIXTURE.read_bytes())
    seen = set()
    for row in re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', text, re.IGNORECASE):
        cells = re.findall(r'<td[^>]*>([\s\S]*?)</td>', row, re.IGNORECASE)
        if len(cells) < 3:
            continue
        for cell, names in ((cells[1], ['zhu', 'chu', 'ke']),
                            (cells[2], ['xinzhu', 'xin', 'xinke'])):
            got = okooo._tag_text(cell, None, names)
            label = (got.get('chu') or got.get('xin') or '').strip()
            if label:
                seen.add(label)
    assert '受三半' in seen, '夹具里应当有受三半这一行'
    unmapped = sorted(t for t in seen if okooo.ah_text_to_line(t) is None)
    assert unmapped == [], f'未映射的盘口文本: {unmapped}'


# ---------------------------------------------------------------------------
# 3. 平均行 vs 公司众数（defect C）
# ---------------------------------------------------------------------------

def test_avg_row_wins_and_disagreement_is_recorded() -> None:
    """夹具本场：平均指数 受一球(-1.0)，26 家里 14 家 受半/一(-0.75)。

    裁决是平均行优先（站方对全部公司的汇总），但分歧必须写出来，
    否则下游会误以为全市场一致。
    """
    parsed = okooo._mobile_ah_line(okooo.decode_okooo_html(AH_FIXTURE.read_bytes()))
    assert parsed['stage'] == 'ok'
    assert parsed['line_source'] == 'avg_row'
    assert parsed['asian_handicap'] == -1.0
    assert parsed['mode_line'] == -0.75
    assert parsed['mode_hits'] == '14/26'
    assert 'avg_row' in parsed['line_disagreement']
    assert 'mode' in parsed['line_disagreement']


def test_mode_is_used_when_avg_row_absent() -> None:
    """没有平均行时退回众数，并标明来源——不能写死一条盘口。"""
    text = okooo.decode_okooo_html(AH_FIXTURE.read_bytes())
    stripped = re.sub(
        r'<tr[^>]*>(?:(?!</tr>)[\s\S])*平均[^<]*(?:(?!</tr>)[\s\S])*</tr>', '', text,
    )
    parsed = okooo._mobile_ah_line(stripped)
    assert parsed['stage'] == 'ok'
    assert parsed['line_source'].startswith('mode(')
    assert parsed['asian_handicap'] == -0.75


# ---------------------------------------------------------------------------
# 4. 尝试合并（报告串台）
# ---------------------------------------------------------------------------

def test_attempt_merge_never_mixes_status_and_stage() -> None:
    """`ou:fetch_failed/HTTP200` 的成因：status 取第一次、stage 取最后一次。

    修好后 summary 的 status/stage/bytes 全部来自同一次尝试，链上每次尝试
    各自带自己的状态码与阶段。
    """
    attempts = [
        {'url': 'u1', 'channel': 'mobile', 'status': 200, 'bytes': 13148,
         'title': '大小球', 'blocked': False, 'error': '', 'stage': 'ok'},
        {'url': 'u2', 'channel': 'desktop', 'status': 405, 'bytes': 900,
         'title': '', 'blocked': True, 'error': 'HTTP 405', 'stage': 'fetch_failed'},
    ]
    chain = okooo._attempt_chain(attempts)
    assert 'mobile HTTP200 ok' in chain
    assert 'desktop HTTP405 http_error_405' in chain
    # 同一次尝试的 status 与 stage 不许被拆开拼在一起
    assert 'fetch_failed/HTTP200' not in chain


def test_real_pack_merge_keeps_status_and_stage_from_one_attempt(monkeypatch) -> None:
    """走真正的六盘口抓取：手机页 200 但解析不出行、桌面页 405。

    旧实现的 summary 会是 status=200（第一次）+ stage=fetch_failed（最后一次），
    也就是用户日志里那条 `ou:fetch_failed/HTTP200`。现在两者必须同源。
    """
    empty_rows_html = '<html><title>大小球</title><table></table></html>'

    def _fake_fetch(url, *a, **k):
        if 'm.okooo.com' in url:
            # 手机页 200 但页面里没有任何可解析行
            return {'ok': True, 'url': url, 'status': 200, 'bytes': 52,
                    'title': '大小球', 'blocked': False, 'origin_blocked': False,
                    'error': '', 'html': empty_rows_html}
        # 桌面页被 WAF 拦
        return {'ok': False, 'url': url, 'status': 405, 'bytes': 900,
                'title': '', 'blocked': True, 'origin_blocked': True,
                'error': 'HTTP 405', 'html': ''}

    monkeypatch.setattr(okooo, '_fetch_detail', _fake_fetch)
    monkeypatch.setattr(okooo, '_jczq_page_cached', lambda *a, **k: ({'ok': False}, False, ''))
    pack = okooo.scrape_okooo_mobile_six_markets('999999')
    report = pack['source_report']['ou']
    assert report['attempts'], '必须保留每次尝试的明细'
    # summary 的 status/stage/bytes 来自同一次尝试（最后一次 = 桌面 405）
    assert report['status'] == 405
    assert report['stage'] == 'fetch_failed'
    assert report['bytes'] == 900
    assert report['reason'] == 'origin_blocked'
    # 每次尝试各自带自己的阶段，不会串台
    stages = [(a['channel'], a['status'], okooo._stage_reason(a.get('stage'), a))
              for a in report['attempts']]
    assert ('mobile', 200, 'parsed_no_rows') in stages
    assert ('desktop', 405, 'origin_blocked') in stages
    assert 'ou' in report['chain'] or 'desktop' in report['chain']


def test_stage_reason_separates_the_four_failure_kinds() -> None:
    """传输失败 / HTTP 错误 / 取到但无盘口线 / 取到但无公司行，四种必须分开。"""
    assert okooo._stage_reason('', {'ok': False, 'transport_error': 'Timeout'}) == 'transport_failed'
    assert okooo._stage_reason('', {'ok': False, 'status': 404, 'error': 'HTTP 404'}) == 'http_error_404'
    assert okooo._stage_reason('', {'ok': False, 'origin_blocked': True}) == 'origin_blocked'
    assert okooo._stage_reason('no_line', {'ok': True, 'status': 200}) == 'parsed_no_line'
    assert okooo._stage_reason('no_rows', {'ok': True, 'status': 200}) == 'parsed_no_rows'
    assert okooo._stage_reason('company_row_missing', {'ok': True, 'status': 200}) == 'parsed_no_company'
    assert okooo._stage_reason('ok', {'ok': True, 'status': 200}) == 'ok'
    # 「取到页面但解析不出」绝不能叫 fetch_failed
    assert okooo._stage_reason('no_line', {'ok': True, 'status': 200}) != 'fetch_failed'


def test_http200_but_no_body_is_not_transport_failure() -> None:
    """200 但正文不可用 ≠ 传输失败：这是 response_empty，处置完全不同。"""
    assert okooo._stage_reason('', {'ok': False, 'status': 200, 'bytes': 0}) == 'response_empty'


# ---------------------------------------------------------------------------
# 5. 六盘口包写回（defect D）
# ---------------------------------------------------------------------------

def test_ah_line_on_record_reaches_the_six_board_pack() -> None:
    """明细抓到 8 家公司但六盘口页没给标量线时，线必须写进**包**。

    用户日志的形状：`📱 亚指=8` 与 `🔄 AH=None` 同时出现。原因是
    apply_mobile_books_lines 只把线写到记录，AH 分支却只 print 不写回包，
    于是闸门读包、判缺盘口、整场拒发。
    """
    rec = {
        'asian_handicap': -0.75,
        'ah_channel': 'mobile',
        'mobile_ah_ou_books': {
            'available': True,
            'asian_handicap': {
                'rows': [{'name': '平均指数', 'latest': {'line': -0.75}}],
                'companies': [{'name': f'公司{i}'} for i in range(8)],
            },
        },
    }
    pack = {
        'asian_handicap': None,
        'ou_line': None,
        'boards_hit': {},
        'missing': ['asian_handicap', 'ou_line', 'odds_1x2'],
    }
    out = app._backfill_six_board_lines_from_record(rec, pack)
    assert out['asian_handicap'] == -0.75
    assert out['asian_handicap_source'] == 'mobile_prior'
    assert out['boards_hit'].get('亚盘让球') is True
    assert 'asian_handicap' not in out['missing']
    # 六盘口包自己抓到值时不覆盖
    pack2 = {'asian_handicap': -1.0, 'boards_hit': {}, 'missing': []}
    out2 = app._backfill_six_board_lines_from_record(rec, pack2)
    assert out2['asian_handicap'] == -1.0
    assert out2.get('asian_handicap_source') is None


def test_ou_line_on_record_reaches_the_six_board_pack() -> None:
    rec = {'ou_line': 2.75, 'ou_channel': 'mobile'}
    pack = {'ou_line': None, 'boards_hit': {}, 'missing': ['ou_line']}
    out = app._backfill_six_board_lines_from_record(rec, pack)
    assert out['ou_line'] == 2.75
    assert out['boards_hit'].get('大小球') is True
    assert 'ou_line' not in out['missing']


def test_zero_line_is_a_real_line_not_a_gap() -> None:
    """0.0 是平手盘 / 大小球 0 线，必须当成有值写回，不能被 `not value` 漏掉。

    但 0.0 仍须通过准入：亚盘 0.0（平手）在 ±5 内、是四分之一球刻度，
    大小球 0.0 不在 0.5..6.5 内——这是主路径既有的口径，不在本轮放宽。
    """
    rec = {'asian_handicap': 0.0, 'ah_channel': 'mobile'}
    pack = {'asian_handicap': None, 'ou_line': None,
            'boards_hit': {}, 'missing': ['asian_handicap', 'ou_line']}
    out = app._backfill_six_board_lines_from_record(rec, pack)
    assert out['asian_handicap'] == 0.0, '平手盘 0.0 必须算有值'
    assert out['boards_hit'].get('亚盘让球') is True
    assert 'asian_handicap' not in out['missing']


def test_six_board_gate_still_blocks_when_ah_and_ou_absent() -> None:
    """闸门不许被这次的修改放宽：亚盘/大小球真缺时仍然拒发。"""
    gaps = app.DeepSeekAnalyzer.six_market_gaps({
        'asian_handicap': None,
        'ou_line': None,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 3.5},
        'jc_handicap': -1,
        'total_goals_odds': {str(i): 5.0 for i in range(8)},
        'htft_odds': {k: 5.0 for k in
                      ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负')},
    })
    assert gaps == ['亚盘让球', '大小球']


def test_six_board_gate_passes_when_only_bookmaker_lines_present() -> None:
    """四个竞彩盘口齐全 + 亚盘/大小球齐全 = 六盘口齐，闸门放行。"""
    gaps = app.DeepSeekAnalyzer.six_market_gaps({
        'asian_handicap': -0.75,
        'ou_line': 2.75,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 3.5},
        'jc_handicap': -1,
        'total_goals_odds': {str(i): 5.0 for i in range(8)},
        'htft_odds': {k: 5.0 for k in
                      ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负')},
    })
    assert gaps == []


def test_six_board_gate_refusal_names_structural_vs_transient() -> None:
    """拒发原因必须分清「结构性不可达」与「本次没解析出来」，但结论仍是拒发。"""
    boards = {
        'asian_handicap': None,
        'ou_line': None,
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 3.5},
        'jc_handicap': -1,
        'total_goals_odds': {str(i): 5.0 for i in range(8)},
        'htft_odds': {k: 5.0 for k in
                      ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负')},
    }
    gate = app.DeepSeekAnalyzer.ensure_judgment_standard_sections(
        'DeepSeek 数据分析研判报告 正文', payload={'six_market_mobile': boards},
    )
    assert gate['reject'] is True, '门禁不许被这次修改放宽'
    assert '结构性缺' in gate['reject_reason']
    assert '亚盘让球' in gate['reject_reason']

    # 竞彩侧的项没解析出来时，必须标成「可修」而不是结构性缺
    boards2 = dict(boards, total_goals_odds={}, htft_odds={},
                   asian_handicap=-0.75, ou_line=2.75)
    gate2 = app.DeepSeekAnalyzer.ensure_judgment_standard_sections(
        'DeepSeek 数据分析研判报告 正文', payload={'six_market_mobile': boards2},
    )
    assert gate2['reject'] is True
    assert '本次未取到' in gate2['reject_reason']
    assert '结构性缺' not in gate2['reject_reason']


# ---------------------------------------------------------------------------
# 7. 日志诚实：结论行与补齐成功
# ---------------------------------------------------------------------------

def test_verdict_line_is_exactly_one_explicit_outcome() -> None:
    """成功/失败各一条，失败必须带原因；不许出现「没有结论」的写法。"""
    ok = app._stage2_verdict_line(True, '', 'A vs B', '1')
    bad = app._stage2_verdict_line(False, '质量门拒发', 'A vs B', '1')
    assert '已推送' in ok and '未推送' not in ok
    assert '未推送' in bad and '质量门拒发' in bad
    assert bad.count('[阶段2·研判]') == 1
    # 失败但没给原因时也必须写出「未给出原因」，不能留空
    assert '未给出原因' in app._stage2_verdict_line(False, '', 'A vs B', '1')


def test_fold_success_line_only_when_audit_is_complete() -> None:
    """「本地折入补齐成功」只许在审计 complete 时出现。

    用户日志里「缺 31 项(全部)」紧跟着「补齐成功」，那句话是假的。
    这里按源码顺序断言：成功行位于 `if audit.complete` 分支内，
    并且紧接着必须有 else 分支打出「仍不齐」。
    """
    src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    idx = src.index('本地折入补齐成功')
    window = src[max(0, idx - 700): idx + 700]
    assert "if _cs31_audit.get('complete'):" in window
    assert '本地折入后仍不齐' in src
    # 成功行必须在 complete 分支之后、else 之前，不能被无条件执行
    complete_at = window.index("if _cs31_audit.get('complete'):")
    success_at = window.index('本地折入补齐成功')
    assert complete_at < success_at


def test_send_tg_reports_failure_instead_of_none() -> None:
    """send_tg 早期退出（空内容/无 chat_id）必须返回 False，调用方才不会记成「已推送」。"""
    assert app.send_tg('') is False
    assert app.send_tg('   ') is False


# ---------------------------------------------------------------------------
# 8. 31 表空但报「补齐成功」的原始矛盾
# ---------------------------------------------------------------------------

def test_31_table_reports_missing_when_nothing_was_folded() -> None:
    """空输入折入后行骨架在、但没有一项成表：审计必须 complete=False 且逐项点名。

    注意 `missing_rows` 是「整行缺」——补表器会先把 31 个骨架行建出来，
    所以它是 0；真正为空的是 theory/market 两列，各 31 项。
    日志里的「缺 31 项(全部)」正是这两个列表，不是 missing_rows。
    """
    pipe = _pipe()
    pack = pipe.DeepSeekAnalyzer.build_theo_mkt_31_pack([], jc_score_odds={})
    audit = pipe.DeepSeekAnalyzer.audit_theo_mkt_31_rows(pack.get('rows'))
    assert audit['complete'] is False
    assert len(audit['missing_theory']) == 31
    assert len(audit['missing_market']) == 31
    assert audit['formed'] == 0
    assert any('理论概率缺31项' in m for m in audit['missing'])
    assert any('市场概率缺31项' in m for m in audit['missing'])


# ---------------------------------------------------------------------------
# 9. DeepSeek 失败不许冒充正文
# ---------------------------------------------------------------------------

def _pipe():
    import blade_deepseek_pipeline as pipe
    return importlib.reload(pipe)


def test_api_failure_is_not_presented_as_analysis(monkeypatch) -> None:
    """API 全挂时 analyze_match_judgment 必须 available=False 且 error 非空，
    正文里不许出现「调用失败」这种话。"""
    pipe = _pipe()
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-key')

    def _boom(*a, **k):
        raise RuntimeError('ConnectionError: boom')

    monkeypatch.setattr('requests.post', _boom)
    raw = pipe.deepseek_chat_completion([{'role': 'user', 'content': 'x'}], max_retries=1)
    assert pipe.completion_failed(raw)
    assert 'boom' in pipe.completion_error()

    judged = pipe.DeepSeekAnalyzer.analyze_match_judgment({'match': 'A vs B'})
    assert judged['available'] is False
    assert judged['error']
    assert '失败' not in judged['content']
    assert judged['content'] == ''


def test_learning_report_failure_is_empty_not_an_apology(monkeypatch) -> None:
    """run_learning_report 同形缺陷：失败不得把告警串当成复盘正文。"""
    pipe = _pipe()
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-key')
    monkeypatch.setattr('requests.post', lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('ConnectionError: boom')))
    body = pipe.run_learning_report({'x': 1})
    assert body == ''


def test_successful_call_still_returns_body(monkeypatch) -> None:
    """成功路径不受影响：available=True、error 空、content 为正文。"""
    pipe = _pipe()
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-key')

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {'choices': [{'message': {'content': '真实研判正文'}}]}

    monkeypatch.setattr('requests.post', lambda *a, **k: _Resp())
    judged = pipe.DeepSeekAnalyzer.analyze_match_judgment({'match': 'A vs B'})
    assert judged['available'] is True
    assert judged['error'] == ''
    assert judged['content'] == '真实研判正文'


# ---------------------------------------------------------------------------
# (a) 市场归属：DOM 是最终裁判
# ---------------------------------------------------------------------------

def _rendered_betway_blocks(block_html):
    """从一条 jsMatchItem 里取出两个 betway 块：rangqiu 值与 (code, odds) 列表。

    真实结构（fixture 逐字）：
      betway="0" → <em class="rangqiu grayd">0</em>，随后每档
                   <div class="listbetbtn ctrl_betopt" data-v="16"><p class="ctrl_txt">胜</p>
                   <p class="ctrl_odds">1.27</p>
      betway="1" → <em class="rangqiu fontred ctrl_special" data-type="rangfen" data-v="-1">-1</em>，
                   各档 data-v 为 13/11/10
    所以 DOM 自己就写着每档的编码，比按数值比对强得多。
    """
    out = []
    marks = list(re.finditer(r'betway="(\d+)"', block_html))
    for idx, m in enumerate(marks):
        # 只切到下一个 betway 标记为止，绝不跨块取数。
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(block_html)
        seg = block_html[m.end(): end]
        em = re.search(r'<em([^>]*)>\s*([+\-\d]+)\s*</em>', seg)
        if not (em and 'rangqiu' in em.group(1)):
            em = None
        rq_attrs = em.group(1) if em else ''
        rq_val = em.group(2) if em else ''
        dv = re.search(r'data-v="([+\-\d]+)"', rq_attrs)
        pairs = [
            (code, float(odd))
            for code, odd in re.findall(
                r'data-v="(\d+)"[\s\S]{0,200}?class="[^"]*ctrl_odds"[^>]*>\s*([1-9][\d.]*)\s*<',
                seg,
            )
        ]
        out.append({
            'betway': m.group(1),
            'rangqiu': dv.group(1) if dv else rq_val,
            'pairs': pairs,
        })
    return out


def test_render_dom_pins_market_assignment() -> None:
    """DOM 断言：平手块(betway=0) 的档位编码是 16/15/14 且等于胜平负；
    让分块(betway=1) 的档位编码是 13/11/10 且等于竞彩让球。

    这条测试是为「市场归属被写反」而加的：不看解析器输出，直接问渲染出来的
    HTML 哪一块是平手、哪一块带让分、各自写着哪些编码，再要求解析器照此分配。

    只有**开了让球**的场次才算样本：没有让球的场次（5011 那类）betway=1 块
    自己也是 `rangqiu grayd` 且不带 data-v 让分值，那正是 block_absent 的形状。
    """
    text = _list_text()
    data = okooo._decode_js_object(text)
    checked = 0
    for block in re.split(r'<div[^>]*class="[^"]*jsMatchItem[^"]*"', text)[1:]:
        oid_m = re.search(r'orderid="(\d+)"', block)
        if not oid_m:
            continue
        slot = (data.get(oid_m.group(1)) or {}).get('OddsList') or {}
        if len(slot.get('SportteryWDL') or {}) != 3 or len(slot.get('SportteryNWDL') or {}) != 3:
            continue
        by_bw: dict = {}
        for _b in _rendered_betway_blocks(block):
            # 同一行里会出现两组 betway（列表摘要 + 展开明细），第一组才是
            # 与 oddsData 对应的那一份；后一组是「更多玩法」面板里的其它玩法，
            # 数值与 oddsData 无关。只认每组第一次出现的标记。
            by_bw.setdefault(_b['betway'], _b)
        if '0' not in by_bw or '1' not in by_bw:
            continue
        flat, hcap = by_bw['0'], by_bw['1']
        if len(flat['pairs']) != 3 or len(hcap['pairs']) != 3:
            continue
        # 平手块：rangqiu 0，编码 16/15/14（= NWDL），数值等于 NWDL 赔率
        assert flat['rangqiu'] in ('0', ''), (oid_m.group(1), flat)
        assert [c for c, _ in flat['pairs']] == ['16', '15', '14'], (oid_m.group(1), flat)
        nwdl = (float(slot['SportteryNWDL']['16']), float(slot['SportteryNWDL']['15']),
                float(slot['SportteryNWDL']['14']))
        assert tuple(v for _, v in flat['pairs']) == nwdl, (oid_m.group(1), flat, nwdl)
        # 让分块：带真实让分值，编码 13/11/10（= WDL），数值等于 WDL 赔率
        if hcap['rangqiu'] in ('', '0'):
            continue  # 该场没开让球，属于 block_absent 样本，另行验证
        assert [c for c, _ in hcap['pairs']] == ['13', '11', '10'], (oid_m.group(1), hcap)
        wdl = (float(slot['SportteryWDL']['13']), float(slot['SportteryWDL']['11']),
               float(slot['SportteryWDL']['10']))
        assert tuple(v for _, v in hcap['pairs']) == wdl, (oid_m.group(1), hcap, wdl)
        checked += 1
    assert checked >= 20, f'只用 {checked} 场核对，样本不足以断言市场归属'
    # 最后把 DOM 结论钉到解析器上：平手块必须喂给胜平负，让分块必须喂给竞彩让球。
    blocks = okooo.parse_jczq_list_boards(text, USER_MATCH_ID)['blocks']
    slot = (data.get(USER_ORDERID) or {}).get('OddsList') or {}
    assert blocks['胜平负']['odds_1x2']['home'] == float(slot['SportteryNWDL']['16'])
    assert blocks['胜平负']['odds_1x2']['away'] == float(slot['SportteryNWDL']['14'])
    assert blocks['竞彩让球']['odds']['home'] == float(slot['SportteryWDL']['13'])
    assert blocks['竞彩让球']['odds']['away'] == float(slot['SportteryWDL']['10'])
    # 没有让球的场次：竞彩让球必须是 block_absent，胜平负不受影响
    for oid in ('5001', '5011'):
        res = okooo.parse_jczq_list_boards(text, {'5001': '1348444', '5011': '1350996'}[oid])
        blocks = res['blocks']
        if not (data.get(oid, {}).get('OddsList') or {}).get('SportteryWDL'):
            assert blocks['竞彩让球']['stage'] == 'block_absent', oid
            assert blocks['胜平负']['stage'] == 'ok', oid


def test_handicap_line_attaches_to_handicap_market_not_straight() -> None:
    """Boundary 挂在竞彩让球上，且**不**挂在胜平负上。"""
    res = okooo.parse_jczq_list_boards(_list_text(), USER_MATCH_ID)
    assert res['blocks']['竞彩让球']['line'] == -1.0
    assert 'line' not in res['blocks']['胜平负']
    assert 'line' not in res['blocks']['胜平负'].get('odds_1x2', {})
    # 胜平负的三项赔率里不应出现让球后的数值
    straight = res['blocks']['胜平负']['odds_1x2']
    assert (straight['home'], straight['draw'], straight['away']) == (1.91, 3.86, 2.8)


@pytest.mark.parametrize('oid,score,bnd', [
    ('4005', (1, 2), 1),
    ('4007', (2, 1), -1),
    ('4009', (3, 1), -2),
    ('4011', (2, 1), -1),
])
def test_result_oracle_confirms_swap_on_discriminating_matches(oid, score, bnd) -> None:
    """Result 作 oracle（仅测试用，生产代码不读它）。

    这四场直盘与让球盘赛果不同，能唯一确定两块的市场归属：
      Result.SportteryWDL  == 让球盘赛果
      Result.SportteryNWDL == 直盘赛果
    """
    data = okooo._decode_js_object(_list_text())
    r = data[oid]['Result']
    h, a = score
    straight = 'Win' if h > a else ('Drawn' if h == a else 'Lost')
    adj = (h - a) + bnd
    handicap = 'Win' if adj > 0 else ('Drawn' if adj == 0 else 'Lost')
    assert straight != handicap, '这一场才是有区分力的样本'
    assert r['SportteryNWDL'] == straight
    assert r['SportteryWDL'] == handicap


# ---------------------------------------------------------------------------
# (b) 准入闸门：只认手机通道的标量盘口线
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('rec,line_key,board', [
    ({'ou_line': 2.75, 'ou_channel': '500'}, 'ou_line', '大小球'),
    ({'asian_handicap': 1.25, 'ah_channel': '500'}, 'asian_handicap', '亚盘让球'),
    ({'asian_handicap': 8.0, 'ah_channel': 'mobile'}, 'asian_handicap', '亚盘让球'),
    ({'asian_handicap': {'rows': [], 'asian_handicap': 1.5}, 'ah_channel': 'mobile'},
     'asian_handicap', '亚盘让球'),
])
def test_backfill_rejects_inadmissible_lines(rec, line_key, board) -> None:
    """500 兜底 / 越界 / dict 一律不得进包、不得算命中、不得从 missing 剔除。"""
    pack = {'asian_handicap': None, 'ou_line': None,
            'boards_hit': {}, 'missing': [line_key]}
    out = app._backfill_six_board_lines_from_record(dict(rec), pack)
    assert out.get(line_key) is None
    assert board not in (out.get('boards_hit') or {})
    assert line_key in (out.get('missing') or [])


def test_backfill_admits_proper_mobile_lines() -> None:
    """手机通道、区间内、四分之一球刻度：正常写回。"""
    rec = {'asian_handicap': -0.75, 'ah_channel': 'mobile',
           'ou_line': 2.75, 'ou_channel': 'mobile'}
    pack = {'asian_handicap': None, 'ou_line': None,
            'boards_hit': {}, 'missing': ['asian_handicap', 'ou_line']}
    out = app._backfill_six_board_lines_from_record(rec, pack)
    assert out['asian_handicap'] == -0.75 and out['ou_line'] == 2.75
    assert out['boards_hit']['亚盘让球'] and out['boards_hit']['大小球']
    assert out['missing'] == []


def test_mobile_ou_line_contract_unchanged() -> None:
    """主路径契约不变：500 兜底仍不进六盘口。"""
    assert app._mobile_ou_line({'ou_line': 2.75, 'ou_channel': '500'}) is None
    assert app._mobile_ou_line({'ou_line': 2.75, 'ou_channel': 'mobile'}) == 2.75


def test_admission_predicate_rejects_one_shared_rule() -> None:
    """AH 与 OU 走同一条准入谓词（不是两套临时判断）。"""
    bad = {'asian_handicap': float('inf'), 'ah_channel': 'mobile'}
    assert app._mobile_ah_line_from_record(bad) is None
    assert app._mobile_ah_line_from_record(
        {'asian_handicap': -6.0, 'ah_channel': 'mobile'}) is None      # 越界
    assert app._mobile_ah_line_from_record(
        {'asian_handicap': '-1.25', 'ah_channel': 'mobile'}) == -1.25  # 字符串可接受


# ---------------------------------------------------------------------------
# (c) ModelPerformanceAnalyzer 不许把失败当正文
# ---------------------------------------------------------------------------

def _review_completed():
    return {
        'm1': {'status': 'completed', 'match': 'A vs B', 'time': '2026-11-26 20:00',
               'predicted_score': '2-1', 'second_score': '1-0', 'actual_score': '2-1',
               'big_small': '大',
               'top15_scores': [('2-1', {'prob': 0.2}), ('1-0', {'prob': 0.1})]},
        'm2': {'status': 'completed', 'match': 'C vs D', 'time': '2026-11-26 20:00',
               'predicted_score': '1-0', 'second_score': '0-1', 'actual_score': '0-1',
               'big_small': '小',
               'top15_scores': [('1-0', {'prob': 0.18}), ('0-1', {'prob': 0.12})]},
    }


def test_review_exception_returns_empty_not_an_apology(monkeypatch) -> None:
    """flash/事实包抛异常时，正文槽必须是空串，原因走 last_status()。

    历史实现 `return f"⚠️ deepseek-flash 模型复盘失败: {e}", "", ""`，
    调用方把它 send_tg 出去并打「已生成并推送」——用户日志里那条 ✅ 就是这么来的。
    """
    def _boom(*a, **k):
        raise RuntimeError('boom')

    monkeypatch.setattr(app, '_review_batch_facts', _boom)
    report, code, skill = app.ModelPerformanceAnalyzer.analyze_and_generate(_review_completed())
    assert report == ''
    assert code == '' and skill == ''
    st = app.ModelPerformanceAnalyzer.last_status()
    assert st['ok'] is False
    assert 'boom' in st['error']
    assert '复盘失败' not in report


def test_review_local_only_stub_is_labelled(monkeypatch) -> None:
    """flash 返回空：本地补算段必须自带「非模型研判」标记，且状态是 local_only。"""
    monkeypatch.setattr(app, '_ds_flash_takeover', lambda *a, **k: '')
    report, _, _ = app.ModelPerformanceAnalyzer.analyze_and_generate(_review_completed())
    st = app.ModelPerformanceAnalyzer.last_status()
    assert st['model_produced'] is False
    assert st['local_only'] is True
    assert report.startswith(app.LOCAL_STUB_MARKER)
    assert '非模型研判' in report.split('\n')[0]


def test_review_success_is_marked_model_produced(monkeypatch) -> None:
    monkeypatch.setattr(
        app, '_ds_flash_takeover',
        lambda *a, **k: '模型表现自动分析报告\n市短市热对照 台账01 真实正文',
    )
    report, _, _ = app.ModelPerformanceAnalyzer.analyze_and_generate(_review_completed())
    st = app.ModelPerformanceAnalyzer.last_status()
    assert st['model_produced'] is True
    assert st['local_only'] is False
    assert not report.startswith(app.LOCAL_STUB_MARKER)
    assert '真实正文' in report


def test_review_does_not_write_into_the_repository(monkeypatch, tmp_path) -> None:
    """A review must leave the checkout untouched.

    `analyze_and_generate` writes `improvements_<date>.py` on every call. Before the
    shared `tests/conftest.py` fixture, each gate run left an untracked
    `code_improvements/` in the repository root, so the side effect dirtied every diff
    we looked at — and patching only this file's three call sites was not enough,
    because `scan_report_surfaces` reaches the same write through `post_match_review`.
    This asserts both halves: nothing appears in the repository, and the write still
    happens (redirected) rather than being dropped.
    """
    monkeypatch.setattr(
        app, '_ds_flash_takeover',
        lambda *a, **k: '模型表现自动分析报告\n台账01 真实正文',
    )
    repo_dir = ROOT / 'code_improvements'
    before = repo_dir.exists()
    app.ModelPerformanceAnalyzer.analyze_and_generate(_review_completed())
    assert repo_dir.exists() == before, 'the review wrote code_improvements/ into the repo'
    # And the write really happened, just redirected: one dated file under tmp_path.
    redirected = tmp_path / 'code_improvements'
    assert redirected.is_dir(), 'the write was dropped entirely instead of redirected'
    assert list(redirected.glob('improvements_*.py')), 'the review wrote no improvement file'


def test_review_log_line_only_says_generated_when_model_spoke(monkeypatch) -> None:
    """`✅ [模型复盘] …已生成并推送` 只许在 model_produced 时出现。"""
    src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    idx = src.index('✅ [模型复盘] deepseek-flash 表现分析')
    window = src[max(0, idx - 600): idx]
    assert "_mpa.get('model_produced')" in window
    # 旧的无条件成功行不许留着
    assert 'print("   ✅ [模型复盘] 表现分析+代码改进建议已生成并推送")' not in src


def test_per_match_review_gaps_always_returns_list() -> None:
    """`list + dict` 曾在**每一场**都抛 TypeError，导致推送的是那句 TypeError。"""
    out = app._per_match_review_gaps('随便一段正文', 2)
    assert isinstance(out, list)
    # 与另外两个 gap 函数拼接必须成立（它们都是 list）
    combined = app._model_report_view_gaps('') + app._ledger_gaps('', 2) + out
    assert isinstance(combined, list)


# ---------------------------------------------------------------------------
# (d) 每场每阶段只采集一次
# ---------------------------------------------------------------------------

def _stub_six(monkeypatch, calls, incomplete=True):
    """让记录「水位都齐」从而走主采集路径，并把真正抓取计到 calls 里。

    水位不齐会先走「整场重抓」分支（那是另一套一次性闸门，由
    `_WATER_FULL_SCRAPE_IDS` 管），这里要测的是主路径的阶段去重。
    """
    pack = {
        'available': True, 'channel': 'mobile',
        'asian_handicap': None, 'ou_line': None, 'odds_1x2': {},
        'boards_hit': {}, 'missing': ['asian_handicap', 'ou_line'],
        'source_report': {'ah': {'stage': 'fetch_failed', 'status': 405, 'bytes': 800,
                                 'channel': 'desktop', 'error': 'HTTP 405',
                                 'origin_blocked': True}},
        'fetch_urls': ['https://m.okooo.com/match/handicap.php?MatchID=1'],
    }
    if not incomplete:
        pack.update({
            'asian_handicap': -0.75, 'ou_line': 2.75,
            'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 3.5},
            'jc_handicap': -1, 'total_goals_odds': {str(i): 5.0 for i in range(8)},
            'htft_odds': {k: 5.0 for k in ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负')},
            'boards_hit': {}, 'missing': [],
        })
    def _fake(mid, *a, **k):
        calls.append(mid)
        return dict(pack)
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets', _fake)
    for name, repl in (
        ('_attach_mobile_exchange_pnl', lambda *a, **k: {}),
        ('_attach_mobile_lineup_if_missing', lambda *a, **k: {}),
        ('_attach_mobile_ah_ou_books', lambda *a, **k: {}),
        ('seed_mobile_ah_ou_books', lambda *a, **k: {}),
        ('apply_mobile_books_lines', lambda *a, **k: {}),
        ('detect_six_market_contradictions', lambda *a, **k: []),
        ('load_six_market_contradictions', lambda *a, **k: []),
        ('save_six_market_contradictions', lambda *a, **k: True),
    ):
        if hasattr(app, name):
            monkeypatch.setattr(app, name, repl)


def _water_complete_rec():
    """水位齐全的记录：跳过「整场重抓」分支，直接进主采集路径。

    通道标记必须写 'mobile'：准入闸门只认手机通道的标量盘口线
    （这正是 (b) 收紧的地方），缺了标记的值会被如实拒绝。
    """
    return {
        'okooo_match_id': '1338958',
        'pipeline_phase': 'stage1',
        'asian_handicap': -0.75, 'ah_channel': 'mobile',
        'ou_line': 2.75, 'ou_channel': 'mobile',
        'odds_1x2': {'home': 2.0, 'draw': 3.0, 'away': 3.5},
        'odds_1x2_channel': 'mobile',
        'jc_handicap': -1,
        'total_goals_odds': {str(i): 5.0 for i in range(8)},
        'htft_odds': {k: 5.0 for k in ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负')},
    }


def test_three_calls_one_stage_scrape_once_and_print_once(monkeypatch, capsys) -> None:
    """同阶段连续三次调用：只抓一次，只打一行 🔄。"""
    calls = []
    _stub_six(monkeypatch, calls)
    app._WATER_FULL_SCRAPE_IDS.clear()
    app._SIX_STAGE_PRINTS.clear()
    rec = _water_complete_rec()
    for _ in range(3):
        app._ensure_mobile_six(rec, '1338958')
    assert len(calls) == 1, f'scrape 调用了 {len(calls)} 次，应只 1 次'
    out = capsys.readouterr().out
    assert out.count('🔄 [手机六盘口]') == 1, out


def test_two_stages_scrape_twice(monkeypatch) -> None:
    """阶段变了才允许再抓一次。"""
    calls = []
    _stub_six(monkeypatch, calls)
    app._WATER_FULL_SCRAPE_IDS.clear()
    app._SIX_STAGE_PRINTS.clear()
    rec = _water_complete_rec()
    app._ensure_mobile_six(rec, '1338958')
    rec['pipeline_phase'] = 'stage2'
    app._ensure_mobile_six(rec, '1338958')
    assert len(calls) == 2, f'scrape 调用了 {len(calls)} 次，应 2 次'


def test_complete_pack_needs_no_extra_scrape(monkeypatch) -> None:
    calls = []
    _stub_six(monkeypatch, calls, incomplete=False)
    app._WATER_FULL_SCRAPE_IDS.clear()
    app._SIX_STAGE_PRINTS.clear()
    rec = _water_complete_rec()
    app._ensure_mobile_six(rec, '1338958')
    app._ensure_mobile_six(rec, '1338958')
    assert len(calls) == 1


def test_failure_still_logged_once_with_reasons(monkeypatch, capsys) -> None:
    """真失败时每阶段仍有一行，且带原因码。"""
    calls = []
    _stub_six(monkeypatch, calls)
    app._WATER_FULL_SCRAPE_IDS.clear()
    app._SIX_STAGE_PRINTS.clear()
    rec = _water_complete_rec()
    app._ensure_mobile_six(rec, '1338958')
    out = capsys.readouterr().out
    assert out.count('🔄 [手机六盘口]') == 1
    assert 'origin_blocked' in out
    assert 'fetch_failed/HTTP' not in out, '不许再出现状态码与阶段串台'


# ---------------------------------------------------------------------------
# (e) 没有 API key 时不许冒充研判
# ---------------------------------------------------------------------------

def test_no_api_key_is_not_a_judgment(monkeypatch) -> None:
    pipe = _pipe()
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_KEY', raising=False)
    raw = pipe.deepseek_chat_completion([{'role': 'user', 'content': 'x'}])
    assert isinstance(raw, str) and raw.strip(), '本地确定性文本仍要返回（契约不变）'
    assert pipe.completion_failed(raw) is True
    assert pipe.completion_error() == 'deepseek_api_key_missing'
    judged = pipe.DeepSeekAnalyzer.analyze_match_judgment({'match': 'A vs B'})
    assert judged['available'] is False
    assert judged['error'] == 'deepseek_api_key_missing'
    assert '本地确定性分析' not in judged['content']
    assert judged['content'] == ''
    assert pipe.run_learning_report({'x': 1}) == ''


def test_json_and_text_mode_agree_on_missing_key(monkeypatch) -> None:
    """同一条件两种模式必须同一结论——这正是原来的不对称缺陷。"""
    pipe = _pipe()
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_KEY', raising=False)
    text_res = pipe.deepseek_chat_completion([{'role': 'user', 'content': 'x'}])
    text_failed = pipe.completion_failed(text_res)
    json_res = pipe.deepseek_chat_completion([{'role': 'user', 'content': 'x'}], json_mode=True)
    json_failed = pipe.completion_failed(json_res)
    assert text_failed is True
    assert json_failed is True
    assert text_failed == json_failed


# ---------------------------------------------------------------------------
# (f) 告警送达与否必须如实
# ---------------------------------------------------------------------------

def test_verdict_line_has_three_distinct_outcomes() -> None:
    ok = app._stage2_verdict_line(True, '', 'A', '1')
    alert_ok = app._stage2_verdict_line(False, '熔断', 'A', '1', alert_sent=True)
    alert_bad = app._stage2_verdict_line(False, '熔断', 'A', '1', alert_sent=False)
    assert '已推送' in ok
    assert '仅发告警' in alert_ok and '已送达' in alert_ok
    assert '❌' in alert_bad and '告警也没送达' in alert_bad
    # 三种必须互不相同
    assert len({ok, alert_ok, alert_bad}) == 3


def test_fuse_branch_reads_send_return_value() -> None:
    """防盲区熔断分支必须看 send 的返回值，不能 try 后就当成功。"""
    src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    idx = src.index('防盲区熔断，未发研判正文')
    window = src[max(0, idx - 900): idx]
    assert 'is True' in window
    assert 'alert_sent=_fuse_sent' in src


# ---------------------------------------------------------------------------
# (g) 盘口齐 ≠ 比分31项齐
# ---------------------------------------------------------------------------

def test_31_block_line_distinguishes_boards_from_score_options() -> None:
    src = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert '这一关卡的是比分选项，不是盘口' in src
    assert '盘口齐不会让 31 表变齐' in src


# ---------------------------------------------------------------------------
# (9) 缓存作废
# ---------------------------------------------------------------------------

def test_old_v2_six_pack_is_rejected_for_board_parser() -> None:
    import okooo_mobile_cs as okooo
    from pathlib import Path
    old = {'cache_schema': 2, 'channel': 'mobile', 'match_id': '1', 'available': True,
           'asian_handicap': -0.5, 'ou_line': 2.5, 'odds_1x2': {'home': 2},
           'boards_hit': {}, 'missing': [], 'source_report': {}, 'fetch_urls': ['u']}
    assert okooo._cache_reject_reason(Path('x.json'), old) == 'cache_schema_board_parser'


def test_current_six_pack_with_new_boards_is_accepted() -> None:
    import okooo_mobile_cs as okooo
    from pathlib import Path
    new = {'cache_schema': okooo.CACHE_SCHEMA, 'channel': 'mobile', 'match_id': '1',
           'available': True, 'asian_handicap': -0.5, 'ou_line': 2.5,
           'odds_1x2': {'home': 2}, 'jc_handicap': -1,
           'total_goals_odds': {str(i): 5.0 for i in range(8)},
           'htft_odds': {k: 5.0 for k in ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负')},
           'jc_list_boards': {'hit': ['胜平负']},
           'boards_hit': {}, 'missing': [], 'source_report': {}, 'fetch_urls': ['u']}
    assert okooo._cache_reject_reason(Path('x.json'), new) == ''
    assert okooo.CACHE_SCHEMA >= 3


# ---------------------------------------------------------------------------
# (8, 测试部分) 六盘口闸门端到端：完整记录必须放行，缺任一必须拒绝
# ---------------------------------------------------------------------------

def _full_six_boards():
    return {
        'asian_handicap': -0.75, 'ou_line': 2.75,
        'odds_1x2': {'home': 1.91, 'draw': 3.86, 'away': 2.8},
        'jc_handicap': -1.0,
        'total_goals_odds': {str(i): 5.0 for i in range(8)},
        'htft_odds': {k: 5.0 for k in ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负')},
        'channel': 'mobile',
        'fetch_urls': ['https://m.okooo.com/match/handicap.php?MatchID=1'],
    }


def test_complete_record_passes_gate_through_real_builder() -> None:
    """完整记录经真实 payload 构建器 + build_cross_market_board + 闸门 => 放行。"""
    pipe = _pipe()
    rec = {'match': 'A vs B', 'six_market_mobile': _full_six_boards()}
    rec.update(_full_six_boards())
    payload = pipe.DeepSeekAnalyzer.build_jc_cross_only_payload(rec)
    payload['cross_market_board'] = pipe.DeepSeekAnalyzer.build_cross_market_board(rec)
    gate = pipe.DeepSeekAnalyzer.ensure_judgment_standard_sections(
        'DeepSeek 数据分析研判报告 正文', payload=payload,
    )
    assert gate['reject'] is False, gate['reject_reason']
    assert gate['missing_boards'] == []


@pytest.mark.parametrize('board_key,label', [
    ('asian_handicap', '亚盘让球'),
    ('ou_line', '大小球'),
    ('odds_1x2', '胜平负'),
    ('jc_handicap', '竞彩让球'),
    ('total_goals_odds', '总进球'),
    ('htft_odds', '半全场'),
])
def test_each_missing_board_rejects_with_its_own_name(board_key, label) -> None:
    """六个盘口逐个缺失：闸门必须拒绝，且原因点名正是那一块。"""
    pipe = _pipe()
    boards = _full_six_boards()
    boards.pop(board_key)
    rec = {'match': 'A vs B', 'six_market_mobile': boards}
    payload = pipe.DeepSeekAnalyzer.build_jc_cross_only_payload(rec)
    payload['cross_market_board'] = pipe.DeepSeekAnalyzer.build_cross_market_board(rec)
    gate = pipe.DeepSeekAnalyzer.ensure_judgment_standard_sections(
        'DeepSeek 数据分析研判报告 正文', payload=payload,
    )
    assert gate['reject'] is True
    assert gate['missing_boards'] == [label], (board_key, gate['missing_boards'])
    assert label in gate['reject_reason']


# ---------------------------------------------------------------------------
# (a) 闸门必须看三层并集，不能被非空的六盘口包遮住顶层
# ---------------------------------------------------------------------------

_GATE_REST = {
    'odds_1x2': {'home': 1.91, 'draw': 3.86, 'away': 2.8},
    'jc_handicap': -1.0,
    'total_goals_odds': {str(i): 5.0 for i in range(8)},
    'htft_odds': {k: 5.0 for k in ('胜胜', '胜平', '胜负', '平胜', '平平', '平负', '负胜', '负平', '负负')},
}
_GATE_LINES = {'asian_handicap': -0.75, 'ou_line': 2.75}
_GATE_NONE = {'asian_handicap': None, 'ou_line': None}


def _gate_with(top_lines, pack_lines):
    """亚盘/大小球可能落在顶层或六盘口包里；构建事实板时都不给（模拟包滞后）。"""
    pipe = _pipe()
    pay = dict(top_lines)
    pay['six_market_mobile'] = {**_GATE_REST, **pack_lines}
    # 事实板只从「没有 ah/ou 的那份记录」构建，正是用户现场的形状
    pay['cross_market_board'] = pipe.DeepSeekAnalyzer.build_cross_market_board(dict(_GATE_REST))
    return pipe.DeepSeekAnalyzer.ensure_judgment_standard_sections(
        'DeepSeek 数据分析研判报告 正文', payload=pay,
    )


def test_gate_sees_top_level_line_when_pack_lags() -> None:
    """只有顶层有线、六盘口包里还是 None：必须放行。

    历史实现里非空的 six_market_mobile 会把顶层整块遮住，这份有效报告被拒。
    """
    gate = _gate_with(_GATE_LINES, _GATE_NONE)
    assert gate['reject'] is False, gate['reject_reason']
    assert gate['missing_boards'] == []


def test_gate_sees_pack_line_when_top_level_absent() -> None:
    gate = _gate_with({}, _GATE_LINES)
    assert gate['reject'] is False, gate['reject_reason']


def test_gate_passes_when_line_in_both_layers() -> None:
    gate = _gate_with(_GATE_LINES, _GATE_LINES)
    assert gate['reject'] is False, gate['reject_reason']


def test_gate_still_rejects_when_line_in_no_layer() -> None:
    """三层都没有：必须仍然拒发，并点名这两块。"""
    gate = _gate_with({}, _GATE_NONE)
    assert gate['reject'] is True
    assert gate['missing_boards'] == ['亚盘让球', '大小球']
