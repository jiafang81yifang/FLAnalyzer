"""压测护栏：分类计数必须齐全、同 seed 必须逐字一致、判据不许用形状检查。

这些测试跑的是 `scripts/stress_score_books.py` 的真实函数，不是在测试里
另写一份压测逻辑。
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import stress_score_books as stress

import score_books as sb


def _small_report(cases: int = 300) -> dict:
    return stress.stress_report(num_cases=cases)


def test_stress_run_has_no_failures_on_a_small_batch() -> None:
    report = _small_report()
    assert report['failure_count'] == 0, report['failures'][:10]


def test_every_required_class_has_a_nonzero_counter() -> None:
    """每个真实出口都要有独立且非零的计数。

    反证：把 `REQUIRED_COUNTERS` 里新增一条没有生成路径的键（例如
    `'never_exercised'`）→ `missing_counters` 非空、`failure_count > 0`，
    本断言变红。这就是「缺一个键本身就是缺陷」的落地。
    """
    report = _small_report()
    assert report['missing_counters'] == []
    for name in stress.REQUIRED_COUNTERS:
        assert report['counters'].get(name, 0) > 0, name


def test_zero_hit_class_is_reported_as_a_defect_not_silence(monkeypatch) -> None:
    """零命中的类必须被点名，而不是「没有要报的」。"""
    extended = stress.REQUIRED_COUNTERS + ('never_exercised',)
    monkeypatch.setattr(stress, 'REQUIRED_COUNTERS', extended)
    report = _small_report()
    assert report['missing_counters'] == ['never_exercised']
    assert report['failure_count'] > 0
    assert any('计数器缺失' in f for f in report['failures'])


def test_same_seed_is_byte_identical_across_two_runs() -> None:
    """同 seed 两次运行结果必须逐字一致（不依赖墙钟或全局可变状态）。"""
    first = json.dumps(_small_report(), ensure_ascii=False, sort_keys=True)
    second = json.dumps(_small_report(), ensure_ascii=False, sort_keys=True)
    assert first == second


def test_different_seed_changes_the_result() -> None:
    """换 seed 结果必须变，否则「固定 seed」只是摆设。"""
    a = json.dumps(stress.stress_report(num_cases=200, seed=1), sort_keys=True)
    b = json.dumps(stress.stress_report(num_cases=200, seed=2), sort_keys=True)
    assert a != b


def test_report_contains_no_performance_numbers() -> None:
    """压测报告不许出现任何行情结论性的数字。

    注意：免责声明本身会提到「命中率/收益率」这几个词（它是在否认），
    所以判据不是「词不出现」，而是「这些词附近没有数字断言」。
    """
    text = stress.format_report(_small_report())
    assert '不产生任何行情结论' in text
    # 免责声明那一行是在否认这些口径，允许它出现；正文其余行不许出现。
    body = text.replace(stress.format_report(_small_report()).splitlines()[-1], '')
    for banned in ('命中率', '收益率', '回报率', 'ROI', '盈利'):
        assert banned not in body, banned


def test_stress_covers_both_books_and_all_policies() -> None:
    counters = _small_report()['counters']
    assert counters.get('book_26', 0) > 0
    assert counters.get('book_25', 0) > 0
    assert counters.get('fifty_goal_in_any_other', 0) > 0
    assert counters.get('fifty_goal_in_team_other', 0) > 0
    assert counters.get('keep_43_34', 0) > 0
    assert counters.get('policy_mixed', 0) > 0


def test_stress_covers_both_fit_exits() -> None:
    """λ 拟合的两个真实出口（扩框成功 / 两轮仍触边界被拒）都要打到。"""
    counters = _small_report()['counters']
    assert counters.get('fit_hit_boundary_expanded', 0) > 0
    assert counters.get('fit_refused_boundary', 0) > 0


def test_stress_covers_tau_clamp_and_kl_zero() -> None:
    counters = _small_report()['counters']
    assert counters.get('tau_clamped', 0) > 0
    assert counters.get('kl_zero_prob_key', 0) > 0


def test_stress_covers_the_four_defect_regressions() -> None:
    """缺陷 1/2/3 修好后新增的五类出口都必须被打到且非零。

    - `alias_duplicate_rejected`    别名重复键被拒（缺陷 2）
    - `out_of_universe_rejected`    表外键被拒（缺陷 3）
    - `rtp_impossible_rejected`     返还率 >= 100% 被拒（缺陷 2 后半）
    - `lossless_verified_true`      无损口径（缺陷 1）
    - `lossless_verified_false`     非无损口径（缺陷 1）
    """
    counters = _small_report()['counters']
    for name in (
        'alias_duplicate_rejected', 'out_of_universe_rejected',
        'rtp_impossible_rejected', 'lossless_verified_true',
        'lossless_verified_false',
        # 缺陷 5：不可达桶与两层无损
        'unreachable_bucket_detected', 'unreachable_bucket_delta_suppressed',
        'aggregate_lossless_true', 'aggregate_lossless_false',
        'book26_team_other_contradiction_warned',
        # 缺陷 6：合计行的不可比渲染
        'other_total_rendered_incomparable',
    ):
        assert counters.get(name, 0) > 0, name


def test_min_cases_floor_is_at_least_ten_thousand() -> None:
    assert stress.MIN_CASES >= 10000


def test_cli_exit_code_reflects_failures(monkeypatch) -> None:
    """CLI 的退出码必须跟着 failures 走，不能永远 0。"""
    monkeypatch.setattr(stress, 'REQUIRED_COUNTERS', stress.REQUIRED_COUNTERS + ('never_exercised',))
    assert stress.main(['--cases', '120']) == 1
    monkeypatch.undo()
    assert stress.main(['--cases', '120']) == 0


@pytest.mark.parametrize('kind,expected', [
    (0, 'odds_zero'),
    (-1.0, 'odds_negative'),
    (float('nan'), 'odds_nan'),
    (float('inf'), 'odds_inf'),
    ('9' * 400, 'odds_oversized_string'),
    (None, 'odds_unusable'),
])
def test_hostile_kinds_map_to_their_own_counter(kind, expected) -> None:
    """敌意值分类必须落到各自的计数键上，不许都塞进「other」。"""
    assert stress._KIND_COUNTER[stress._hostile_kind(kind)] == expected


# --------------------------------------------------------------------------- #
# 缺陷 7：抑制计数不许被空集满足，且必须与检出计数相等
# --------------------------------------------------------------------------- #
def test_stress_suppression_counter_requires_a_real_unreachable_bucket() -> None:
    """`unreachable_buckets == ()` 的用例不许让抑制计数上涨。

    修前：`set() == set()` 为真，25 项盘面 / any_other / keep 口径下
    抑制计数照样 +1，6020 里有 4710 是空集通过（1310 才是真的）。

    反证：把 `elif expected_unreachable:` 的守卫去掉（回到 `if structural ==
    set(...)` 无条件 +1）→ 本断言变红。
    """
    # 25 项 + TEAM_OTHER：逐桶无损、没有不可达桶
    assert sb.verify_lossless(sb.POLICY_TEAM_OTHER, sb.market_book(25))['unreachable_buckets'] == ()

    counters: Counter = Counter()
    rng = random.Random(stress.STRESS_SEED)
    # 驱动真实用例函数，只把盘面/口径钉死
    failures = stress._run_one_case(
        rng, counters, book_size=25, policy=sb.POLICY_TEAM_OTHER,
    )
    assert failures == [], failures
    detected = counters.get('unreachable_bucket_detected', 0)
    suppressed = counters.get('unreachable_bucket_delta_suppressed', 0)
    assert detected == 0, '25 项 + TEAM_OTHER 不该检出不可达桶'
    assert suppressed == 0, (
        f'没有不可达桶时抑制计数不许涨，实际 {suppressed}（空集相等被误计）'
    )


def test_stress_suppression_counter_equals_detected_counter() -> None:
    """跑一撮混合用例（两种盘面 × 三种口径），两个计数必须相等。

    反证：让抑制分支漏一例（把 `elif expected_unreachable:` 改成
    `elif False:`）→ 本断言变红（实测 detected=62 suppressed=0）。
    """
    report = stress.stress_report(num_cases=900, seed=stress.STRESS_SEED)
    counters = report['counters']
    assert counters['unreachable_bucket_detected'] > 0
    assert counters['unreachable_bucket_detected'] == counters['unreachable_bucket_delta_suppressed'], (
        f"detected={counters['unreachable_bucket_detected']} "
        f"suppressed={counters['unreachable_bucket_delta_suppressed']}"
    )
    assert report['failure_count'] == 0, report['failures'][:5]


def test_kl_zero_counter_is_not_the_same_class_as_suppression() -> None:
    """`kl_zero_prob_key` 与抑制计数同值是共享外层守卫的巧合，不是缺陷。

    这里只把「两者可以不等」钉住：KL 的 p=0 出口由 probe 显式构造，
    与盘面尺寸/口径无关，因此它的计数不承担「等于 detected」的义务。
    """
    counters = stress.stress_report(num_cases=600, seed=stress.STRESS_SEED)['counters']
    assert counters['kl_zero_prob_key'] > 0
    # 两者同值时也不构成不变量；这里只断言各自非零，避免把巧合写成约束。
    assert counters['unreachable_bucket_detected'] > 0
