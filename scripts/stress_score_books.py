"""Randomized stress harness for the score-book pipeline (>= 10000 cases).

This does NOT produce market conclusions. It generates synthetic odds to force
crash paths, invariant breaks and logic contradictions. Nothing here is evidence
about any real market: no hit rate, no return, no historical frequency.

Every real exit has its own counter. A missing counter is itself a defect, not
"nothing to report": `REQUIRED_COUNTERS` is asserted to be a subset of what a run
actually produced, and the run refuses to report a total-only result.

Determinism: one fixed seed, no wall clock, no global mutable state. Running twice
with the same seed must produce a byte-identical report — `stress_report()`
serialises with `sort_keys=True` so that is directly checkable.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import score_books as sb

#: 每个真实出口一个独立的非零计数键。缺任何一条都是缺陷。
REQUIRED_COUNTERS: tuple[str, ...] = (
    # 样本完整性
    'sample_complete',
    'sample_missing_one_key',
    'sample_missing_many_keys',
    'sample_missing_all_keys',
    # 敌意值
    'odds_zero',
    'odds_negative',
    'odds_nan',
    'odds_inf',
    'odds_oversized_string',
    'odds_unusable',
    # 桶边界口径
    'fifty_goal_in_team_other',
    'fifty_goal_in_any_other',
    'keep_43_34',
    # λ 拟合
    'fit_hit_boundary_expanded',
    'fit_refused_boundary',
    # rho 越界
    'tau_clamped',
    # KL
    'kl_zero_prob_key',
    # 盘面
    'book_26',
    'book_25',
    'policy_mixed',
    # 缺陷 2/3/1 回归后新增的真实出口
    'alias_duplicate_rejected',
    'out_of_universe_rejected',
    'rtp_impossible_rejected',
    'lossless_verified_true',
    'lossless_verified_false',
)

#: 固定种子。报告里原样给出，任何两次同 seed 的运行必须逐字一致。
STRESS_SEED = 20261127

#: 单次运行至少这么多例。
MIN_CASES = 10000

_HOSTILE_ODDS: tuple[Any, ...] = (
    0, -1.0, -0.5, float('nan'), float('inf'), float('-inf'),
    '1e400', '9' * 400, 10 ** 400, None, 'x', '', True,
)

#: 计数器名按「出口」命名，与 `REQUIRED_COUNTERS` 逐字对应。
_KIND_COUNTER = {
    'zero': 'odds_zero',
    'negative': 'odds_negative',
    'nan': 'odds_nan',
    'inf': 'odds_inf',
    'oversized': 'odds_oversized_string',
    'other': 'odds_unusable',
}


def _hostile_kind(value: Any) -> str:
    """敌意值分类。这是计数用的，不是判据。"""
    if value is None or value is True or value is False:
        return 'other'
    if isinstance(value, str):
        if value == '1e400':
            return 'inf'
        if len(value) > 50:
            return 'oversized'
        return 'other'
    if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > 200:
        return 'oversized'
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 'other'
    if not math.isfinite(number):
        return 'nan' if math.isnan(number) else 'inf'
    if number == 0:
        return 'zero'
    if number < 0:
        return 'negative'
    return 'other'


#: 同一个比分键的另一种写法。`1:0` -> `1-0`；`胜其他` -> `胜其它`。
_OTHER_ALIAS = {
    '胜其他': '胜其它', '平其他': '平其它', '负其他': '负其它',
    '胜其它': '胜其他', '平其它': '平其他', '负其它': '负其他',
}


def _alias_form_of(key: str) -> str:
    """给一个键造一个**归一后相同、写法不同**的别名。

    `1:0` -> `1-0`；`胜其他` -> `胜其它`；其余用全角冒号 `1：0`。
    绝不能用「加尾空格」——归一化会把它抹掉，别名就成了同一个字符串，
    压测会误报「别名重复未被识别」（这是压测自身的 bug，不是实现的）。
    """
    if key in _OTHER_ALIAS:
        return _OTHER_ALIAS[key]
    if ':' in key:
        head, _, tail = key.partition(':')
        return rng_choice_separator(head, tail)
    return key


def rng_choice_separator(head: str, tail: str) -> str:
    """`1:0` 的另一种写法：横杠或全角冒号。"""
    return f'{head}-{tail}' if (len(head) + len(tail)) % 2 == 0 else f'{head}：{tail}'


def _random_jc_odds(rng: random.Random, *, hostile_count: int = 0) -> dict[str, Any]:
    """生成一份 31 项赔率；前 `hostile_count` 项被替换成敌意值。"""
    odds: dict[str, Any] = {}
    for key in sb.JC_31_KEYS:
        odds[key] = round(rng.uniform(2.0, 400.0), 2)
    keys = list(sb.JC_31_KEYS)
    rng.shuffle(keys)
    for key in keys[:hostile_count]:
        odds[key] = rng.choice(_HOSTILE_ODDS)
    return odds


def _random_market_odds(rng: random.Random, book: sb.MarketBook) -> dict[str, Any]:
    odds: dict[str, Any] = {}
    for key in book.keys:
        odds[key] = round(rng.uniform(2.0, 400.0), 2)
    return odds


def _random_policy(rng: random.Random) -> sb.MarketBucketPolicy:
    return rng.choice(sb.ALL_POLICIES)


def _run_one_case(rng: random.Random, counters: Counter) -> list[str]:
    """跑一例，返回这一例的断言失败清单（空 = 通过）。"""
    failures: list[str] = []

    book_size = rng.choice((25, 26))
    book = sb.market_book(book_size)
    policy = _random_policy(rng)
    counters['book_26' if book_size == 26 else 'book_25'] += 1

    # ---- 样本完整性：完整 / 缺 1 / 缺多 / 全缺 -------------------------------
    mode = rng.choices(
        ('complete', 'missing_one', 'missing_many', 'missing_all'),
        weights=(60, 15, 15, 10),
    )[0]
    hostile_count = rng.choices((0, 1, 2), weights=(80, 15, 5))[0]
    jc_odds = _random_jc_odds(rng, hostile_count=0)
    # 先放敌意值再删键：顺序反了会把一个敌意值又盖回合法值，
    # 于是「缺项集合」与生成器记的对不上（压测自己先错）。
    hostile_keys = rng.sample(list(sb.JC_31_KEYS), hostile_count)
    for key in hostile_keys:
        jc_odds[key] = rng.choice(_HOSTILE_ODDS)
        counters[_KIND_COUNTER[_hostile_kind(jc_odds[key])]] += 1

    # 敌意值本身也会让该键缺席：0/负数/nan/inf/超长串/None 一律跳过，
    # 所以「该键缺席」与「给了敌意值」在这里是同一件事。
    expected_missing: set[str] = set(hostile_keys)
    if mode == 'complete':
        counters['sample_complete'] += 1
    elif mode == 'missing_one':
        drop = rng.choice(list(sb.JC_31_KEYS))
        jc_odds.pop(drop, None)
        expected_missing.add(drop)
        counters['sample_missing_one_key'] += 1
    elif mode == 'missing_many':
        dropped = rng.sample(list(sb.JC_31_KEYS), rng.randint(2, 12))
        for key in dropped:
            jc_odds.pop(key, None)
        expected_missing.update(dropped)
        counters['sample_missing_many_keys'] += 1
    else:
        jc_odds = {}
        expected_missing = set(sb.JC_31_KEYS)
        counters['sample_missing_all_keys'] += 1

    jc_norm = sb.normalize_jc_odds(jc_odds)
    market_odds = _random_market_odds(rng, book)
    market_norm = sb.normalize_market_odds(market_odds, book)

    # ---- 判据 1：rtp 只在三种情况为 None --------------------------------
    #    a) 样本不完整  b) 有别名重复键  c) 返还率 >= 100%（样本自相矛盾）
    # 反向不成立：complete=True 也可能因 (b)(c) 而 rtp 为 None。
    for side, norm in (('jc', jc_norm), ('market', market_norm)):
        if norm.rtp is None:
            explained = (
                (not norm.complete) or bool(norm.duplicate_inputs)
                or any('自相矛盾' in w for w in norm.warnings)
            )
            if not explained:
                failures.append(f'{side}: rtp 为 None 但无法归因')
        else:
            if not norm.complete:
                failures.append(f'{side}: 不完整却给出了 rtp')
            if norm.duplicate_inputs:
                failures.append(f'{side}: 有别名重复却给出了 rtp')
            if not (0.0 < norm.rtp < 1.0):
                failures.append(f'{side}: rtp={norm.rtp} 不在 (0,1)')
    if mode == 'complete' and hostile_count == 0 and not jc_norm.complete:
        failures.append('jc: 齐全样本被判为不完整')
    if jc_norm.missing and expected_missing and set(jc_norm.missing) != expected_missing:
        failures.append(
            f'jc: missing 集合不符 expected={sorted(expected_missing)[:4]} got={list(jc_norm.missing)[:4]}'
        )

    # ---- 判据 2：归一化后 abs(sum - 1) < 1e-9（有概率时） -------------------
    for side, norm in (('jc', jc_norm), ('market', market_norm)):
        if norm.probs:
            if abs(sum(norm.probs.values()) - 1.0) >= 1e-9:
                failures.append(f'{side}: 去水概率和不为 1')
            if any(not math.isfinite(v) or v < 0.0 for v in norm.probs.values()):
                failures.append(f'{side}: 出现非有限或负概率')

    # ---- 判据 3a：表外键不许进分母（缺陷 3） -----------------------------
    out_of_universe = dict(jc_odds)
    probe_outside = rng.choice(('9:9', '12:0', '0:12', '15:15'))
    out_of_universe[probe_outside] = round(rng.uniform(50.0, 500.0), 2)
    dirty_norm = sb.normalize_jc_odds(out_of_universe)
    if dirty_norm.rejected:
        counters['out_of_universe_rejected'] += 1
    if probe_outside in dirty_norm.probs:
        failures.append(f'jc: 表外键 {probe_outside} 进了 probs')
    both_sums_known = jc_norm.raw_sum is not None and dirty_norm.raw_sum is not None
    if both_sums_known and abs(dirty_norm.raw_sum - jc_norm.raw_sum) >= 1e-12:
        failures.append('jc: 表外键改变了分母')

    # ---- 判据 3b：别名重复键不许求和（缺陷 2） ---------------------------
    if jc_odds:
        alias_src = rng.choice(list(jc_odds))
        alias_form = _alias_form_of(alias_src)
        duplicated = dict(jc_odds)
        duplicated[alias_form] = jc_odds[alias_src]
        dup_norm = sb.normalize_jc_odds(duplicated)
        if dup_norm.duplicate_inputs:
            counters['alias_duplicate_rejected'] += 1
        if dup_norm.rtp is not None:
            failures.append('jc: 别名重复键仍给出了 RTP')
        hit = [k for k, _ in dup_norm.duplicate_inputs]
        expected_key = sb.normalize_score_key(alias_src)
        # 源值不可用时（0/负数/nan/inf/超长串/None/bool）两份都会被 skip，
        # 本来就不该有「重复」——那不是漏检。判据必须是「这个值本身可用」，
        # 不能用 _hostile_kind()=='other'（它对普通数字和 None/True 都返回
        # 'other'，会把「不可用」当成「普通数字」，于是误报漏检）。
        source_is_usable = sb._valid_odd(jc_odds[alias_src]) is not None
        if source_is_usable and expected_key in sb.JC_31_KEYS and hit != [expected_key]:
            failures.append(f'jc: 别名重复未被识别（src={alias_src} -> {expected_key}，got {hit}）')

    # ---- 判据 3c：返还率 >= 100% 必须被拒（缺陷 2 后半） ------------------
    # 31 项要 sum(1/o) < 1 才得到 >100% 的返还率，故赔率取 34.0（隐含 91% 不到）
    impossible = {k: 34.0 for k in sb.JC_31_KEYS}
    imp_norm = sb.normalize_jc_odds(impossible)
    if imp_norm.rtp is None and imp_norm.complete:
        counters['rtp_impossible_rejected'] += 1
    elif imp_norm.rtp is not None and imp_norm.rtp >= 1.0:
        failures.append(f'jc: 给出了不可能的返还率 {imp_norm.rtp}')

    # ---- 判据 3d：probs 键集必须等于 required，且和为 1 ------------------
    if jc_norm.complete:
        if set(jc_norm.probs) != set(sb.JC_31_KEYS):
            failures.append('jc: complete 时 probs 键集不等于 required')
        if abs(sum(jc_norm.probs.values()) - 1.0) >= 1e-12:
            failures.append('jc: complete 时 probs 和不为 1')

    # ---- 判据 4：档 A 守恒，逐桶等于按映射求和 ---------------------------
    if jc_norm.probs:
        projected = sb.project_jc31_onto_market(jc_norm.probs, policy=policy, book=book)
        if abs(sum(projected['probs'].values()) - 1.0) >= 1e-9:
            failures.append('tier_a: 投影后总和不为 1')
        mapping = sb.jc_to_market_map(policy, book).mapping
        manual = {key: 0.0 for key in book.keys}
        for jc_key, target in mapping.items():
            manual[target] += jc_norm.probs.get(jc_key, 0.0)
        for key in book.keys:
            if abs(projected['probs'][key] - manual[key]) >= 1e-12:
                failures.append(f'tier_a: 桶 {key} 与手算不符')
                break

    # ---- 判据 5：6 个 5 球格永远不落进市场精确格 ------------------------
    for score in sb.JC_FIVE_GOAL_CELLS:
        target = sb.jc_to_market_key(score, policy=policy, book=book)
        if not target or target in book.precise:
            failures.append(f'5球格 {score} 落到了 {target!r}')
        if policy.five_goals_in_any_other:
            if target != sb.MARKET_ANY_OTHER:
                failures.append(f'5球格 {score} 未按口径进任何其他: {target}')
            counters['fifty_goal_in_any_other'] += 1
        elif policy.five_goals_in_team_other:
            if target not in (sb.MARKET_HOME_OTHER, sb.MARKET_AWAY_OTHER):
                failures.append(f'5球格 {score} 未按口径进方向桶: {target}')
            counters['fifty_goal_in_team_other'] += 1
    if policy.keep_43_34:
        counters['keep_43_34'] += 1

    # ---- 判据 5b：lossless 必须是算出来的，且与自检数字一致（缺陷 1） ----
    verdict = sb.verify_lossless(policy, book)
    if verdict['lossless']:
        counters['lossless_verified_true'] += 1
    else:
        counters['lossless_verified_false'] += 1
    check = sb.caliber_self_check(policy, book)
    if verdict['lossless'] != check['clean']:
        failures.append(
            f'lossless 判定与自检不一致：lossless={verdict["lossless"]} '
            f'max|delta|={check["max_abs_delta"]}'
        )
    if verdict['lossless'] and check['max_abs_delta'] >= 1e-12:
        failures.append(f'自称无损但同分布两边喂 max|delta|={check["max_abs_delta"]}')
    if policy == sb.POLICY_TEAM_OTHER and not verdict['lossless']:
        failures.append('five_goals_in_team_other 本应是无损口径')
    if policy == sb.POLICY_ANY_OTHER and verdict['lossless']:
        failures.append('five_goals_in_any_other 本不该被判为无损')

    # ---- 判据 6：档 B（有模型时）守恒与 KL -------------------------------
    lam_h = rng.uniform(0.3, 3.5)
    lam_a = rng.uniform(0.3, 3.0)
    rho = rng.choice((0.0, -0.04, 0.05, 0.12, -0.12))
    if rng.random() < 0.15:
        rho = rng.choice((0.5, -0.5, 0.9))
    try:
        expansion = sb.expand_market_to_jc31(
            market_norm.probs, lam_h, lam_a, rho, book=book, policy=policy,
        )
    except ValueError:
        failures.append('tier_b: 有 λ 却拒绝摊薄')
    else:
        if not expansion.modeled:
            failures.append('tier_b: modeled 不是 True')
        if any(not math.isfinite(v) or v < 0.0 for v in expansion.probs.values()):
            failures.append('tier_b: 出现非有限或负概率')
        for bucket, dist in expansion.bucket_models.items():
            if abs(sum(dist.values()) - 1.0) >= 1e-9:
                failures.append(f'tier_b: 桶 {bucket} 条件分布和不为 1')
        if expansion.model['tau_clamped_cells'] > 0:
            counters['tau_clamped'] += 1
        # 档 B 的 31 维再聚合回桶：回投值 + 结构差 = 市场原值
        reaggregated = sb.project_jc31_onto_market(
            {k: v for k, v in expansion.probs.items()}, policy=policy, book=book,
        )
        for key in book.keys:
            returned = reaggregated['probs'][key]
            lost = expansion.roundtrip_loss.get(key, 0.0)
            own = (expansion.bucket_reconciliation.get(key) or {}).get('returned_to_same_bucket')
            if own is None:
                if abs(returned - market_norm.probs[key]) >= 1e-9:
                    failures.append(f'tier_b: 精确格 {key} 不守恒')
                    break
            elif abs(own + lost - market_norm.probs[key]) >= 1e-9:
                failures.append(f'tier_b: 桶 {key} 回投+结构差 不等于市场值')
                break

    # ---- 判据 7：KL 跳过 p=0，且 zero_prob_keys 与输入 p=0 集合相等 ------
    if jc_norm.probs and market_norm.probs:
        try:
            model_probs = sb.expand_market_to_jc31(
                market_norm.probs, lam_h, lam_a, rho, book=book, policy=policy,
            ).probs
        except ValueError:
            pass
        else:
            # 显式造一个 p=0 键：真实数据里 0 概率只在极端尾部出现，
            # 不主动构造就永远压不到这条出口。
            probe_key = rng.choice(list(jc_norm.probs))
            probe = dict(jc_norm.probs)
            probe[probe_key] = 0.0
            kl, zeros = sb.kl_divergence(probe, model_probs)
            expected_zeros = {k for k, v in probe.items() if v <= 0.0}
            if set(zeros) != expected_zeros:
                failures.append('kl: zero_prob_keys 与输入 p=0 集合不等')
            if zeros:
                counters['kl_zero_prob_key'] += 1
            if math.isnan(kl):
                failures.append('kl: 返回 nan')

    # ---- 判据 8：混合口径（同一次调用里两个 policy 不得互相污染） --------
    other_policy = _random_policy(rng)
    if other_policy != policy:
        counters['policy_mixed'] += 1
        a = sb.jc_to_market_map(policy, book).mapping
        b = sb.jc_to_market_map(other_policy, book).mapping
        if set(a) != set(b):
            failures.append('口径混合: 两次映射的键集合不同')

    return failures


def _run_fit_case(rng: random.Random, counters: Counter) -> list[str]:
    """λ 拟合的随机用例：扩框 / 两轮仍触边界被拒。"""
    failures: list[str] = []
    truth_h = rng.uniform(0.15, 5.0)
    truth_a = rng.uniform(0.15, 4.0)
    home, draw, away, over, _ = sb._model_1x2_ou(truth_h, truth_a, line=2.5)
    if not all(math.isfinite(v) for v in (home, draw, away, over)):
        return ['fit: 目标概率非有限']
    if abs(home + draw + away - 1.0) >= 1e-9:
        failures.append('fit: 目标 1X2 和不为 1')

    half_width = rng.choice((0.05, 0.2, 0.8))
    max_rounds = rng.choice((1, 2, 3))
    fit = sb.fit_lambda_from_market_1x2_and_ou(
        home, draw, away, over, half_width=half_width, max_rounds=max_rounds,
    )
    if fit.accepted:
        if fit.lam_h is None or fit.lam_a is None:
            failures.append('fit: 接受但 λ 为 None')
        if fit.hit_boundary:
            failures.append('fit: 接受却仍触边界')
        if fit.loss is None or not math.isfinite(fit.loss):
            failures.append('fit: 接受但 loss 非有限')
        elif fit.loss > sb.DEFAULT_FIT_LOSS_TOL:
            failures.append('fit: 接受但 loss 超容差')
        if fit.rounds > 1:
            counters['fit_hit_boundary_expanded'] += 1
    else:
        if fit.lam_h is not None or fit.lam_a is not None:
            failures.append('fit: 拒绝却给了 λ')
        if not fit.reason:
            failures.append('fit: 拒绝但没有原因')
        if fit.loss is not None and not math.isfinite(fit.loss):
            failures.append('fit: 拒绝时 loss 非有限')
        if fit.hit_boundary:
            counters['fit_refused_boundary'] += 1
    return failures


def stress_report(num_cases: int = MIN_CASES, seed: int = STRESS_SEED) -> dict[str, Any]:
    """跑 `num_cases` 例，返回结构化报告（确定性：同 seed 同结果）。"""
    rng = random.Random(seed)
    counters: Counter = Counter()
    all_failures: list[str] = []

    for index in range(num_cases):
        if index % 3 == 2:
            # 每三例抽一例走 λ 拟合路径
            failures = _run_fit_case(rng, counters)
            if failures:
                counters['fit_cases'] += 1
        else:
            failures = _run_one_case(rng, counters)
            counters['pipeline_cases'] += 1
        for failure in failures:
            all_failures.append(f'case#{index}: {failure}')

    # 每个真实出口都必须有非零计数：缺一条就是缺陷，不是「没有要报的」。
    missing = [name for name in REQUIRED_COUNTERS if counters.get(name, 0) == 0]
    if missing:
        all_failures.append(f'计数器缺失（该类零命中即缺陷）: {sorted(missing)}')

    return {
        'seed': seed,
        'cases': num_cases,
        'counters': {k: int(v) for k, v in sorted(counters.items())},
        'required_counters': list(REQUIRED_COUNTERS),
        'missing_counters': sorted(missing),
        'failures': sorted(all_failures),
        'failure_count': len(all_failures),
        'note': (
            '合成赔率压测：只找崩溃与逻辑矛盾，不产生任何行情结论。'
            '本报告不含、也不得推导命中率 / 收益率 / 历史频率。'
        ),
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f'压测 seed={report["seed"]} cases={report["cases"]}',
        f'failures={report["failure_count"]}',
        '分类计数：',
    ]
    for name, value in report['counters'].items():
        lines.append(f'  {name:<28} {value}')
    if report['missing_counters']:
        lines.append(f'缺失计数（即缺陷）: {report["missing_counters"]}')
    for failure in report['failures'][:40]:
        lines.append(f'  FAIL {failure}')
    if report['failure_count'] > 40:
        lines.append(f'  … 还有 {report["failure_count"] - 40} 条')
    lines.append(report['note'])
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description='score_books 随机化压测')
    parser.add_argument('--cases', type=int, default=MIN_CASES)
    parser.add_argument('--seed', type=int, default=STRESS_SEED)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    report = stress_report(num_cases=args.cases, seed=args.seed)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(format_report(report))
    return 0 if report['failure_count'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
