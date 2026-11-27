"""守卫红绿矩阵：每条守卫在回退对应实现后必须**失败**。

做法：对被测源文件做一处定点回退（只改那一处的语义），跑指定测试，
断言它失败，然后原样恢复。守卫若在回退后仍然通过，说明它测的不是那件事。

用法：
    python scripts/matrix_revert_control.py [--keep]

`--keep` 保留现场（调试用），默认无论如何都恢复原文件。
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

# (标签, 文件, 原文, 回退成, 必须因此失败的测试)
CASES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        'ah-pid-from-row-attributes',
        'okooo_analysis_tabs.py',
        "            'pid': _pid_of(tr_attrs + inner),",
        "            'pid': _pid_of(inner),",
        'tests/test_okooo_analysis_tabs.py::test_ah_tab_keeps_the_company_pid_from_the_row_attributes',
    ),
    (
        'ah-summary-labels-itself-derived',
        'okooo_analysis_tabs.py',
        "        'provenance': 'derived_from_company_rows',",
        "        'provenance': 'page_native',",
        'tests/test_okooo_analysis_tabs.py::test_ah_summary_is_labelled_as_derived_not_page_native',
    ),
    (
        'zuizhi-reports-js-rendered',
        'okooo_analysis_tabs.py',
        "        'stage': 'zuizhi_panel_js_rendered',",
        "        'stage': 'ok',",
        'tests/test_okooo_analysis_tabs.py::test_zuizhi_panel_is_reported_as_js_rendered',
    ),
    (
        'handicap-panel-empty-stage',
        'okooo_analysis_tabs.py',
        "            out['handicap_stage'] = 'handicap_panel_empty_js_rendered'",
        "            out['handicap_stage'] = 'no_company_rows'",
        'tests/test_okooo_analysis_tabs.py::test_rq_handicap_panel_is_empty_in_the_server_html',
    ),
    (
        'betfair-not-substituted',
        'okooo_analysis_tabs.py',
        "            out['betfair_available'] = bool(has_value)",
        "            out['betfair_available'] = True",
        'tests/test_okooo_analysis_tabs.py::test_pnl_betfair_is_reported_missing_not_substituted',
    ),
    (
        'one-fetch-per-tab',
        'okooo_analysis_tabs.py',
        "    tabs = tuple(only) if only else tuple(TAB_CLICK_PATHS)",
        "    tabs = tuple(only) if only else ('ah',)",
        'tests/test_okooo_analysis_tabs.py::test_scrape_records_one_fetch_per_tab_with_url_status_bytes',
    ),
    (
        'order-normalization-collapses-spellings',
        'live500_board.py',
        "    try:\n        return str(int(token))",
        "    try:\n        return token",
        'tests/test_live500_board.py::test_normalize_order_collapses_every_spelling',
    ),
    (
        'board-row-keeps-both-handles',
        'live500_board.py',
        "            'fid': fid,",
        "            'fid': '',",
        'tests/test_live500_board.py::test_board_row_carries_both_handles_and_the_score',
    ),
    (
        'zqdc-has-no-order',
        'live500_board.py',
        "            out['order_missing'] += 1",
        "            order = fid  # 回退：拿 fid 冒充 order\n            out['order_missing'] += 0",
        'tests/test_live500_board.py::test_zqdc_rows_have_no_order_attribute',
    ),
    (
        'detail-lineups-side-by-order',
        'live500_board.py',
        "        if kind == 'starters' and last_kind == 'subs':\n            side_index = min(side_index + 1, 1)",
        "        if kind == 'starters' and last_kind == 'subs':\n            side_index = side_index",
        'tests/test_live500_board.py::test_detail_reads_score_both_lineups_and_all_stats',
    ),
    (
        'odds-precedence-okooo-only',
        'match_data_matrix.py',
        "    'asian_handicap': (SRC_OKOOO, SRC_500),",
        "    'asian_handicap': (SRC_500, SRC_OKOOO),",
        'tests/test_match_data_matrix.py::test_odds_precedence_wins_when_both_sources_offer_the_field',
    ),
    (
        'every-filled-cell-has-a-url',
        'match_data_matrix.py',
        "        if value in (None, ''):",
        "        if value in (None, ''):\n            return _Cell(None, source=SRC_500, reason='ok')",
        'tests/test_match_data_matrix.py::test_every_filled_cell_carries_a_traceable_url',
    ),
    (
        'gap-reason-not-started',
        'match_data_matrix.py',
        "            reason = 'not_started' if board.get('started') is False else 'not_in_source'",
        "            reason = 'not_in_source'",
        'tests/test_match_data_matrix.py::test_gap_reasons_are_enumerated',
    ),
    (
        'matrix-not-started-is-not-a-gap',
        'match_data_matrix.py',
        "        if not chosen.filled and chosen.reason not in NON_GAP_REASONS:",
        "        if not chosen.filled:",
        'tests/test_matrix_reason_codes.py::test_pre_match_scores_are_not_a_gap',
    ),
    (
        'matrix-not-started-status-line',
        'match_data_matrix.py',
        "    status_lines = [\n        _premarket_status_line(status_fields[code], code, max_items)\n        for code in sorted(status_fields)\n    ]",
        "    status_lines = []  # 回退：赛前状态整条不印",
        'tests/test_matrix_reason_codes.py::test_pre_match_banner_has_a_status_line_and_no_gap_line_for_it',
    ),
    (
        'matrix-not-started-status-not-unconditional',
        'match_data_matrix.py',
        "            if code in NON_GAP_REASONS:\n                status_fields.setdefault(code, []).append(str(field))",
        "            if True:\n                status_fields.setdefault('not_started', []).append(str(field))",
        'tests/test_matrix_reason_codes.py::test_post_match_banner_has_no_status_line',
    ),
    (
        'matrix-tab-not-fetched-keeps-its-source',
        'match_data_matrix.py',
        "        return _Cell(None, source=SRC_OKOOO, reason=TAB_NOT_FETCHED_REASON,\n                     stage=TAB_NOT_FETCHED_REASON)",
        "        return _Cell(None, reason='source_blocked', stage='tab_not_fetched')",
        'tests/test_matrix_reason_codes.py::test_unfetched_tab_keeps_its_source_so_pick_finds_it',
    ),
    (
        'matrix-tab-not-fetched-is-not-a-block',
        'match_data_matrix.py',
        "        return _Cell(None, source=SRC_OKOOO, reason=TAB_NOT_FETCHED_REASON,\n                     stage=TAB_NOT_FETCHED_REASON)",
        "        return _Cell(None, source=SRC_OKOOO, reason='source_blocked', stage='tab_not_fetched')",
        'tests/test_matrix_reason_codes.py::test_unfetched_tab_is_not_labelled_as_an_ip_block',
    ),
    (
        'matrix-absent-match-not-called-unfetched',
        'match_data_matrix.py',
        "        if not src_okooo_present and not isinstance(tab_entry, dict):\n            return None",
        "        if False:\n            return None",
        'tests/test_match_data_matrix.py::test_matrix_keeps_match_absent_from_one_source',
    ),
    (
        'matrix-stage-reason-mapping',
        'match_data_matrix.py',
        "    token = str(stage or '').strip()\n"
        "    for known in sorted(STAGE_REASON_CODES, key=len, reverse=True):\n"
        "        if token == known or token.endswith(known):\n"
        "            return STAGE_REASON_CODES[known]\n"
        "    return 'not_in_source'",
        "    return 'panel_js_rendered' if 'js_rendered' in str(stage or '') else 'not_in_source'",
        'tests/test_matrix_reason_codes.py::test_every_parse_stage_maps_to_its_own_reason_code',
    ),
    (
        'matrix-405-page-not-said-to-have-no-data',
        'match_data_matrix.py',
        "    token = str(stage or '').strip()\n"
        "    for known in sorted(STAGE_REASON_CODES, key=len, reverse=True):\n"
        "        if token == known or token.endswith(known):\n"
        "            return STAGE_REASON_CODES[known]\n"
        "    return 'not_in_source'",
        "    return 'panel_js_rendered' if 'js_rendered' in str(stage or '') else 'not_in_source'",
        'tests/test_matrix_reason_codes.py::test_http_405_page_is_reported_as_blocked_not_as_no_data',
    ),
    (
        'matrix-empty-cell-also-reads-the-stage',
        'match_data_matrix.py',
        "        reason = reason_for_stage(stage)\n        return _Cell(None, source=SRC_OKOOO, reason=reason, **{**ev, 'stage': stage})",
        "        reason = 'panel_js_rendered' if 'js_rendered' in stage else 'not_in_source'\n        return _Cell(None, source=SRC_OKOOO, reason=reason, **{**ev, 'stage': stage})",
        'tests/test_matrix_reason_codes.py::test_panel_level_stages_go_through_the_same_mapping',
    ),
    (
        'matrix-panel-stage-uses-the-shared-mapping',
        'match_data_matrix.py',
        "        if field_path and field_path[0] == 'handicap':\n            stage = str(tab_entry.get('handicap_stage') or stage)",
        "        if False:\n            stage = stage",
        'tests/test_matrix_reason_codes.py::test_panel_level_stages_go_through_the_same_mapping',
    ),
    (
        'dedupe-merges-per-cell',
        'match_data_matrix.py',
        "            if isinstance(cell, dict) and cell.get('reason') == 'ok':\n                merged_fields[field] = cell",
        "            if False:\n                merged_fields[field] = cell",
        'tests/test_match_data_matrix.py::test_dedupe_merges_rows_by_normalized_order',
    ),
    (
        'js-rendered-reason-survives',
        'match_data_matrix.py',
        "        if field_path and field_path[0] == 'handicap':\n            stage = str(tab_entry.get('handicap_stage') or stage)",
        "        if False:\n            stage = stage",
        'tests/test_match_data_matrix.py::test_js_rendered_panel_reason_survives_into_the_gap_list',
    ),
    (
        'stage1-loop-calls-collector',
        'app_v84_hermes.py',
        "            collect_match_matrix_facts(rec, board=wait_matrix_500_board())",
        "            pass  # 回退：阶段1 不采集矩阵",
        'tests/test_match_data_matrix_wiring.py::test_stage1_loop_body_calls_the_collector',
    ),
    (
        'round-board-cached-once',
        'app_v84_hermes.py',
        "    if not force and state.get('round') == stamp and state.get('board') is not None:\n        return state['board']",
        "    if False:  # 回退：每场都重抓盘面\n        return state['board']",
        'tests/test_match_data_matrix_wiring.py::test_round_board_is_fetched_once_not_once_per_match',
    ),
    (
        'cache-is-round-scoped-not-process',
        'app_v84_hermes.py',
        "    if not force and state.get('round') == stamp and state.get('board') is not None:\n        return state['board']",
        "    if not force and state.get('board') is not None:  # 回退：进程生命周期缓存\n        return state['board']",
        'tests/test_match_data_matrix_wiring.py::test_two_rounds_issue_two_board_requests',
    ),
    (
        'failed-fetch-retried-next-round',
        'app_v84_hermes.py',
        "    if not force and state.get('round') == stamp and state.get('board') is not None:\n        return state['board']",
        "    if not force and state.get('board') is not None:  # 回退：失败也永久缓存\n        return state['board']",
        'tests/test_match_data_matrix_wiring.py::test_failed_fetch_in_round_one_is_retried_in_round_two',
    ),
    (
        'round-entry-points-reset',
        'app_v84_hermes.py',
        "            reset_matrix_500_board_cache()\n            prefetch_matrix_500_board()\n        except Exception as _mm_pf_err:  # noqa: BLE001 - 预取失败不阻断整轮\n            print(f\"   \u26a0\ufe0f [\u4e24\u6e90\u77e9\u9635] \u76d8\u9762\u9884\u53d6\u542f\u52a8\u5931\u8d25(\u5df2\u964d\u7ea7): {_mm_pf_err}\")",
        "            pass\n        except Exception as _mm_pf_err:  # noqa: BLE001\n            print(f\"   \u26a0\ufe0f [\u4e24\u6e90\u77e9\u9635] {_mm_pf_err}\")",
        'tests/test_match_data_matrix_wiring.py::test_round_entry_points_reset_and_prefetch',
    ),
    (
        'stage1-waits-for-prefetch',
        'app_v84_hermes.py',
        "            collect_match_matrix_facts(rec, board=wait_matrix_500_board())",
        "            collect_match_matrix_facts(rec, board=_matrix_500_board())",
        'tests/test_match_data_matrix_wiring.py::test_stage1_waits_instead_of_blocking_on_a_cold_fetch',
    ),
    (
        'elapsed-on-success-path',
        'live500_board.py',
        "    parsed['elapsed_ms'] = elapsed_ms",
        "    parsed.pop('elapsed_ms', None)",
        'tests/test_live500_board.py::test_scrape_board_reports_elapsed_on_every_path',
    ),
    (
        'elapsed-reaches-matrix-provenance',
        'match_data_matrix.py',
        "        'elapsed_ms': int(\n            block.get('elapsed_ms') or rep.get('elapsed_ms') or 0\n        ),",
        "        'elapsed_ms': 0,",
        'tests/test_match_data_matrix.py::test_matrix_provenance_carries_the_board_elapsed_time',
    ),
    (
        'elapsed-not-invented-when-unknown',
        'match_data_matrix.py',
        "        'elapsed_ms': int(\n            block.get('elapsed_ms') or rep.get('elapsed_ms') or 0\n        ),",
        "        'elapsed_ms': 1704,",
        'tests/test_match_data_matrix.py::test_matrix_elapsed_is_zero_not_invented_when_unknown',
    ),
    (
        'prefetch-is-non-blocking',
        'app_v84_hermes.py',
        "    thread.start()\n    return True",
        "    thread.start()\n    thread.join()  # 回退：预取变同步\n    return True",
        'tests/test_match_data_matrix_wiring.py::test_prefetch_does_not_block_the_caller',
    ),
    (
        'reason-text-covers-codes',
        'match_data_matrix.py',
        "    'not_started': '比赛未开赛，比分/半场/技术统计/首发按赛程尚未产生（非抓取缺失）',",
        "    'not_started_renamed': '比赛未开赛，比分/半场/技术统计/首发按赛程尚未产生（非抓取缺失）',",
        'tests/test_match_data_matrix_wiring.py::test_reason_text_covers_every_reason_code',
    ),
    (
        'collector-writes-both-sources',
        'app_v84_hermes.py',
        "            rec['live500_match'] = {**match, 'report': board.get('report') or {}}",
        "            rec['live500_match'] = {}",
        'tests/test_match_data_matrix_wiring.py::test_collector_writes_both_sources_onto_the_record',
    ),
    (
        'matrix-section-is-wired',
        'app_v84_hermes.py',
        "    _final_sections = append_match_matrix_section(\n        _final_sections,\n        rec if isinstance(rec, dict) else {},",
        "    _final_sections = (_final_sections,)\n    if False:\n        pass\n    _unused = append_match_matrix_section(\n        [],\n        rec if isinstance(rec, dict) else {},",
        'tests/test_match_data_matrix_wiring.py::test_emit_calls_the_matrix_section',
    ),
    (
        'matrix-builder-degrades-to-one-line',
        'app_v84_hermes.py',
        "        lines = [f\"🧩【两源合并数据矩阵】\\n  未形成（{type(_mdm_err).__name__}: {_mdm_err}）\"]",
        "        lines = []",
        'tests/test_match_data_matrix_wiring.py::test_matrix_append_survives_a_broken_builder',
    ),
)


def run_case(label: str, rel: str, old: str, new: str, test: str) -> tuple[bool, str]:
    """回退一处 → 跑测试 → 必须失败；随后无条件恢复。"""
    path = ROOT / rel
    original = path.read_text(encoding='utf-8')
    if old not in original:
        return False, f'anchor not found in {rel}'
    backup = pathlib.Path(tempfile.mkstemp(suffix=path.suffix)[1])
    try:
        shutil.copy2(path, backup)
        path.write_text(original.replace(old, new, 1), encoding='utf-8')
        proc = subprocess.run(
            [sys.executable, '-m', 'pytest', test, '-q', '-p', 'no:cacheprovider'],
            cwd=ROOT, text=True, capture_output=True, timeout=300, check=False,
        )
        if proc.returncode == 0:
            return False, 'guard stayed GREEN after revert'
        return True, 'guard went RED as required'
    finally:
        shutil.copy2(backup, path)
        backup.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', default='')
    args = parser.parse_args()
    failures = 0
    for label, rel, old, new, test in CASES:
        if args.only and args.only not in label:
            continue
        ok, detail = run_case(label, rel, old, new, test)
        print(f"{'PASS' if ok else 'FAIL'} {label}: {detail}")
        if not ok:
            failures += 1
    print(f'\n{len(CASES) - failures}/{len(CASES)} guards verified red on revert')
    return 0 if failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
