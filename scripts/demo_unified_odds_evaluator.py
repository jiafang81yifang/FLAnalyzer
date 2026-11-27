"""演示：统一赔率量化评估器的同场对比与异常过滤。

演示代码原先写在 unified_odds_evaluator.py 的 `__main__` 块里；该模块会被
scripts/build_single_file.py 内嵌进单文件版，所以演示移到这里，模块本身保持
无顶层副作用。运行：

    python scripts/demo_unified_odds_evaluator.py

注意：本演示的阈值区间是 [0.15, 0.35]，而样本两个盘口的抽水差值只有
+0.065905（1X2）与 +0.097461（OU），都低于下界，因此两本盘口都会被判为
MARGIN_SPREAD_TOO_LOW；在 exclude_anomalous_from_ev=True 下最终 +EV 选项为
零。这是真实行为，不是演示故障。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unified_odds_evaluator import UnifiedOddsEvaluator


def main() -> int:
    # 抽水差值（CN - INTL）要求在 [0.15, 0.35] 内，相对赔率偏差 <= 15%，去水概率差 <= 0.05
    evaluator = UnifiedOddsEvaluator(
        min_ev_thresholds={"CN": 0.05, "INTL": 0.015},
        kelly_fractions={"CN": 0.125, "INTL": 0.25},
        max_stake_fractions={"CN": 0.02, "INTL": 0.05},
        min_allowed_margin_spread=0.15,
        max_allowed_margin_spread=0.35,
        max_allowed_odds_rel_diff=0.15,
        max_allowed_fair_prob_diff=0.05,
        exclude_anomalous_from_ev=True,
    )

    sample_matches = [
        {
            "match_id": "20261126_EPL_01",
            "markets": [
                {
                    "source_type": "CN",
                    "type": "1X2",
                    "odds": (1.50, 3.60, 5.20),
                    "true_probs": (0.68, 0.19, 0.13),
                },
                {
                    "source_type": "INTL",
                    "type": "1X2",
                    "odds": (1.85, 3.55, 4.25),
                    "true_probs": (0.58, 0.22, 0.20),
                },
                {
                    "source_type": "CN",
                    "type": "OU",
                    "line": 2.5,
                    "odds": (1.72, 1.75),
                    "true_probs": (0.55, 0.45),
                },
                {
                    "source_type": "INTL",
                    "type": "OU",
                    "line": 2.5,
                    "odds": (1.95, 1.91),
                    "true_probs": (0.54, 0.46),
                },
            ],
        }
    ]

    _, ev_picks, match_comparisons = evaluator.batch_evaluate_and_filter_ev(sample_matches)

    print("=== 同场次对比与异常标记 ===")
    for comp in match_comparisons:
        print(comp)

    print("\n=== 过滤异常盘口后的 +EV 选项 ===")
    for pick in ev_picks:
        print(pick)
    print(f"\n+EV 选项数: {len(ev_picks)}（两本盘口抽水差值均低于 0.15 下界，故为 0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
