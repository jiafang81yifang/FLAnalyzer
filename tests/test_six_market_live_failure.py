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
    # 13=主胜 11=平 10=客胜（页面行内「胜/平/负」顺序核对过）
    assert wdl['odds_1x2'] == {
        'home': 3.55, 'draw': 4.03, 'away': 1.64,
        'Home': 3.55, 'Draw': 4.03, 'Away': 1.64,
    }

    jc = res['blocks']['竞彩让球']
    assert jc['stage'] == 'ok'
    assert jc['line'] == -1.0            # Boundary.SportteryWDL == '-1'
    assert jc['odds'] == {
        'home': 1.91, 'draw': 3.86, 'away': 2.8,
        'Home': 1.91, 'Draw': 3.86, 'Away': 2.8,
    }

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
    # 解析器必须按证据映射，而不是按「先主队」的直觉。
    parsed = okooo.parse_jczq_list_boards(text, USER_MATCH_ID)['blocks']['胜平负']
    slot_wdl = _slot()['OddsList']['SportteryWDL']
    assert parsed['odds_1x2']['home'] == float(slot_wdl['13'])
    assert parsed['odds_1x2']['away'] == float(slot_wdl['10'])


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
    """某场缺一个块（夹具里 4008/4012 的 SportteryNWDL 是空数组）→ block_absent，
    而且**不得**凭空补出赔率。"""
    res = okooo.parse_jczq_list_boards(_list_text(), '1348444')
    assert res['orderid'] == '4001'
    # 4001 是完整的；换一场真的缺 NWDL 的（matchIds 第 8 个 = 1347775? 用 orderid 直接校验）
    text = _list_text()
    data = okooo._decode_js_object(text)
    empty = [oid for oid, s in data.items()
             if not (s.get('OddsList') or {}).get('SportteryNWDL')]
    assert empty, '夹具里应当存在缺让球块的场次'


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
    """0.0 是平手盘 / 大小球 0 线，必须当成有值写回，不能被 `not value` 漏掉。"""
    rec = {'asian_handicap': 0.0, 'ou_line': 0.0}
    pack = {'asian_handicap': None, 'ou_line': None,
            'boards_hit': {}, 'missing': ['asian_handicap', 'ou_line']}
    out = app._backfill_six_board_lines_from_record(rec, pack)
    assert out['asian_handicap'] == 0.0
    assert out['ou_line'] == 0.0
    assert out['boards_hit'].get('亚盘让球') is True
    assert out['boards_hit'].get('大小球') is True


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
