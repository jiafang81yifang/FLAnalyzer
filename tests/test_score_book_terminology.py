"""术语落地与同口径比较点：报告文案用「竞彩/市场」，比较点必须重分桶。

守卫驱动真实函数（`build_mkt_short_matrix_from_pack`、各格式化函数），
不在测试里复制一份映射或比较逻辑。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import blade_deepseek_pipeline as bd
import score_books as sb

JC_KEYS_HYPHEN = [k.replace(':', '-') for k in sb.JC_31_KEYS]


def _pack() -> dict:
    """31 全表：竞彩给全，市场只覆盖它能报的那些键（真实形态）。"""
    market_25 = {
        '1-0': 9.0, '2-0': 15.0, '2-1': 8.0, '3-0': 30.0, '3-1': 20.0, '3-2': 30.0,
        '4-0': 70.0, '4-1': 50.0, '4-2': 80.0, '4-3': 90.0, '3-4': 95.0,
        '0-0': 12.0, '1-1': 7.0, '2-2': 16.0, '3-3': 55.0, '4-4': 120.0,
        '0-1': 12.0, '0-2': 22.0, '1-2': 15.0, '0-3': 50.0, '1-3': 37.0, '2-3': 55.0,
        '0-4': 120.0, '1-4': 90.0, '2-4': 140.0,
    }
    rows = []
    jc_odds = 6.0
    for option in JC_KEYS_HYPHEN:
        rows.append({
            'score': option,
            'jc_odds': jc_odds,
            'jc_odds_pct': 100.0 / 31.0,
            'market': market_25.get(option),
            'market_source': 'desktop_cs' if option in market_25 else '',
        })
    return {'rows': rows, 'available': True}


def test_pinned_report_headers_are_not_renamed() -> None:
    """既有报告段标题是 10 处既有守卫的契约，术语改动不得顺手改它。

    改标题会改动用户看到的段落名，却换不来任何口径正确性；口径正确性
    由 `SCORE_BOOK_TERMINOLOGY_NOTE`（进提示词）与新段（`same_caliber`）承担。

    反证：把「31项市均×竞彩对比表」改成「31项市场×竞彩对比表」→
    tests/test_runtime.py::test_tg_payload_contains_all_31_options_and_three_matrices
    与 tests/test_always_send_policy.py 的同名守卫一起变红（已实测）。
    """
    source = (ROOT / 'blade_deepseek_pipeline.py').read_text(encoding='utf-8')
    assert '📈【31项市均×竞彩对比表】' in source
    assert '📊【市短矩阵｜市均隐含 > 竞彩隐含】' in source
    assert "source': '市均×竞彩31比分'" in source


def test_market_short_matrix_source_is_unchanged_numerically() -> None:
    matrix = bd.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(_pack())
    assert matrix['source'] == '市均×竞彩31比分'


def test_terminology_mandate_lives_in_the_prompt_not_the_header() -> None:
    """术语要求落在提示词常量里，且常量本身逐条对上事实。"""
    note = bd.SCORE_BOOK_TERMINOLOGY_NOTE
    assert '「竞彩」专指中国竞彩足球 31 项比分盘' in note
    assert '「市场」专指境外主流公司 25/26 项波胆盘' in note
    assert '不是同义词' in note
    for score in ('5:0', '5:1', '5:2', '0:5', '1:5', '2:5'):
        assert score in note


def test_new_same_caliber_section_uses_market_terminology() -> None:
    """新增的同口径段落用「市场」，且与既有段标题并存不冲突。"""
    same = bd.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(_pack())['same_caliber']
    assert same['available'] is True
    row = same['rows'][0]
    assert set(row) == {'bucket', 'jc', 'market', 'delta', 'is_other'}


def test_same_caliber_buckets_is_attached_and_uses_score_books_mapping() -> None:
    """逐项差额保留原口径，另附同口径桶比较。

    反证：把 `'same_caliber': _same_caliber_buckets(p)` 删掉 → 本断言变红。
    """
    matrix = bd.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(_pack())
    same = matrix['same_caliber']
    assert same['available'] is True
    assert same['policy'] == sb.DEFAULT_MARKET_POLICY.as_dict()

    # 默认口径（唯一无损的那套）把 6 个 5 球格并进方向桶——这是
    # score_books 给的规则，不是这里另写的一份。
    for score in ('5:0', '5:1', '5:2'):
        assert sb.jc_to_market_key(score) == '主胜其他'
    for score in ('0:5', '1:5', '2:5'):
        assert sb.jc_to_market_key(score) == '客胜其他'

    # 31 表里市场没报「任何其他」这个键（而且默认口径下它结构上不可达），
    # 所以它必须出现在不可比集合里，而不是被当成 0 参与排序。
    assert '任何其他' in same['incomparable_buckets']
    assert '任何其他' not in same['comparable_buckets']
    # 结构性不可达单独点名，不与「市场没报价」混为一个概念。
    assert '任何其他' in same['structural_buckets']
    assert all(
        r['bucket'] != '任何其他' for r in same['rows']
    ), '不可达桶不许出现在定价差行里（它没有 Δ）'


def test_same_caliber_comparable_buckets_have_real_deltas() -> None:
    same = bd.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(_pack())['same_caliber']
    for name in same['comparable_buckets']:
        row = next(r for r in same['rows'] if r['bucket'] == name)
        assert row['market'] is not None
        assert row['delta'] == pytest.approx(row['jc'] - row['market'], abs=1e-12)


def test_same_caliber_absent_when_no_jc_probabilities() -> None:
    same = bd.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack({'rows': []})['same_caliber']
    assert same['available'] is False
    assert '无法重分桶' in same['reason']


def test_same_caliber_five_goal_mass_goes_to_directional_buckets() -> None:
    """6 个 5 球格的竞彩质量，在默认（无损）口径下落进方向桶。

    这条证明比较点真的用了重分桶，而不是把 5:0 当成市场也有的一格；
    也钉住默认口径是 `five_goals_in_team_other`（缺陷 1 的回归）。
    """
    same = bd.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(_pack())['same_caliber']
    # 26 项盘面 + TEAM_OTHER：逐桶不成立（任何其他不可达），聚合层成立。
    assert same['per_bucket_lossless'] is False
    assert same['aggregate_lossless'] is True
    home_other = next(r for r in same['rows'] if r['bucket'] == '主胜其他')
    away_other = next(r for r in same['rows'] if r['bucket'] == '客胜其他')
    per_cell = (100.0 / 31.0) / 100.0
    # 主胜其他 = 胜其他 + 5:0 + 5:1 + 5:2（4 格）
    assert home_other['jc'] == pytest.approx(4 * per_cell, abs=1e-9)
    # 客胜其他 = 负其他 + 0:5 + 1:5 + 2:5（4 格）
    assert away_other['jc'] == pytest.approx(4 * per_cell, abs=1e-9)
    # 任何其他在竞彩侧无独立质量，且被归到结构性差异（不是定价差行）
    assert '任何其他' in same['structural_buckets']
    assert all(r['bucket'] != '任何其他' for r in same['rows'])


def test_existing_per_row_numbers_are_unchanged() -> None:
    """术语与同口径段落不得改动既有逐项差额（数值结论不变）。

    手算一条：夹具里 2-1 的市场列是 8.0（该 pack 字段本身就是百分数），
    竞彩 100/31 ≈ 3.2258%（源里 round 到 4 位小数）→ gap ≈ 4.7742pp。
    """
    matrix = bd.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(_pack())
    row = next(r for r in matrix['rows'] if r['score'] == '2-1')
    assert row['market_pct'] == pytest.approx(8.0, abs=1e-9)
    assert row['jc_pct'] == pytest.approx(round(100.0 / 31.0, 4), abs=1e-9)
    assert row['gap_pct'] == pytest.approx(8.0 - round(100.0 / 31.0, 4), abs=1e-9)
