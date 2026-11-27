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
import os
import tempfile
from unittest import mock

import pytest

ROOT = __import__('pathlib').Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')


def _emit(rec, monkeypatch, tmp_path):
    """Run the real stage-2 emit; return the bodies that were sent."""
    hist = tmp_path / 'match_history.json'
    hist.write_text(json.dumps({str(rec['id']): rec}, ensure_ascii=False), encoding='utf-8')
    sent: list[str] = []

    def _send(text, parse_mode=None):
        sent.append(text)
        return True

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
