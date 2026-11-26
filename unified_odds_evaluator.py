"""统一赔率量化评估器：竞彩盘(CN)与国际盘(INTL)分源评估与同场对比。

本文件是 app_v84_hermes.py 的 sibling 运行时模块，会被
scripts/build_single_file.py 原样内嵌进单文件版，因此顶层不允许有任何副作用：
不读写文件、不联网、不打印，也不含 __main__ 演示（演示见
scripts/demo_unified_odds_evaluator.py，算术参考值见 tests/test_unified_odds_evaluator.py）。
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, ClassVar


class UnifiedOddsEvaluator:
    """
    统一赔率量化评估器：强制区分“竞彩盘(CN)”与“国际盘(INTL)”，支持独立设置
    双边/三边来源各自的最低 +EV 门槛与凯利仓位系数，提供同场次竞彩盘(CN)
    与国际盘(INTL)的返奖率差值、抽水差值与赔率偏差对比，并支持基于
    抽水差值与赔率偏差的异常盘口识别与过滤。
    """

    VALID_SOURCES: ClassVar[frozenset[str]] = frozenset({"CN", "INTL"})

    def __init__(
        self,
        min_ev_thresholds: dict[str, float] | None = None,
        kelly_fractions: dict[str, float] | None = None,
        max_stake_fractions: dict[str, float] | None = None,
        default_min_ev: float = 0.0,
        default_kelly_fraction: float = 0.25,
        default_max_stake: float = 0.05,
        max_allowed_margin_spread: float | None = None,
        min_allowed_margin_spread: float | None = None,
        max_allowed_odds_rel_diff: float | None = None,
        max_allowed_fair_prob_diff: float | None = None,
        exclude_anomalous_from_ev: bool = True,
    ) -> None:
        self._require_finite(default_min_ev, "default_min_ev")
        self._require_finite(default_kelly_fraction, "default_kelly_fraction")
        self._require_finite(default_max_stake, "default_max_stake")
        if default_min_ev < 0.0:
            raise ValueError("default_min_ev 不能小于 0.0")
        if not (0.0 <= default_kelly_fraction <= 1.0):
            raise ValueError("default_kelly_fraction 必须在 [0.0, 1.0] 内")
        if not (0.0 <= default_max_stake <= 1.0):
            raise ValueError("default_max_stake 必须在 [0.0, 1.0] 内")
        for name, value in (
            ("max_allowed_margin_spread", max_allowed_margin_spread),
            ("min_allowed_margin_spread", min_allowed_margin_spread),
            ("max_allowed_odds_rel_diff", max_allowed_odds_rel_diff),
            ("max_allowed_fair_prob_diff", max_allowed_fair_prob_diff),
        ):
            if value is not None:
                self._require_finite(value, name)
        if (
            max_allowed_margin_spread is not None
            and min_allowed_margin_spread is not None
            and min_allowed_margin_spread > max_allowed_margin_spread
        ):
            raise ValueError("min_allowed_margin_spread 不能大于 max_allowed_margin_spread")

        self.min_ev_thresholds: dict[str, float] = {
            "CN": default_min_ev,
            "INTL": default_min_ev,
        }
        self.kelly_fractions: dict[str, float] = {
            "CN": default_kelly_fraction,
            "INTL": default_kelly_fraction,
        }
        self.max_stake_fractions: dict[str, float] = {
            "CN": default_max_stake,
            "INTL": default_max_stake,
        }

        if min_ev_thresholds:
            for k, v in min_ev_thresholds.items():
                src = self._normalize_source(k)
                self._require_finite(v, f"{src} 的 min_ev 阈值")
                if float(v) < 0.0:
                    raise ValueError(f"{src} 的 min_ev 阈值不能小于 0.0")
                self.min_ev_thresholds[src] = float(v)

        if kelly_fractions:
            for k, v in kelly_fractions.items():
                src = self._normalize_source(k)
                self._require_finite(v, f"{src} 的凯利系数")
                if not (0.0 <= float(v) <= 1.0):
                    raise ValueError(f"{src} 的凯利系数必须在 [0.0, 1.0] 内")
                self.kelly_fractions[src] = float(v)

        if max_stake_fractions:
            for k, v in max_stake_fractions.items():
                src = self._normalize_source(k)
                self._require_finite(v, f"{src} 的最大仓位上限")
                if not (0.0 <= float(v) <= 1.0):
                    raise ValueError(f"{src} 的最大仓位上限必须在 [0.0, 1.0] 内")
                self.max_stake_fractions[src] = float(v)

        # 异常盘口过滤阈值（基于同场 CN 与 INTL 对比）
        # margin_spread 定义为 M_cn - M_intl
        self.max_allowed_margin_spread: float | None = max_allowed_margin_spread
        self.min_allowed_margin_spread: float | None = min_allowed_margin_spread
        # 相对赔率偏差绝对值上限：|(O_intl - O_cn) / O_cn|
        self.max_allowed_odds_rel_diff: float | None = max_allowed_odds_rel_diff
        # 去水隐含概率差绝对值上限：|q_intl - q_cn|
        self.max_allowed_fair_prob_diff: float | None = max_allowed_fair_prob_diff
        # 是否在筛选 +EV 选项时自动剔除被标记为异常的盘口/选项
        self.exclude_anomalous_from_ev: bool = exclude_anomalous_from_ev

    @staticmethod
    def _require_finite(value: Any, label: str) -> None:
        """拒绝 NaN/inf：它们能绕过 `<` / `<=` 比较，静默把下游算成 NaN。"""
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} 必须是数值，收到: {value!r}") from exc
        if not math.isfinite(number):
            raise ValueError(f"{label} 必须是有限数值，收到: {value!r}")

    @classmethod
    def _normalize_source(cls, source_type: str) -> str:
        """校验并标准化盘口来源：'CN'（竞彩盘）或 'INTL'（国际盘）。"""
        src = str(source_type).strip().upper()
        if src not in cls.VALID_SOURCES:
            raise ValueError(
                f"source_type 必须明确标明为 'CN'（竞彩盘）或 'INTL'（国际盘），收到: {source_type}"
            )
        return src

    @staticmethod
    def _source_label(src: str) -> str:
        return "竞彩盘(CN)" if src == "CN" else "国际盘(INTL)"

    @staticmethod
    def _calc_margin_and_fair_probs(
        odds: list[float],
    ) -> tuple[float, float, float, list[float]]:
        """计算隐含概率和 Σ、理论返奖率 R、抽水率 M 与归一化去水概率 q_i。"""
        if not odds:
            raise ValueError("赔率不能为空")
        values: list[float] = []
        for o in odds:
            try:
                value = float(o)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"赔率必须为数值，收到: {o!r}") from exc
            if not math.isfinite(value):
                raise ValueError(f"赔率必须为有限数值，收到: {o!r}")
            values.append(value)
        if any(o <= 1.0 for o in values):
            raise ValueError("所有赔率必须大于 1.0")
        inv_list = [1.0 / o for o in values]
        overround = sum(inv_list)
        payout_rate = 1.0 / overround
        margin_rate = 1.0 - payout_rate
        fair_probs = [x / overround for x in inv_list]
        return overround, payout_rate, margin_rate, fair_probs

    @staticmethod
    def _validate_probs(
        probs: Sequence[float],
        size: int,
        label: str,
        push_prob: float = 0.0,
    ) -> list[float]:
        """校验真概率向量：长度、有限性、非负性，以及加上走水概率后是否归一。"""
        if len(probs) != size:
            raise ValueError(f"{label} 必须为长度 {size}")
        values: list[float] = []
        for p in probs:
            try:
                value = float(p)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label} 的每个概率都必须为数值") from exc
            # NaN 同时绕过 `< 0` 与 `abs(sum-1) > 1e-6`，必须显式拦掉。
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{label} 的每个概率都必须是 [0, 1] 内的有限数值")
            values.append(value)
        if abs(sum(values) + push_prob - 1.0) > 1e-6:
            raise ValueError(f"{label} 的概率和加上走水概率必须等于 1.0")
        return values

    def _calc_kelly_stakes(
        self,
        src: str,
        odds: list[float],
        true_probs: list[float] | None,
        ev_list: list[float] | None,
    ) -> list[dict[str, float]] | None:
        """按盘口来源计算全凯利 f*、折算凯利与单注上限截断后的建议仓位。

        公式为 f* = ev / (odds - 1)：在“胜/负/走水”三结果盘口中它相当于
        (p_win * b - p_loss) / (b * (p_win + p_loss)) 少除了一个未走水概率
        (p_win + p_loss)，因此走水概率越大越偏低；三结果盘口由
        evaluate_two_way 在 kelly_note 里显式标注。
        """
        if true_probs is None or ev_list is None:
            return None

        k_frac = self.kelly_fractions[src]
        max_cap = self.max_stake_fractions[src]
        stakes: list[dict[str, float]] = []

        for o, ev in zip(odds, ev_list):
            b = o - 1.0
            full_kelly = max(0.0, ev / b) if ev > 0.0 else 0.0
            scaled_kelly = full_kelly * k_frac
            final_stake = min(scaled_kelly, max_cap)
            stakes.append(
                {
                    "full_kelly": round(full_kelly, 6),
                    "scaled_kelly": round(scaled_kelly, 6),
                    "recommended_stake": round(final_stake, 6),
                }
            )
        return stakes

    def evaluate_1x2(
        self,
        match_id: str,
        source_type: str,
        odds: tuple[float, float, float],
        true_probs: tuple[float, float, float] | None = None,
        market_name: str = "1X2",
    ) -> dict[str, Any]:
        """评估单场胜平负(1X2)盘口（须标明 CN 竞彩盘 或 INTL 国际盘）。"""
        src = self._normalize_source(source_type)
        if len(odds) != 3:
            raise ValueError("1X2 赔率必须为长度 3 的元组 (胜, 平, 负)")

        overround, payout_rate, margin_rate, fair_probs = self._calc_margin_and_fair_probs(
            list(odds)
        )
        outcomes = ["HOME_WIN", "DRAW", "AWAY_WIN"]

        true_list: list[float] | None = None
        ev_list: list[float] | None = None
        if true_probs is not None:
            # 旧实现只校验长度与概率和：负数或 NaN 概率能通过，直接把 EV/Kelly 算成垃圾。
            true_list = self._validate_probs(true_probs, 3, "true_probs")
            ev_list = [p * o - 1.0 for p, o in zip(true_list, odds)]

        kelly_info = self._calc_kelly_stakes(src, list(odds), true_list, ev_list)

        return self._build_report(
            match_id=match_id,
            source_type=src,
            market_type="1X2",
            market_name=f"[{self._source_label(src)}] {market_name}",
            line=None,
            outcomes=outcomes,
            odds=list(odds),
            overround=overround,
            payout_rate=payout_rate,
            margin_rate=margin_rate,
            fair_probs=fair_probs,
            true_probs=true_list,
            ev_list=ev_list,
            kelly_info=kelly_info,
        )

    def evaluate_two_way(
        self,
        match_id: str,
        source_type: str,
        market_type: str,
        odds: tuple[float, float],
        outcomes: tuple[str, str],
        line: float | None = None,
        true_probs: tuple[float, float] | None = None,
        push_prob: float = 0.0,
        true_push_prob: float = 0.0,
        market_name: str | None = None,
    ) -> dict[str, Any]:
        """评估双边盘口：让球/亚盘(AH)或大小球(OU)（须标明 CN 竞彩盘 或 INTL 国际盘）。"""
        src = self._normalize_source(source_type)
        if len(odds) != 2 or len(outcomes) != 2:
            raise ValueError("双边盘口赔率与选项名称必须各为长度 2")
        if not (0.0 <= push_prob < 1.0) or not (0.0 <= true_push_prob < 1.0):
            raise ValueError("push_prob 与 true_push_prob 必须在 [0.0, 1.0) 内")

        overround, payout_rate, margin_rate, fair_probs = self._calc_margin_and_fair_probs(
            list(odds)
        )
        effective_payout = (1.0 - push_prob) * payout_rate + push_prob * 1.0

        true_list: list[float] | None = None
        ev_list: list[float] | None = None
        if true_probs is not None:
            true_list = self._validate_probs(true_probs, 2, "true_probs", true_push_prob)
            p_a, p_b = true_list
            ev_a = p_a * (odds[0] - 1.0) - p_b * 1.0
            ev_b = p_b * (odds[1] - 1.0) - p_a * 1.0
            ev_list = [ev_a, ev_b]

        kelly_info = self._calc_kelly_stakes(src, list(odds), true_list, ev_list)

        base_label = market_name or (
            f"{market_type}({line})" if line is not None else market_type
        )
        full_label = f"[{self._source_label(src)}] {base_label}"

        report = self._build_report(
            match_id=match_id,
            source_type=src,
            market_type=market_type,
            market_name=full_label,
            line=line,
            outcomes=list(outcomes),
            odds=list(odds),
            overround=overround,
            payout_rate=payout_rate,
            margin_rate=margin_rate,
            fair_probs=fair_probs,
            true_probs=true_list,
            ev_list=ev_list,
            kelly_info=kelly_info,
        )
        report["push_prob_input"] = round(push_prob, 6)
        report["effective_payout_with_push"] = round(effective_payout, 6)
        # 走水盘口的凯利只是近似：f* = ev/(o-1) 假定只有“赢/输”两种结果，
        # 而含走水的精确解是 ev/((o-1)*(p_win+p_loss))，即再除以未走水概率。
        # 因此 push 概率越大，这里的 full_kelly 越**低估**真实最优仓位：
        # full_kelly * 1/(1-push_prob) 才等于增长最优仓位。必须显式说明，
        # 不能让调用方以为它在 push_prob>0 时仍然精确。
        if kelly_info is not None and (push_prob > 0.0 or true_push_prob > 0.0):
            report["kelly_note"] = (
                "push_prob>0 时 ev/(o-1) 低估真实最优仓位，精确解需除以 (1-push_prob)（即未走水概率 "
                "p_win+p_loss）：精确 f* = ev/((odds-1)*(p_win+p_loss))，"
                "full_kelly * 1/(1-push_prob) 才等于增长最优仓位。"
                f"本盘口 push_prob={round(push_prob, 6)}、true_push_prob={round(true_push_prob, 6)}，"
                "故此处 full_kelly/scaled_kelly 为近似值（偏低估），仅供仓位上限参考。"
            )
        return report

    def evaluate_match(self, match_payload: dict[str, Any]) -> list[dict[str, Any]]:
        """评估单场比赛包含的多个盘口（每个盘口必须标明 source_type: "CN" 或 "INTL"）。"""
        match_id = str(match_payload["match_id"])
        markets = match_payload.get("markets") or []
        results: list[dict[str, Any]] = []

        for m in markets:
            if not m.get("source_type"):
                raise ValueError(
                    f"场次 {match_id} 的盘口缺少 source_type，必须标明 'CN'（竞彩盘）或 'INTL'（国际盘）"
                )
            src = self._normalize_source(m["source_type"])
            m_type = str(m.get("type", "")).upper()

            if m_type == "1X2":
                res = self.evaluate_1x2(
                    match_id=match_id,
                    source_type=src,
                    odds=tuple(m["odds"]),
                    true_probs=tuple(m["true_probs"]) if m.get("true_probs") is not None else None,
                    market_name=m.get("name") or "1X2",
                )
            elif m_type in ("AH", "HANDICAP"):
                ah_outcomes: tuple[str, str] = tuple(m.get("outcomes") or ("AH_HOME", "AH_AWAY"))  # type: ignore[assignment]
                res = self.evaluate_two_way(
                    match_id=match_id,
                    source_type=src,
                    market_type="AH",
                    line=m.get("line"),
                    odds=tuple(m["odds"]),
                    outcomes=ah_outcomes,
                    true_probs=tuple(m["true_probs"]) if m.get("true_probs") is not None else None,
                    push_prob=float(m.get("push_prob") or 0.0),
                    true_push_prob=float(m.get("true_push_prob") or 0.0),
                    market_name=m.get("name"),
                )
            elif m_type in ("OU", "OVER_UNDER"):
                ou_outcomes: tuple[str, str] = tuple(m.get("outcomes") or ("OVER", "UNDER"))  # type: ignore[assignment]
                res = self.evaluate_two_way(
                    match_id=match_id,
                    source_type=src,
                    market_type="OU",
                    line=m.get("line"),
                    odds=tuple(m["odds"]),
                    outcomes=ou_outcomes,
                    true_probs=tuple(m["true_probs"]) if m.get("true_probs") is not None else None,
                    push_prob=float(m.get("push_prob") or 0.0),
                    true_push_prob=float(m.get("true_push_prob") or 0.0),
                    market_name=m.get("name"),
                )
            else:
                raise ValueError(f"不支持的盘口类型: {m_type}")

            results.append(res)

        return results

    @staticmethod
    def _align_outcomes(
        cn_outcomes: Sequence[Any],
        intl_outcomes: Sequence[Any],
    ) -> tuple[str, list[tuple[int, int]]]:
        """决定 CN 与 INTL 选项的配对方式。

        两边携带同一个选项集合时按名称对齐（"by_name"），否则退回下标对齐
        （"by_index"）。选项名重复时名称对齐会有歧义，因此同样退回下标。
        旧实现无条件按下标配对：只要两个来源把 HOME_WIN/AWAY_WIN 的顺序写反，
        就会拿主胜去比客胜，赔率偏差、去水概率差与异常标记全部失真。
        """
        cn_names = [str(x) for x in cn_outcomes]
        intl_names = [str(x) for x in intl_outcomes]
        if (
            len(set(cn_names)) == len(cn_names)
            and len(set(intl_names)) == len(intl_names)
            and set(cn_names) == set(intl_names)
        ):
            intl_pos = {name: idx for idx, name in enumerate(intl_names)}
            return "by_name", [(idx, intl_pos[name]) for idx, name in enumerate(cn_names)]
        return "by_index", [(idx, idx) for idx in range(min(len(cn_names), len(intl_names)))]

    def compare_cn_vs_intl_same_match(
        self,
        match_evaluations: list[dict[str, Any]],
        max_allowed_margin_spread: float | None = None,
        min_allowed_margin_spread: float | None = None,
        max_allowed_odds_rel_diff: float | None = None,
        max_allowed_fair_prob_diff: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        对比同一场比赛（相同 match_id、相同 market_type、相同 line）下，
        竞彩盘(CN)与国际盘(INTL)的返奖率差值、抽水差值与赔率偏差，
        并依据阈值标记异常盘口（is_anomalous 与 anomaly_reasons）。

        记录里 outcome_alignment 标明选项配对方式："by_name"（两边选项集合相同，
        按名称配对）或 "by_index"（集合不同或名称重复，按顺序配对）。
        """
        max_m_spread = (
            self.max_allowed_margin_spread
            if max_allowed_margin_spread is None
            else max_allowed_margin_spread
        )
        min_m_spread = (
            self.min_allowed_margin_spread
            if min_allowed_margin_spread is None
            else min_allowed_margin_spread
        )
        max_o_rel_diff = (
            self.max_allowed_odds_rel_diff
            if max_allowed_odds_rel_diff is None
            else max_allowed_odds_rel_diff
        )
        max_p_diff = (
            self.max_allowed_fair_prob_diff
            if max_allowed_fair_prob_diff is None
            else max_allowed_fair_prob_diff
        )

        grouped: dict[tuple[str, str, float | None], dict[str, list[dict[str, Any]]]] = {}
        for item in match_evaluations:
            key = (item["match_id"], item["market_type"], item["line"])
            grouped.setdefault(key, {}).setdefault(str(item["source_type"]), []).append(item)

        comparisons: list[dict[str, Any]] = []
        for (match_id, market_type, line), pair in grouped.items():
            if "CN" not in pair or "INTL" not in pair:
                continue

            cn_list = pair["CN"]
            intl_list = pair["INTL"]
            cn_item = cn_list[0]
            intl_item = intl_list[0]

            r_cn = float(cn_item["payout_rate"])
            r_intl = float(intl_item["payout_rate"])
            m_cn = float(cn_item["margin_rate"])
            m_intl = float(intl_item["margin_rate"])

            payout_diff_intl_minus_cn = r_intl - r_cn
            margin_diff_cn_minus_intl = m_cn - m_intl

            market_anomaly_reasons: list[str] = []
            # 同一 (match_id, market_type, line) 下出现多个同源盘口时，旧实现按 dict
            # 覆盖、静默只留最后一条：被覆盖那本赔率既不参与对比也不参与异常判定，
            # 却照样进入 +EV 池。这里显式标记，交给下游按异常盘口剔除。
            if len(cn_list) > 1 or len(intl_list) > 1:
                market_anomaly_reasons.append(
                    f"DUPLICATE_SOURCE_MARKET(CN={len(cn_list)},INTL={len(intl_list)})"
                )
            if max_m_spread is not None and margin_diff_cn_minus_intl > max_m_spread:
                market_anomaly_reasons.append(
                    f"MARGIN_SPREAD_TOO_HIGH({margin_diff_cn_minus_intl:.4f}>{max_m_spread:.4f})"
                )
            if min_m_spread is not None and margin_diff_cn_minus_intl < min_m_spread:
                market_anomaly_reasons.append(
                    f"MARGIN_SPREAD_TOO_LOW({margin_diff_cn_minus_intl:.4f}<{min_m_spread:.4f})"
                )

            cn_outcomes = list(cn_item["outcomes"])
            intl_outcomes = list(intl_item["outcomes"])
            cn_odds = cn_item["odds"]
            intl_odds = intl_item["odds"]
            cn_fair = cn_item["fair_probs_no_vig"]
            intl_fair = intl_item["fair_probs_no_vig"]
            outcome_alignment, index_pairs = self._align_outcomes(cn_outcomes, intl_outcomes)

            outcome_spreads: list[dict[str, Any]] = []
            for cn_idx, intl_idx in index_pairs:
                o_cn = float(cn_odds[cn_idx])
                o_intl = float(intl_odds[intl_idx])
                q_cn = float(cn_fair[cn_idx])
                q_intl = float(intl_fair[intl_idx])

                odds_abs_diff = o_intl - o_cn
                rel_diff = (o_intl - o_cn) / o_cn if o_cn != 0 else 0.0
                prob_diff = q_intl - q_cn

                outcome_reasons: list[str] = []
                if max_o_rel_diff is not None and abs(rel_diff) > max_o_rel_diff:
                    outcome_reasons.append(
                        f"ODDS_REL_DIFF_EXCEEDED(|{rel_diff:.4f}|>{max_o_rel_diff:.4f})"
                    )
                if max_p_diff is not None and abs(prob_diff) > max_p_diff:
                    outcome_reasons.append(
                        f"FAIR_PROB_DIFF_EXCEEDED(|{prob_diff:.4f}|>{max_p_diff:.4f})"
                    )

                combined_reasons = market_anomaly_reasons + outcome_reasons
                cn_name = cn_outcomes[cn_idx]
                intl_name = intl_outcomes[intl_idx]
                outcome_label = (
                    cn_name if cn_name == intl_name else f"{cn_name} vs {intl_name}"
                )
                outcome_spreads.append(
                    {
                        "outcome": outcome_label,
                        "cn_outcome": cn_name,
                        "intl_outcome": intl_name,
                        "cn_odds": round(o_cn, 4),
                        "intl_odds": round(o_intl, 4),
                        "odds_diff_intl_minus_cn": round(odds_abs_diff, 4),
                        "odds_rel_diff_ratio": round(rel_diff, 6),
                        "cn_fair_prob": round(q_cn, 6),
                        "intl_fair_prob": round(q_intl, 6),
                        "fair_prob_diff_intl_minus_cn": round(prob_diff, 6),
                        "is_anomalous": len(combined_reasons) > 0,
                        "anomaly_reasons": combined_reasons,
                    }
                )

            market_is_anomalous = len(market_anomaly_reasons) > 0 or any(
                x["is_anomalous"] for x in outcome_spreads
            )

            comparisons.append(
                {
                    "match_id": match_id,
                    "market_type": market_type,
                    "line": line,
                    "comparison_pair": "竞彩盘(CN) vs 国际盘(INTL)",
                    "cn_payout_rate": round(r_cn, 6),
                    "intl_payout_rate": round(r_intl, 6),
                    "payout_diff_intl_minus_cn": round(payout_diff_intl_minus_cn, 6),
                    "cn_margin_rate": round(m_cn, 6),
                    "intl_margin_rate": round(m_intl, 6),
                    "margin_diff_cn_minus_intl": round(margin_diff_cn_minus_intl, 6),
                    "is_anomalous": market_is_anomalous,
                    "market_anomaly_reasons": market_anomaly_reasons,
                    "outcome_alignment": outcome_alignment,
                    "outcome_odds_deviations": outcome_spreads,
                }
            )

        return comparisons

    def filter_anomalous_markets_and_comparisons(
        self,
        match_evaluations: list[dict[str, Any]],
        comparisons: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """
        根据同场次 CN vs INTL 对比结果，对评估明细和对比记录执行过滤：
        返回：
        1. clean_evaluations：剔除异常盘口后的正常评估明细
        2. clean_comparisons：剔除异常盘口后的正常同场对比列表
        3. filtered_anomalies：被过滤掉的异常对比记录明细
        """
        anomalous_keys = {
            (c["match_id"], c["market_type"], c["line"])
            for c in comparisons
            if c.get("is_anomalous")
        }

        clean_evaluations = [
            item
            for item in match_evaluations
            if (item["match_id"], item["market_type"], item["line"]) not in anomalous_keys
        ]
        clean_comparisons = [
            c for c in comparisons if not c.get("is_anomalous")
        ]
        filtered_anomalies = [
            c for c in comparisons if c.get("is_anomalous")
        ]
        return clean_evaluations, clean_comparisons, filtered_anomalies

    def batch_evaluate_and_filter_ev(
        self,
        matches: list[dict[str, Any]],
        override_min_ev: dict[str, float] | None = None,
        source_filter: str | None = None,
        exclude_anomalous: bool | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """
        批量输入多场比赛盘口，返回：
        1. 全部盘口评估明细列表（默认若开启 exclude_anomalous 则仅保留非异常盘口评估；
           若需查看全部含异常的原始评估，可先调 evaluate_match + compare_cn_vs_intl_same_match）
        2. 筛选出的满足各自来源 +EV 阈值的投注选项（按 EV 降序排列）
        3. 同场次竞彩盘(CN)与国际盘(INTL)对比列表（含异常标记与原因）
        """
        thresholds = dict(self.min_ev_thresholds)
        if override_min_ev:
            for k, v in override_min_ev.items():
                thresholds[self._normalize_source(k)] = float(v)

        src_filter = self._normalize_source(source_filter) if source_filter else None
        drop_anomalous = (
            self.exclude_anomalous_from_ev
            if exclude_anomalous is None
            else exclude_anomalous
        )

        raw_evaluations: list[dict[str, Any]] = []
        for match_payload in matches:
            raw_evaluations.extend(self.evaluate_match(match_payload))

        same_match_comparisons = self.compare_cn_vs_intl_same_match(raw_evaluations)

        # 建立按 (match_id, market_type, line, outcome) 的异常映射。
        # 选项是按下标还是按名称配对由对比决定，所以三种标签都登记一次，
        # 否则 by_index 下 "A vs B" 的标签永远匹配不到评估明细里的选项名。
        outcome_anomaly_map: dict[tuple[str, str, float | None, str], list[str]] = {}
        market_anomaly_map: dict[tuple[str, str, float | None], list[str]] = {}
        for comp in same_match_comparisons:
            m_key = (comp["match_id"], comp["market_type"], comp["line"])
            m_reasons = list(comp.get("market_anomaly_reasons") or [])
            if m_reasons:
                market_anomaly_map[m_key] = m_reasons
            for od in comp.get("outcome_odds_deviations") or []:
                if od.get("is_anomalous"):
                    reasons = list(od.get("anomaly_reasons") or [])
                    for label in (od.get("outcome"), od.get("cn_outcome"), od.get("intl_outcome")):
                        if label:
                            outcome_anomaly_map[(comp["match_id"], comp["market_type"], comp["line"], label)] = reasons

        if drop_anomalous:
            active_evaluations, _, _ = self.filter_anomalous_markets_and_comparisons(
                raw_evaluations, same_match_comparisons
            )
        else:
            active_evaluations = raw_evaluations

        positive_ev_options: list[dict[str, Any]] = []
        for item in active_evaluations:
            src = item["source_type"]
            if src_filter is not None and src != src_filter:
                continue
            ev_list = item.get("ev_list")
            if ev_list is None:
                continue

            src_threshold = thresholds[src]
            kelly_list = item.get("kelly_stakes") or []
            m_key = (item["match_id"], item["market_type"], item["line"])

            for idx, ev_val in enumerate(ev_list):
                outcome_name = item["outcomes"][idx]
                o_key = (item["match_id"], item["market_type"], item["line"], outcome_name)
                reasons = outcome_anomaly_map.get(o_key) or market_anomaly_map.get(m_key) or []
                is_anom = len(reasons) > 0

                if drop_anomalous and is_anom:
                    continue

                # 门槛是严格大于：ev == 门槛 属于不赚不亏，不算 +EV。
                if ev_val > src_threshold:
                    k_item = kelly_list[idx] if idx < len(kelly_list) else {}
                    option = {
                        "match_id": item["match_id"],
                        "source_type": src,
                        "source_label": item["source_label"],
                        "market_type": item["market_type"],
                        "market_name": item["market_name"],
                        "line": item["line"],
                        "outcome": outcome_name,
                        "odds": item["odds"][idx],
                        "fair_prob": item["fair_probs_no_vig"][idx],
                        "true_prob": item["true_probs"][idx],
                        "margin_rate": item["margin_rate"],
                        "ev": ev_val,
                        "min_ev_threshold_used": src_threshold,
                        "kelly_fraction_used": self.kelly_fractions[src],
                        "full_kelly": k_item.get("full_kelly"),
                        "scaled_kelly": k_item.get("scaled_kelly"),
                        "recommended_stake": k_item.get("recommended_stake"),
                        "is_anomalous": is_anom,
                        "anomaly_reasons": reasons,
                    }
                    # 走水盘口的凯利是近似值，标注必须跟着数字一起走到投注选项里。
                    if "kelly_note" in item:
                        option["kelly_note"] = item["kelly_note"]
                    positive_ev_options.append(option)

        positive_ev_options.sort(key=lambda x: x["ev"], reverse=True)
        return active_evaluations, positive_ev_options, same_match_comparisons

    @classmethod
    def _build_report(
        cls,
        match_id: str,
        source_type: str,
        market_type: str,
        market_name: str,
        line: float | None,
        outcomes: list[str],
        odds: list[float],
        overround: float,
        payout_rate: float,
        margin_rate: float,
        fair_probs: list[float],
        true_probs: list[float] | None,
        ev_list: list[float] | None,
        kelly_info: list[dict[str, float]] | None,
    ) -> dict[str, Any]:
        # 1/odds 是单选项的保本概率（break-even），不是“最小真实概率”。
        # 旧键名 min_true_prob_thresholds 保留（调用方已在用），新增语义正确的
        # break_even_prob_thresholds 作为别名，两者取同一组数值。
        min_true_probs = [1.0 / o for o in odds]
        break_even_probs = [round(p, 6) for p in min_true_probs]
        return {
            "match_id": match_id,
            "source_type": source_type,
            "source_label": cls._source_label(source_type),
            "market_type": market_type,
            "market_name": market_name,
            "line": line,
            "outcomes": outcomes,
            "odds": [round(o, 4) for o in odds],
            "overround": round(overround, 6),
            "payout_rate": round(payout_rate, 6),
            "margin_rate": round(margin_rate, 6),
            "fair_probs_no_vig": [round(p, 6) for p in fair_probs],
            "min_true_prob_thresholds": list(break_even_probs),
            "break_even_prob_thresholds": list(break_even_probs),
            "min_edge_ratio_p_over_q": round(overround, 6),
            "true_probs": [round(p, 6) for p in true_probs] if true_probs is not None else None,
            "ev_list": [round(e, 6) for e in ev_list] if ev_list is not None else None,
            "kelly_stakes": kelly_info,
        }
