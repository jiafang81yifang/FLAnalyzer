"""市均−竞彩（原始赔率差）四个新段的守卫。

四段全部由同一份 31 全表渲染，但列与口径各不相同：

  📈【市均***竞彩对比表】          31 项逐项 市均 / 竞彩 / 市-竞（原始赔率差）
  ★市短·胜平负比分矩阵             市短项按三向分列，比分列按 **市-竞升序**
  市热·胜平负比分矩阵              市热项按三向分列，比分列按 **市均升序**
  市均前15·进球赔率矩阵            市均最低 15 项铺进 0..7+ 八列，等宽对齐

它们与既有的 `📈【31项市均×竞彩对比表】`（概率 + 来源）和
`📊【市短矩阵｜市均隐含 > 竞彩隐含】`（归一化隐含概率差）**并存**。

## 简洁后的形状（本文件按此断言）

* 表格一律走 `build_grid_lines`：严格等宽、`|` 竖直到线；
* 单位只在列头（`市均赔率` / `市-竞差`），格子里只有数字；
* 比分列只列比分（赔率已在本段第一张 31 项表里给过）；
* 表格之外最多一行数据解读。

样本锚（用户给的同一场）：市短 胜8/13 平3/5 负6/13，均市-竞
胜-4.83 / 平-9.29 / 负-3.77，两两比对得分 胜+0/平-2/负+2，
对抗 1/-1/-1，首位对次位比值 1.28，胜负绝对值比值 1.28，落点负。
"""
from __future__ import annotations

import contextlib
import importlib
import io
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_boot = io.StringIO()
with contextlib.redirect_stdout(_boot), contextlib.redirect_stderr(_boot):
    pipe = importlib.import_module('blade_deepseek_pipeline')

DSA = pipe.DeepSeekAnalyzer
ALL31 = pipe.JCZQ_ODDS_ORDER
HOME, AWAY = '曼城', '阿森纳'

SAMPLE_MARKET: dict[str, float] = {
    '1-0': 14.55, '2-0': 18.30, '2-1': 10.11, '3-0': 27.18, '3-1': 16.45, '3-2': 16.82,
    '4-0': 54.23, '4-1': 32.46, '4-2': 33.36, '5-0': 80.00, '5-1': 70.00, '5-2': 88.00,
    '胜其它': 47.10,
    '0-0': 18.05, '1-1': 8.05, '2-2': 9.79, '3-3': 21.58, '平其它': 72.49,
    '0-1': 12.84, '0-2': 14.62, '0-3': 21.06, '0-4': 37.63, '0-5': 60.00,
    '1-2': 9.10, '1-3': 13.30, '1-4': 24.29, '1-5': 44.00, '2-3': 14.86, '2-4': 26.95,
    '2-5': 52.00, '负其它': 41.07,
}

SAMPLE_SHORT_DIFF: dict[str, float] = {
    '4-0': -20.77, '4-2': -6.64, '3-0': -5.82, '4-1': -2.54, '1-0': -1.45, '2-0': -0.70,
    '3-1': -0.55, '3-2': -0.18,
    '平其它': -17.51, '0-0': -8.95, '3-3': -1.42,
    '0-4': -12.37, '1-4': -3.71, '2-4': -3.05, '0-3': -1.94, '0-1': -1.16, '0-2': -0.38,
}

SAMPLE_HOT_DIFF: dict[str, float] = {
    '2-1': 0.45, '胜其它': 1.20,
    '1-1': 0.32, '2-2': 0.55,
    '1-2': 0.60, '1-3': 0.85, '2-3': 0.70, '负其它': 1.10,
}


def _jc_from(market: dict[str, float], diffs: dict[str, float]) -> dict[str, float]:
    return {score: round(odd - diffs.get(score, 0.0), 2) for score, odd in market.items()}


def _pack(market: dict[str, float], diffs: dict[str, float], **kwargs) -> dict:
    return DSA.build_theo_mkt_31_pack(
        [],
        jc_score_odds=_jc_from(market, diffs),
        extra_market_odds=market,
        lam_home=kwargs.pop('lam_home', 1.45),
        lam_away=kwargs.pop('lam_away', 1.10),
        market_provenance={k: 'desktop_cs' for k in market},
        **kwargs,
    )


def _sample_pack() -> dict:
    return _pack(SAMPLE_MARKET, {**SAMPLE_SHORT_DIFF, **SAMPLE_HOT_DIFF})


def _lines(pack: dict, renderer: str, **kwargs) -> list[str]:
    fn = getattr(DSA, renderer)
    kwargs.setdefault('home', HOME)
    kwargs.setdefault('away', AWAY)
    return list(fn(pack, **kwargs))


def _grid_rows(lines: list[str]) -> list[list[str]]:
    """把网格行拆成 cell（去掉首尾 `|`）。"""
    out = []
    for ln in lines:
        if re.match(r'^\|.*\|$', ln):
            out.append([c.strip() for c in ln.strip('|').split('|')])
    return out


def _cells(lines: list[str], first_col: str) -> list[str]:
    return [row[0] for row in _grid_rows(lines) if row and row[0] == first_col]


# --------------------------------------------------------------------------- #
# 段名与登记
# --------------------------------------------------------------------------- #
def test_new_section_names_are_literal_and_registered() -> None:
    """段名逐字：`***` 是三个半角星号，不是 `×`。"""
    assert _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')[0] == '📈【市均***竞彩对比表】'
    assert _lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines')[0] == '★市短·胜平负比分矩阵'
    assert _lines(_sample_pack(), 'format_jingcai_hot_wdl_matrix_tg_lines')[0] == '市热·胜平负比分矩阵'
    assert _lines(_sample_pack(), 'format_market_goal_top_matrix_tg_lines')[0] == '市均前15·进球赔率矩阵'
    for name in ('市均***竞彩对比表', '★市短·胜平负比分矩阵',
                 '市热·胜平负比分矩阵', '市均前15·进球赔率矩阵'):
        assert name in DSA.REGISTERED_TG_SECTIONS, name
        assert name not in DSA.REQUIRED_TG_SECTIONS, f'{name} 不可能是必备段'


def test_existing_sections_are_untouched() -> None:
    source = (ROOT / 'blade_deepseek_pipeline.py').read_text(encoding='utf-8')
    assert '📈【31项市均×竞彩对比表】' in source
    assert '📊【市短矩阵｜市均隐含 > 竞彩隐含】' in source
    assert '市均***竞彩对比表' != '市均×竞彩对比表'


def test_asterisks_survive_the_html_renderer() -> None:
    body = '\n'.join(_lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines'))
    out = DSA.format_matrix_dual_prejudge_html(body)
    assert '市均***竞彩对比表' in out, out[:200]
    assert out.count('*') == 3, out.count('*')


# --------------------------------------------------------------------------- #
# 段 1：市均***竞彩对比表
# --------------------------------------------------------------------------- #
def test_31_rows_cover_every_option_in_the_user_order() -> None:
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')
    rows = _grid_rows(lines)
    body = [r[0] for r in rows[1:]]  # 去掉列头
    assert len(body) == 31, len(body)
    assert body == ALL31, body


def test_diff_is_raw_odds_minus_not_implied_probability() -> None:
    """`市-竞差` 列 = 原始赔率差；不是隐含概率差。"""
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')
    rows = _grid_rows(lines)
    header = rows[0]
    assert header == ['比分', '市均赔率', '竞彩赔率', '市-竞差'], header
    row = next(r for r in rows[1:] if r[0] == '1-0')
    market, jc, diff = float(row[1]), float(row[2]), float(row[3])
    assert (market, jc) == (14.55, 16.00)
    assert diff == pytest.approx(market - jc, abs=0.005)
    assert diff == pytest.approx(-1.45, abs=0.005)


def test_short_list_line_has_no_truncation_and_agrees_on_the_count() -> None:
    """列出的项数 == 声称的项数 == 实际负数项数。"""
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')
    tail = next(ln for ln in lines if '市短共' in ln)
    claimed = int(re.search(r'市短共(\d+)项', tail).group(1))
    listed = [s.strip() for s in tail.split('：', 1)[1].split('/') if s.strip()]
    rows = _grid_rows(lines)[1:]
    negatives = sum(1 for r in rows if r[3].startswith('-') and r[3] != GRID_NOT_APPLICABLE)
    assert claimed == len(listed) == negatives, (claimed, len(listed), negatives)
    assert '...' not in tail and '…' not in tail
    assert claimed == 17, tail


GRID_NOT_APPLICABLE = '不适用'


def test_missing_odds_rows_say_delta_not_applicable_and_stay_out_of_short() -> None:
    market = {k: v for k, v in SAMPLE_MARKET.items() if k != '1-0'}
    diffs = {k: v for k, v in {**SAMPLE_SHORT_DIFF, **SAMPLE_HOT_DIFF}.items() if k != '1-0'}
    lines = _lines(_pack(market, diffs), 'format_market_vs_jingcai_odds_tg_lines')
    row = next(r for r in _grid_rows(lines)[1:] if r[0] == '1-0')
    assert GRID_NOT_APPLICABLE in row, row
    assert not any('0.00' == c for c in row[1:]), row
    tail = next(ln for ln in lines if '市短共' in ln)
    assert '1-0' not in tail, tail


def test_whole_table_unformed_when_nothing_is_computable() -> None:
    lines = _lines({}, 'format_market_vs_jingcai_odds_tg_lines')
    assert lines[0] == '📈【市均***竞彩对比表】'
    assert len(lines) == 2 and '未形成' in lines[1], lines


def test_source_and_channel_come_from_the_record_or_dash() -> None:
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')
    caliber = next(ln for ln in lines if ln.startswith(' 口径：'))
    assert '源=—' in caliber and '通道=—' in caliber

    pack = dict(_sample_pack())
    pack['cs_route_used'] = '代理#3'
    pack['correct_score_channel'] = 'okooo_cs'
    caliber = next(ln for ln in _lines(pack, 'format_market_vs_jingcai_odds_tg_lines')
                   if ln.startswith(' 口径：'))
    assert '通道=代理#3' in caliber and '源=okooo_cs' in caliber


def test_team_line_is_omitted_when_names_are_absent() -> None:
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines', home='', away='')
    assert not any(' 对 ' in ln for ln in lines), lines[:4]
    assert not any('未知' in ln for ln in lines)


def test_numbers_follow_the_record_not_a_constant() -> None:
    """两份不同记录 → 31 行数值必须跟着变。"""
    pack_b = _pack({k: round(v * 1.7, 2) for k, v in SAMPLE_MARKET.items()}, {})
    a = next(r for r in _grid_rows(_lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines'))[1:]
             if r[0] == '2-1')
    b = next(r for r in _grid_rows(_lines(pack_b, 'format_market_vs_jingcai_odds_tg_lines'))[1:]
             if r[0] == '2-1')
    assert a[1] != b[1]
    assert float(b[1]) == pytest.approx(10.11 * 1.7, abs=0.01)


# --------------------------------------------------------------------------- #
# 段 2：★市短·胜平负比分矩阵 —— 样本锚
# --------------------------------------------------------------------------- #
def test_star_short_matches_the_sample_count_line() -> None:
    lines = _lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines')
    # 判据被改坏时整段会退化成「未形成」，这里要明确报红而不是靠 IndexError。
    assert len(lines) > 2 and '未形成' not in '\n'.join(lines), lines
    rows = _grid_rows(lines)
    assert rows[0][1] == '市短/总项', rows[0]
    by_dir = {r[0]: r for r in rows[1:]}
    assert by_dir['【胜】'][1] == '8/13', by_dir
    assert by_dir['【平】'][1] == '3/5', by_dir
    assert by_dir['【负】'][1] == '6/13', by_dir


def test_star_short_matches_the_sample_rows_verbatim() -> None:
    """三向的均市-竞与比分顺序逐字对上样本。"""
    lines = _lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines')
    by_dir = {r[0]: r for r in _grid_rows(lines)[1:]}
    assert by_dir['【胜】'][2] == '-4.83', by_dir['【胜】']
    assert by_dir['【胜】'][3] == '4-0 / 4-2 / 3-0 / 4-1 / 1-0 / 2-0 / 3-1 / 3-2', by_dir['【胜】']
    assert by_dir['【平】'][2] == '-9.29', by_dir['【平】']
    assert by_dir['【平】'][3] == '平其它 / 0-0 / 3-3', by_dir['【平】']
    assert by_dir['【负】'][2] == '-3.77', by_dir['【负】']
    assert by_dir['【负】'][3] == '0-4 / 1-4 / 2-4 / 0-3 / 0-1 / 0-2', by_dir['【负】']


def test_star_short_score_column_order_is_diff_ascending() -> None:
    pack = _sample_pack()
    rows = {r['score']: r['diff'] for r in DSA._raw_odds_rows(pack)}
    lines = _lines(pack, 'format_jingcai_short_wdl_matrix_tg_lines')
    for row in _grid_rows(lines)[1:]:
        seq = [s.strip() for s in row[3].split('/')]
        diffs = [rows[s] for s in seq]
        assert diffs == sorted(diffs), (row[0], seq, diffs)


def test_star_short_sample_anchor_numbers() -> None:
    """4.83 / 9.29 / 3.77 三个数复现样本的五个派生结论。"""
    abs_means = {'胜': 4.83, '平': 9.29, '负': 3.77}
    pair = DSA.score_market_pairwise(abs_means)
    assert pair['scores'] == {'胜': 0, '平': -2, '负': 2}, pair
    ordered = sorted(abs_means.items(), key=lambda kv: kv[1])
    assert ordered[0] == ('负', 3.77)
    assert round(ordered[1][1] / ordered[0][1], 2) == 1.28
    assert round(abs_means['胜'] / abs_means['负'], 2) == 1.28
    top = max(pair['scores'].values())
    assert [d for d, v in pair['scores'].items() if v == top] == ['负']


def test_star_short_renders_the_sample_numbers_with_sample_data() -> None:
    """真实渲染：解读那一行必须带上样本的五个派生结论。"""
    text = '\n'.join(_lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines'))
    for token in ('绝对均市-竞 胜4.83/平9.29/负3.77', '得分胜+0/平-2/负+2',
                  '对抗1/-1/-1', '首位负对次位1.28', '胜负比1.28',
                  '对照落点负', '不改方向锁'):
        assert token in text, (token, text)


def test_pairwise_rule_minus_one_for_the_larger_abs() -> None:
    assert DSA.score_market_pairwise({'胜': 1.0, '平': 2.0, '负': 3.0})['scores'] == {
        '胜': 2, '平': 0, '负': -2,
    }
    assert DSA.score_market_pairwise({'胜': 2.0, '平': 2.0, '负': 2.0})['scores'] == {
        '胜': 0, '平': 0, '负': 0,
    }
    assert DSA.score_market_pairwise({'胜': 4.83, '平': 9.29, '负': 3.77})['scores'] == {
        '胜': 0, '平': -2, '负': 2,
    }


def test_shape_is_never_numbered_without_a_confirmed_scheme() -> None:
    text = '\n'.join(_lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines'))
    assert '形态未判定' in text, text
    assert not re.search(r'形态\d', text), text
    shape = DSA.decide_market_shape({'胜': 4.83, '平': 9.29, '负': 3.77},
                                    {'胜': 8, '平': 3, '负': 6})
    assert shape['form_no'] is None
    assert shape['form_name'] == '主辅反转形态', shape
    assert shape['hit_direction'] == '负'


def test_missing_odds_are_reported_not_silently_dropped() -> None:
    market = {k: v for k, v in SAMPLE_MARKET.items() if k != '0-0'}
    pack = _pack(market, SAMPLE_SHORT_DIFF)
    lines = _lines(pack, 'format_jingcai_short_wdl_matrix_tg_lines')
    joined = '\n'.join(lines)
    assert '缺赔' in joined and '0-0' in joined.split('缺赔', 1)[1], joined
    # 缺赔的项不进比分列。
    scores = [r[3] for r in _grid_rows(lines)[1:]]
    assert all('0-0' not in s for s in scores), scores


def test_unformed_when_no_direction_has_a_short_item() -> None:
    lines = _lines(_pack(SAMPLE_MARKET, {}), 'format_jingcai_short_wdl_matrix_tg_lines')
    assert lines[0] == '★市短·胜平负比分矩阵'
    assert len(lines) == 2 and '未形成（三向均无市短项）' in lines[1], lines


def test_rendering_never_touches_the_direction_lock() -> None:
    pack = _sample_pack()
    payload = {
        'theo_mkt_31_pack': pack,
        'direction_lock': {'locked': True, 'direction': '胜', 'scores': ['2-1', '1-0']},
        'first_score': '2-1',
    }
    before = (payload['direction_lock']['direction'], payload['first_score'],
              tuple(payload['direction_lock']['scores']))
    for renderer in (
        'format_market_vs_jingcai_odds_tg_lines',
        'format_jingcai_short_wdl_matrix_tg_lines',
        'format_jingcai_hot_wdl_matrix_tg_lines',
        'format_market_goal_top_matrix_tg_lines',
        'format_hot_short_distribution_tg_lines',
    ):
        getattr(DSA, renderer)(pack, **payload)
    DSA.build_deepseek_only_tg_sections(ds_payload=payload)
    after = (payload['direction_lock']['direction'], payload['first_score'],
             tuple(payload['direction_lock']['scores']))
    assert after == before
    assert DSA.matrix_direction_lock(payload)['direction'] == '胜'


# --------------------------------------------------------------------------- #
# 段 3：市热·胜平负比分矩阵
# --------------------------------------------------------------------------- #
def test_hot_counts_and_rows_match_the_sample() -> None:
    lines = _lines(_sample_pack(), 'format_jingcai_hot_wdl_matrix_tg_lines')
    assert ' 计数：胜2 平2 负4' in lines, lines
    by_dir = {r[0]: r for r in _grid_rows(lines)[1:]}
    assert by_dir['【胜】'][1] == '2' and by_dir['【胜】'][2] == '2-1 / 胜其它', by_dir
    assert by_dir['【平】'][2] == '1-1 / 2-2', by_dir
    assert by_dir['【负】'][2] == '1-2 / 1-3 / 2-3 / 负其它', by_dir


def test_hot_score_column_order_is_market_ascending() -> None:
    pack = _sample_pack()
    odds = {r['score']: r['market'] for r in DSA._raw_odds_rows(pack)}
    lines = _lines(pack, 'format_jingcai_hot_wdl_matrix_tg_lines')
    for row in _grid_rows(lines)[1:]:
        seq = [s.strip() for s in row[2].split('/')]
        vals = [odds[s] for s in seq]
        assert vals == sorted(vals), (row[0], seq, vals)
    neg = next(r for r in _grid_rows(lines)[1:] if r[0] == '【负】')
    assert neg[2].startswith('1-2'), neg


def test_hot_caliber_line_drops_the_false_top15_claim() -> None:
    text = '\n'.join(_lines(_sample_pack(), 'format_jingcai_hot_wdl_matrix_tg_lines'))
    assert '市均最低前15' not in text, text
    assert '前15' not in text, text
    assert '市热=市均−竞彩为正，共8项' in text, text


def test_hot_and_short_are_complementary_and_disjoint() -> None:
    rows = DSA._raw_odds_rows(_sample_pack())
    short = {r['score'] for r in rows if r['diff'] is not None and r['diff'] < 0}
    hot = {r['score'] for r in rows if r['diff'] is not None and r['diff'] > 0}
    nonzero = {r['score'] for r in rows if r['diff'] not in (None, 0.0)}
    zero = {r['score'] for r in rows if r['diff'] == 0.0}
    assert not (short & hot)
    assert short | hot == nonzero
    assert zero and not (zero & (short | hot))
    assert len(short) + len(hot) + len(zero) == 31


def test_hot_prints_every_direction_row_even_when_empty() -> None:
    diffs = {'0-0': 1.00, '1-1': 0.50, '0-1': 0.80, '0-2': 0.40}
    lines = _lines(_pack(SAMPLE_MARKET, diffs), 'format_jingcai_hot_wdl_matrix_tg_lines')
    assert '未形成' not in '\n'.join(lines), lines
    by_dir = {r[0]: r for r in _grid_rows(lines)[1:]}
    assert by_dir['【胜】'][1] == '0' and by_dir['【胜】'][2] == '（无）', by_dir


# --------------------------------------------------------------------------- #
# 段 4：市均前15·进球赔率矩阵
# --------------------------------------------------------------------------- #
def test_goal_grid_cells_match_the_sample_verbatim() -> None:
    text = '\n'.join(_lines(_sample_pack(), 'format_market_goal_top_matrix_tg_lines'))
    for token in ('0-0', '1-0', '2-0', '2-1', '1-1', '0-1', '0-2',
                  '1-2 / 0-3', '9.10/21.06', '18.05', '14.55', '18.30', '10.11', '8.05'):
        assert token in text, (token, text)
    assert '第1页 0-3球' in text and '第2页 4-7+' in text
    for direction in ('胜', '平', '负'):
        rows = [ln for ln in text.splitlines() if ln.lstrip().startswith(f'| 【{direction}】')]
        assert len(rows) == 2, (direction, rows)


def test_goal_mean_row_is_the_true_arithmetic_mean() -> None:
    built = DSA.build_market_goal_top_matrix(_sample_pack())
    means = [page['column_means'] for page in built['pages']]
    assert means[0] == (18.05, 13.70, 13.66, 13.42), means
    assert means[1][:3] == (13.18, 15.84, 21.58), means
    assert means[1][3] is None


def test_goal_mean_row_is_not_mean_divided_by_goal_count() -> None:
    """将来谁把「÷球数」改回来，这条就红。"""
    built = DSA.build_market_goal_top_matrix(_sample_pack())
    means = list(built['pages'][0]['column_means']) + list(built['pages'][1]['column_means'])
    assert means == [18.05, 13.70, 13.66, 13.42, 13.18, 15.84, 21.58, None], means
    divided = [6.02, 13.70, 6.83, 4.47, 3.29, 3.17, 3.60, None]
    assert means != divided, (means, divided)
    assert means[0] == 18.05, means


def test_goal_summary_numbers_match_the_sample() -> None:
    """表格之外那一行必须带齐用户点名的每个数字。"""
    lines = _lines(_sample_pack(), 'format_market_goal_top_matrix_tg_lines')
    summary = next(ln for ln in lines if ln.startswith(' 计数 胜'))
    for token in ('胜5/平4/负6', '入选15', '胜33.3%', '平26.7%', '负40.0%', '最多【负】',
                  '少球6席40.0%', '多球9席60.0%', '主导【多球】',
                  '1-1 8.05', '2球', '少球均赔14.40', '多球均赔14.79'):
        assert token in summary, (token, summary)


def test_goal_low_ball_boundary_is_le_two() -> None:
    assert DSA.MARKET_GOAL_LOW_MAX == 2
    built = DSA.build_market_goal_top_matrix(_sample_pack())
    assert built['low_count'] == 6 and built['high_count'] == 9
    assert built['low_mean'] == pytest.approx(14.40, abs=0.005)
    assert built['high_mean'] == pytest.approx(14.79, abs=0.005)


def test_goal_unpriced_unjudged_items_are_flagged_not_invented() -> None:
    text = '\n'.join(_lines(_sample_pack(), 'format_market_goal_top_matrix_tg_lines'))
    assert '未判定（判据未确认）' in text, text
    for invented in ('胜穿透5', '负穿透3', '大球扎实核9', '少球扎实核5'):
        assert invented not in text, invented


def test_goal_matrix_unformed_without_market_odds() -> None:
    lines = _lines({}, 'format_market_goal_top_matrix_tg_lines')
    assert lines[0] == '市均前15·进球赔率矩阵'
    assert len(lines) == 2 and '未形成' in lines[1]


# --------------------------------------------------------------------------- #
# 装配
# --------------------------------------------------------------------------- #
def test_all_five_sections_reach_the_assembled_report() -> None:
    pack = _sample_pack()
    text = '\n'.join(DSA.build_deepseek_only_tg_sections(
        title_line='📌 t',
        theo_mkt_31_tg_lines=DSA.format_theo_mkt_31_tg_lines(pack),
        ds_payload={'theo_mkt_31_pack': pack},
        heat_tg_lines=['HEATM'],
        home=HOME, away=AWAY,
    ))
    order = [text.find(n) for n in (
        '31项市均×竞彩对比表', '市均***竞彩对比表', '市短矩阵',
        '市热矩阵', '★市短·胜平负比分矩阵', '市热·胜平负比分矩阵',
        '【市热·市短分布｜冷门窗口】', '市均前15·进球赔率矩阵', 'HEATM',
    )]
    assert all(i >= 0 for i in order), order
    assert order == sorted(order), order


def test_each_new_section_is_its_own_section_boundary() -> None:
    pack = _sample_pack()
    body = '\n'.join(DSA.build_deepseek_only_tg_sections(
        ds_payload={'theo_mkt_31_pack': pack}, home=HOME, away=AWAY,
    ))
    for name in ('市均***竞彩对比表', '★市短·胜平负比分矩阵',
                 '市热·胜平负比分矩阵', '市均前15·进球赔率矩阵',
                 '【市热·市短分布｜冷门窗口】'):
        assert not DSA._section_is_empty(body, name), name
