# Contradiction Removal Ledger — `cursor/purge-contradictions`

Branch `cursor/purge-contradictions`, created from the frozen base `cursor/fix-live-log-gaps`
@ `3b46c68cd3508f67b4f7f8a315a041e371f20989`.

Laws are cited by their number in `2026-11-26-score-convergence-spec-v1.md` §1
(L1 单向因果/无对冲, L2 零兜底, L3 时间边界), with the implementing clauses in
`2026-11-26-matrix-macro-veto-addendum.md` (§1.1–§1.16) and
`2026-11-26-v16-22-engine-addendum.md` (§1.1–§1.11).

All removals live in `tests/test_contradiction_purge.py`. Every row's test was
revert-verified: restoring the old block turns exactly that test red.

---

## A. Output that recommends two opposing directions

| # | File | Symbol | What it did | Law | Replaced by | Test |
|---|---|---|---|---|---|---|
| A1 | `app_v84_hermes.py` | `DeviationDrivenEngine.build_prediction_output` | Took `allocations[:2]` by score. `2-0`(home) + `1-1`(draw) both TIER_1 → locked **主胜 + 平局**. | L1; add. §1.8 | Second slot restricted to the first pick's 1X2 direction; `<2` same-direction candidates → `available=False`, `reason='candidates_below_two_in_one_direction'`, `scores=[]`. | `test_deviation_prediction_never_locks_two_opposing_directions`, `test_deviation_prediction_refuses_when_one_direction_only_has_one` |
| A2 | `app_v84_hermes.py` | `run_ventropy_pipeline` | `secondary` fell back to `tier_2[0]` / `classified[1]` with no side check → primary `2-0`(home) + secondary `1-1`(draw). | L1; add. §1.1 | `_same_direction_pick()` scans same-side candidates only; `N/A` when absent, with the reason appended to `log`. | `test_ventropy_secondary_stays_in_the_primary_direction`, `test_ventropy_never_fills_secondary_from_another_tier_direction` |
| A3 | `app_v84_hermes.py` | `DeviationDrivenEngine.prioritize_allocation` + `evaluate_records` | Split a budget across every TIER row → probe returned **`2-0` 530.41 + `1-1` 469.59** on one match; emitted `bet_amount`/`total_budget`/`allocated_total`. | L1; add. §1.8 | Money allocation deleted. Function only ranks by `score`; no amount, budget or exposure keys anywhere. | `test_allocation_output_has_no_money_fields` |
| A4 | `app_v84_hermes.py` | `ShannonEntropyEngine.allocate_deviation_driven` | Re-derived `allocation_weight`/`allocation_pct`/`total_exposure` from `bet_amount`. | L1; add. §1.8 | Re-emission deleted; returns rank-only, `allocation_method='deviation_driven_dual_track_rank_only'`. | `test_allocation_output_has_no_money_fields` |
| A5 | `app_v84_hermes.py` | `compare_matches` (TG) | Printed 「建议双选」 and 「建议双选或回避」 for close matches. | L1 | **User ruling 1 (final):** the lines are kept, purely as risk statements — 「★ 分歧过大，不做推荐：{match}（差距仅X个百分点）」 and 「⚠️ 分歧最大的场次（不做推荐）：」 — with **no** 首选/次选/双选/强推/推荐 vocabulary in any summary line (`不做推荐` is the one allowed use: it withholds a recommendation). A match whose two scores straddle 1X2 reports 「方向冲突(...)：不推荐」 instead of offering both. | `test_summary_lines_carry_no_selection_guidance`, `test_comparison_report_never_offers_two_directions_for_one_match`, `test_comparison_report_single_direction_states_it_once` |
| A6 | `app_v84_hermes.py` | `MarketDefenseInsightAnalyzer.evaluate_trap_risk` | `secondary = picks[1] if len>1 else primary` → the same score used as both picks (one score presented as a pair). | L1; add. §1.7 | `secondary = picks[1] if len>1 else None`. | `test_market_defense_secondary_is_never_the_primary_key` |
| A7 | `app_v84_hermes.py` | 15→2 `second_score` fallback | `remaining_all` / `sorted_final[1][0]` dropped the direction filter, so the 2nd score could be the opposite 1X2 result. | L1 | All candidate pools passed through `_same_direction_only`; empty string when no same-side 2nd exists. | covered by `test_deviation_prediction_*` + sweep matrix |
| A8 | `app_v84_hermes.py` | `ScoreOddsAnalyzer` second-score pick | `cold_items.iloc[0]` / `rest.iloc[0]` were not direction-constrained. | L1 | Filtered on `outcome_group == first pick's group`; no fallback to the other side. | covered by full suite (`test_tg_command_dispatcher_offline`) |
| A9 | `app_v84_hermes.py` | trap-warning report block | Published 「反向首选 / 反向次选」 — two bettable picks on the opposite side of the locked direction. | L1 | Relabelled 「关注比分（庄家利益侧，非推荐方向）」; UI keying and `①` line updated (`反向置信度`→`陷阱置信度`). | `test_trap_report_labels_the_bookie_side_as_non_recommendation` |
| A10 | `app_v84_hermes.py` | `run_ventropy_pipeline` deep-handicap bait fallback | `_clean_ranked[0]` / `_clean_ranked[1]` replaced the pair regardless of side. | L1 | Replacement also routed through `_same_direction_pick`. | `test_deep_handicap_fallback_keeps_the_primary_direction` |
| A11 | `app_v84_hermes.py` | report labels | `NL对冲:` metric label and 「双核对冲覆盖」 reasoning string read as hedging advice. | L1 (wording) | `NL压缩:` / 「保留双核核对」. | full suite (`test_production_shaped_tg_has_every_option_exactly_once`) |

**Kept deliberately:** two scores inside ONE direction is the required output
(spec §4.2 `locked_scores` length == 2) and is preserved everywhere.

---

## B. Silent numeric fabrication on the analysis path

| # | File | Symbol | What it did | Law | Replaced by | Test |
|---|---|---|---|---|---|---|
| B1 | `convergence_arbitrator.py` | `_to_float(value, default=2.5)` | Module-wide default: **every** missing number became `2.5` (odds, line, handicap). | L2; spec §4.3 | **User ruling 2 (final):** the `default` parameter is **deleted** — signature is `_to_float(value)`. Missing/invalid → `None`; passing a fallback is now a `TypeError`. `ou_from_scores` / `resolve_cores` return a missing state instead of a 2.5-derived verdict. | `test_convergence_to_float_has_no_default_parameter_at_all`, `test_missing_line_yields_a_missing_conclusion_not_a_2_5_verdict`, `test_resolve_cores_reports_missing_line_instead_of_assuming_2_5`, `test_missing_value_yields_missing_state_not_2_5` |
| B2 | `convergence_arbitrator.py` | `ou_from_scores` | With no line it used the fabricated 2.5 and returned `'走'` for a 2.5-goal average. | L2 | Delegates to new `ou_verdict()`, which returns `available=False`, `missing=['ou_line']`, `verdict=''`; non-positive line is also `missing`. | `test_ou_verdict_reports_missing_line_instead_of_assuming_2_5`, `test_ou_verdict_rejects_a_non_positive_line` |
| B3 | `app_v84_hermes.py` | `execute_15to2_matrix` 1X2 precompute | `_safe_odds(Home, 3.0)` / `(Away, 3.0)` / `(Draw, 3.3)` invented a market → `odds_gap=0.0` → 「囚徒困境」 fired on a match with **no odds at all**. | L2 | `_safe_odds(...)` with no default; `_have_1x2` gate; `odds_gap=None` when incomplete; missing names logged and written to `anomaly_payload_out['odds_1x2_missing']`; 4 consumers (囚徒/鹰鸽/混合纳什/odds_gap) gated. | `test_game_theory_does_not_invent_1x2_odds` |
| B4 | `blade_ml_engine.py` | `_safe_float(value, default=0.0)` | Any missing/non-finite number became `0.0`, indistinguishable from a real 0. | L2 | Default `None`; `math.isfinite` failure → `None`. | `test_ml_safe_float_reports_missing_rather_than_zero` |
| B5 | `blade_ml_engine.py` | `calc_bivariate_poisson_matrix` | `max(0.01, _safe_float(lam_h, 1.2))` built a full normalized 121-cell matrix from nothing. | L2 | Returns `{}` when either λ is missing/non-positive. | `test_ml_engine_refuses_to_invent_lambdas` |
| B6 | `blade_ml_engine.py` | `_extract_training_rows` | Every history row got `lam_h=1.2`, `lam_a=0.9`, `ou_line=2.5`, `ah_line=0.0` — i.e. 「没抓到」 was trained as 「主队必进 1.2 球」. | L2 | Rows missing any of the four inputs are skipped and listed in `skipped_rows[].missing`; only complete rows train. | `test_ml_training_skips_rows_missing_market_inputs` (plus `..._keeps_rows_that_have_every_input` for the hand-computed means 2.0/1.0) |
| B7 | `blade_ml_engine.py` | `train_from_history` | Empty history still produced `home_mean=1.2 / away_mean=0.9` — a model out of no data. | L2 | `trained=False`, `reason='no_usable_completed_scores'`, no mean keys. | `test_ml_training_skips_rows_missing_market_inputs` |
| B8 | `blade_ml_engine.py` | `_predict_lambdas_from_bundle` | Feature vector filled `ou_line=2.5`, `ah_line=0.0`, and means `1.2`/`0.9` when absent. | L2 | Returns `(None, None)` if any feature is missing; `_bundle_value` only accepts finite positive means. | `test_ml_predict_reports_unavailable_without_lambda` |
| B9 | `blade_ml_engine.py` | `predict_match_ml` | Always `used=True` with an invented λ; reported `local_poisson_fallback` as if it were a result. | L2 | Explicit `available=False`, `used=False`, `reason='lambda_not_formed'`, `missing=[...]`, `expected_total_goals=None`. | `test_ml_predict_reports_unavailable_without_lambda` |
| B10 | `blade_deepseek_pipeline.py` | `format_mkt_hot_wdl_matrix_tg_lines` | `float(r.get('market') or 0)` rendered a missing market/theory as `0.00%` with a `+0.00pp` Δ. | L2 | Missing renders as `-`; Δ is `-` unless both sides are numeric. | `test_market_matrix_renders_missing_as_dash_not_zero` |
| B11 | `blade_deepseek_pipeline.py` | `format_mkt_short_wdl_matrix_tg_lines` | `market_pct or 0`, `jc_pct or 0`, `gap_pct or 0` — same 0-as-missing confusion. | L2 | All three columns render `-` when absent. | `test_market_matrix_renders_missing_as_dash_not_zero` |
| B12 | `blade_deepseek_pipeline.py` | `build_mkt_short_wdl_matrix` | `mkt_minus_jc or delta or 0.0` wrote a **real 0.0 reading** into `discrete_vector` for rows with no Δ, which is consumed as the direction-lock vector. | L2 | Row omitted from `discrete_vector` when Δ is missing; a genuine 0.0 is still kept. | `test_market_short_matrix_leaves_missing_gap_out_of_the_vector` |
| B13 | `app_v84_hermes.py` | `ou_votes_final` (convergence arbitration) | Consumed `ou_from_scores` and stored the (previously fabricated) verdict as valid with no provenance. | L2 | Now records `available`, `missing`, `reason` so an empty verdict cannot be read as a verdict. | `test_ou_verdict_reports_missing_line_instead_of_assuming_2_5` |

---

## C. Defaulting to draw on a tie

| # | File | Symbol | What it did | Law | Replaced by | Test |
|---|---|---|---|---|---|---|
| C1 | `convergence_arbitrator.py` | `resolve_direction` | `max(counts, ...)` over a dict ordered 胜/平/负. `['1-1','2-1']` → counts 胜1/平1/负0 → `max` returns **平**. A 1-1 split was reported as a draw verdict. | L1; add-2 §1.3 | Tie detected explicitly: equal top counts → `direction=''`, `tie=True`, `tied_directions=[...]`. | `test_direction_vote_tie_is_unresolved_not_draw`, `test_three_way_tie_is_unresolved_not_draw` |
| C2 | `convergence_arbitrator.py` | `resolve_direction` | A three-way tie (胜1/平1/负1) likewise resolved to 平. | L1; add-2 §1.3 | Same fix: three-way tie is `tie=True`, `direction=''`. | `test_three_way_tie_is_unresolved_not_draw` |

**Not** changed: `detect_cup_mode` returns 「平局中性」 (λ multipliers `1.0/1.0`) for a
level first leg. That is a *λ neutralisation* for a real observed draw, not a
direction inference, so it is not a tie→draw rule. `_actual_wdl_from_score`
returns `平` only for an actual level score — also not a tie rule.

---

## D. Hardcoded odds tables used as data

| # | File | Symbol | What it did | Law | Replaced by | Test |
|---|---|---|---|---|---|---|
| D1 | — | — | **No literal 31-item odds dict and no mock candidate pool exists in this tree.** `mock_pool` (the V16.22 4-key per-channel pool) was never merged into `cursor/fix-live-log-gaps`. | D | Guard tests added so it cannot be re-introduced. | `test_no_mock_candidate_pool_is_ever_returned`, `test_no_literal_31_item_odds_table_in_runtime_modules` |
| D2 | `blade_deepseek_pipeline.py` | odds maps | Only constructed from live payload (`correct_score_*`, `jc_score_odds`) or from frozen λ via `_poisson_score_probs` (a *derivation*, not a literal). | — | Unchanged — inspected and judged compliant. | `test_jczq_odds_feed_the_31_pool_pack` |

---

## E. Anything shrinking the complete tree below 31 items

| # | File | Symbol | What it did | Law | Replaced by | Test |
|---|---|---|---|---|---|---|
| E1 | `app_v84_hermes.py` | `MarketDefenseInsightAnalyzer.build_market_inputs` | `if not key or '其它' in key or '其他' in key: continue` (theory) and the same test in both market branches — structurally dropped 胜其它/平其它/负其它, so the market/theory ranking ran on **28** rows and the three buckets could never reach the market heat list. | add. §1.5 | Both filters removed; buckets carry real odds and rank like any other option. | `test_market_defense_keeps_aggregate_buckets_in_the_tree` |
| E2 | `app_v84_hermes.py` | `MarketDefenseInsightAnalyzer` secondary | A single surviving candidate was duplicated into `primary` and `secondary`, i.e. the tree effectively yielded one pick presented as two. | add. §1.7 | `secondary=None`. (Also listed as A6.) | `test_market_defense_secondary_is_never_the_primary_key` |
| E3 | — | `build_pool31_option_rows`, `build_theo_mkt_31_pack`, `resolve_cs31_provenance`, `cs31_extras` | Verified intact: 12+4+12+3 = 31, per-row `market_source`, 市短/市热 matrices, recorded/derived provenance, and 4-3/3-4/4-4 kept in `cs31_extras` outside the 31 rows. | frozen | **Not touched.** New guard tests pin it. | `test_jczq_all_options_still_has_exactly_31_items`, `test_cs31_extras_still_hold_the_three_extra_lines` |

---

## F. Circular direction sources

| # | File | Symbol | What it did | Law | Replaced by | Test |
|---|---|---|---|---|---|---|
| F1 | — | `reduce_matrix_stratified` / `evaluate_direction_lock_normalized` / `13.0/5.0` | **Not present** in this tree (A-段 code was never merged). Guarded by inspection + the tests below. | add. §1.1–1.2 | — | — |
| F2 | `app_v84_hermes.py` | `run_ventropy_pipeline` result dict | Would have become a new circular source had I added a `direction` key computed from its own survivors. | add. §1.1 | Result carries **no** `direction` key; same-side property of primary/secondary is enforced by tests only. Comment records why. | `test_ventropy_direction_comes_from_the_picks_not_a_handicap_filter` |
| F3 | `app_v84_hermes.py` | `run_ventropy_pipeline` deep-handicap fallback | Filtered survivors by the RTM bait name-set, then read `_clean_ranked[0]` as the new primary — a direction derived from its own filtered survivors. | add. §1.1 | Fallback keeps the existing primary's side and never re-derives a direction. | `test_deep_handicap_fallback_keeps_the_primary_direction` |

---

## Gate edits (every one, before → after)

All three are inversions of assertions that pinned the fabrication itself. No
assertion count was reduced: `full_audit` still reports **462** checks.

1. `scripts/full_audit.py` — `ml:poisson-degenerate`
   - before: `lambda v: isinstance(v, dict) and abs(sum(v.values()) - 1.0) < 1e-6`
   - after:  `lambda v: isinstance(v, dict) and v == {}`
   - reason: asserted that `calc_bivariate_poisson_matrix(None, 'x')` produced a
     *normalized 121-cell matrix* from invented λ. That is exactly the L2 breach.

2. `scripts/full_audit.py` — `ml:safe-float-none`
   - before: `lambda v: v == 0.0` / after: `lambda v: v is None`

3. `scripts/full_audit.py` — `ml:safe-float-garbage`
   - before: `lambda v: v == 0.0` / after: `lambda v: v is None`

No other gate was touched. `check_all.py` gate list unchanged.

---

## Judged NOT a violation (left alone)

| Location | Why it stays |
|---|---|
| `odds_fallback.build_heat_from_odds_map` | Derives heat `1/odds` from odds the caller already supplied, and tags `source`/`heat_source`. Records provenance, fabricates nothing. |
| `blade_deepseek_pipeline._poisson_score_probs` / `build_theo_mkt_31_pack` theory | Theory comes from **frozen λ** (`theory_source='frozen_lambda_poisson'`). This is a derivation from a recorded parameter, explicitly permitted. |
| `blade_deepseek_pipeline.build_theo_mkt_31_pack` `market_source` / `cs31_provenance` | Cached/provenance-tagged market values — explicitly permitted by the brief ("cached odds with a source tag are fine"). |
| `blade_ml_engine._line_or_none` | `primary is not None` — keeps a genuine `0.0` level handicap. Correct already. |
| `odds_fallback._line_present`, `_local_water_gaps` | Explicitly reject `not value` so a real `0.0` line counts as present. Correct already. |
| `MarketDefenseInsightAnalyzer.evaluate_trap_risk` `main_profit > 500.0 / draw_profit < -100.0` | Bookmaker P&L classification thresholds (「庄家亏损最重方向」), not a fabrication of a market value. |
| `_safe_lambda(value, default=0.0)` | Used only for display/gating in the λ-missing branch; the value never enters arithmetic (the gate returns before any math). |
| `detect_cup_mode` 「平局中性」 | λ neutralisation for a real observed level first leg, not a tie→direction rule. |
| `_actual_wdl_from_score` / `_score_wdl` | `平` returned only for an actually level score; not a tie-break. |
| `DeviationDrivenEngine` thresholds (`dev>200`, `2.0<=ibi<=6.58`, `IBI>6.58` cull) | Model parameters, not fabricated data. |
| `cold_hedge` / `is_cold_hedge` naming | Names a bookmaker behaviour (low odds on a cold score). It never emits a second opposing pick; the *consumer* that could pick across sides was fixed (A8). |
| 「原型E/G」 prototype description strings | Describe bookmaker mechanics (「双边造热」) in a pattern catalogue; they do not recommend a direction. |
| `betfair_null_imputation_smoothing` | Body is `return lam_h, lam_a, '不改写进球期望'` — a no-op whose docstring now says so. |
| `execute_15to2_matrix(..., lam_h=1.2, lam_a=0.9)` signature defaults | Dead: the λ gate (`if not lambda_ready(lam_h, lam_a): return None, None, 'lambda_not_formed'`) returns before any use, and both call sites pass explicit values. |
| `map_over_under(matrix, line=2.5)` | Every call site passes `ou_line` explicitly and is guarded by `if ou_line is not None`. Parameter default is a signature, not a silent fill. |
| 31-item constants `JCZQ_*` | The canonical frozen option set — must stay. |

---

## Revert matrix (proof each test fails on the old code)

Restore one block, rebuild with `scripts/build_single_file.py`, run the named
test, restore.

| Block restored | Test(s) | Result |
|---|---|---|
| `convergence_arbitrator._to_float(default=2.5)` + old `ou_from_scores` | `test_convergence_to_float_has_no_default_parameter_at_all`, `test_missing_line_yields_a_missing_conclusion_not_a_2_5_verdict`, `test_resolve_cores_reports_missing_line_instead_of_assuming_2_5`, `test_missing_value_yields_missing_state_not_2_5` | 2 failed |
| `convergence_arbitrator.resolve_direction` `max()` tie | `test_direction_vote_tie_is_unresolved_not_draw`, `test_three_way_tie_is_unresolved_not_draw` | 2 failed |
| `blade_ml_engine` (`_safe_float` 0.0, λ 1.2/0.9, `2.5`, training fills) | `test_ml_engine_refuses_to_invent_lambdas`, `test_ml_safe_float_reports_missing_rather_than_zero`, `test_ml_predict_reports_unavailable_without_lambda`, `test_ml_training_skips_rows_missing_market_inputs` | 4 failed |
| `blade_deepseek_pipeline` `or 0` formatting + `or 0.0` gap | `test_market_matrix_renders_missing_as_dash_not_zero`, `test_market_short_matrix_leaves_missing_gap_out_of_the_vector` | 2 failed |
| `build_prediction_output` `top = allocations[:2]` | `test_deviation_prediction_never_locks_two_opposing_directions`, `test_deviation_prediction_refuses_when_one_direction_only_has_one` | 2 failed |
| `run_ventropy_pipeline` old `secondary` fallback chain | `test_ventropy_secondary_stays_in_the_primary_direction`, `test_ventropy_never_fills_secondary_from_another_tier_direction` | 2 failed |
| `compare_matches` 「★ 首选与次选贴近…」 line | `test_summary_lines_carry_no_selection_guidance` | 1 failed |
| `evaluate_trap_risk` `secondary = ... else primary` | `test_market_defense_secondary_is_never_the_primary_key` | 1 failed |
| `build_market_inputs` `'其它' in key: continue` | `test_market_defense_keeps_aggregate_buckets_in_the_tree` | 1 failed |
| `_safe_odds(..., 3.0/3.3)` | `test_game_theory_does_not_invent_1x2_odds` | 1 failed |

All ten reverts produced red. No revert left a test green.

---

## Gate numbers at HEAD

| Gate | Before (frozen base) | After |
|---|---|---|
| `python3 scripts/check_all.py` | 472 PASS / 0 FAIL | **472 PASS / 0 FAIL** |
| `python3 scripts/full_audit.py` | 462 / 462 | **462 / 462** |
| `pytest` | 126 passed | **155 passed** (126 + 29 new) |
| `python3 scripts/audit_module_api.py` | PASS | **PASS** |
| rebuild determinism | identical | **identical** (`22ceb07b…` before and after a second build) |

`app_v84_hermes.py` sha256: `22ceb07bbe4087206f55ea329d25b732af7775cb850457de21794263c92a6ca8`

Removal counts: **A 11, B 13, C 2, D 1 (guards only — nothing to remove), E 2, F 1 (+2 preventive)**

## User rulings applied after PR #9 was opened

| Ruling | What changed | Before | After |
|---|---|---|---|
| 1. 多场汇总表只作风险提示 | `compare_matches` wording | 「★ 首选次选均可：…（差距仅X个百分点，建议双选）」 / 「⚠️ 最不确定的场次（建议双选或回避）：」 | 「★ 分歧过大，不做推荐：{match}（差距仅X个百分点）」 / 「⚠️ 分歧最大的场次（不做推荐）：」；同场两方向 → 「方向冲突(主胜/平局)：不推荐」。汇总行内不出现 首选/次选/双选/强推/推荐 |
| 2. `_to_float` 彻底删默认值 | `convergence_arbitrator._to_float` | `_to_float(value, default: float \| None = None)` | `_to_float(value)` — no `default` parameter at all |
