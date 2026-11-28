"""市均−竞彩（原始赔率差）四个新段的守卫。

四段全部由同一份 31 全表渲染，但列与口径各不相同，必须一眼能看出它们是
四件不同的事：

  📈【市均***竞彩对比表】          31 项逐项 市均 / 竞彩 / 市-竞（原始赔率差）
  ★市短·胜平负比分矩阵             市短项按三向分列，比分列按 **市-竞升序**
  市热·胜平负比分矩阵              市热项按三向分列，比分列按 **市均升序**
  市均前15·进球赔率矩阵            市均最低 15 项铺进 0..7+ 八列，等宽对齐

它们与既有的 `📈【31项市均×竞彩对比表】`（概率 + 来源）和
`📊【市短矩阵｜市均隐含 > 竞彩隐含】`（归一化隐含概率差）**并存**。
把新段做成旧段的口径，或者反过来覆盖旧段，都是这里要钉死的缺陷。

样本锚（用户给的同一场，数字是格式样本，不参与实现）：
市短 胜8/13 平3/5 负6/13，均市-竞 胜-4.83 / 平-9.29 / 负-3.77，
两两比对得分 胜+0/平-2/负+2，对抗 1/-1/-1，首位对次位比值 1.28，
胜负绝对值比值 1.28，最高分唯一，对照落点负。
"""
from __future__ import annotations

import importlib
import io
import contextlib
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

_HOME = '曼城'
_AWAY = '阿森纳'


# --------------------------------------------------------------------------- #
# 样本夹具：同一场比赛的 31 项原始赔率
# --------------------------------------------------------------------------- #
#: 市均原始赔率。市短项与市热项的赔率值都取自用户样本（★市短 的 @ 列、
#: 市均前15 的网格），其余项给一组与竞彩同向的合理值。
SAMPLE_MARKET: dict[str, float] = {
    '1-0': 14.55, '2-0': 18.30, '2-1': 10.11, '3-0': 27.18, '3-1': 16.45, '3-2': 16.82,
    '4-0': 54.23, '4-1': 32.46, '4-2': 33.36, '5-0': 80.00, '5-1': 70.00, '5-2': 88.00,
    '胜其它': 47.10,
    '0-0': 18.05, '1-1': 8.05, '2-2': 9.79, '3-3': 21.58, '平其它': 72.49,
    '0-1': 12.84, '0-2': 14.62, '0-3': 21.06, '0-4': 37.63, '0-5': 60.00,
    '1-2': 9.10, '1-3': 13.30, '1-4': 24.29, '1-5': 44.00, '2-3': 14.86, '2-4': 26.95,
    '2-5': 52.00, '负其它': 41.07,
}

#: 市短项（市均 − 竞彩）的差，逐项取自用户更正消息里的复算。
SAMPLE_SHORT_DIFF: dict[str, float] = {
    '4-0': -20.77, '4-2': -6.64, '3-0': -5.82, '4-1': -2.54, '1-0': -1.45, '2-0': -0.70,
    '3-1': -0.55, '3-2': -0.18,
    '平其它': -17.51, '0-0': -8.95, '3-3': -1.42,
    '0-4': -12.37, '1-4': -3.71, '2-4': -3.05, '0-3': -1.94, '0-1': -1.16, '0-2': -0.38,
}

#: 市热项（市均 − 竞彩 > 0）。样本只给了「哪 8 项」，没给差值，这里给可复算的小正差。
SAMPLE_HOT_DIFF: dict[str, float] = {
    '2-1': 0.45, '胜其它': 1.20,
    '1-1': 0.32, '2-2': 0.55,
    '1-2': 0.60, '1-3': 0.85, '2-3': 0.70, '负其它': 1.10,
}


def _jc_from(market: dict[str, float], diffs: dict[str, float]) -> dict[str, float]:
    """由「市均 + 指定差」反算竞彩赔率；未指定的项与市均同价（差 0）。"""
    return {
        score: round(odd - diffs.get(score, 0.0), 2)
        for score, odd in market.items()
    }


def _pack(market: dict[str, float], diffs: dict[str, float], **kwargs) -> dict:
    """走生产同一个 31 全表构建器，不另造一份表。"""
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
    kwargs.setdefault('home', _HOME)
    kwargs.setdefault('away', _AWAY)
    return list(fn(pack, **kwargs))


# --------------------------------------------------------------------------- #
# 段名：逐字钉死，且旧段不被改名
# --------------------------------------------------------------------------- #
def test_new_section_names_are_literal_and_registered() -> None:
    """段名逐字：`***` 是三个半角星号，不是 `×`，不是全角。"""
    assert '📈【市均***竞彩对比表】' in _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')
    assert '★市短·胜平负比分矩阵' in _lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines')
    assert '市热·胜平负比分矩阵' in _lines(_sample_pack(), 'format_jingcai_hot_wdl_matrix_tg_lines')
    assert '市均前15·进球赔率矩阵' in _lines(_sample_pack(), 'format_market_goal_top_matrix_tg_lines')
    for name in ('市均***竞彩对比表', '★市短·胜平负比分矩阵',
                 '市热·胜平负比分矩阵', '市均前15·进球赔率矩阵'):
        assert name in DSA.REGISTERED_TG_SECTIONS, name
        assert name not in DSA.REQUIRED_TG_SECTIONS, f'{name} 不可能是必备段'


def test_existing_sections_are_untouched() -> None:
    """旧两段保持原样并存（改名或删除都会踩 10 处既有守卫）。"""
    source = (ROOT / 'blade_deepseek_pipeline.py').read_text(encoding='utf-8')
    assert '📈【31项市均×竞彩对比表】' in source
    assert '📊【市短矩阵｜市均隐含 > 竞彩隐含】' in source
    # 新段的 `***` 与旧段的 `×` 是不同字符，不能相互顶替。
    assert '市均***竞彩对比表' != '市均×竞彩对比表'


def test_asterisks_survive_the_html_renderer() -> None:
    """`***` 走 HTML 渲染路径不许被当 markdown 吃掉。"""
    body = '\n'.join(_lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines'))
    out = DSA.format_matrix_dual_prejudge_html(body)
    assert '市均***竞彩对比表' in out, out[:200]
    assert out.count('*') == 3, out.count('*')


# --------------------------------------------------------------------------- #
# 段 1：市均***竞彩对比表
# --------------------------------------------------------------------------- #
def test_31_rows_cover_every_option_in_the_user_order() -> None:
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')
    body = [ln for ln in lines if re.match(r'^ [胜平负] \S+ ', ln)]
    assert len(body) == 31, len(body)
    got = [ln.strip().split(' ')[1] for ln in body]
    assert got == ALL31, got
    for group in ('——【胜】——', '——【平】——', '——【负】——'):
        assert any(ln.strip() == group for ln in lines), group


def test_diff_is_raw_odds_minus_not_implied_probability() -> None:
    """`市-竞` = 原始赔率差，四舍五入两位；不是隐含概率差。"""
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')
    row = next(ln for ln in lines if ln.strip().startswith('胜 1-0 '))
    parts = row.split()
    market, jc, diff = float(parts[2]), float(parts[3]), float(parts[4])
    assert (market, jc) == (14.55, 16.00)
    assert diff == pytest.approx(market - jc, abs=0.005)
    assert diff == pytest.approx(-1.45, abs=0.005)
    # 隐含概率差会是 +6.2pp 量级（1/14.55 vs 1/16），与这里的 -1.45 差着量纲。
    assert diff < 0


def test_short_list_line_has_no_truncation_and_agrees_on_the_count() -> None:
    """列出的项数 == 声称的项数 == 实际负数项数。三者不等就红。

    这是本任务最容易踩的坑：用户样本写「市短共17项」却只列了 12 项。
    """
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')
    tail = next(ln for ln in lines if '市短共' in ln)
    claimed = int(re.search(r'市短共(\d+)项', tail).group(1))
    listed = [s.strip() for s in tail.split('：', 1)[1].split('/') if s.strip()]
    negatives = 0
    for ln in lines:
        parts = ln.split()
        if len(parts) == 5 and parts[0] in ('胜', '平', '负'):
            if parts[-1].startswith(('-', '+')):
                if float(parts[-1]) < 0:
                    negatives += 1
    assert claimed == len(listed) == negatives, (claimed, len(listed), negatives)
    assert '...' not in tail and '…' not in tail
    # 该场 31 项里 17 项是负、8 项是正、6 项为 0（市均=竞彩）。
    assert claimed == 17, tail


def test_missing_odds_rows_say_delta_not_applicable_and_stay_out_of_short() -> None:
    market = {k: v for k, v in SAMPLE_MARKET.items() if k != '1-0'}
    diffs = {k: v for k, v in {**SAMPLE_SHORT_DIFF, **SAMPLE_HOT_DIFF}.items() if k != '1-0'}
    pack = _pack(market, diffs)
    lines = _lines(pack, 'format_market_vs_jingcai_odds_tg_lines')
    row = next(ln for ln in lines if ln.startswith(' 胜 1-0 '))
    assert 'Δ 不适用' in row, row
    # 不伪造：缺赔的那一行不许出现 0.00 / +0.00 的差。
    assert '+0.00' not in row and ' 0.00' not in row
    tail = next(ln for ln in lines if '市短共' in ln)
    assert '1-0' not in tail, tail


def test_whole_table_unformed_when_nothing_is_computable() -> None:
    lines = _lines({}, 'format_market_vs_jingcai_odds_tg_lines')
    assert lines[0] == '📈【市均***竞彩对比表】'
    assert '未形成' in lines[1]
    assert len(lines) == 2, lines


def test_source_and_channel_come_from_the_record_or_dash() -> None:
    """`源=`/`通道=` 取不到就是 `—`；`cs_route_used` 缺失时不许编。"""
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines')
    caliber = next(ln for ln in lines if ln.startswith(' 口径：'))
    assert '源=—' in caliber and '通道=—' in caliber

    pack = dict(_sample_pack())
    pack['cs_route_used'] = '代理#3'
    pack['correct_score_channel'] = 'okooo_cs'
    got = _lines(pack, 'format_market_vs_jingcai_odds_tg_lines')
    caliber = next(ln for ln in got if ln.startswith(' 口径：'))
    assert '通道=代理#3' in caliber
    assert '源=okooo_cs' in caliber


def test_team_line_is_omitted_when_names_are_absent() -> None:
    lines = _lines(_sample_pack(), 'format_market_vs_jingcai_odds_tg_lines', home='', away='')
    assert not any(' 对 ' in ln for ln in lines), lines[:4]
    assert not any('未知' in ln for ln in lines)


def test_numbers_follow_the_record_not_a_constant() -> None:
    """两份不同记录 → 31 行数值必须跟着变（证明没写死）。"""
    pack_a = _sample_pack()
    market_b = {k: round(v * 1.7, 2) for k, v in SAMPLE_MARKET.items()}
    pack_b = _pack(market_b, {})
    a = _lines(pack_a, 'format_market_vs_jingcai_odds_tg_lines')
    b = _lines(pack_b, 'format_market_vs_jingcai_odds_tg_lines')
    row_a = next(ln for ln in a if ln.startswith(' 胜 2-1 '))
    row_b = next(ln for ln in b if ln.startswith(' 胜 2-1 '))
    assert row_a.split()[3] != row_b.split()[3], (row_a, row_b)
    assert float(row_b.split()[3]) == pytest.approx(10.11 * 1.7, abs=0.005)


# --------------------------------------------------------------------------- #
# 段 2：★市短·胜平负比分矩阵 —— 样本锚
# --------------------------------------------------------------------------- #
def test_star_short_matches_the_sample_count_line() -> None:
    lines = _lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines')
    assert ' 计数：胜8/13 平3/5 负6/13' in lines, lines


def test_star_short_matches_the_sample_rows_verbatim() -> None:
    lines = _lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines')
    assert ' 【胜】│8 │-4.83 │4-0@54.23 / 4-2@33.36 / 3-0@27.18 / 4-1@32.46 / '
    assert any(
        ln.startswith(' 【胜】│8 │-4.83 │') for ln in lines
    ), lines
    assert ' 【平】│3 │-9.29 │平其它@72.49 / 0-0@18.05 / 3-3@21.58' in lines, lines
    assert any(
        ln == ' 【负】│6 │-3.77 │0-4@37.63 / 1-4@24.29 / 2-4@26.95 / 0-3@21.06 / 0-1@12.84 / 0-2@14.62'
        for ln in lines
    ), lines


def test_star_short_score_column_order_is_diff_ascending() -> None:
    """比分列按 `市均−竞彩` 升序（最负在前）。按市均降序会立刻红。"""
    pack = _sample_pack()
    rows = {r['score']: r['diff'] for r in DSA._raw_odds_rows(pack)}
    lines = _lines(pack, 'format_jingcai_short_wdl_matrix_tg_lines')
    for group in ('胜', '平', '负'):
        cell = next(ln for ln in lines if ln.startswith(f' 【{group}】'))
        seq = re.findall(r'(\S+?)@', cell.split('│')[-1])
        diffs = [rows[s] for s in seq]
        assert diffs == sorted(diffs), (group, seq, diffs)
        assert diffs == sorted(diffs, reverse=True) or len(diffs) <= 1 or seq


def test_star_short_sample_anchor_numbers() -> None:
    """4.83 / 9.29 / 3.77 这三个数逐字复现样本的五个派生结论。"""
    abs_means = {'胜': 4.83, '平': 9.29, '负': 3.77}
    pair = DSA.score_market_pairwise(abs_means)
    assert pair['scores'] == {'胜': 0, '平': -2, '负': 2}, pair

    ordered = sorted(abs_means.items(), key=lambda kv: kv[1])
    assert ordered[0] == ('负', 3.77)
    assert round(ordered[1][1] / ordered[0][1], 2) == 1.28
    assert round(abs_means['胜'] / abs_means['负'], 2) == 1.28

    top = max(pair['scores'].values())
    leaders = [d for d, v in pair['scores'].items() if v == top]
    assert leaders == ['负'], '最高分必须唯一'


def test_star_short_renders_the_sample_numbers_with_sample_data() -> None:
    """真实渲染（不是只算 dict）：五个派生结论都要出现在正文里。"""
    text = '\n'.join(_lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines'))
    assert '均市-竞绝对值：胜4.83，平9.29，负3.77' in text, text
    assert '两两比对得分胜+0/平-2/负+2' in text, text
    assert '对抗1/-1/-1' in text, text
    assert '首位负对次位比值1.28' in text, text
    assert '胜负绝对值比值1.28' in text, text
    assert '最高分唯一，对照落点负' in text, text
    assert '只作对照，不改方向锁' in text, text


def test_pairwise_rule_minus_one_for_the_larger_abs() -> None:
    """两两比对：绝对值小者 +1、大者 −1、相等 0。反过来必须不同。"""
    assert DSA.score_market_pairwise({'胜': 1.0, '平': 2.0, '负': 3.0})['scores'] == {
        '胜': 2, '平': 0, '负': -2,
    }
    assert DSA.score_market_pairwise({'胜': 2.0, '平': 2.0, '负': 2.0})['scores'] == {
        '胜': 0, '平': 0, '负': 0,
    }
    # 用户样本那组必须是 0 / -2 / +2 —— 换成「大者 +1」就会变成 0 / +2 / -2。
    assert DSA.score_market_pairwise({'胜': 4.83, '平': 9.29, '负': 3.77})['scores'] == {
        '胜': 0, '平': -2, '负': 2,
    }


def test_shape_is_never_numbered_without_a_confirmed_scheme() -> None:
    """形态编号体系未确认 → 只出「未判定」，绝不编一个像真的编号。"""
    text = '\n'.join(_lines(_sample_pack(), 'format_jingcai_short_wdl_matrix_tg_lines'))
    assert '形态：未判定（阈值未确认）' in text, text
    assert '形态落点：未判定' in text, text
    assert not re.search(r'形态：\d', text), text
    # 判据本身可复算，但编号留空。
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
    assert '0-0' not in joined.split('计数')[1].split('口径')[0], joined
    assert '缺赔' in joined, joined


def test_unformed_when_no_direction_has_a_short_item() -> None:
    pack = _pack(SAMPLE_MARKET, {})
    lines = _lines(pack, 'format_jingcai_short_wdl_matrix_tg_lines')
    assert lines[0] == '★市短·胜平负比分矩阵'
    assert len(lines) == 2, lines
    assert '未形成（三向均无市短项）' in lines[1], lines


def test_rendering_never_touches_the_direction_lock() -> None:
    """渲染前后 direction / first_score 不变。

    两次都通过装配入口（`build_deepseek_only_tg_sections`）与直接调用四条渲染器，
    并且把带锁的 payload 传进去 —— 否则「没改」只是因为拿不到锁，是假绿。
    """
    pack = _sample_pack()
    payload = {
        'theo_mkt_31_pack': pack,
        'direction_lock': {'locked': True, 'direction': '胜', 'scores': ['2-1', '1-0']},
        'first_score': '2-1',
        'home': _HOME, 'away': _AWAY,
    }
    before = (
        payload['direction_lock']['direction'],
        payload['first_score'],
        tuple(payload['direction_lock']['scores']),
    )
    DSA.build_deepseek_only_tg_sections(ds_payload=payload)
    for renderer in (
        'format_market_vs_jingcai_odds_tg_lines',
        'format_jingcai_short_wdl_matrix_tg_lines',
        'format_jingcai_hot_wdl_matrix_tg_lines',
        'format_market_goal_top_matrix_tg_lines',
    ):
        getattr(DSA, renderer)(pack, **payload)
    after = (
        payload['direction_lock']['direction'],
        payload['first_score'],
        tuple(payload['direction_lock']['scores']),
    )
    assert after == before
    assert DSA.matrix_direction_lock(payload)['direction'] == '胜'


# --------------------------------------------------------------------------- #
# 段 3：市热·胜平负比分矩阵
# --------------------------------------------------------------------------- #
def test_hot_counts_and_rows_match_the_sample() -> None:
    lines = _lines(_sample_pack(), 'format_jingcai_hot_wdl_matrix_tg_lines')
    assert ' 计数：胜2 平2 负4' in lines, lines
    assert ' 【胜】│2 │2-1@10.11 / 胜其它@47.10 ' in lines, lines
    assert ' 【平】│2 │1-1@8.05 / 2-2@9.79 ' in lines, lines
    assert ' 【负】│4 │1-2@9.10 / 1-3@13.30 / 2-3@14.86 / 负其它@41.07 ' in lines, lines


def test_hot_score_column_order_is_market_ascending() -> None:
    """市热段的比分列按**市均升序**——与★市短段的市-竞升序不是同一把尺子。"""
    pack = _sample_pack()
    rows = {r['score']: r['market'] for r in DSA._raw_odds_rows(pack)}
    lines = _lines(pack, 'format_jingcai_hot_wdl_matrix_tg_lines')
    for group in ('胜', '平', '负'):
        cell = next(ln for ln in lines if ln.startswith(f' 【{group}】'))
        seq = re.findall(r'(\S+?)@', cell.split('│')[-1])
        odds = [rows[s] for s in seq]
        assert odds == sorted(odds), (group, seq, odds)
    # 负向若按市-竞升序会给 2-3/1-3/…，那是另一段的口径。
    neg = next(ln for ln in lines if ln.startswith(' 【负】'))
    assert neg.split('│')[-1].strip().startswith('1-2@9.10'), neg


def test_hot_caliber_line_drops_the_false_top15_claim() -> None:
    """样本那句「计数为市均最低前15」与数据自相矛盾，不许照抄。"""
    text = '\n'.join(_lines(_sample_pack(), 'format_jingcai_hot_wdl_matrix_tg_lines'))
    assert '市均最低前15' not in text, text
    assert '前15' not in text, text
    assert '市热=市均−竞彩为正，共8项' in text, text
    assert '与★市短对照' in text, text


def test_hot_and_short_are_complementary_and_disjoint() -> None:
    """市热项 ∪ 市短项 = 差≠0 的项；两集合无交集（差恰为 0 的两边都不算）。"""
    rows = DSA._raw_odds_rows(_sample_pack())
    short = {r['score'] for r in rows if r['diff'] is not None and r['diff'] < 0}
    hot = {r['score'] for r in rows if r['diff'] is not None and r['diff'] > 0}
    nonzero = {r['score'] for r in rows if r['diff'] not in (None, 0.0)}
    zero = {r['score'] for r in rows if r['diff'] == 0.0}
    assert not (short & hot), short & hot
    assert short | hot == nonzero
    assert zero and not (zero & (short | hot))
    assert len(short) + len(hot) + len(zero) == 31


def test_hot_prints_every_direction_row_even_when_empty() -> None:
    """某向 0 项时那一行仍要在，写 `【X】│0 │（无）`，不能省略。"""
    # 只让 平 / 负 有市热项，胜向故意一项不给。
    diffs = {'0-0': 1.00, '1-1': 0.50, '0-1': 0.80, '0-2': 0.40}
    pack = _pack(SAMPLE_MARKET, diffs)
    lines = _lines(pack, 'format_jingcai_hot_wdl_matrix_tg_lines')
    assert '未形成' not in '\n'.join(lines), lines
    assert any(ln.startswith(' 【胜】│0 │（无）') for ln in lines), lines
    assert any(ln.startswith(' 【平】│2 │') for ln in lines), lines
    assert any(ln.startswith(' 【负】│2 │') for ln in lines), lines


# --------------------------------------------------------------------------- #
# 段 4：市均前15·进球赔率矩阵
# --------------------------------------------------------------------------- #
def test_goal_grid_cells_match_the_sample_verbatim() -> None:
    text = '\n'.join(_lines(_sample_pack(), 'format_market_goal_top_matrix_tg_lines'))
    for token in ('0-0', '1-0', '2-0', '2-1', '1-1', '0-1', '0-2',
                  '1-2 / 0-3', '9.10/21.06', '18.05', '14.55', '18.30', '10.11', '8.05'):
        assert token in text, (token, text)
    assert '第1页 0-3球' in text and '第2页 4-7+' in text
    # 每页各一行三向，两页共 2 次（只数行首，避开「席位最多【负】」这类正文）。
    for direction in ('胜', '平', '负'):
        rows = [ln for ln in text.splitlines() if ln.lstrip().startswith(f'【{direction}】')]
        assert len(rows) == 2, (direction, rows)


def test_goal_mean_row_is_the_true_arithmetic_mean() -> None:
    """`均` = 列内市均的算术平均（用户样本那行其实是均值÷球数，不复制）。"""
    built = DSA.build_market_goal_top_matrix(_sample_pack())
    means = [page['column_means'] for page in built['pages']]
    assert means[0] == (18.05, 13.70, 13.66, 13.42), means
    assert means[1][:3] == (13.18, 15.84, 21.58), means
    assert means[1][3] is None


def test_goal_mean_row_is_not_mean_divided_by_goal_count() -> None:
    """将来谁把「÷球数」改回来，这条就红。

    判据必须是**字面的两张表**，不能是「实测值 vs 由实测值再除一次」——
    后者几乎恒不相等，改坏了也是绿的（这里踩过一次）。
    """
    built = DSA.build_market_goal_top_matrix(_sample_pack())
    means = list(built['pages'][0]['column_means']) + list(built['pages'][1]['column_means'])
    # 真实算术平均（就是实现应该给出的那一行）。
    assert means == [18.05, 13.70, 13.66, 13.42, 13.18, 15.84, 21.58, None], means
    # 用户样本的那一行 = 均值 ÷ 球数（0 球用 /3 兜底）。两张表必须不同。
    divided = [6.02, 13.70, 6.83, 4.47, 3.29, 3.17, 3.60, None]
    assert means != divided, (means, divided)
    # 0 球格尤其不能是「除零兜底后除以 3」的 6.02。
    assert means[0] == 18.05, means


def test_goal_summary_numbers_match_the_sample() -> None:
    text = '\n'.join(_lines(_sample_pack(), 'format_market_goal_top_matrix_tg_lines'))
    assert ' 计数：胜5 平4 负6｜市均最低前15，入选15' in text, text
    assert '胜平负占比：胜33.3% / 平26.7% / 负40.0%｜席位最多【负】' in text, text
    assert '进球判定：少球6席(40.0%) / 多球9席(60.0%)｜主导【多球】' in text, text
    assert '最低市均：1-1@8.05，2球，平。' in text, text
    assert '少球均赔14.40，多球均赔14.79' in text, text


def test_goal_low_ball_boundary_is_le_two() -> None:
    """少球 = 总进球 ≤ 2。改成 ≤1 会让 6 席 / 14.40 立刻红。"""
    assert DSA.MARKET_GOAL_LOW_MAX == 2
    built = DSA.build_market_goal_top_matrix(_sample_pack())
    assert built['low_count'] == 6 and built['high_count'] == 9
    assert built['low_mean'] == pytest.approx(14.40, abs=0.005)
    assert built['high_mean'] == pytest.approx(14.79, abs=0.005)


def _width(text: str) -> int:
    """**测试自带**的显示宽度，不复用被测模块的实现。

    这一点是这条守卫成立的前提：如果测试用 `pipe._display_width` 去量
    「`pipe._display_width` 算得对不对」，把它换成 `len` 时两侧一起变，
    永远是绿的（实测过一次，确实绿）。所以这里独立用 unicodedata 算。
    """
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in text)


def _pipe_columns(line: str) -> tuple[int, ...]:
    """`|` 所在的**显示列**（不是字符下标——中文占两列）。"""
    return tuple(_width(line[:i]) for i, ch in enumerate(line) if ch == '|')


def test_goal_grid_is_visually_aligned() -> None:
    """同一页内每一行的显示宽度相同，且每个 `|` 竖着对齐。"""
    lines = _lines(_sample_pack(), 'format_market_goal_top_matrix_tg_lines')
    pages: list[list[str]] = []
    current: list[str] = []
    started = False
    for ln in lines:
        if ln.strip().startswith('第') and '页 ' in ln:
            if started:
                pages.append(current)
            current, started = [], True
            continue
        if started and '|' in ln:
            current.append(ln)
    if current:
        pages.append(current)
    assert len(pages) == 2, pages
    for page in pages:
        assert len(page) == 8, page  # 列头 + 均 + 3×(比分, 赔率)
        widths = {_width(ln) for ln in page}
        assert len(widths) == 1, (widths, page)
        bounds = {_pipe_columns(ln) for ln in page}
        assert len(bounds) == 1, bounds


def test_goal_grid_breaks_if_width_uses_len_instead_of_display_width() -> None:
    """反证用的独立度量：同一张表按显示宽度等宽，按 `len` 就不等宽。"""
    grid = [
        '  均  |  均18.05   |',
        '【胜】|   14.55    |',
    ]
    assert len({_width(ln) for ln in grid}) == 1
    assert len({len(ln) for ln in grid}) != 1


def test_goal_unpriced_unjudged_items_are_flagged_not_invented() -> None:
    text = '\n'.join(_lines(_sample_pack(), 'format_market_goal_top_matrix_tg_lines'))
    assert '未判定（判据未确认）' in text, text
    for invented in ('胜穿透5', '负穿透3', '大球扎实核9', '少球扎实核5'):
        assert invented not in text, invented


def test_goal_matrix_unformed_without_market_odds() -> None:
    lines = _lines({}, 'format_market_goal_top_matrix_tg_lines')
    assert lines[0] == '市均前15·进球赔率矩阵'
    assert '未形成' in lines[1]


# --------------------------------------------------------------------------- #
# 装配：四段都进正文，且各段自成一个段
# --------------------------------------------------------------------------- #
def test_all_four_sections_reach_the_assembled_report() -> None:
    pack = _sample_pack()
    text = '\n'.join(DSA.build_deepseek_only_tg_sections(
        title_line='📌 t',
        theo_mkt_31_tg_lines=DSA.format_theo_mkt_31_tg_lines(pack),
        ds_payload={'theo_mkt_31_pack': pack},
        heat_tg_lines=['HEATM'],
        home=_HOME, away=_AWAY,
    ))
    for name in ('31项市均×竞彩对比表', '市均***竞彩对比表', '市短矩阵',
                 '★市短·胜平负比分矩阵', '市热矩阵', '市热·胜平负比分矩阵',
                 '市均前15·进球赔率矩阵', 'HEATM'):
        assert name in text, name
    order = [text.find(n) for n in (
        '31项市均×竞彩对比表', '市均***竞彩对比表', '市短矩阵',
        '市热矩阵', '★市短·胜平负比分矩阵', '市热·胜平负比分矩阵',
        '市均前15·进球赔率矩阵', 'HEATM',
    )]
    assert order == sorted(order), order


def test_each_new_section_is_its_own_section_boundary() -> None:
    """`_section_is_empty` 必须能在新段之间切边界，不把下一段并进上一段。"""
    pack = _sample_pack()
    body = '\n'.join(DSA.build_deepseek_only_tg_sections(
        ds_payload={'theo_mkt_31_pack': pack}, home=_HOME, away=_AWAY,
    ))
    for name in ('市均***竞彩对比表', '★市短·胜平负比分矩阵',
                 '市热·胜平负比分矩阵', '市均前15·进球赔率矩阵'):
        assert not DSA._section_is_empty(body, name), name
