"""The delivered first line must not carry the match title twice.

`rebuild_pipeline_bundle_from_history` returns an already-formatted
`title_line` (📌 + match no + kickoff). `emit_pipeline_deepseek_tg_report` fed
that whole line back through `format_tg_match_header`, which prepends 📌 and
appends the kickoff again. After PR #28 the result is pinned to the first line
and bolded, so the duplication became the first thing a reader sees.

These tests drive the real rebuild -> emit path; nothing in the title path is
monkeypatched.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')

KICKOFF = '2026-11-28 03:30'


def _record() -> dict:
    jc = {'1-0': 12.9, '2-0': 17.0, '2-1': 10.1, '1-1': 7.8, '0-0': 11.5, '0-1': 8.9}
    return {
        'id': 't1', 'match': '海登海姆 vs 纽伦堡',
        'cn_name': '[周五002] 海登海姆 vs 纽伦堡', 'no': '周五002',
        'status': 'pending', 'time': KICKOFF, 'match_time': KICKOFF,
        'kickoff_source': 'okooo_match_time', 'okooo_match_id': '1347776',
        'predicted_score': '2-1', 'pipeline_scrape_done': True,
        'lam_h': 1.55, 'lam_a': 1.12,
        'six_market_mobile': {
            'asian_handicap': -0.25, 'ou_line': 3.25,
            'odds_1x2': {'Home': 1.91, 'Draw': 3.6, 'Away': 4.2},
            'jc_handicap': -1.0,
            'total_goals_odds': {'0': 9.5, '1': 5.2, '2': 3.4},
            'htft_odds': {'胜胜': 4.5, '平平': 7.0},
        },
        'correct_score_page_avg': {'1-0': 6.4, '2-0': 4.1, '2-1': 5.5,
                                   '1-1': 6.2, '0-0': 7.9, '0-1': 8.8},
        'jc_score_odds': jc,
    }


def _deliver(tmp_path, selector=None):
    """Real rebuild -> real emit; return the delivered bodies."""
    rec = _record()
    bundle = app.rebuild_pipeline_bundle_from_history(dict(rec), fid='t1',
                                                     cn_name=rec['match'])
    assert isinstance(bundle, dict) and bundle.get('title_line'), 'bundle must carry a title'
    rec['pipeline_report_bundle'] = bundle
    hist = tmp_path / 'match_history.json'
    hist.write_text(json.dumps({'t1': rec}, ensure_ascii=False), encoding='utf-8')

    sent: list[tuple] = []

    def _send(text, parse_mode=None):
        sent.append((parse_mode, text))
        return True

    patches = [mock.patch.object(app, 'HISTORY_FILE', str(hist)),
               mock.patch.object(app, 'send_judgment_report_tg', _send),
               mock.patch.object(app, 'send_tg', _send),
               mock.patch.object(app, '_ensure_mobile_six', lambda *a, **k: False),
               mock.patch.object(app, 'enrich_history_okooo_cs_facts', lambda *a, **k: False),
               mock.patch.object(app, 'JUDGMENT_TG_REPORT_OUTPUT', True),
               mock.patch('requests.get', side_effect=ConnectionError('offline')),
               mock.patch('requests.post', side_effect=ConnectionError('offline')),
               mock.patch('time.sleep', side_effect=lambda *a, **k: None)]
    if selector == 'raise':
        patches.append(mock.patch.object(
            app.DeepSeekAnalyzer, 'format_matrix_dual_prejudge_html',
            staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError('render boom')))))
    elif selector == 'empty':
        patches.append(mock.patch.object(
            app.DeepSeekAnalyzer, 'format_matrix_dual_prejudge_html',
            staticmethod(lambda *a, **k: '')))
    for p in patches:
        p.start()
    try:
        app.emit_pipeline_deepseek_tg_report(
            {'id': 't1', 'cn_name': rec['cn_name'], 'time': KICKOFF,
             'kickoff_source': 'okooo_match_time'}, i=0, total=1)
    finally:
        for p in patches:
            p.stop()
    return sent


def _first_line(sent) -> str:
    """The delivered title line.

    #28 pins the match title to the first line of message 1, but a gap banner can
    precede it in the same body, so "the first non-empty line" is not reliable.
    The title is the first line carrying 📌.
    """
    for _pm, text in sent:
        for line in (text or '').splitlines():
            if '📌' in line:
                return line.strip()
    return ''


def test_delivered_first_line_has_one_title_and_one_kickoff(tmp_path) -> None:
    """📌 exactly once and the kickoff exactly once on the first delivered line.

    Revert tested: restoring the unconditional `format_tg_match_header(_raw_title, ...)`
    -> the first line reads '📌 [周五002] 📌 [周五002] … 时间 时间' and both counts are 2.
    """
    sent = _deliver(tmp_path)
    first = _first_line(sent)
    assert first, 'nothing was delivered'
    assert first.count('📌') == 1, f'title emitted twice: {first!r}'
    assert first.count(KICKOFF) == 1, f'kickoff emitted twice: {first!r}'
    assert first.count('周五002') == 1, f'match no emitted twice: {first!r}'


def test_delivered_first_line_has_one_title_on_the_raising_fallback(tmp_path) -> None:
    sent = _deliver(tmp_path, selector='raise')
    first = _first_line(sent)
    assert first, 'nothing was delivered'
    assert first.count('📌') == 1, f'title emitted twice: {first!r}'
    assert first.count(KICKOFF) == 1, f'kickoff emitted twice: {first!r}'


def test_delivered_first_line_has_one_title_on_the_empty_fallback(tmp_path) -> None:
    sent = _deliver(tmp_path, selector='empty')
    first = _first_line(sent)
    assert first, 'nothing was delivered'
    assert first.count('📌') == 1, f'title emitted twice: {first!r}'
    assert first.count(KICKOFF) == 1, f'kickoff emitted twice: {first!r}'


# ---------------------------------------------------------------------------
# E9 must compare bytes (minus the one exempt line), not report-shape.
#
# These tests drive `run_shard` itself so they exercise the shipped comparison
# rather than a copy of it. An earlier version of this file re-implemented E9
# locally, which meant swapping the strict comparison for the old
# `len(body) > 200` shape check left all three tests green — the counter-proof
# was testing its own code. Routing through run_shard fixes that: if the
# comparison in the sweep is weakened, these fail.
# ---------------------------------------------------------------------------
def _e9_shard(drop: str = '', n: int = 12, seed: int = 20261127,
              dropped: list | None = None) -> tuple[int, int]:
    """Run a real shard; return (produced, E9 failure count).

    `drop` truncates stage 2's sections on the *writeback* run only, via a spy
    that knows which call is the replay. `dropped` collects what was actually
    removed, so a counter-proof cannot pass by dropping nothing.
    """
    import contextlib
    import io
    import sys as _sys
    _scripts = str(ROOT / 'scripts')
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import sweep_100k_e2e as S
        import blade_deepseek_pipeline as _pipe_mod

    # `test_six_market_live_failure._pipe` reloads blade_deepseek_pipeline, which
    # rebinds the module's DeepSeekAnalyzer while app_v84_hermes keeps the old
    # class. That is a real hygiene bug in that helper (reported separately);
    # here it only matters that emit and the patch agree on one class.
    S.APP.DeepSeekAnalyzer = _pipe_mod.DeepSeekAnalyzer

    orig = S.PIPE.DeepSeekAnalyzer.build_deepseek_only_tg_sections
    real_run_once = S._run_once
    replaying = {'on': False}

    def _truncating(*a, **k):
        sections = orig(*a, **k)
        if not replaying['on'] or not drop:
            return sections
        if drop == 'table_row':
            out = []
            for sec in sections:
                lines = sec.splitlines()
                kept = [ln for ln in lines
                        if not (ln.startswith('  ') and ln[2:4].strip().isdigit())]
                if len(kept) != len(lines) and dropped is not None:
                    dropped.append(f'{len(lines) - len(kept)} table row(s)')
                out.append('\n'.join(kept or lines))
            return out
        if drop == 'fact_panel':
            kept = [sec for sec in sections if '六盘口' not in sec]
            if len(kept) != len(sections) and dropped is not None:
                dropped.append('六盘口事实板')
            return kept
        return sections

    def _spy(case, hist_path, reseed=True):
        replaying['on'] = not reseed
        try:
            return real_run_once(case, hist_path, reseed=reseed)
        finally:
            replaying['on'] = False

    with mock.patch.object(S, '_run_once', side_effect=_spy), \
         mock.patch.object(S.PIPE.DeepSeekAnalyzer,
                           'build_deepseek_only_tg_sections',
                           staticmethod(_truncating)), \
         contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):
        result = S.run_shard(list(range(n)), seed)

    e9 = sum(1 for f in result['failures'] if f['invariant'] == 'E9')
    return result['produced'], e9


def test_e9_passes_when_the_replay_is_identical() -> None:
    """A clean shard replays identically: no E9, every case produced."""
    produced, e9 = _e9_shard()
    assert (produced, e9) == (12, 0), f'produced={produced} E9={e9}'


def test_e9_catches_one_dropped_table_row() -> None:
    """The shape-only check this replaced passes this; byte equality must not.

    Revert tested: swapping the canonicalised comparison for
    `[(len(t) > 200) for t in bodies]` leaves the dropped-row bodies at
    6974 -> 4888 chars, both >200, so E9 goes silent and this fails.
    """
    dropped: list = []
    produced, e9 = _e9_shard(drop='table_row', dropped=dropped)
    # 12 cases, one of which (index 0) takes the pack-absent exit and never
    # calls this builder — so 11 bodies lose the rows and 11 must be caught.
    assert len(dropped) == 11, f'the counter-proof removed rows from {len(dropped)} cases'
    assert e9 == 11, f'a lost 31-table row on writeback went unnoticed (E9={e9})'
    assert produced == 1, produced


def test_e9_catches_a_dropped_fact_panel() -> None:
    dropped: list = []
    produced, e9 = _e9_shard(drop='fact_panel', dropped=dropped)
    # Same denominator as the table-row case: index 0 bypasses this builder.
    assert dropped == ['六盘口事实板'] * 11, dropped
    assert e9 == 11, f'a lost 六盘口 panel on writeback went unnoticed (E9={e9})'
    assert produced == 1, produced


def test_model_stub_resets_the_completion_latch(tmp_path) -> None:
    """The stub must leave the transport's process-wide verdict cleared.

    `deepseek_chat_completion` resets `_LAST_COMPLETION_ERROR`/
    `_LAST_COMPLETION_LOCAL` on entry, and `completion_failed()` reads them to
    decide whether a non-empty string is model output or a local placeholder.
    A stub that only returns a string inherits whatever the last real call left
    behind: run one case with no API key and every later stubbed call in that
    process reports `available=False`, so the report silently falls back to the
    deterministic body.

    That is not hypothetical — it is what made the E9 baseline look like an
    application replay defect. It was harness state, and this pins it.
    """
    import contextlib
    import io
    import sys as _sys
    _scripts = str(ROOT / 'scripts')
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import sweep_100k_e2e as S

    S.PIPE._LAST_COMPLETION_LOCAL = True
    S.PIPE._LAST_COMPLETION_ERROR = 'deepseek_api_key_missing'
    with mock.patch.object(S.PIPE, 'deepseek_chat_completion', side_effect=S._model_reply):
        result = S.PIPE.DeepSeekAnalyzer.analyze_match_judgment(
            {'home': 'A', 'away': 'B', 'match': 'A vs B'})
    assert result.get('available') is True, (
        f'stale completion latch leaked into the stub: {result.get("error")!r}')
    assert not result.get('error'), result.get('error')
