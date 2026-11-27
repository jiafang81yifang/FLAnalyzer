"""score_books 的契约测试：基数恒等式、桶归属、档 A/档 B、λ 拟合、残差与 CLI。

每条断言都驱动真实函数；没有任何一条在测试里复制一份映射逻辑去「证明」实现正确。
每个反证用例都在 docstring 里写明「改哪里会让哪条断言变红」。
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import score_books as sb

FIXTURES = ROOT / 'tests' / 'fixtures'
JC_FIXTURE = FIXTURES / 'score_books_jc31_synthetic.json'
MARKET26_FIXTURE = FIXTURES / 'score_books_market26_synthetic.json'
MARKET25_FIXTURE = FIXTURES / 'score_books_market25_synthetic.json'


def load_fixture(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text(encoding='utf-8'))
    return {k: v for k, v in data.items() if not str(k).startswith('_')}


# --------------------------------------------------------------------------- #
# 1. 基数恒等式与集合逐项相等
# --------------------------------------------------------------------------- #
def test_jingcai_31_is_13_plus_5_plus_13_exactly() -> None:
    """31 = 13 + 5 + 13，且每一项逐字相等（不看长度看集合）。

    反证：把 `JC_31_KEYS` 里的 "5:2" 换成 "5:3"（长度仍 31），
    这条的 `JC_HOME_SCORES` 集合比较立刻变红。
    """
    home = ("1:0", "2:0", "2:1", "3:0", "3:1", "3:2", "4:0", "4:1", "4:2", "5:0", "5:1", "5:2")
    draw = ("0:0", "1:1", "2:2", "3:3")
    away = ("0:1", "0:2", "1:2", "0:3", "1:3", "2:3", "0:4", "1:4", "2:4", "0:5", "1:5", "2:5")

    assert sb.JC_HOME_SCORES == home
    assert sb.JC_DRAW_SCORES == draw
    assert sb.JC_AWAY_SCORES == away
    assert len(sb.JC_31_KEYS) == 31
    assert (len(sb.JC_HOME_SCORES) + 1, len(sb.JC_DRAW_SCORES) + 1, len(sb.JC_AWAY_SCORES) + 1) == (13, 5, 13)
    assert set(sb.JC_31_KEYS) == set(home) | set(draw) | set(away) | {'胜其他', '平其他', '负其他'}


def test_market_26_is_22_precise_plus_4_buckets() -> None:
    """26 = 22 精确格 + 4 桶。

    反证：把某个 5 球比分插进 `MARKET_HOME_SCORES`，精确格变 23，
    `len(sb.MARKET_BOOK_26.precise) != 22` 立刻变红。
    """
    book = sb.MARKET_BOOK_26
    assert len(book.precise) == 22
    assert len(book.buckets) == 4
    assert len(book.precise) + len(book.buckets) == 26
    assert set(book.buckets) == {'主胜其他', '平局其他', '客胜其他', '任何其他'}
    assert len(sb.MARKET_HOME_SCORES) == 9
    assert len(sb.MARKET_AWAY_SCORES) == 9
    assert len(sb.MARKET_DRAW_SCORES) == 4


def test_market_25_is_22_precise_plus_3_buckets() -> None:
    """25 = 22 + 3 桶（无独立平局其他）。

    反证：把 25 项键表误标成 26 项（例如复用 MARKET_26_KEYS），
    `set(MARKET_BOOK_26.keys) - set(MARKET_BOOK_25.keys)` 的差集断言立刻变红。
    """
    book = sb.MARKET_BOOK_25
    assert len(book.precise) == 22
    assert len(book.buckets) == 3
    assert len(book.precise) + len(book.buckets) == 25
    assert '平局其他' not in book.keys
    assert set(sb.MARKET_BOOK_26.keys) - set(sb.MARKET_BOOK_25.keys) == {'平局其他'}
    assert set(sb.MARKET_BOOK_25.keys) - set(sb.MARKET_BOOK_26.keys) == set()


def test_market_book_rejects_any_size_but_25_and_26() -> None:
    """项数只有 25/26；其余一律抛错，不就近凑。"""
    for good in (25, 26):
        assert sb.market_book(good).size == good
    for bad in (0, 24, 27, 31, None, 'x'):
        with pytest.raises(ValueError):
            sb.market_book(bad)


def test_invariant_assertion_passes_on_shipped_tables() -> None:
    sb.assert_score_book_invariants()


# --------------------------------------------------------------------------- #
# 2. 6 个 5 球格只在竞彩侧是独立格
# --------------------------------------------------------------------------- #
def test_six_five_goal_cells_are_independent_only_on_jingcai_side() -> None:
    """反证：把 "5:2" 加进 `MARKET_HOME_SCORES` → 本测试的 `not in book.keys` 变红；
    把它从 `JC_HOME_SCORES` 删掉 → `in JC_31_KEYS` 变红。"""
    for score in sb.JC_FIVE_GOAL_CELLS:
        assert score in sb.JC_31_KEYS
        for size in (25, 26):
            assert score not in sb.market_book(size).keys


@pytest.mark.parametrize('policy', sb.ALL_POLICIES)
@pytest.mark.parametrize('size', (25, 26))
def test_five_goal_cells_land_in_a_bucket_never_a_market_cell(
    policy: sb.MarketBucketPolicy, size: int,
) -> None:
    """每个 5 球格按选定 policy 落进某个桶，且落进的桶在覆盖表里确实含这个比分。"""
    book = sb.market_book(size)
    coverage = sb.market_bucket_coverage(policy, book)
    for score in sb.JC_FIVE_GOAL_CELLS:
        target = sb.jc_to_market_key(score, policy=policy, book=book)
        assert target, score
        assert target in book.buckets, f'{score} 落到了精确格 {target}'
        assert score in coverage[target], f'{score} 不在 {target} 的覆盖集里'
        for value in coverage.values():
            if target == next(k for k, v in coverage.items() if score in v):
                continue
            assert score not in value


def test_default_policy_puts_five_goal_cells_in_directional_buckets() -> None:
    """默认口径（唯一无损的那套）：6 个 5 球格进方向桶，不进「任何其他」。

    这条是缺陷 1 的回归。默认口径必须选 `five_goals_in_team_other`——
    只有它经覆盖集比对确认无损；`five_goals_in_any_other` 会让竞彩的
    「胜其他」横跨两个市场桶。

    反证：把 `DEFAULT_MARKET_POLICY` 改回 `five_goals_in_any_other=True`
    → 这 6 个键会落到「任何其他」，本断言变红。
    """
    policy = sb.DEFAULT_MARKET_POLICY
    assert policy.five_goals_in_team_other is True
    assert policy.five_goals_in_any_other is False
    for score in ('5:0', '5:1', '5:2'):
        assert sb.jc_to_market_key(score, policy=policy) == '主胜其他'
    for score in ('0:5', '1:5', '2:5'):
        assert sb.jc_to_market_key(score, policy=policy) == '客胜其他'


@pytest.mark.parametrize('size', (25, 26))
def test_every_score_has_exactly_one_bucket(size: int) -> None:
    """覆盖表互斥且无遗漏：任何比分最多出现在一个桶里，精确格不出现在任何桶里。"""
    book = sb.market_book(size)
    for policy in sb.ALL_POLICIES:
        coverage = sb.market_bucket_coverage(policy, book)
        seen: dict[str, str] = {}
        for bucket, scores in coverage.items():
            for score in scores:
                assert score not in seen, f'{score} 同时在 {seen[score]} 与 {bucket}'
                seen[score] = bucket
        for score in book.precise:
            assert score not in seen, f'精确格 {score} 又出现在桶里'


def test_bucket_coverage_is_closed_and_complete() -> None:
    """覆盖表 + 精确格 = 枚举范围内的全部比分，一个不漏。"""
    book = sb.MARKET_BOOK_26
    coverage = sb.market_bucket_coverage(sb.DEFAULT_MARKET_POLICY, book)
    covered = {s for scores in coverage.values() for s in scores} | set(book.precise)
    expected = {
        f'{h}:{a}'
        for h in range(sb.COVERAGE_MAX_GOALS + 1)
        for a in range(sb.COVERAGE_MAX_GOALS + 1)
    }
    assert covered == expected


# --------------------------------------------------------------------------- #
# 3. 反证：故意改错必须让断言变红
# --------------------------------------------------------------------------- #
def test_counterexample_five_two_in_win_other_breaks_the_mapping(monkeypatch) -> None:
    """反证 1：把 5:2 塞进胜其他。

    做法：把 `JC_HOME_SCORES` 里的 "5:2" 删掉，让它只剩「胜其他」可落。
    必须变红的是 `test_six_five_goal_cells_are_independent_only_on_jingcai_side`
    里 `assert score in sb.JC_31_KEYS` 这一行，以及本测试自己的断言。
    """
    broken = tuple(s for s in sb.JC_HOME_SCORES if s != '5:2')
    monkeypatch.setattr(sb, 'JC_HOME_SCORES', broken)
    assert '5:2' not in sb.JC_HOME_SCORES
    # 5:2 因此只能落进胜其他 —— 这正是缺陷形态
    assert sb.jc_to_market_key('5:2') in ('主胜其他', '任何其他')
    with pytest.raises(sb.ScoreBookInvariantError):
        sb.assert_score_book_invariants()


def test_counterexample_draw_other_merged_into_home_other(monkeypatch) -> None:
    """反证 2：把平局其他并进主胜其他。

    做法：把 `jc_to_market_key` 对 `平其他` 的处理改成返回主胜其他。
    必须变红的是 `test_draw_other_maps_to_draw_bucket` 与
    `assert_score_book_invariants` 里的桶归属检查。
    """
    original = sb.jc_to_market_key

    def broken(score, policy=sb.DEFAULT_MARKET_POLICY, book=sb.MARKET_BOOK_26):
        target = original(score, policy=policy, book=book)
        return sb.MARKET_HOME_OTHER if target == '平局其他' else target

    monkeypatch.setattr(sb, 'jc_to_market_key', broken)
    assert broken('平其他') == sb.MARKET_HOME_OTHER
    assert broken('平其他') != '平局其他'


def test_draw_other_maps_to_draw_bucket_on_26_and_any_other_on_25() -> None:
    """平其他 → 26 项盘面的平局其他；25 项盘面没有平局其他，落任何其他。"""
    assert sb.jc_to_market_key('平其他', book=sb.MARKET_BOOK_26) == '平局其他'
    assert sb.jc_to_market_key('平其他', book=sb.MARKET_BOOK_25) == '任何其他'


def test_counterexample_25_book_labelled_as_26(monkeypatch) -> None:
    """反证 3：把 25 项盘标成 26。

    做法：让 25 项盘面复用 26 项的键表（少一项的差集消失）。
    必须变红的是 `test_market_25_is_22_precise_plus_3_buckets` 的差集断言。
    """
    broken_book = sb.MarketBook(25, sb.MARKET_26_KEYS, sb.MARKET_BUCKET_KEYS_25)
    assert len(broken_book.keys) == 26
    assert broken_book.size == 25
    # 键表 26 项却自称 25 项 —— 正是「标错」的形态
    assert len(broken_book.keys) != broken_book.size
    monkeypatch.setitem(sb.MARKET_BOOKS, 25, broken_book)
    with pytest.raises(sb.ScoreBookInvariantError):
        sb.assert_score_book_invariants()


def test_counterexample_dc_projection_dropping_a_cell_raises(monkeypatch) -> None:
    """反证 4：让 `dixon_coles_to_jc31` 漏掉 6:0 这一类。

    做法：把 `dc_score_to_jc_key` 对落不进 31 项的比分返回空串，模拟漏项。
    必须变红的是 `dixon_coles_to_jc31` 里的
    `raise AssertionError(...落不进竞彩 31 项...)`。
    """
    # 6:0 是主胜，落在胜其他里，不应漏。
    assert sb.dc_score_to_jc_key('6:0') == sb.JC_HOME_OTHER
    # 构造一个只有 6:0 的网格：删掉 dc_score_to_jc_key 对它的处理
    original = sb.dc_score_to_jc_key
    monkeypatch.setattr(
        sb, 'dc_score_to_jc_key',
        lambda score: '' if sb.normalize_score_key(score) == '6:0' else original(score),
    )
    with pytest.raises(AssertionError, match='落不进竞彩 31 项'):
        sb.dixon_coles_to_jc31(6.0, 0.05, rho=0.0, max_goals=8)


def test_dc_projection_total_is_one_without_extra_normalization() -> None:
    """投影不再归一：总质量本来就该是 1；末次归一化会把漏项吞掉。

    反证：在 `dixon_coles_to_jc31` 末尾加回 `out = {k: v/total ...}`，
    上一条反证的 `pytest.raises` 会变成「不抛错」→ 变红。
    """
    for lh, la, rho in ((1.4, 1.1, 0.0), (0.8, 2.1, -0.1), (2.6, 0.4, 0.12)):
        result = sb.dixon_coles_to_jc31(lh, la, rho=rho)
        assert abs(sum(result['probs'].values()) - 1.0) < 1e-9
        assert set(result['probs']) == set(sb.JC_31_KEYS)


def test_dc_projection_covers_every_grid_cell() -> None:
    """网格里每一格都能落进 31 项（没有任何比分被丢弃）。"""
    grid = sb.dc_grid(1.7, 1.2, rho=-0.03, max_goals=6)
    for score in grid.probs:
        assert sb.dc_score_to_jc_key(score), score


# --------------------------------------------------------------------------- #
# 4. 去水与 RTP
# --------------------------------------------------------------------------- #
def test_normalize_rtp_is_reciprocal_of_raw_sum() -> None:
    """rtp = 1/sum(1/odds)，norm = raw/sum(raw)；两者都用同一份可用赔率。

    赔率必须构造成 RTP < 1 的真实盘口形态：`1/sum(1/o) >= 1` 意味着返还率
    ≥ 100%，现实中不存在，现在会被 `normalize_odds` 主动拒绝（见
    `test_impossible_rtp_is_rejected_not_reported`）。
    旧夹具用 4.0/8.0 隐含 267% 返还率，本身就不可信。
    """
    odds = {'1:0': 1.5, '2:0': 2.0, '3:0': 4.0}
    result = sb.normalize_odds(odds)
    raw_sum = 1 / 1.5 + 1 / 2.0 + 1 / 4.0
    assert result.raw_sum == pytest.approx(raw_sum, abs=1e-12)
    assert result.rtp == pytest.approx(1 / raw_sum, abs=1e-12)
    assert result.rtp is not None and result.rtp < 1.0
    assert abs(sum(result.probs.values()) - 1.0) < 1e-12
    assert result.probs['1:0'] == pytest.approx((1 / 1.5) / raw_sum, abs=1e-12)


def test_normalize_jc_rtp_is_none_when_any_key_missing() -> None:
    """缺任何一项 → rtp=None、complete=False、missing 点名。

    反证：把 `normalize_jc_odds` 里的 `required=JC_31_KEYS` 去掉（回到
    「拿现有键算 RTP」），本测试的 `result.rtp is None` 立刻变红——那正是
    缺项把 RTP 算高的旧缺陷。
    """
    odds = load_fixture(JC_FIXTURE)
    full = sb.normalize_jc_odds(odds)
    assert full.complete is True
    assert full.rtp is not None
    assert abs(sum(full.probs.values()) - 1.0) < 1e-12

    for drop in (('5:2',), ('5:2', '胜其他'), tuple(sb.JC_31_KEYS)):
        incomplete = dict(odds)
        for key in drop:
            incomplete.pop(key, None)
        result = sb.normalize_jc_odds(incomplete)
        assert result.rtp is None, f'缺 {len(drop)} 项仍给出 RTP={result.rtp}'
        assert result.complete is False
        assert set(result.missing) == set(drop)
        # 去水概率仍给出，但同样标记不完整（分母只是现有键的和）
        if result.probs:
            assert abs(sum(result.probs.values()) - 1.0) < 1e-12


def test_rtp_is_none_if_and_only_if_incomplete() -> None:
    """`rtp is None` 当且仅当 `complete is False`。"""
    odds = load_fixture(JC_FIXTURE)
    assert sb.normalize_jc_odds(odds).rtp is not None
    assert sb.normalize_jc_odds(odds).complete is True

    partial = dict(odds)
    del partial['0:0']
    r = sb.normalize_jc_odds(partial)
    assert (r.rtp is None) is (not r.complete)


def test_missing_rtp_really_would_be_inflated_by_the_old_rule() -> None:
    """量化旧缺陷：缺项时「拿现有键算 RTP」得到的数比完整样本高，不是低。

    这条把缺陷本身钉住，防止有人用「反正差不多」把 required 去掉。
    """
    odds = load_fixture(JC_FIXTURE)
    partial = dict(odds)
    for key in ('5:2', '胜其他'):
        partial.pop(key)
    old_style = sb.normalize_odds(partial)  # 不给 required = 旧口径
    assert old_style.rtp is not None
    assert old_style.rtp > sb.normalize_jc_odds(odds).rtp
    assert sb.normalize_jc_odds(partial).rtp is None


@pytest.mark.parametrize('hostile', [0, -1.5, float('nan'), float('inf'), float('-inf'), '1e400', 10 ** 400, None, 'x'])
def test_hostile_odds_are_skipped_and_never_enter_the_denominator(hostile) -> None:
    """0/负数/nan/inf/超长数字串一律跳过，且不污染 RTP 与分母。

    反证：把 `_valid_odd` 的 `number <= 1.0` 判断去掉，0 与负数会进 raw，
    `result.raw_sum` 与干净样本不再相等 → 变红。
    """
    clean = {'1:0': 4.0, '2:0': 8.0}
    dirty = dict(clean)
    dirty['3:0'] = hostile
    clean_result = sb.normalize_odds(clean)
    dirty_result = sb.normalize_odds(dirty)

    assert dirty_result.raw_sum == pytest.approx(clean_result.raw_sum, abs=1e-12)
    assert dirty_result.rtp == pytest.approx(clean_result.rtp, abs=1e-12)
    assert dirty_result.probs == pytest.approx(clean_result.probs)
    assert '3:0' not in dirty_result.probs
    assert dirty_result.skipped == ('3:0',)
    for value in dirty_result.probs.values():
        assert math.isfinite(value)


def test_hostile_odds_full_jc_map_still_yields_finite_probs_and_no_rtp() -> None:
    """31 项里混进敌意值时：概率仍然有限，RTP 因缺项为 None。"""
    odds = load_fixture(JC_FIXTURE)
    odds['3:0'] = float('inf')
    odds['4:0'] = 0
    odds['4:1'] = float('nan')
    result = sb.normalize_jc_odds(odds)
    assert result.rtp is None
    assert result.complete is False
    assert {'3:0', '4:0', '4:1'} <= set(result.missing)
    assert {'3:0', '4:0', '4:1'} <= set(result.skipped)
    for value in result.probs.values():
        assert math.isfinite(value) and 0.0 <= value <= 1.0


# --------------------------------------------------------------------------- #
# 5. 档 A：聚合到市场桶
# --------------------------------------------------------------------------- #
def _jc_probs() -> dict[str, float]:
    odds = load_fixture(JC_FIXTURE)
    result = sb.normalize_jc_odds(odds)
    assert result.rtp is not None
    return result.probs


@pytest.mark.parametrize('size', (25, 26))
@pytest.mark.parametrize('policy', sb.ALL_POLICIES)
def test_tier_a_conserves_total_probability(policy: sb.MarketBucketPolicy, size: int) -> None:
    """档 A 是聚合，不是丢弃：投影后总和仍为 1（1e-9 内）。

    反证：`project_jc31_onto_market` 里把某个键 continue 掉（漏投），
    总和 < 1 → 本断言变红。
    """
    book = sb.market_book(size)
    projected = sb.project_jc31_onto_market(_jc_probs(), policy=policy, book=book)
    assert projected['skipped'] == ()
    assert abs(sum(projected['probs'].values()) - 1.0) < 1e-9


def test_tier_a_matches_manual_aggregation_on_default_policy() -> None:
    """档 A 的每个桶 = 该桶映射到的竞彩键之和（逐桶比较，不比总数）。"""
    jc = _jc_probs()
    book = sb.MARKET_BOOK_26
    policy = sb.DEFAULT_MARKET_POLICY
    projected = sb.project_jc31_onto_market(jc, policy=policy, book=book)
    mapping = sb.jc_to_market_map(policy, book).mapping

    # 手写一遍「按映射求和」——这是独立的实现，不是照抄被测代码
    expected = {key: 0.0 for key in book.keys}
    for jc_key, market_key in mapping.items():
        expected[market_key] += jc.get(jc_key, 0.0)

    for key in book.keys:
        assert projected['probs'][key] == pytest.approx(expected[key], abs=1e-15), key


def test_tier_a_five_goal_total_equals_the_six_cells() -> None:
    """6 个 5 球格在竞彩侧的合计，等于这 6 个键的概率和（不是别的什么）。"""
    jc = _jc_probs()
    analysis = sb.analyze_residuals(load_fixture(JC_FIXTURE), load_fixture(MARKET26_FIXTURE))
    expected = sum(jc[k] for k in sb.JC_FIVE_GOAL_CELLS)
    assert analysis['five_goal_total'] == pytest.approx(expected, abs=1e-15)
    assert set(analysis['five_goal_cells']) == set(sb.JC_FIVE_GOAL_CELLS)


def test_tier_a_other_delta_is_sum_of_bucket_deltas() -> None:
    """「其他」合计差 = 各可达桶差之和；有不可达桶时合计**必须**不可比（None）。

    竞彩 31 / 市场 26 的对照里，「任何其他」在默认口径下不可达，
    它那份市场概率无法换算到竞彩侧，所以「其他」合计差没有可比值 ——
    给 None 是诚实的，凑一个「只加可达桶」的数字反而会读成定价差。
    """
    analysis = sb.analyze_residuals(load_fixture(JC_FIXTURE), load_fixture(MARKET26_FIXTURE))
    tier_a = analysis['tier_a']
    other_rows = [r for r in tier_a['rows'] if r['is_other'] and r['delta'] is not None]
    assert other_rows
    if tier_a['structural_rows']:
        # 有结构性桶：合计不可比，且每个结构性桶的 delta 都是 None。
        assert tier_a['other_market'] is None
        assert tier_a['other_delta'] is None
        for row in tier_a['structural_rows']:
            assert row['delta'] is None
        assert any('结构性差异' in w for w in tier_a['warnings'])
    else:
        assert tier_a['other_delta'] == pytest.approx(
            sum(r['delta'] for r in other_rows), abs=1e-12,
        )


def test_tier_a_is_lossless_only_under_default_policy() -> None:
    """`keep_43_34` 打开时口径不可无损比较，必须如实标记。

    反证：把 `jc_to_market_map` 里的 `lossless=not policy.keep_43_34` 改成
    恒 True → 第一条断言变红。
    """
    lossless = sb.jc_to_market_map(sb.DEFAULT_MARKET_POLICY, sb.MARKET_BOOK_25)
    lossy = sb.jc_to_market_map(sb.POLICY_KEEP_43_34, sb.MARKET_BOOK_26)
    assert lossless.per_bucket_lossless is True
    assert lossless.unresolved == ()
    assert lossy.per_bucket_lossless is False
    assert set(lossy.unresolved) == {'胜其他', '负其他'}


# --------------------------------------------------------------------------- #
# 6. 档 B：模型摊薄
# --------------------------------------------------------------------------- #
def test_tier_b_refuses_without_model_parameters() -> None:
    """没有 λ 就拒绝摊薄，不用平均分摊冒充。

    反证：把 `expand_market_to_jc31` 里对 lam_h 的 None 检查删掉，
    `pytest.raises(ValueError)` 变红。
    """
    with pytest.raises(ValueError, match='没有模型参数'):
        sb.expand_market_to_jc31({'主胜其他': 0.1}, None, 1.2)
    with pytest.raises(ValueError, match='没有模型参数'):
        sb.expand_market_to_jc31({'主胜其他': 0.1}, 1.2, None)


def test_tier_b_is_always_marked_modeled() -> None:
    analysis = sb.analyze_residuals(
        load_fixture(JC_FIXTURE), load_fixture(MARKET26_FIXTURE),
        model={'lam_h': 1.45, 'lam_a': 1.05, 'rho': -0.04},
    )
    assert analysis['tier_b'] is not None
    assert analysis['tier_b']['modeled'] is True
    assert analysis['tier_b']['model']['lam_h'] == pytest.approx(1.45)
    assert analysis['tier_b']['model']['lam_a'] == pytest.approx(1.05)
    assert analysis['tier_b']['model']['rho'] == pytest.approx(-0.04)


def test_tier_b_absent_without_model_but_tier_a_still_present() -> None:
    analysis = sb.analyze_residuals(load_fixture(JC_FIXTURE), load_fixture(MARKET26_FIXTURE))
    assert analysis['tier_b'] is None
    assert analysis['tier_a']['rows']
    assert any('档 B 未运行' in w for w in analysis['warnings'])


def test_tier_b_bucket_models_sum_to_one() -> None:
    """每个桶内的条件分布合计必须为 1（分桶不漏项）。"""
    analysis = sb.analyze_residuals(
        load_fixture(JC_FIXTURE), load_fixture(MARKET26_FIXTURE),
        model={'lam_h': 1.45, 'lam_a': 1.05, 'rho': -0.04},
    )
    models = analysis['tier_b']['bucket_models']
    assert models
    for bucket, dist in models.items():
        assert abs(sum(dist.values()) - 1.0) < 1e-9, bucket


def test_tier_b_degenerate_model_matches_tier_a_within_tolerance() -> None:
    """退化一致性：档 B 摊薄后的 31 维，重新聚合回市场桶必须守恒。

    守恒的口径：`回投值 + roundtrip_loss == 市场原始桶值`，每一项都在 1e-9 内。
    结构差（6:0 之类只存在于市场「任何其他」、在竞彩 31 维里只有「胜其他」
    这一个槽）不参与守恒，但必须被量化——这正是 `roundtrip_loss` 的用途。

    反证：把 `expand_market_to_jc31` 里的 `roundtrip_loss` 记账删掉，
    `roundtrip_loss_total` 变 0 → 本测试的守恒等式变红（因为回投值偏多）。
    """
    market_odds = load_fixture(MARKET26_FIXTURE)
    jc_odds = load_fixture(JC_FIXTURE)
    analysis = sb.analyze_residuals(
        jc_odds, market_odds, model={'lam_h': 0.02, 'lam_a': 0.02, 'rho': 0.0},
    )
    tier_b = analysis['tier_b']
    assert tier_b is not None

    market_norm = sb.normalize_market_odds(market_odds, sb.MARKET_BOOK_26)
    tier_b_probs = {r['score']: r['model'] for r in tier_b['residuals'] if r['model'] is not None}
    reagg = sb.project_jc31_onto_market(
        tier_b_probs, policy=sb.DEFAULT_MARKET_POLICY, book=sb.MARKET_BOOK_26,
    )
    losses = tier_b['roundtrip_loss']
    recon = tier_b['bucket_reconciliation']
    for key in sb.MARKET_BOOK_26.keys:
        returned = reagg['probs'][key]
        lost = losses.get(key, 0.0)
        # 该桶自己摊出去又回到该桶的质量
        own = (recon.get(key) or {}).get('returned_to_same_bucket')
        if own is None:
            # 精确格：直接原样复制，必然守恒
            assert returned == pytest.approx(market_norm.probs[key], abs=1e-9), key
            continue
        # 桶：回投值 = 本桶摊出去又回来的部分 + 别的桶摊到本桶键上的部分
        assert own + lost == pytest.approx(market_norm.probs[key], abs=1e-9), key
        assert returned >= own - 1e-12

    # 档 A 的桶值来自竞彩侧，总量也必须守恒
    tier_a = analysis['tier_a']
    assert sum(r['jc'] for r in tier_a['rows']) == pytest.approx(1.0, abs=1e-9)


def test_roundtrip_loss_is_reported_and_nonzero_for_any_other_bucket() -> None:
    """「任何其他」的结构差必须被量化，且带一条可读告警。

    反证：把 `if jc_to_market_key(target,...) != market_key: lost += share`
    删掉，`roundtrip_loss_total` 变 0，本断言变红。
    """
    analysis = sb.analyze_residuals(
        load_fixture(JC_FIXTURE), load_fixture(MARKET26_FIXTURE),
        model={'lam_h': 1.45, 'lam_a': 1.05, 'rho': -0.04},
    )
    tier_b = analysis['tier_b']
    assert tier_b is not None
    losses = tier_b['roundtrip_loss']
    assert losses, '「任何其他」里的 6:0 这类比分回不到原桶，必须记账'
    assert tier_b['roundtrip_loss_total'] > 0.0
    assert any('结构差' in w for w in tier_b['warnings'])
    assert '任何其他' in losses


def test_bucket_with_no_model_mass_is_counted_as_structural_loss() -> None:
    """压测发现的缺陷：模型在某桶上完全没有质量时，该桶概率曾静默消失。

    触发输入：`keep_43_34=True` 且 5 球及以上并入「任何其他」时，
    **主胜其他的覆盖集是空集**（4:3 成了独立格，4:0/4:1/4:2 本来就是精确格，
    5 球及以上又都去了任何其他）——这是个合法的口径组合，不是非法输入。
    此时该桶永远拿不到条件分布。

    修复前：这类桶被 `continue` 跳过，市场给它的概率从输出里凭空蒸发，
    守恒式对不上却没有任何告警。
    修复后：整笔计入 `roundtrip_loss` 并留一条告警，守恒式重新成立。

    反证：把 `expand_market_to_jc31` 里 `if not conditional:` 分支的
    `roundtrip_loss[market_key] = mass` 删掉 → 下面的守恒断言变红。
    """
    policy = sb.POLICY_KEEP_43_34
    empty = [
        b for b in sb.MARKET_BOOK_26.buckets
        if not sb.market_bucket_coverage(policy, sb.MARKET_BOOK_26)[b]
    ]
    assert sorted(empty) == ['主胜其他', '客胜其他'], f'该口径下两个方向桶的覆盖集本应为空，实际 {empty}'

    market_odds = {k: 8.0 for k in sb.MARKET_26_KEYS}
    market_norm = sb.normalize_market_odds(market_odds, sb.MARKET_BOOK_26)
    expansion = sb.expand_market_to_jc31(
        market_norm.probs, 1.45, 1.05, -0.04,
        book=sb.MARKET_BOOK_26, policy=policy,
    )

    bucket = '主胜其他'
    assert bucket in expansion.roundtrip_loss, bucket
    recon = expansion.bucket_reconciliation[bucket]
    assert recon['returned_to_same_bucket'] == 0.0
    assert recon['unrepresentable'] == pytest.approx(market_norm.probs[bucket], abs=1e-12)
    assert any('没有质量' in w for w in expansion.warnings)

    # 每个桶：回投 + 结构差 == 市场原值
    for bucket in sb.MARKET_BOOK_26.buckets:
        own = expansion.bucket_reconciliation[bucket]['returned_to_same_bucket']
        lost = expansion.roundtrip_loss.get(bucket, 0.0)
        assert own + lost == pytest.approx(market_norm.probs[bucket], abs=1e-12), bucket


def test_tier_b_bucket_model_matches_dc_grid() -> None:
    """桶内条件分布必须来自 DC 网格，而不是平均分摊。

    反证：把 `bucket_probability_mass` 改成 `1/len(members)` 平均分摊，
    本断言变红（主胜其他桶里的 4:3 概率不再是网格给的）。
    """
    grid = sb.dc_grid(1.4, 1.1, rho=-0.03, max_goals=10)
    dist = sb.bucket_probability_mass('主胜其他', grid, sb.MARKET_BOOK_26, sb.DEFAULT_MARKET_POLICY)
    coverage = sb.market_bucket_coverage(sb.DEFAULT_MARKET_POLICY, sb.MARKET_BOOK_26)
    members = coverage['主胜其他']
    assert set(dist) <= set(members)
    assert abs(sum(dist.values()) - 1.0) < 1e-9

    expected_total = sum(grid.probs.get(s, 0.0) for s in members)
    for score, share in dist.items():
        assert share == pytest.approx(grid.probs[score] / expected_total, abs=1e-12), score

    # 平摊与网格给出的分布一定不同。默认口径下「任何其他」的覆盖集只剩
    # 5 球及以上以外的罕见比分，所以改用覆盖集最大的那个桶来比。
    biggest = max(coverage, key=lambda b: len(coverage[b]))
    wide = sb.bucket_probability_mass(biggest, grid, sb.MARKET_BOOK_26, sb.DEFAULT_MARKET_POLICY)
    assert wide, biggest
    flat = 1.0 / len(coverage[biggest])
    assert max(abs(wide[s] - flat) for s in wide) > 1e-6


def test_tier_b_kl_skips_zero_probability_keys() -> None:
    """p=0 的键不进 KL；`zero_prob_keys` 与输入里 p=0 的键集相等。

    反证：把 `kl_divergence` 里的 `if value <= 0.0: continue` 换成
    `max(value, 1e-12)` → `zero_prob_keys` 变空 → 本断言变红。
    """
    p = {'1:0': 0.5, '2:0': 0.0, '3:0': 0.5}
    q = {'1:0': 0.5, '2:0': 0.0, '3:0': 0.5}
    kl, zeros = sb.kl_divergence(p, q)
    assert set(zeros) == {'2:0'}
    assert kl == pytest.approx(0.0, abs=1e-12)
    # 若把 0 项也算进去，KL 会变成 0.5*log(0.5/1e-12) 这种虚高值
    inflated = 0.5 * math.log(0.5 / 1e-12)
    assert kl < inflated / 1000.0


def test_kl_zero_set_equals_input_zero_set() -> None:
    """zero_prob_keys 与输入 p=0 的集合逐项相等（不是「有报就行」）。"""
    odds = load_fixture(JC_FIXTURE)
    jc = sb.normalize_jc_odds(odds).probs
    jc = dict(jc)
    for key in ('5:0', '5:1', '0:5'):
        jc[key] = 0.0
    result = sb.analyze_residuals(
        odds, load_fixture(MARKET26_FIXTURE), model={'lam_h': 1.4, 'lam_a': 1.1},
    )
    assert result['tier_b'] is not None
    _, zeros = sb.kl_divergence(jc, {r['score']: (r['model'] or 0.0) for r in result['tier_b']['residuals']})
    assert set(zeros) == {k for k, v in jc.items() if v == 0.0}


def test_kl_returns_inf_when_q_is_zero_where_p_is_positive() -> None:
    kl, _zeros = sb.kl_divergence({'1:0': 1.0}, {'1:0': 0.0})
    assert kl == math.inf


# --------------------------------------------------------------------------- #
# 7. λ 拟合
# --------------------------------------------------------------------------- #
def test_fit_lambda_recovers_a_known_generating_pair() -> None:
    """从模型自己的 (λh, λa) 生成的 1X2+大小球 反推回同一对 λ。"""
    truth_h, truth_a = 1.55, 1.05
    home, draw, away, over, _ = sb._model_1x2_ou(truth_h, truth_a, line=2.5, rho=0.0)
    fit = sb.fit_lambda_from_market_1x2_and_ou(home, draw, away, over)
    assert fit.accepted is True, fit.reason
    assert fit.loss is not None and fit.loss < 1e-6
    assert fit.lam_h == pytest.approx(truth_h, abs=0.12)
    assert fit.lam_a == pytest.approx(truth_a, abs=0.12)


def test_fit_lambda_expands_the_box_when_the_optimum_is_outside() -> None:
    """最优解在 ±0.8 框外时扩框重搜，最终 hit_boundary 为 False。

    构造：一个强主队（λh≈3.6, λa≈0.9）。中心公式给的位置离真实解很远，
    框住不下，只能靠扩框。

    反证：把 `max_rounds` 设为 1（回到旧的一轮逻辑）→
    `fit.accepted` 变 False / `fit.hit_boundary` 变 True → 本断言变红。
    """
    truth_h, truth_a = 3.6, 0.9
    home, draw, away, over, _ = sb._model_1x2_ou(truth_h, truth_a, line=2.5, rho=0.0)

    one_round = sb.fit_lambda_from_market_1x2_and_ou(
        home, draw, away, over, max_rounds=1,
    )
    assert one_round.hit_boundary is True or one_round.accepted is False, (
        f'旧的一轮逻辑本应在边界上：{one_round.as_dict()}'
    )

    expanded = sb.fit_lambda_from_market_1x2_and_ou(home, draw, away, over)
    assert expanded.hit_boundary is False
    assert expanded.accepted is True, expanded.reason
    assert expanded.rounds >= 2
    assert expanded.lam_h == pytest.approx(truth_h, abs=0.2)
    assert expanded.lam_a == pytest.approx(truth_a, abs=0.2)


def test_fit_lambda_refuses_when_two_rounds_still_hit_the_boundary() -> None:
    """两轮仍触边界就拒绝输出，不许把坏拟合当结果。

    `max_rounds=2` 且 `half_width` 小到即使扩两轮仍框不住时，
    必须 `accepted=False` 且 `reason` 点名边界。

    反证：把「两轮后仍触边界就拒绝」的分支删掉（直接返回 best），
    `fit.accepted is False` 变红。
    """
    truth_h, truth_a = 5.5, 0.35
    home, draw, away, over, _ = sb._model_1x2_ou(truth_h, truth_a, line=2.5, rho=0.0)
    fit = sb.fit_lambda_from_market_1x2_and_ou(
        home, draw, away, over, half_width=0.05, max_rounds=2,
    )
    assert fit.accepted is False
    assert fit.hit_boundary is True
    assert fit.lam_h is None and fit.lam_a is None
    assert '边界' in fit.reason


def test_fit_lambda_refuses_when_loss_exceeds_tolerance() -> None:
    """残差超容差 → 拒绝，且 loss 仍被报出来（供读者判断有多坏）。

    构造：给一组自洽的目标概率加一个极小的容差，拟合本身能收敛但达不到要求。

    反证：把 `best_loss > loss_tol` 的分支删掉 → `accepted is False` 变红。
    """
    home, draw, away, over, _ = sb._model_1x2_ou(1.5, 1.1, line=2.5)
    fit = sb.fit_lambda_from_market_1x2_and_ou(home, draw, away, over, loss_tol=1e-14)
    assert fit.accepted is False
    assert fit.loss is not None and fit.loss > 1e-14
    assert '容差' in fit.reason
    assert fit.lam_h is None and fit.lam_a is None


def test_fit_lambda_boundary_refusal_names_the_boundary_not_the_tolerance() -> None:
    """两轮仍触边界时，原因必须说边界（不是拿容差搪塞）。"""
    truth_h, truth_a = 5.5, 0.35
    home, draw, away, over, _ = sb._model_1x2_ou(truth_h, truth_a, line=2.5)
    fit = sb.fit_lambda_from_market_1x2_and_ou(
        home, draw, away, over, half_width=0.05, max_rounds=2, loss_tol=1e-9,
    )
    assert fit.accepted is False
    assert fit.hit_boundary is True
    assert '边界' in fit.reason
    assert '容差' not in fit.reason


def test_fit_lambda_rejects_missing_or_out_of_range_inputs() -> None:
    for args in ((None, 0.3, 0.3, 0.5), (0.5, float('nan'), 0.3, 0.5),
                 (0.5, 0.3, 0.3, 1.7), (-0.1, 0.5, 0.5, 0.5)):
        fit = sb.fit_lambda_from_market_1x2_and_ou(*args)
        assert fit.accepted is False
        assert fit.lam_h is None


def test_fit_lambda_loss_is_always_reported() -> None:
    """无论接受还是拒绝，loss 都必须是一个可打印的数（不许 None 混过去）。"""
    home, draw, away, over, _ = sb._model_1x2_ou(1.5, 1.1, line=2.5)
    for fit in (
        sb.fit_lambda_from_market_1x2_and_ou(home, draw, away, over),
        sb.fit_lambda_from_market_1x2_and_ou(home, draw, away, over, loss_tol=1e-14),
        sb.fit_lambda_from_market_1x2_and_ou(0.98, 0.01, 0.01, 0.999,
                                             half_width=0.05, max_rounds=2),
    ):
        assert fit.loss is not None
        assert math.isfinite(fit.loss)


# --------------------------------------------------------------------------- #
# 8. tau clamp 必须上报
# --------------------------------------------------------------------------- #
def test_tau_clamp_is_counted_not_silently_flattened() -> None:
    """rho 越界时 tau 被截断，且计数如实上报。

    反证：把 `dc_tau` 里的 `clamped` 去掉（永远返回 False），
    `grid.tau_clamped_cells > 0` 变红。
    """
    # 1 - lam_h*lam_a*rho < 0 需要 rho > 1/(lam_h*lam_a)；取大的 rho
    grid = sb.dc_grid(2.0, 2.0, rho=0.9, max_goals=6)
    assert grid.tau_clamped_cells > 0
    tau, clamped = sb.dc_tau(0, 0, 2.0, 2.0, 0.9)
    assert clamped is True
    assert tau == pytest.approx(sb.DC_TAU_FLOOR)


def test_tau_not_clamped_for_ordinary_rho() -> None:
    tau, clamped = sb.dc_tau(1, 1, 1.5, 1.1, -0.04)
    assert clamped is False
    assert tau == pytest.approx(1.04)


def test_tau_clamp_surfaces_in_projection_output() -> None:
    """投影结果带着 tau_clamped_cells，CLI 才有东西可提示。"""
    result = sb.dixon_coles_to_jc31(2.0, 2.0, rho=0.9)
    assert result['tau_clamped_cells'] > 0


# --------------------------------------------------------------------------- #
# 9. 端到端：齐全输入 + 缺项输入
# --------------------------------------------------------------------------- #
def test_end_to_end_complete_input_produces_no_completeness_warning() -> None:
    """数据齐全时：两侧 complete，RTP 都是真数，且只有档 B 缺失这一条告警。"""
    analysis = sb.analyze_residuals(load_fixture(JC_FIXTURE), load_fixture(MARKET26_FIXTURE))
    assert analysis['jc']['complete'] is True
    assert analysis['market']['complete'] is True
    assert analysis['jc']['rtp'] is not None
    assert analysis['market']['rtp'] is not None
    assert analysis['jc']['probs'] and abs(sum(analysis['jc']['probs'].values()) - 1) < 1e-12
    assert analysis['market']['probs'] and abs(sum(analysis['market']['probs'].values()) - 1) < 1e-12
    completeness = [w for w in analysis['warnings'] if '样本不完整' in w]
    assert completeness == []


def test_end_to_end_incomplete_input_yields_none_rtp_not_a_fake_number() -> None:
    """缺项输入：RTP 明确是 None，不是任何「看起来还行」的数字。"""
    odds = load_fixture(JC_FIXTURE)
    del odds['5:2']
    analysis = sb.analyze_residuals(odds, load_fixture(MARKET26_FIXTURE))
    assert analysis['jc']['complete'] is False
    assert analysis['jc']['rtp'] is None
    assert analysis['jc']['missing'] == ['5:2']
    assert any('样本不完整' in w for w in analysis['warnings'])


def test_end_to_end_25_book_skips_draw_other_bucket() -> None:
    analysis = sb.analyze_residuals(
        load_fixture(JC_FIXTURE), load_fixture(MARKET25_FIXTURE), book_size=25,
    )
    assert analysis['book_size'] == 25
    buckets = {r['bucket'] for r in analysis['tier_a']['rows']}
    assert '平局其他' not in buckets
    assert '任何其他' in buckets
    assert analysis['jc']['complete'] is True
    assert analysis['market']['complete'] is True


def test_rendered_text_has_no_non_finite_figure() -> None:
    """渲染出的正文里不许出现 inf/nan 数字（number_text 守卫在边界上）。"""
    analysis = sb.analyze_residuals(
        load_fixture(JC_FIXTURE), load_fixture(MARKET26_FIXTURE),
        model={'lam_h': 1.45, 'lam_a': 1.05, 'rho': -0.04},
    )
    text = sb.format_analysis_text(analysis)
    assert 'inf' not in text.lower()
    assert 'nan' not in text.lower()
    assert 'RTP' in text


# --------------------------------------------------------------------------- #
# 10. CLI
# --------------------------------------------------------------------------- #
def test_cli_runs_offline_and_reports_backtest_as_pending(capsys) -> None:
    sys.path.insert(0, str(ROOT / 'scripts'))
    import compare_score_books as cli

    code = cli.main(['--jc', str(JC_FIXTURE), '--market', str(MARKET26_FIXTURE)])
    out = capsys.readouterr().out
    assert code == 0
    assert '待跑' in out
    assert '第四维·分档回测' in out
    # 不许出现任何命中率/收益率口径的数字
    for banned in ('命中率', '收益率', '回报率', 'ROI'):
        assert banned not in out


def test_cli_json_output_is_deterministic_and_modeled_flag_present(capsys) -> None:
    sys.path.insert(0, str(ROOT / 'scripts'))
    import compare_score_books as cli

    argv = ['--jc', str(JC_FIXTURE), '--market', str(MARKET26_FIXTURE),
            '--model', 'lam_h=1.45,lam_a=1.05,rho=-0.04', '--json']
    cli.main(argv)
    first = capsys.readouterr().out
    cli.main(argv)
    second = capsys.readouterr().out
    assert first == second
    payload = json.loads(first)
    assert payload['tier_b']['modeled'] is True
    assert payload['backtest']['pending'] is True


def test_cli_policy_combination_is_printed(capsys) -> None:
    sys.path.insert(0, str(ROOT / 'scripts'))
    import compare_score_books as cli

    cli.main(['--jc', str(JC_FIXTURE), '--market', str(MARKET26_FIXTURE),
              '--policy', 'five_goals_in_team_other,keep_43_34'])
    out = capsys.readouterr().out
    assert '单队5球及以上→方向桶' in out
    assert '4:3/3:4=独立格' in out


def test_cli_rejects_conflicting_policy_flags() -> None:
    sys.path.insert(0, str(ROOT / 'scripts'))
    import compare_score_books as cli

    with pytest.raises(SystemExit):
        cli.main(['--jc', str(JC_FIXTURE), '--market', str(MARKET26_FIXTURE),
                  '--policy', 'five_goals_in_team_other,five_goals_in_any_other'])


def test_cli_rejects_model_without_both_lambdas() -> None:
    sys.path.insert(0, str(ROOT / 'scripts'))
    import compare_score_books as cli

    with pytest.raises(SystemExit):
        cli.main(['--jc', str(JC_FIXTURE), '--market', str(MARKET26_FIXTURE),
                  '--model', 'lam_h=1.4'])


# --------------------------------------------------------------------------- #
# 11. 口径冲突的构造必须拒绝
# --------------------------------------------------------------------------- #
def test_conflicting_five_goal_policy_is_rejected() -> None:
    """两个 five_goals 开关同时为真没有唯一答案，必须抛错而不是挑一个。"""
    with pytest.raises(ValueError, match='只能选一个'):
        sb.MarketBucketPolicy(five_goals_in_team_other=True, five_goals_in_any_other=True)


def test_policy_label_is_stable_and_prints_both_axes() -> None:
    assert '5球' in sb.DEFAULT_MARKET_POLICY.label()
    assert '4:3/3:4' in sb.DEFAULT_MARKET_POLICY.label()
    assert sb.POLICY_TEAM_OTHER_KEEP.label().endswith('4:3/3:4=独立格')


# --------------------------------------------------------------------------- #
# 12. 缺陷 1 回归：lossless 必须算出来，默认口径必须是无损的那套
# --------------------------------------------------------------------------- #
def test_lossless_is_computed_from_coverage_not_declared_from_policy() -> None:
    """`lossless` 来自逐桶覆盖集比对，不是 policy 标志位。

    历史实现写死 `not policy.keep_43_34`，于是默认口径（5 球→任何其他）
    被标成无损——而竞彩的「胜其他」实际横跨「主胜其他」与「任何其他」两个桶。

    反证：把 `jc_to_market_map` 里的 `lossless=verdict["lossless"]` 改回
    `lossless=not policy.keep_43_34` → 本测试第一段对默认口径的
    `m.lossless is False` 立刻变红。
    """
    lossy = sb.jc_to_market_map(sb.POLICY_ANY_OTHER, sb.MARKET_BOOK_26)
    assert lossy.lossless is False, '5球→任何其他 不是无损口径'
    assert set(lossy.one_to_many_buckets) == {'主胜其他', '客胜其他', '任何其他'}
    assert set(lossy.unresolved) == {'胜其他', '负其他'}

    # 26 项盘面下 TEAM_OTHER 的「任何其他」不可达：盘面必报这个键，
    # 口径却判定竞彩落不进任何概率 —— 逐桶不成立（聚合层仍成立，另测）。
    good26 = sb.jc_to_market_map(sb.POLICY_TEAM_OTHER, sb.MARKET_BOOK_26)
    assert good26.per_bucket_lossless is False
    assert good26.unreachable_buckets == ('任何其他',)
    assert good26.one_to_many_buckets == ()
    assert good26.bucket_gaps == {}

    # 25 项盘面没有独立平局其他，平局其他落进任何其他，因此逐桶无损。
    good25 = sb.jc_to_market_map(sb.POLICY_TEAM_OTHER, sb.MARKET_BOOK_25)
    assert good25.per_bucket_lossless is True
    assert good25.unreachable_buckets == ()
    assert good25.one_to_many_buckets == ()
    assert good25.bucket_gaps == {}


def test_lossless_verdict_agrees_with_the_same_distribution_self_check() -> None:
    """口径判定与「同分布两边喂 Δ 应全 0」的实测必须一致。

    这是缺陷 1 的决定性证据：`lossless=True` 的口径自检 max|Δ| 必须是 0，
    `lossless=False` 的口径必须非 0。

    反证：把 `verify_lossless` 改成永远返回 True → 第一段对
    `POLICY_ANY_OTHER` 的 `assert check['max_abs_delta'] > 1e-6` 之前，
    `assert lossy.lossless is False` 先变红。
    """
    good = sb.caliber_self_check(sb.POLICY_TEAM_OTHER, sb.MARKET_BOOK_25)
    assert good['max_abs_delta'] < 1e-12, good['max_abs_delta']

    lossy = sb.caliber_self_check(sb.POLICY_ANY_OTHER, sb.MARKET_BOOK_26)
    assert lossy['lossless'] is False
    assert lossy['max_abs_delta'] > 1e-6
    # 用户实测的数字：0.01451（λh=1.9 λa=0.7）
    assert lossy['max_abs_delta'] == pytest.approx(0.01451, abs=5e-4)


def test_default_policy_is_the_lossless_one() -> None:
    """默认口径必须就是经比对确认无损的那一套。

    反证：把 `DEFAULT_MARKET_POLICY` 的 `five_goals_in_any_other` 改回 True
    → `assert sb.DEFAULT_MARKET_POLICY.five_goals_in_team_other is True` 变红，
    且 `caliber_self_check(DEFAULT)['clean']` 也变红。
    """
    assert sb.DEFAULT_MARKET_POLICY.five_goals_in_team_other is True
    assert sb.DEFAULT_MARKET_POLICY.five_goals_in_any_other is False

    # 25 项盘面：逐桶与聚合都无损。
    book25 = sb.market_book(25)
    assert sb.jc_to_market_map(sb.DEFAULT_MARKET_POLICY, book25).per_bucket_lossless is True
    assert sb.caliber_self_check(sb.DEFAULT_MARKET_POLICY, book25)['clean'] is True

    # 26 项盘面：默认口径的「任何其他」不可达，所以逐桶不成立，聚合仍成立。
    # 这是盘面尺寸决定的，不是默认口径选错 —— 见
    # `test_book26_with_team_other_warns_its_any_other_is_empty`。
    book26 = sb.market_book(26)
    default26 = sb.jc_to_market_map(sb.DEFAULT_MARKET_POLICY, book26)
    assert default26.per_bucket_lossless is False
    assert default26.aggregate_lossless is True
    assert default26.unreachable_buckets == ('任何其他',)


def test_non_lossless_policy_warns_with_the_self_check_number() -> None:
    """选了不可无损的口径时，bucket_deltas 必须带自检数字，不许让读者当定价差。

    反证：把 `bucket_deltas` 里 `if not projected["lossless"]:` 那段
    告警删掉 → `assert tier_a['warnings']` 变红。
    """
    jc = {k: 1.0 / 31.0 for k in sb.JC_31_KEYS}
    market = {k: 1.0 / 26.0 for k in sb.MARKET_26_KEYS}
    tier_a = sb.bucket_deltas(jc, market, policy=sb.POLICY_ANY_OTHER, book=sb.MARKET_BOOK_26)
    assert tier_a['lossless'] is False
    assert tier_a['warnings'], '不可无损的口径必须告警'
    joined = ' '.join(tier_a['warnings'])
    assert '口径伪差' in joined
    assert '0.01451' in joined or 'max|Δ|' in joined
    assert tier_a['caliber_self_check']['max_abs_delta'] > 1e-6


def test_lossless_policy_emits_no_caliber_warning() -> None:
    """真正逐桶无损的组合（25 项 + TEAM_OTHER）不该有任何口径告警。"""
    jc = {k: 1.0 / 31.0 for k in sb.JC_31_KEYS}
    market = {k: 1.0 / 25.0 for k in sb.MARKET_25_KEYS}
    tier_a = sb.bucket_deltas(jc, market, policy=sb.POLICY_TEAM_OTHER, book=sb.MARKET_BOOK_25)
    assert tier_a['per_bucket_lossless'] is True
    assert tier_a['aggregate_lossless'] is True
    assert tier_a['warnings'] == []
    assert tier_a['structural_rows'] == []


def test_jc_key_spans_buckets_identifies_win_other() -> None:
    """胜其他在 5球→任何其他 口径下确实横跨两个市场桶。"""
    assert sb.jc_key_spans_buckets('胜其他', policy=sb.POLICY_ANY_OTHER) is True
    assert sb.jc_key_spans_buckets('负其他', policy=sb.POLICY_ANY_OTHER) is True
    # 5球→方向桶 口径下不横跨
    assert sb.jc_key_spans_buckets('胜其他', policy=sb.POLICY_TEAM_OTHER) is False
    # 精确格永不横跨
    assert sb.jc_key_spans_buckets('5:0', policy=sb.POLICY_ANY_OTHER) is False


# --------------------------------------------------------------------------- #
# 13. 缺陷 2 回归：别名重复键不许求和
# --------------------------------------------------------------------------- #
def test_alias_duplicate_keys_are_rejected_not_summed() -> None:
    """`{'1:0':3.0,'1-0':3.0}` 归一后是同一个比分，不许累加。

    历史实现累加进 raw，分母变大，算出 rtp=1.1538（返还率 115%，现实不存在）。

    反证：把 `normalize_odds` 里 `duplicate_inputs` 的检测删掉（恢复累加）
    → `assert result.rtp is None` 变红（会得到 1.1538）。
    """
    result = sb.normalize_odds(
        {'1:0': 3.0, '1-0': 3.0, '0:0': 5.0}, required=['1:0', '0:0'],
    )
    assert result.duplicate_inputs == (('1:0', ('1:0', '1-0')),)
    assert result.complete is False
    assert result.rtp is None
    assert any('别名重复' in w for w in result.warnings)


def test_alias_duplicate_cannot_produce_a_100_percent_return() -> None:
    """31 项全 10.0 再在别名上重复一份，旧口径把 rtp 从 1.0 变成 0.170。

    修后：重复即拒绝，绝不给出一个「看起来还行」的返还率。
    """
    odds = {k: 10.0 for k in sb.JC_31_KEYS}
    clean = sb.normalize_jc_odds(odds)
    # 10.0 × 31 → 隐含 310%，本身也不是真实盘口；这里关心的是「重复」行为
    assert clean.duplicate_inputs == ()

    duplicated = dict(odds)
    duplicated['1-0'] = 10.0          # 1:0 的别名
    dup = sb.normalize_jc_odds(duplicated)
    assert dup.duplicate_inputs == (('1:0', ('1:0', '1-0')),)
    assert dup.rtp is None
    assert dup.complete is False


def test_impossible_rtp_is_rejected_not_reported() -> None:
    """返还率 >= 100% 不是真实盘口，必须置 None 并告警。

    反证：把 `if candidate >= 1.0:` 那条分支删掉 → `assert result.rtp is None`
    变红（会返回 1.8182）。
    """
    # 只有两个选项时，赔率之和 < 2 就意味着 1/sum(1/o) > 1
    result = sb.normalize_odds({'1:0': 2.5, '2:0': 2.5}, required=['1:0', '2:0'])
    assert result.complete is True          # 键是齐的
    assert result.rtp is None               # 但样本自相矛盾
    assert any('自相矛盾' in w for w in result.warnings)
    assert result.raw_sum is not None and result.raw_sum < 1.0


def test_fair_book_still_reports_a_sub_one_rtp() -> None:
    """正常盘口（隐含 155%）照常给出 rtp，不被上面那条误杀。"""
    odds = {k: 20.0 for k in sb.JC_31_KEYS}
    result = sb.normalize_jc_odds(odds)
    assert result.complete is True
    assert result.rtp is not None
    assert 0.0 < result.rtp < 1.0


# --------------------------------------------------------------------------- #
# 14. 缺陷 3 回归：表外键不许进分母
# --------------------------------------------------------------------------- #
def test_out_of_universe_keys_are_rejected_and_do_not_move_the_denominator() -> None:
    """多给一个 `9:9` 不许拉偏分母。

    历史实现把它算进 raw 与 probs，rtp 从 0.322581 变成 0.321543，
    每个桶的 Δ 都被同一个量整体推移。

    反证：把 `if universe is not None and score not in universe:` 那段删掉
    → `assert dirty.rtp == pytest.approx(clean.rtp)` 变红。
    """
    odds = {k: 10.0 for k in sb.JC_31_KEYS}
    clean = sb.normalize_jc_odds(odds)
    dirty_map = dict(odds)
    dirty_map['9:9'] = 100.0
    dirty = sb.normalize_jc_odds(dirty_map)

    assert dirty.rejected == ('9:9',)
    assert '9:9' not in dirty.probs
    assert dirty.rtp == pytest.approx(clean.rtp, abs=1e-15)
    assert dirty.raw_sum == pytest.approx(clean.raw_sum, abs=1e-15)
    assert any('不属于本盘口' in w for w in dirty.warnings)


def test_probs_keys_equal_required_and_sum_to_one_when_complete() -> None:
    """`complete=True` 时 probs 键集恰好等于 required，且和恒为 1（1e-12 内）。"""
    odds = {k: 10.0 for k in sb.JC_31_KEYS}
    odds['9:9'] = 100.0
    odds['12:0'] = 500.0
    result = sb.normalize_jc_odds(odds)
    assert result.complete is True
    assert set(result.probs) == set(sb.JC_31_KEYS)
    assert abs(sum(result.probs.values()) - 1.0) < 1e-12


def test_rejected_and_skipped_are_distinct_categories() -> None:
    """`skipped`=该有的键给了坏值；`rejected`=根本不属于这个盘口。"""
    odds = {k: 10.0 for k in sb.JC_31_KEYS}
    odds['3:0'] = 0            # 该有的键，坏值 → skipped
    odds['9:9'] = 100.0        # 不属于 → rejected
    result = sb.normalize_jc_odds(odds)
    assert result.skipped == ('3:0',)
    assert result.rejected == ('9:9',)
    assert set(result.skipped).isdisjoint(set(result.rejected))


# --------------------------------------------------------------------------- #
# 15. 缺陷 4 回归：空桶不告警、tier_b 告警上行到顶层
# --------------------------------------------------------------------------- #
def test_empty_bucket_does_not_emit_a_structural_loss_warning() -> None:
    """桶质量本来就是 0 时不许发「无法摊薄」告警。

    反证：把 `if mass <= 0.0:` 那段删掉 → `assert not any('没有质量' ...)`
    变红（mass=0.0 也会走进告警分支）。
    """
    market_probs = {k: 0.0 for k in sb.MARKET_26_KEYS}
    expansion = sb.expand_market_to_jc31(
        market_probs, 1.45, 1.05, 0.0, book=sb.MARKET_BOOK_26,
    )
    assert not any('没有质量' in w for w in expansion.warnings)
    assert expansion.roundtrip_loss == {}


def test_tier_b_warnings_reach_the_top_level() -> None:
    """tier_b 的告警必须合并进顶层 warnings 并去重。

    历史实现只在 tier_b 分支内拼给自己，顶层 warnings 是空的，
    读者看不到那条 1.2% 的结构差。

    反证：把 `warnings.extend(tier_b.get("warnings") or [])` 删掉
    → `assert '结构差' in joined` 变红。
    """
    analysis = sb.analyze_residuals(
        {k: 8.0 for k in sb.JC_31_KEYS}, {k: 9.0 for k in sb.MARKET_26_KEYS},
        model={'lam_h': 1.45, 'lam_a': 1.05},
    )
    tier_b = analysis['tier_b']
    assert tier_b is not None
    assert tier_b['warnings'], '这一场本应产生结构差告警'
    joined = '\n'.join(analysis['warnings'])
    for warning in tier_b['warnings']:
        assert warning in joined, warning[:80]
    assert len(analysis['warnings']) == len(set(analysis['warnings']))


def test_structural_loss_names_its_cause() -> None:
    """结构差告警必须点名来源：5 球及以上与 4:3/3:4 的归并，不是实现缺陷。"""
    analysis = sb.analyze_residuals(
        {k: 8.0 for k in sb.JC_31_KEYS}, {k: 9.0 for k in sb.MARKET_26_KEYS},
        model={'lam_h': 1.45, 'lam_a': 1.05},
    )
    joined = '\n'.join(analysis['warnings'])
    assert '5 球及以上' in joined
    assert '不是实现缺陷' in joined


def test_wrong_shaped_market_probs_is_refused_not_silently_zero() -> None:
    """把 `dixon_coles_to_jc31` 的整个返回 dict 当 market_probs 传进来必须抛错。

    反证：删掉 `if not overlap:` 那段 → `pytest.raises(ValueError)` 变红
    （会静默返回全 0、零告警）。
    """
    wrong = sb.dixon_coles_to_jc31(1.4, 1.1)          # {'probs': ..., 'lam_h': ...}
    with pytest.raises(ValueError, match='没有任何交集'):
        sb.expand_market_to_jc31(wrong, 1.4, 1.1)


# --------------------------------------------------------------------------- #
# 16. 缺陷 5 回归：不可达桶 + 逐桶/聚合两层无损
# --------------------------------------------------------------------------- #
def test_unreachable_quoted_bucket_is_not_reported_as_a_price_gap() -> None:
    """26 项盘面 + TEAM_OTHER：`任何其他` 盘面必报、竞彩却落不进任何键。

    此时它的 Δ 恒等于 −market，是构造出来的。修前它被当成定价差排在榜上
    （用户实测：`任何其他 jc=0.00% market=2.35% Δ-2.35pp` 排第 3 行），
    而且 `lossless=True`、零告警。

    反证 1：把 `verify_lossless` 里的 `unreachable` 收集删掉 →
      本测试 `assert verdict['unreachable_buckets'] == ('任何其他',)` 变红。
    反证 2：把 `bucket_deltas` 里 `if key in unreachable:` 分支删掉
      （恢复算数字）→ `assert '任何其他' not in gap_buckets` 变红。
    """
    mk = {k: 20.0 for k in sb.MARKET_26_KEYS}
    mk['任何其他'] = 2.0
    jc = {k: 20.0 for k in sb.JC_31_KEYS}

    analysis = sb.analyze_residuals(jc, mk)
    tier_a = analysis['tier_a']

    # 1) 不可达桶被识别，逐桶无损为假，且带明确告警。
    assert list(tier_a['unreachable_buckets']) == ['任何其他']
    assert tier_a['per_bucket_lossless'] is False
    assert any('结构性差异' in w for w in tier_a['warnings'])

    # 2) 它不在定价差行里，而在结构性差异行里，delta 是 None。
    gap_buckets = {r['bucket'] for r in tier_a['rows']}
    assert '任何其他' not in gap_buckets
    structural = {r['bucket']: r for r in tier_a['structural_rows']}
    assert '任何其他' in structural
    assert structural['任何其他']['delta'] is None
    assert '构造' in structural['任何其他']['reason']

    # 3) 排序里没有它 —— 不许用「Δ 最大」把构造值排到前面。
    assert all(r['bucket'] != '任何其他' for r in analysis['top_deltas'])

    # 4) 它的 Δ 若被算出来，会恰好等于 −market（这就是「构造」的定义）。
    market_norm = sb.normalize_market_odds(mk, sb.MARKET_BOOK_26)
    assert market_norm.probs['任何其他'] > 0.0


def test_unreachable_bucket_delta_is_none_not_a_number() -> None:
    """不可达桶的 delta 必须是 None，不许是 0 或 −market。

    反证：把 `bucket_deltas` 里结构性分支改成 `jc_value - mk_value`
    → `assert row['delta'] is None` 变红。
    """
    jc = {k: 1.0 / 31.0 for k in sb.JC_31_KEYS}
    mk = {k: 1.0 / 26.0 for k in sb.MARKET_26_KEYS}
    tier_a = sb.bucket_deltas(jc, mk, policy=sb.DEFAULT_MARKET_POLICY, book=sb.MARKET_BOOK_26)
    assert tier_a['structural_rows'], '26 项 + 默认口径必有不可达桶'
    for row in tier_a['structural_rows']:
        assert row['delta'] is None
        assert 'delta' in row and row['delta'] is None
    # 定价差行里不许出现结构性桶
    assert not ({r['bucket'] for r in tier_a['structural_rows']}
                & {r['bucket'] for r in tier_a['rows']})


def test_per_bucket_and_aggregate_losslessness_are_reported_separately() -> None:
    """两个层次必须分别报，且实测数字对得上用户复验结果。

    - book26 TEAM_OTHER：逐桶否（任何其他不可达）／聚合是 234==234
    - book26 ANY_OTHER：逐桶否（胜其他横跨）／聚合是 234==234
    - book26 KEEP：逐桶否／聚合否 232 vs 234
    - book25 TEAM_OTHER：逐桶是／聚合是

    反证：把 `verify_lossless` 的返回值合并成一个布尔（只留 `lossless`）
    → 本测试读 `aggregate_lossless` 时 KeyError/断言红。
    """
    cases = [
        (26, sb.POLICY_TEAM_OTHER, False, True, 234, 234),
        (26, sb.POLICY_ANY_OTHER, False, True, 234, 234),
        (26, sb.POLICY_KEEP_43_34, False, False, 232, 234),
        (25, sb.POLICY_TEAM_OTHER, True, True, None, None),
    ]
    for size, policy, per, agg, m_union, jc_union in cases:
        verdict = sb.verify_lossless(policy, sb.market_book(size))
        assert verdict['per_bucket_lossless'] is per, (size, policy.label())
        assert verdict['aggregate_lossless'] is agg, (size, policy.label())
        if m_union is not None:
            assert verdict['aggregate_sizes']['market_buckets_union'] == m_union
            assert verdict['aggregate_sizes']['jc_other_union'] == jc_union


def test_book26_with_team_other_warns_its_any_other_is_empty() -> None:
    """26 项 + TEAM_OTHER 自相矛盾：盘面必报「任何其他」，口径判它覆盖集为空。

    这不是让改默认，而是必须告警并指明出路。

    反证：把 `verify_lossless` 里 `self_contradictory` 与那条告警删掉
    → `assert verdict['self_contradictory_buckets']` / `'自相矛盾' in joined` 变红。
    """
    verdict = sb.verify_lossless(sb.POLICY_TEAM_OTHER, sb.MARKET_BOOK_26)
    assert verdict['self_contradictory_buckets'] == ('任何其他',)
    joined = '\n'.join(verdict['warnings'])
    assert '自相矛盾' in joined
    assert 'five_goals_in_any_other' in joined or '只读可达桶' in joined

    # 同一口径在 25 项盘面上没有这个矛盾（平局其他落进任何其他，覆盖集非空）。
    verdict25 = sb.verify_lossless(sb.POLICY_TEAM_OTHER, sb.MARKET_BOOK_25)
    assert verdict25['self_contradictory_buckets'] == ()
    assert verdict25['unreachable_buckets'] == ()


def test_default_policy_choice_depends_on_book_size() -> None:
    """默认口径的可用性依赖盘面尺寸：26 项下逐桶无损不可得，25 项下可得。

    反证：把 `book.has_draw_other()` 在桶覆盖里忽略掉（25 项也留平局其他）
    → 本测试 25 项那段变红。
    """
    v26 = sb.verify_lossless(sb.DEFAULT_MARKET_POLICY, sb.MARKET_BOOK_26)
    v25 = sb.verify_lossless(sb.DEFAULT_MARKET_POLICY, sb.MARKET_BOOK_25)
    assert v26['per_bucket_lossless'] is False      # 任何其他不可达
    assert v26['aggregate_lossless'] is True
    assert v25['per_bucket_lossless'] is True
    assert v25['aggregate_lossless'] is True


# --------------------------------------------------------------------------- #
# 17. 缺陷 6 回归：合计行不许把不可比渲染成 Δ+0.00pp
# --------------------------------------------------------------------------- #
def _other_total_line(text: str) -> str:
    """渲染文本里「其他」合计那一行（测试自用，不复制渲染逻辑）。"""
    return next(ln for ln in text.splitlines() if '「其他」合计' in ln)


def test_other_total_line_says_incomparable_not_zero() -> None:
    """26 项 + 默认口径：合计行必须说「不可比」，不许出 §+0.00pp。

    修前：`market- Δ+0.00pp` —— 左边诚实打「-」，右边却给 0.00，
    读者会当成两边合计相等。根因是 `(other_delta or 0) * 100`。

    反证：把 `or 0` 加回（`num_text((other_delta or 0) * 100, ...)`）
    → `assert '+0.00pp' not in line` 变红。
    """
    jc = {k: 20.0 for k in sb.JC_31_KEYS}
    mk = {k: 20.0 for k in sb.MARKET_26_KEYS}
    mk['任何其他'] = 2.0
    analysis = sb.analyze_residuals(jc, mk)

    assert analysis['tier_a']['other_delta'] is None
    assert analysis['tier_a']['other_market'] is None

    line = _other_total_line(sb.format_analysis_text(analysis))
    assert '+0.00pp' not in line, line
    assert '-0.00pp' not in line, line
    assert ('不可比' in line) or ('Δ-' in line), line


def test_other_total_line_still_prints_a_real_number_when_comparable() -> None:
    """25 项 + 默认口径（逐桶无损、无结构性桶）：合计行必须有真数字。

    这条防「把数字都删掉」——修不可比不许把可比情形一起变成不可比。

    反证：把合计行改成无条件打印「不可比」→ 本断言变红。
    """
    jc = {k: 20.0 for k in sb.JC_31_KEYS}
    mk = {k: 21.0 for k in sb.MARKET_25_KEYS}
    analysis = sb.analyze_residuals(jc, mk, book_size=25)
    tier_a = analysis['tier_a']
    assert tier_a['per_bucket_lossless'] is True
    assert tier_a['structural_rows'] == []
    assert tier_a['other_delta'] is not None

    line = _other_total_line(sb.format_analysis_text(analysis))
    assert '不可比' not in line, line
    match = re.search(r'Δ([+-]\d+\.\d{2})pp', line)
    assert match, line
    assert float(match.group(1)) == pytest.approx(tier_a['other_delta'] * 100, abs=0.01)


def test_no_none_coerced_to_zero_ppt_in_renderer() -> None:
    """全文扫一遍：凡是同一行出现「市场-」（市场侧缺值），Δ 位不许是数字。

    反证：任一处回退 `or 0` 兜底 → 本断言变红。
    """
    cases = []
    jc = {k: 20.0 for k in sb.JC_31_KEYS}
    base = {k: 20.0 for k in sb.MARKET_26_KEYS}
    cases.append((jc, {**base, '任何其他': 2.0}, 26))
    missing = dict(base)
    del missing['0:0']
    cases.append((jc, missing, 26))
    cases.append((jc, {k: 21.0 for k in sb.MARKET_25_KEYS}, 25))

    for jc_odds, mk_odds, size in cases:
        text = sb.format_analysis_text(sb.analyze_residuals(jc_odds, mk_odds, book_size=size))
        for line in text.splitlines():
            if '市场-' not in line and '竞彩-' not in line:
                continue
            # 该行有一侧缺值：Δ 位置不许是数字
            assert not re.search(r'Δ[+-]\d+\.\d{2}pp', line), line


def test_renderer_has_no_none_to_zero_coercion_anywhere() -> None:
    """源码级守卫：渲染层不许出现 `(x or 0) * 100` 这类兜底。

    行为测试只能抓到「已经走到那条分支」的回归。上面那条
    `if other_delta is None:` 修好之后，`or 0` 即使写回 else 分支也不会被
    行为测试触发（分支不可达）。所以再加一条源码级断言，把这类写法本身挡掉。

    反证：把 `f"Δ{num_text((other_delta or 0) * 100, '+.2f')}pp"` 加回
    → `assert not offenders` 变红（`test_renderer_has_no_none_to_zero_coercion_anywhere`）。
    """
    source = (ROOT / 'score_books.py').read_text(encoding='utf-8')
    offenders = [
        f'{lineno}: {line.strip()}'
        for lineno, line in enumerate(source.splitlines(), 1)
        if re.search(r'or\s+0(\.0)?\)\s*\*\s*100', line)
        and '不许' not in line and '兜' not in line  # 允许文档里提到这个反模式
    ]
    assert not offenders, '渲染层出现 None→0 兜底: ' + '; '.join(offenders)
