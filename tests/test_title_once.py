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
