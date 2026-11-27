"""最小可用比分盘对比 CLI：竞彩 31 项 × 市场 26/25 项。

用法：

    python scripts/compare_score_books.py --jc jc.json --market market.json
    python scripts/compare_score_books.py --jc jc.json --market market.json \
        --book 25 --policy five_goals_in_team_other,keep_43_34
    python scripts/compare_score_books.py --jc jc.json --market market.json \
        --model lam_h=1.52,lam_a=1.09,rho=-0.04 --json

输入是两个 `{"比分键": 赔率}` 的 JSON。不依赖 streamlit、不联网。
本脚本只做口径换算与残差，不产出任何历史频率、命中率或收益结论。

四维框架的落点：

  1. 去水概率与 RTP 结构  → `NormalizedOdds.rtp` / `.probs`（缺项时 rtp 为 None）
  2. 「其他」项聚合差值     → `tier_a.other_jc` / `.other_market` / `.other_delta`
                              + `five_goal_total`（6 个 5 球格在竞彩侧的合计）
  3. 31 维投影与残差        → `tier_b.residuals` / `.kl`（需 --model）
  4. 分档回测               → 本脚本**不产出**：没有分档样本与赛前时间窗数据，
                              只打印一行「待跑」，绝不编造数字。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import score_books as sb

#: 分档回测（第四维）需要分档样本与赛前时间窗，本脚本没有这些数据。
BACKTEST_PENDING_LINE = (
    "【第四维·分档回测】待跑：需按让球档位与赛前时间窗分组的样本，本脚本不产出数字。"
)


def _load_json(path: str) -> dict[str, Any]:
    """读 `{"比分键": 赔率}`。下划线开头的键是注释元数据（合成数据标记），跳过。"""
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: 顶层必须是 {{'比分键': 赔率}} 对象，实际是 {type(data).__name__}")
    out: dict[str, Any] = {}
    for key, value in data.items():
        if str(key).startswith("_"):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SystemExit(f"{path}: 键 {key!r} 的值必须是数字，实际是 {type(value).__name__}")
        out[str(key)] = value
    return out


def _parse_policy(spec: str | None) -> sb.MarketBucketPolicy:
    """`a,b` 形式的开关列表 → 口径对象。空串表示全关（非法组合仍会抛错）。"""
    if not spec:
        return sb.DEFAULT_MARKET_POLICY
    flags = {part.strip() for part in spec.split(",") if part.strip()}
    unknown = flags - {"five_goals_in_team_other", "five_goals_in_any_other", "keep_43_34"}
    if unknown:
        raise SystemExit(f"未知的口径开关: {sorted(unknown)}")
    if flags == {"five_goals_in_team_other", "five_goals_in_any_other"} or (
        {"five_goals_in_team_other", "five_goals_in_any_other"} <= flags
    ):
        raise SystemExit(
            "口径冲突：five_goals_in_team_other 与 five_goals_in_any_other 只能选一个"
        )
    if not flags:
        return sb.MarketBucketPolicy(
            five_goals_in_team_other=False, five_goals_in_any_other=False, keep_43_34=False,
        )
    return sb.MarketBucketPolicy(
        five_goals_in_team_other="five_goals_in_team_other" in flags,
        five_goals_in_any_other="five_goals_in_any_other" in flags,
        keep_43_34="keep_43_34" in flags,
    )


def _parse_model(spec: str | None) -> dict[str, float] | None:
    """`lam_h=1.5,lam_a=1.1,rho=-0.04` → 模型参数。"""
    if not spec:
        return None
    out: dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        key, sep, value = part.partition("=")
        if not sep:
            raise SystemExit(f"--model 片段缺少 '='：{part!r}（形如 lam_h=1.5）")
        try:
            out[key.strip()] = float(value)
        except ValueError:
            raise SystemExit(f"--model 的值不是数字：{part!r}") from None
    missing = {"lam_h", "lam_a"} - set(out)
    if missing:
        raise SystemExit(f"--model 缺参数 {sorted(missing)}；没有模型参数时不要用 --model")
    out.setdefault("rho", 0.0)
    return out


def build_analysis(
    jc_odds: dict[str, Any],
    market_odds: dict[str, Any],
    *,
    book_size: int = sb.MARKET_26_COUNT,
    policy: sb.MarketBucketPolicy = sb.DEFAULT_MARKET_POLICY,
    model: dict[str, float] | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """CLI 与测试共用的唯一入口：解析 → 四维框架前三块。"""
    analysis = sb.analyze_residuals(
        jc_odds, market_odds,
        book_size=book_size, policy=policy, model=model, top_n=top_n,
    )
    analysis["backtest"] = {"pending": True, "line": BACKTEST_PENDING_LINE}
    return analysis


def _render(analysis: dict[str, Any], top_n: int) -> str:
    text = sb.format_analysis_text(analysis, top_n=top_n)
    return text + "\n" + BACKTEST_PENDING_LINE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="竞彩 31 项 × 市场 26/25 项 口径对比")
    parser.add_argument("--jc", required=True, help='竞彩 31 项赔率 JSON（{"比分键": 赔率}）')
    parser.add_argument("--market", required=True, help='市场 26/25 项赔率 JSON（{"键": 赔率}）')
    parser.add_argument("--book", type=int, default=sb.MARKET_26_COUNT, choices=(25, 26),
                        help="市场盘面项数，默认 26")
    parser.add_argument("--policy", default="",
                        help="桶边界开关，逗号分隔：five_goals_in_team_other / "
                             "five_goals_in_any_other / keep_43_34")
    parser.add_argument("--model", default="",
                        help="模型参数 lam_h=..,lam_a=..[,rho=..]；不给则只出档 A")
    parser.add_argument("--top", type=int, default=10, help="按 |Δ| 排序输出的前 N 项，默认 10")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是可读文本")
    args = parser.parse_args(argv)

    jc_odds = _load_json(args.jc)
    market_odds = _load_json(args.market)
    policy = _parse_policy(args.policy)
    model = _parse_model(args.model)

    analysis = build_analysis(
        jc_odds, market_odds,
        book_size=args.book, policy=policy, model=model, top_n=args.top,
    )
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2, default=str))
    else:
        print(_render(analysis, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
