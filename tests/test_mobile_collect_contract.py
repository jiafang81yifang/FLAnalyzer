"""Pins the per-match collection and diagnostic contract for the mobile six-board path.

The live log for match 1338958 showed the same three warnings three times in a row.
The cause was not the printing: one pipeline stage called the collectors three times,
so the same three blocked endpoints were hit three times. These tests therefore count
**both** the collector calls and the printed lines, on the same fixture, because a fix
that only silences the prints would still waste the network round trips.

The memo and the diagnostic counters live on the record (bucketed by stage), never in a
module-global keyed by match id: a global leaked between records and between tests.

Acceptance cases, all required:
  * 3 calls in one stage  -> 1 scrape, 1 line
  * 2 stages              -> 2 scrapes
  * a complete pack       -> no extra scrape
  * forced after cached   -> genuinely re-collects
  * two records, same mid -> each gets its own scrape and its own lines
"""
from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('BLADE_HEADLESS', '1')


def _app():
    module = sys.modules.get('_collect_contract_app')
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location('_collect_contract_app', ROOT / 'app_v84_hermes.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['_collect_contract_app'] = module
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(module)
    return module


class _Calls:
    def __init__(self) -> None:
        self.pnl = 0
        self.books = 0
        self.six = 0

    def pnl_pack(self, *a, **k):
        self.pnl += 1
        return {'available': False, 'channel': 'mobile', 'block_reason': 'okooo_exchange_no_pnl_rows'}

    def books_pack(self, *a, **k):
        self.books += 1
        return {'available': False, 'channel': 'mobile', 'block_reason': 'okooo_ah_ou_books_fetch_failed'}

    def six_pack(self, match_id=None, *a, **k):
        self.six += 1
        return _failing_pack(match_id)


def _failing_pack(match_id='1338958'):
    """The 1338958 shape: three boards tried, none parsed."""
    return {
        'available': False, 'channel': 'mobile', 'match_id': str(match_id),
        'ou_line': None, 'asian_handicap': None, 'odds_1x2': {},
        'boards_hit': {}, 'missing': ['asian_handicap', 'ou_line', 'odds_1x2'],
        'source_report': {
            'ah': {'stage': 'no_line', 'status': 200, 'reason': 'ah_no_line'},
            'ou': {'stage': 'fetch_failed', 'status': 200, 'reason': 'ou_fetch_failed'},
            '1x2': {'stage': 'company_row_missing', 'status': 200, 'reason': 'row_missing'},
        },
        'fetch_urls': ['https://m.okooo.com/match/handicap.php?MatchID=1338958'],
        'block_reason': 'external_okooo_six_market_fetch_failed',
    }


def _complete_pack(match_id='1338958'):
    """A pack that satisfies `_core_ok`, so no scrape should be attempted at all."""
    return {
        'available': True, 'channel': 'mobile', 'match_id': str(match_id),
        'ou_line': 2.5, 'asian_handicap': -1.0, 'odds_1x2': {'home': 2.1},
        'boards_hit': {'亚盘让球': True, '大小球': True, '胜平负': True},
        'total_goals_odds': {str(i): 5.0 for i in range(8)},
        'total_goals_source': '手机竞足→更多玩法→总进球',
        'htft_odds': {str(i): 6.0 for i in range(9)},
        'htft_source': '手机竞足→更多玩法→半全场',
        'jc_score_odds': {f'{i}': 7.0 for i in range(25)},
        'jc_score_source': '手机竞足→更多玩法→胜平负比分',
        'jc_handicap': -1, 'missing': [], 'source_report': {},
        'fetch_urls': ['https://m.okooo.com/match/handicap.php?MatchID=1338958'],
        'block_reason': '',
    }


def _rec(stage='stage1'):
    """Water scalars already present, so `_ensure_mobile_six` reaches the collect branch."""
    return {
        'okooo_match_id': '1338958',
        'match': '海登海姆 vs 纽伦堡',
        'cn_name': '[竞彩-周五002] 海登海姆 vs 纽伦堡',
        'asian_handicap': 0.0, 'ou_line': 2.5, 'odds_1x2': {'home': 2.1},
        'jc_handicap': -1, 'ou_channel': 'mobile', 'ah_channel': 'mobile',
        'pipeline_phase': stage,
    }


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch):
    app = _app()
    c = _Calls()
    monkeypatch.setattr(app, 'scrape_okooo_mobile_exchange_pnl', c.pnl_pack)
    monkeypatch.setattr(app, 'scrape_okooo_mobile_ah_ou_books', c.books_pack)
    monkeypatch.setattr(app, 'scrape_okooo_mobile_six_markets', c.six_pack)
    monkeypatch.setattr(app, 'scrape_okooo_mobile_lineup',
                        lambda *a, **k: {'available': False, 'channel': 'mobile'})
    monkeypatch.setattr(app, '_WATER_FULL_SCRAPE_IDS', set())
    if hasattr(app, '_SIX_STAGE_PRINTS'):
        app._SIX_STAGE_PRINTS.clear()
    return app, c


def _run_captured(fn) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()


def test_three_calls_in_one_stage_scrape_and_print_once(calls, capsys) -> None:
    app, c = calls
    rec = _rec()
    captured = _run_captured(lambda: [app._ensure_mobile_six(rec, '1338958') for _ in range(3)])
    assert c.six == 1, f'six-board collector ran {c.six} times, expected 1'
    assert captured.count('🔄 [手机六盘口]') == 1, captured
    # The three warnings must also each appear once, not once per call.
    assert captured.count('[手机盈亏] 不可用') <= 1, captured
    assert captured.count('公司明细] 不可用') <= 1, captured


def test_failing_board_warns_exactly_once(calls, capsys) -> None:
    """The user-facing requirement: a match with a failing board warns once."""
    app, c = calls
    rec = _rec()
    out = _run_captured(lambda: [app._ensure_mobile_six(rec, '1338958') for _ in range(3)])
    assert out.count('🔄 [手机六盘口]') == 1, out


def test_two_stages_scrape_twice(calls) -> None:
    """A new stage is a new collection: stage 2 must not reuse stage 1's pack."""
    app, c = calls
    rec = _rec('stage1')
    with contextlib.redirect_stdout(io.StringIO()):
        app._ensure_mobile_six(rec, '1338958')
        rec['pipeline_phase'] = 'stage2'
        app._ensure_mobile_six(rec, '1338958')
    assert c.six == 2, f'expected one scrape per stage, got {c.six}'


def test_complete_pack_needs_no_extra_scrape(calls) -> None:
    app, c = calls
    rec = _rec()
    rec['six_market_mobile'] = _complete_pack()
    rec['water_gaps'] = []
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(3):
            app._ensure_mobile_six(rec, '1338958')
    assert c.six == 0, f'a complete pack still triggered {c.six} scrapes'


def test_forced_recollect_after_cache_genuinely_refetches(calls) -> None:
    """`force` is the escape hatch for the water re-scrape and the 31-table self-heal.

    Dedupe must not turn a forced re-scrape into a no-op: that would weaken the gate.
    """
    app, c = calls
    rec = _rec()
    with contextlib.redirect_stdout(io.StringIO()):
        app._ensure_mobile_six(rec, '1338958')
        assert c.six == 1
        app._ensure_mobile_six(rec, '1338958', force=True)
    assert c.six == 2, 'the forced re-scrape was served from the cache'


def test_forced_recollect_does_not_reprint_the_summary(calls, capsys) -> None:
    """One collection means one summary line — the forced call is still one collection."""
    app, c = calls
    rec = _rec()

    def _go():
        app._ensure_mobile_six(rec, '1338958')
        app._ensure_mobile_six(rec, '1338958', force=True)

    out = _run_captured(_go)
    assert out.count('🔄 [手机六盘口]') == 1, out


def test_two_records_do_not_share_diagnostic_suppression(calls, capsys) -> None:
    """The counter must live on the record, not in a module-global keyed by match id.

    With a global set, the second record for the same match printed no summary at all,
    so a real failure on that record was invisible in the log.
    """
    app, c = calls
    a, b = _rec(), _rec()

    def _go():
        app._ensure_mobile_six(a, '1338958')
        app._ensure_mobile_six(b, '1338958')

    out = _run_captured(_go)
    assert c.six == 2, 'each record must do its own collection'
    assert out.count('🔄 [手机六盘口]') == 2, out


def test_diagnostic_counter_is_stored_on_the_record(calls) -> None:
    app, c = calls
    rec = _rec()
    with contextlib.redirect_stdout(io.StringIO()):
        app._ensure_mobile_six(rec, '1338958')
    assert app.SIX_STAGE_PRINTS_KEY in rec, sorted(rec)
    assert '1338958' not in {
        k for k in vars(app) if 'SIX_STAGE_PRINTS' in k and isinstance(vars(app)[k], dict)
    }
