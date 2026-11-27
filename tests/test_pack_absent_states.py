"""Three states of a missing judgment pack, each driving the real emit path.

The old exit said 「history 无可用精算结果… 请重新跑阶段1抓取」 and returned None.
Both halves were wrong: the record had already run stage 1, and a one-line
warning is not a report.

Each test builds a record, calls `emit_pipeline_deepseek_tg_report`, and asserts
on the body that actually went out through `send_judgment_report_tg` — not on a
string in the source.
"""
from __future__ import annotations

import importlib
import json
from unittest import mock

import pytest

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')


def _emit(rec, monkeypatch, tmp_path, send=None):
    """Run the real stage-2 emit; return the bodies that were sent."""
    hist = tmp_path / 'match_history.json'
    hist.write_text(json.dumps({str(rec['id']): rec}, ensure_ascii=False), encoding='utf-8')
    sent: list[str] = []

    def _send(text, parse_mode=None):
        sent.append(text)
        return True

    if send is not None:
        _send = send

    monkeypatch.setattr(app, 'HISTORY_FILE', str(hist))
    monkeypatch.setattr(app, 'send_judgment_report_tg', _send)
    monkeypatch.setattr(app, 'send_tg', _send)
    monkeypatch.setattr(app, '_ensure_mobile_six', lambda *a, **k: False)
    monkeypatch.setattr(app, 'enrich_history_okooo_cs_facts', lambda *a, **k: False)
    monkeypatch.setattr(app, 'JUDGMENT_TG_REPORT_OUTPUT', True)
    with mock.patch('requests.get', side_effect=ConnectionError('offline')), \
            mock.patch('requests.post', side_effect=ConnectionError('offline')), \
            mock.patch('time.sleep', side_effect=lambda *a, **k: None):
        app.emit_pipeline_deepseek_tg_report(
            {'id': str(rec['id']), 'cn_name': rec.get('cn_name') or rec.get('match') or '',
             'time': rec.get('time') or '', 'kickoff_source': 'okooo_match_time'},
            i=0, total=1)
    return sent


def _base_rec(**over):
    rec = {
        'id': 'm1', 'match': '甲队 vs 乙队', 'cn_name': '[周六001] 甲队 vs 乙队',
        'no': '周六001', 'order': '4001', 'status': 'pending',
        'time': '2026-11-30 20:00', 'match_time': '2026-11-30 20:00',
        'kickoff_source': 'okooo_match_time', 'okooo_match_id': '1347776',
    }
    rec.update(over)
    return rec


def test_state_payload_broken_sends_deterministic_body_and_says_do_not_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """State 1: bundle present, ds_payload the wrong shape -> program defect.

    The body must be a real report that discloses "deterministic, not model
    output", and must NOT tell the operator to re-run stage 1.
    """
    rec = _base_rec(pipeline_report_bundle={'ds_payload': 'not-a-dict'},
                    predicted_score='2-1', pipeline_scrape_done=True)
    # #22 will try to rebuild over a broken payload, so make that fail too —
    # otherwise the test exercises the rebuild path, not this state.
    monkeypatch.setattr(app, 'rebuild_pipeline_bundle_from_history',
                        lambda *a, **k: {'ds_payload': 'still-broken'})
    sent = _emit(rec, monkeypatch, tmp_path)
    body = '\n'.join(t for t in sent if t)
    assert body.strip(), 'nothing was sent'
    assert '甲队 vs 乙队' in body, 'the body must identify the match'
    assert '4001' in body, 'the body must carry the order'
    assert '确定性' in body and '非模型产出' in body, body[:300]
    assert '重新跑阶段1' not in body, 'stage 1 already ran; that instruction is wrong'
    assert len(body) > 200, f'a one-line warning is not a report: {body!r}'


def test_state_rebuild_failed_carries_the_real_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """State 2: facts exist, rebuild raises -> the exception is the only clue.

    It must reach the body (or a searchable log field), not be printed and lost.
    """
    rec = _base_rec(predicted_score='2-1', pipeline_scrape_done=True)

    def _boom(*a, **k):
        raise RuntimeError('rebuild exploded on pool31')

    monkeypatch.setattr(app, 'rebuild_pipeline_bundle_from_history', _boom)
    sent = _emit(rec, monkeypatch, tmp_path)
    body = '\n'.join(t for t in sent if t)
    assert body.strip(), 'nothing was sent'
    assert 'rebuild exploded on pool31' in body, (
        f'the rebuild exception must be visible, got: {body[:400]}')
    assert '甲队 vs 乙队' in body
    assert '不必重跑阶段1' in body or '不要重跑' in body, body[-200:]


def test_state_no_scrape_facts_still_sends_a_report_with_reason_and_next_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """State 3: nothing at all -> 「无法研判」 is a fact, but still a report.

    Must not be a single ⚠️ line, must identify the match and the order, and must
    not accuse the operator of skipping a step that produced no record.
    """
    rec = _base_rec()          # no bundle, no facts
    sent = _emit(rec, monkeypatch, tmp_path)
    body = '\n'.join(t for t in sent if t)
    assert body.strip(), 'nothing was sent'
    assert '甲队 vs 乙队' in body
    assert '4001' in body
    assert '没有阶段1结果' in body or '缺少阶段1结果' in body, body[:300]
    assert '下一步' in body, body[-200:]
    # It must not be the old one-liner shape.
    assert not body.strip().startswith('⚠️【阶段2缺省】'), body[:80]
    assert len(body) > 200, f'a one-line warning is not a report: {body!r}'


# ---------------------------------------------------------------------------
# The three states must never fabricate a gap. A record whose six boards are all
# present and whose odds allow a 31 table must be reported as exactly that.
# ---------------------------------------------------------------------------
FULL_SIX = {
    'asian_handicap': -0.25,
    'ou_line': 3.25,
    'odds_1x2': {'Home': 1.91, 'Draw': 3.6, 'Away': 4.2},
    'jc_handicap': -1.0,
    'total_goals_odds': {'0': 9.5, '1': 5.2, '2': 3.4, '3': 3.8, '4': 6.0, '5': 9.0},
    'htft_odds': {'胜胜': 4.5, '平胜': 6.0, '负胜': 15.0, '胜平': 16.0,
                  '平平': 7.0, '负平': 18.0, '胜负': 34.0, '平负': 11.0, '负负': 5.2},
}
FULL_PAGE_AVG = {'1-0': 6.4, '2-0': 4.1, '2-1': 5.5, '1-1': 6.2, '0-0': 7.9, '0-1': 8.8}
FULL_JC = {'1-0': 12.9, '2-0': 17.0, '2-1': 10.1, '1-1': 7.8, '0-0': 11.5, '0-1': 8.9}
BOARD_LABELS = ('亚盘让球', '大小球', '胜平负', '竞彩让球', '总进球', '半全场')


def _full_rec(**over):
    rec = _base_rec(
        six_market_mobile=dict(FULL_SIX),
        correct_score_page_avg=dict(FULL_PAGE_AVG),
        jc_score_odds=dict(FULL_JC),
        predicted_score='2-1',
        lam_h=1.55, lam_a=1.12,
    )
    rec.update(over)
    return rec


def _assert_no_fabricated_gap(body: str) -> None:
    """No board line may claim 无数据 when the record carries that board."""
    falsely = [
        ln.strip() for ln in body.splitlines()
        if any(ln.strip().startswith(f'{b}：') for b in BOARD_LABELS) and '无数据' in ln
    ]
    assert not falsely, f'fabricated gaps for boards the record has: {falsely}'


def _assert_31_built(body: str) -> None:
    rows = [ln for ln in body.splitlines() if ln.startswith('  ') and ln[2:4].strip().isdigit()]
    assert '31项市均×竞彩对比表' in body, 'the 31 table header must be present'
    assert '两个源都为空' not in body, 'the record has both sources; not an empty-source claim'
    assert rows, f'the 31 table must have rows, got none:\n{body[:600]}'


def test_payload_broken_does_not_fabricate_gaps(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    rec = _full_rec(pipeline_report_bundle={'ds_payload': 'not-a-dict'})
    monkeypatch.setattr(app, 'rebuild_pipeline_bundle_from_history',
                        lambda *a, **k: {'ds_payload': 'still-broken'})
    body = '\n'.join(t for t in _emit(rec, monkeypatch, tmp_path) if t)
    _assert_no_fabricated_gap(body)
    _assert_31_built(body)


def test_rebuild_failed_does_not_fabricate_gaps(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    rec = _full_rec()

    def _boom(*a, **k):
        raise RuntimeError('rebuild exploded on purpose')

    monkeypatch.setattr(app, 'rebuild_pipeline_bundle_from_history', _boom)
    body = '\n'.join(t for t in _emit(rec, monkeypatch, tmp_path) if t)
    _assert_no_fabricated_gap(body)
    _assert_31_built(body)


def test_no_scrape_facts_does_not_fabricate_gaps(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Even this state may carry boards and odds: report them, do not deny them."""
    rec = _full_rec()
    for key in ('predicted_score', 'pipeline_scrape_done', 'pipeline_report_bundle'):
        rec.pop(key, None)
    body = '\n'.join(t for t in _emit(rec, monkeypatch, tmp_path) if t)
    _assert_no_fabricated_gap(body)
    _assert_31_built(body)


def test_send_failure_is_recorded_as_not_sent(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A failed send must not be recorded as delivered.

    Revert tested: restoring the unconditional sent=True -> tg_sent is True
    although the transport raised.
    """
    rec = _full_rec()

    def _boom(*a, **k):
        raise RuntimeError('rebuild exploded on purpose')

    def _send_fail(text, parse_mode=None):
        raise ConnectionError('telegram unreachable')

    monkeypatch.setattr(app, 'rebuild_pipeline_bundle_from_history', _boom)
    _emit(rec, monkeypatch, tmp_path, send=_send_fail)
    written = app.load_json(str(tmp_path / 'match_history.json'))['m1']
    assert written.get('tg_sent') is False, (
        'a send that raised must not be recorded as delivered')
    assert written.get('tg_not_sent_reason'), 'the failure reason must be recorded'


# ---------------------------------------------------------------------------
# Defect D: 大小球盘口 denied a line the record carries, while the very next
# line printed it. `_deterministic_judgment_lines` read only its `ou_line`
# parameter; the pack-absent path does not pass one.
# ---------------------------------------------------------------------------
OU_SIX = {'ou_line': 3.25, 'asian_handicap': -0.25,
          'odds_1x2': {'Home': 1.91, 'Draw': 3.6, 'Away': 4.2},
          'jc_handicap': -1.0}


def _ou_line_of(body: str) -> str:
    for line in body.splitlines():
        if line.strip().startswith('大小球盘口：'):
            return line.strip()
    return ''


def _rec_with_ou_only(**over):
    rec = _base_rec(six_market_mobile=dict(OU_SIX), predicted_score='2-1')
    rec.update(over)
    return rec


def test_d_rebuild_failed_reads_ou_line_from_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    rec = _rec_with_ou_only()

    def _boom(*a, **k):
        raise RuntimeError('rebuild exploded')

    monkeypatch.setattr(app, 'rebuild_pipeline_bundle_from_history', _boom)
    body = '\n'.join(t for t in _emit(rec, monkeypatch, tmp_path) if t)
    line = _ou_line_of(body)
    assert '无数据' not in line, f'the record carries ou_line=3.25: {line!r}'
    assert '3.25' in line, line


def test_d_payload_broken_reads_ou_line_from_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    rec = _rec_with_ou_only(pipeline_report_bundle={'ds_payload': 'not-a-dict'})
    monkeypatch.setattr(app, 'rebuild_pipeline_bundle_from_history',
                        lambda *a, **k: {'ds_payload': 'still-broken'})
    body = '\n'.join(t for t in _emit(rec, monkeypatch, tmp_path) if t)
    line = _ou_line_of(body)
    assert '无数据' not in line, f'the record carries ou_line=3.25: {line!r}'
    assert '3.25' in line, line


def test_d_no_scrape_facts_reads_ou_line_from_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    rec = _rec_with_ou_only()
    for key in ('predicted_score', 'pipeline_scrape_done', 'pipeline_report_bundle'):
        rec.pop(key, None)
    body = '\n'.join(t for t in _emit(rec, monkeypatch, tmp_path) if t)
    line = _ou_line_of(body)
    assert '无数据' not in line, f'the record carries ou_line=3.25: {line!r}'
    assert '3.25' in line, line


def test_d_a_missing_ou_line_is_still_reported_as_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """The fallback must not turn a genuine absence into a value.

    Revert tested: making the fallback invent a default -> this fails, because
    the line would carry a number the record never had.
    """
    rec = _base_rec(six_market_mobile={'asian_handicap': -0.25, 'jc_handicap': -1.0},
                    predicted_score='2-1')

    def _boom(*a, **k):
        raise RuntimeError('rebuild exploded')

    monkeypatch.setattr(app, 'rebuild_pipeline_bundle_from_history', _boom)
    body = '\n'.join(t for t in _emit(rec, monkeypatch, tmp_path) if t)
    line = _ou_line_of(body)
    assert '无数据' in line, f'no source has ou_line; must say so: {line!r}'
    assert '原因' in line, line
