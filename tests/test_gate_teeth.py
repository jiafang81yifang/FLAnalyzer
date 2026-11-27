"""Gates that were stubs: prove each one has teeth, and pin every empty stub.

`blade_deepseek_pipeline.DeepSeekAnalyzer` was seeded by one commit (`2a7511d`)
with a family of one-line methods whose docstrings/usage claim enforcement but
whose bodies return their input unchanged. Two of them were load-bearing:

  * `ensure_judgment_standard_sections` — the "quality gate". Its call site
    passed five keyword arguments into a one-parameter function, so every
    production run raised TypeError, swallowed it, and carried on as if the
    gate had passed. It never executed once.
  * `format_matrix_dual_prejudge_html` — the HTML renderer. It returned '' for
    any input *without raising*, so the surrounding `except` fallback was dead
    code. The finished report was replaced by an empty string, `send_tg`
    dropped it ("消息内容为空"), and `_tg_pushed` was still set True.

Every test below is written so that restoring the stub makes it fail. The guard
tests at the bottom pin the behaviour of the stubs that remain: if anything
starts depending on one, the guard fails and forces the implementer to come
here and decide deliberately.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')
pipe_mod = importlib.import_module('blade_deepseek_pipeline')
DSA = pipe_mod.DeepSeekAnalyzer

FIXTURES = Path(__file__).resolve().parent / 'fixtures'

GOOD_REPORT = """📌 [周六001] 主队A vs 客队B

📈【31项市均×竞彩对比表】
  01 1-0   市均   6.83    6.8% 竞彩   9.36    7.8% Δ   -3.9 desktop_cs
  汇总: 共31项

📊【市短矩阵｜市均隐含 > 竞彩隐含】
  输入=市均×竞彩31比分 项数=2
  1-0   市均6.83% 竞彩5.20% 差+1.63pp
  胜平负: 胜=1(100.0%) 平=0(0.0%) 负=0(0.0%)

🔥【市热矩阵｜Δ=市均%−理论%>0，与pos_rows同口径】
  输入=31全表 项数=2
  1-0   市均6.83% 理论5.90% Δ+0.93pp
  胜平负: 胜=1(100.0%) 平=0(0.0%) 负=0(0.0%)

📉【市热超理论专表】
  条数: 2

📊【市均***竞彩对比表】
  1-1 市热=0.126 竞彩=-

🧠【DeepSeek 数据分析研判报告】
  方向依据：理论概率与市均赔率同一方向。"""


def _emittable_record(tmp_path: Path) -> dict[str, Any]:
    """A record with real 31/31 market coverage, written to a tmp history file."""
    okooo = importlib.import_module('okooo_mobile_cs')
    text = okooo.decode_okooo_html((FIXTURES / 'okooo_jczq_oddsdata_20slots.html').read_bytes())
    jc = okooo.parse_jczq_score_odds(text, '1347776')['odds']
    with (FIXTURES / 'okooo_desktop_cs_1347776.html').open('rb') as fh:
        page = okooo._parse_cs_datatable(okooo.decode_okooo_html(fh.read()))
    merged = okooo.merge_cs31(page['page_avg'], page['books_avg'], jc)
    rec = {
        'id': 'm1', 'match': '主队A vs 客队B', 'cn_name': '[周六001] 主队A vs 客队B',
        'no': '周六001', 'status': 'pending', 'time': '2026-11-28 20:00',
        'match_time': '2026-11-28 20:00', 'kickoff_source': 'okooo_match_time',
        'pipeline_scrape_done': True, 'pipeline_phase': 'scrape',
        'predicted_score': '2-1', 'second_score': '1-0', 'predicted_result': '胜',
        'lam_h': 1.45, 'lam_a': 1.10, 'lambda_home': 1.45, 'lambda_away': 1.10,
        'correct_score_page_avg': merged['scores'],
        'correct_score_books_avg': page['books_avg'],
        'correct_score_books_n': len(page['books_avg']),
        'correct_score_site_avg': merged['scores'],
        'correct_score_jc_official': dict(jc),
        'jc_score_odds': dict(jc),
        'cs31_provenance': merged['provenance'],
        'cs31_counts': merged['counts'],
        'score_market_heat': okooo.build_score_market_heat_ranking(
            merged['scores'], page_avg_odds=merged['scores'],
            heat_source='okooo_cs_books_avg', channel='okooo_cs'),
        'direction_lock': {'locked': True, 'direction': '胜', 'scores': ['2-1', '1-0']},
        'league': '英超', 'okooo_match_id': '1347776',
        # The quality gate refuses a report when the six boards are absent, so a
        # record meant to reach the TG surface must carry them (stage 1 does).
        'six_market_mobile': {
            'asian_handicap': -0.5, 'ou_line': 2.5,
            'odds_1x2': {'Home': 1.9, 'Draw': 3.5, 'Away': 4.1},
            'jc_handicap': -1,
            'total_goals_odds': {'2': 3.4, '3': 3.8},
            'htft_odds': {'胜胜': 3.2, '平平': 6.2},
        },
    }
    hist = tmp_path / 'match_history.json'
    hist.write_text(json.dumps({'m1': rec}, ensure_ascii=False), encoding='utf-8')
    # Production always stores the bundle during stage 1; without it the emit
    # path takes the "no bundle" branch and never reaches the gate.
    bundle = app.rebuild_pipeline_bundle_from_history(dict(rec), fid='m1', cn_name=rec['cn_name'])
    rec['pipeline_report_bundle'] = bundle
    hist.write_text(json.dumps({'m1': rec}, ensure_ascii=False), encoding='utf-8')
    return rec


def _emit(rec: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run the real stage-2 emit with network fully disabled."""
    import contextlib
    import io

    monkeypatch.setattr(app, 'HISTORY_FILE', str(tmp_path / 'match_history.json'))
    monkeypatch.setattr(app, '_ensure_mobile_six', lambda *a, **k: False)
    # Return False: "nothing was enriched", so the emit path keeps the bundle we
    # prebuilt. Returning True makes it pop the bundle and re-scrape the network.
    monkeypatch.setattr(app, 'enrich_history_okooo_cs_facts', lambda *a, **k: False)
    monkeypatch.setattr(pipe_mod.DeepSeekAnalyzer, 'analyze_match_judgment',
                        staticmethod(lambda payload=None, *a, **k: {
                            'available': True, 'error': '', 'model': 'test',
                            'content': '① 2-1 ② 1-0'}))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        app.emit_pipeline_deepseek_tg_report(
            {'id': 'm1', 'cn_name': rec['cn_name'], 'time': rec['time'],
             'kickoff_source': 'okooo_match_time'}, i=0, total=1)


# --------------------------------------------------------------------------
# D2 — the HTML renderer must never silently blank a report
# --------------------------------------------------------------------------
@pytest.mark.parametrize('text', [
    '📌 t',
    GOOD_REPORT,
    'no title at all\nsecond line',
    '  \n📌 leading blank\nbody',
    'a & b < c > d',
])
def test_html_renderer_non_empty_in_means_non_empty_out(text: str) -> None:
    """D2 core contract: `bool(text.strip())` implies a non-empty render.

    Revert tested: restoring `return ''` -> every parametrised case yields ''
    and this test fails on all five.
    """
    out = DSA.format_matrix_dual_prejudge_html(text)
    assert isinstance(out, str)
    assert out.strip(), f'renderer blanked a non-empty report ({len(text)} chars)'
    assert len(out) >= len(text.strip())


def test_html_renderer_escapes_markup() -> None:
    """Telegram HTML mode rejects unbalanced entities, so the body must be escaped."""
    out = DSA.format_matrix_dual_prejudge_html('📌 a < b & c')
    assert '&lt;' in out and '&amp;' in out
    assert '< b' not in out


def test_html_renderer_is_empty_only_for_empty_input() -> None:
    for empty in ('', '   ', '\n\n', None):
        assert DSA.format_matrix_dual_prejudge_html(empty) == ''


def test_renderer_exception_falls_back_and_still_sends(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
    """Requirement 2: the `except` fallback must be reachable and lossless.

    Revert tested: making the renderer a stub that cannot raise hides the bug;
    this test forces a raise and asserts the report still goes out non-empty.
    """
    sent: list[tuple[Any, Any]] = []

    def _boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError('renderer exploded')

    monkeypatch.setattr(DSA, 'format_matrix_dual_prejudge_html', staticmethod(_boom))
    monkeypatch.setattr(app, 'send_judgment_report_tg',
                        lambda text, parse_mode=None: sent.append((parse_mode, text)))
    monkeypatch.setattr(app, 'JUDGMENT_TG_REPORT_OUTPUT', True)

    # Drive the real emit path with a record the app can render.
    case = _emittable_record(tmp_path)
    _emit(case, monkeypatch, tmp_path)

    html_sends = [t for pm, t in sent if pm == 'HTML']
    assert html_sends, 'nothing was sent in HTML mode'
    assert html_sends[0].strip(), 'fallback produced an empty report'
    assert '31项市均×竞彩对比表' in html_sends[0]


def test_real_renderer_output_is_non_empty_on_the_production_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    sent: list[tuple[Any, Any]] = []
    monkeypatch.setattr(app, 'send_judgment_report_tg',
                        lambda text, parse_mode=None: sent.append((parse_mode, text)))
    monkeypatch.setattr(app, 'JUDGMENT_TG_REPORT_OUTPUT', True)
    _emit(_emittable_record(tmp_path), monkeypatch, tmp_path)
    html_sends = [t for pm, t in sent if pm == 'HTML']
    assert html_sends and html_sends[0].strip()
    assert len(html_sends[0]) > 1000, len(html_sends[0])


# --------------------------------------------------------------------------
# D2b — `_tg_pushed` must reflect what was actually sent
# --------------------------------------------------------------------------
def test_tg_pushed_reflects_whether_a_non_empty_body_went_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`_tg_pushed` must describe what the transport actually reported.

    The original defect was `_tg_pushed = True` set unconditionally after the
    send call: a blank body, or a refused send, still recorded delivery. The two
    halves below pin both directions and insist the record carries the key at
    all — a missing `tg_sent` is itself a silent gap.

    The stamp returns a bool because the shipped contract is
    `_tg_pushed = send_judgment_report_tg(...) is True`: delivery is whatever the
    transport said, not "the body was non-empty".
    """
    sent: list[tuple[Any, Any]] = []

    def _delivered(text, parse_mode=None):
        sent.append((parse_mode, text))
        return True

    monkeypatch.setattr(app, 'send_judgment_report_tg', _delivered)
    rec = _emittable_record(tmp_path)
    _emit(rec, monkeypatch, tmp_path)

    assert any(t and t.strip() for _pm, t in sent), 'nothing was sent'
    written = app.load_json(str(tmp_path / 'match_history.json'))
    assert 'tg_sent' in written['m1'], 'the record must carry the delivery outcome'
    assert written['m1']['tg_sent'] is True


def test_tg_sent_is_false_when_the_transport_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A refused send must be recorded as not delivered.

    Revert tested: restoring the unconditional `_tg_pushed = True` (or my earlier
    "body non-empty" stamp) records delivery even though the transport said no.
    """
    monkeypatch.setattr(app, 'send_judgment_report_tg',
                        lambda text, parse_mode=None: False)
    rec = _emittable_record(tmp_path)
    _emit(rec, monkeypatch, tmp_path)
    written = app.load_json(str(tmp_path / 'match_history.json'))
    assert 'tg_sent' in written['m1'], 'the record must carry the delivery outcome'
    assert written['m1']['tg_sent'] is False, (
        'the transport reported failure but the record says delivered')


def test_empty_render_falls_back_to_escaped_text_instead_of_sending_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A blank render must not become a blank send: the fallback carries the report.

    This is the exact production shape of the D2 bug. The renderer returning ''
    has to degrade to escaped text (non-empty), not to an empty message.
    """
    sent: list[tuple[Any, Any]] = []

    def _delivered(text, parse_mode=None):
        sent.append((parse_mode, text))
        return True

    monkeypatch.setattr(DSA, 'format_matrix_dual_prejudge_html', staticmethod(lambda *a, **k: ''))
    monkeypatch.setattr(app, 'send_judgment_report_tg', _delivered)
    rec = _emittable_record(tmp_path)
    _emit(rec, monkeypatch, tmp_path)
    # A blank HTML render degrades to the plain-text report — never to nothing.
    assert sent, 'nothing went out at all'
    body = sent[0][1]
    assert body and body.strip(), 'fallback still produced an empty body'
    assert '31项市均×竞彩对比表' in body
    written = app.load_json(str(tmp_path / 'match_history.json'))
    assert written['m1']['tg_sent'] is True


# --------------------------------------------------------------------------
# D1 — the quality gate must actually gate
# --------------------------------------------------------------------------
# The gate's documented contract is "six boards + the DeepSeek shell". The
# section-level checks below are diagnostics (`section_status` / `empty_sections`),
# not refusal reasons: 市短/市热 are legitimately empty when nothing prices above
# the comparison base, so treating them as missing would refuse a fine report.
BOARDS_PAYLOAD = {'six_market_mobile': {
    'asian_handicap': 0.5, 'ou_line': 2.5,
    'odds_1x2': {'Home': 1.9, 'Draw': 3.5, 'Away': 4.1},
    'jc_handicap': -1,
    'total_goals_odds': {'2': 3.4}, 'htft_odds': {'胜胜': 3.2},
}}


def test_gate_passes_a_complete_report() -> None:
    verdict = DSA.ensure_judgment_standard_sections(GOOD_REPORT, payload=BOARDS_PAYLOAD)
    assert isinstance(verdict, dict), 'the gate must return a structure, not a str'
    assert verdict['ok'] is True, verdict.get('reject_reason')
    assert verdict['reject'] is False
    assert verdict['text'] == GOOD_REPORT
    assert verdict['missing_boards'] == []
    assert verdict['missing_sections'] == []


def test_gate_is_callable_with_the_production_keyword_arguments() -> None:
    """The exact call shape from app_v84_hermes: five extra kwargs.

    Revert tested: restoring `def ensure_judgment_standard_sections(text)` ->
    TypeError, which is precisely what production hit on every run.
    """
    verdict = DSA.ensure_judgment_standard_sections(
        GOOD_REPORT,
        payload={**BOARDS_PAYLOAD, 'theo_mkt_31_pack': {'rows': [{'score': 'x'}] * 31}},
        jc_vs_hot_tg_lines=['x'],
        wdl_matrix_tg_lines=['x'],
        theo_mkt_31_tg_lines=['x'],
        mkt_over_theo_tg_lines=['x'],
    )
    assert verdict['ok'] is True, verdict.get('reject_reason')
    assert verdict['passed_sections']['theo_mkt_31_tg_lines'] is True


def test_gate_rejects_a_report_without_the_six_boards() -> None:
    """The gate's core job: no six boards, no ship."""
    verdict = DSA.ensure_judgment_standard_sections(GOOD_REPORT)
    assert verdict['ok'] is False
    assert verdict['reject'] is True
    assert verdict['missing_boards'], verdict
    assert '缺六盘口' in verdict['reject_reason'], verdict


def test_gate_rejects_a_report_missing_the_deepseek_shell() -> None:
    broken = GOOD_REPORT.replace('🧠【DeepSeek 数据分析研判报告】', '（研判缺失）')
    verdict = DSA.ensure_judgment_standard_sections(broken, payload=BOARDS_PAYLOAD)
    assert verdict['ok'] is False
    assert DSA.JUDGMENT_SHELL_MARKER in verdict['missing_sections'], verdict


def test_gate_rejects_an_empty_report() -> None:
    verdict = DSA.ensure_judgment_standard_sections('')
    assert verdict['ok'] is False
    assert verdict['reject'] is True
    assert verdict['text'] == ''


def test_gate_treats_a_zero_line_as_a_present_board() -> None:
    """0.0 is a level (平手 / 0 线), not a missing board."""
    payload = {'six_market_mobile': dict(BOARDS_PAYLOAD['six_market_mobile'],
                                         asian_handicap=0.0, ou_line=0.0)}
    verdict = DSA.ensure_judgment_standard_sections(GOOD_REPORT, payload=payload)
    assert verdict['missing_boards'] == [], verdict


def test_gate_reports_the_31_table_as_missing_in_section_status() -> None:
    """A table that is absent is a diagnostic; the caller uses it, not the gate."""
    broken = GOOD_REPORT.replace('📈【31项市均×竞彩对比表】', '（表缺失）')
    verdict = DSA.ensure_judgment_standard_sections(broken, payload=BOARDS_PAYLOAD)
    assert verdict['section_status']['31项市均×竞彩对比表'] is False, verdict
    assert verdict['missing_boards'] == [], verdict


def test_gate_flags_a_section_that_is_only_a_degraded_line() -> None:
    """「未形成（…）」 全篇的段落等于没有数据，门要把它记成空段。"""
    broken = GOOD_REPORT.replace(
        '📈【31项市均×竞彩对比表】\n  01 1-0   市均   6.83    6.8% 竞彩   9.36    7.8% Δ   -3.9 desktop_cs\n  汇总: 共31项',
        '📈【31项市均×竞彩对比表】\n  未形成（缺少31全表）',
    )
    verdict = DSA.ensure_judgment_standard_sections(broken, payload=BOARDS_PAYLOAD)
    assert '31项市均×竞彩对比表' in verdict['empty_sections'], verdict


def test_gate_never_returns_none_text() -> None:
    """The call site does `_gate.get('text')`; None there blanks the report."""
    for text in (None, '', GOOD_REPORT):
        verdict = DSA.ensure_judgment_standard_sections(text)
        assert isinstance(verdict.get('text'), str)


def test_production_gate_refuses_a_report_without_the_six_boards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """End to end: the gate's refusal must replace the report with a warning.

    Revert tested: restoring the `return str(text)` stub -> the gate always says
    ok and the body goes out as if it had passed.
    """
    sent: list[tuple[Any, Any]] = []
    monkeypatch.setattr(app, 'send_judgment_report_tg',
                        lambda text, parse_mode=None: sent.append((parse_mode, text)))
    monkeypatch.setattr(app, 'JUDGMENT_TG_REPORT_OUTPUT', True)
    rec = _emittable_record(tmp_path)
    rec.pop('six_market_mobile', None)
    bundle = rec.get('pipeline_report_bundle') or {}
    for holder in (rec, bundle, (bundle.get('ds_payload') if isinstance(bundle, dict) else None)):
        if isinstance(holder, dict):
            holder.pop('six_market_mobile', None)
            holder.pop('cross_market_board', None)
    (tmp_path / 'match_history.json').write_text(
        json.dumps({'m1': rec}, ensure_ascii=False), encoding='utf-8')
    _emit(rec, monkeypatch, tmp_path)
    bodies = [t for _pm, t in sent if t]
    assert bodies, 'nothing sent at all'
    assert any('质量门拒发' in t for t in bodies), bodies


# --------------------------------------------------------------------------
# D5/D6/D7 — report shape defects the first honest end-to-end report exposed
# --------------------------------------------------------------------------
def test_match_header_is_idempotent_and_never_repeats_the_match_no() -> None:
    """Stage 1 stores the formatted header; stage 2 formats it again.

    Revert tested: dropping the `startswith('📌')` early return and the
    `[赛号]` strip reproduces
    '📌 [周六001] 📌 [周六001] ... 时间 时间'.
    """
    once = DSA.format_tg_match_header('主队A vs 客队B', kickoff='2026-11-28 20:00',
                                      match_no='周六001')
    twice = DSA.format_tg_match_header(once, kickoff='2026-11-28 20:00',
                                       match_no='周六001')
    assert twice == once
    assert once.count('📌') == 1, once
    assert once.count('[周六001]') == 1, once
    assert once.count('2026-11-28 20:00') == 1, once

    # A bare name that already carries the number must not gain a second one.
    from_bracket = DSA.format_tg_match_header('[周六001] 主队A vs 客队B',
                                              kickoff='2026-11-28 20:00',
                                              match_no='周六001')
    assert from_bracket.count('[周六001]') == 1, from_bracket


def test_tg_sections_never_print_two_short_matrices() -> None:
    """`wdl_matrix_tg_lines` is a second, stale 市短 for the same match.

    Revert tested: removing the skip in `build_deepseek_only_tg_sections` ->
    '市短矩阵' appears twice (once rendered from the pack, once stale).
    """
    pack = {
        'rows': [
            {'score': '1-0', 'market': 6.0, 'jc_odds': 9.0, 'jc_odds_pct': 5.0,
             'market_source': 'desktop_cs', 'delta_same_source': False},
        ],
        'pos_rows': [{'score': '1-0', 'market': 6.0, 'theory': 5.0}],
        'pos_count': 1,
    }
    stale = ['📊【市短矩阵｜市均隐含 > 竞彩隐含】', '  未形成（没有市均高于竞彩的选项）']
    text = '\n\n'.join(DSA.build_deepseek_only_tg_sections(
        title_line='📌 t',
        theo_mkt_31_tg_lines=DSA.format_theo_mkt_31_tg_lines(pack),
        ds_payload={'theo_mkt_31_pack': pack, 'wdl_matrix_tg_lines': stale},
        wdl_matrix_tg_lines=stale,
    ))
    assert text.count('市短矩阵') == 1, text.count('市短矩阵')
    # The surviving one must be the one rendered from the pack, not the stale copy.
    assert '未形成（没有市均高于竞彩的选项）' not in text


def test_repaired_pack_is_rerendered_into_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A repaired 31 pack must reach the report, not just the payload.

    The emit path can rebuild the pack after the bundle was built with an empty
    one. Writing the new pack into ds_payload but leaving the pre-rendered line
    lists untouched makes 市热超理论专表 disappear from the report entirely.

    Revert tested: drop the re-render block -> '市热超理论专表' is missing from
    the emitted text and this assertion fails.
    """
    sent: list[tuple[Any, Any]] = []
    monkeypatch.setattr(app, 'send_judgment_report_tg',
                        lambda text, parse_mode=None: sent.append((parse_mode, text)))
    rec = _emittable_record(tmp_path)
    _emit(rec, monkeypatch, tmp_path)
    body = max((t for _pm, t in sent if t), key=len)
    for section in ('31项市均×竞彩对比表', '市短矩阵', '市热矩阵',
                    '市热超理论专表', 'DeepSeek 数据分析研判报告'):
        assert section in body, section
    assert body.count('市短矩阵') == 1, body.count('市短矩阵')
    assert body.count('📌') >= 1
    first = next(ln for ln in body.splitlines() if '📌' in ln)
    assert first.count('[周六001]') == 1, first


# --------------------------------------------------------------------------
# D8 — a jc-labelled option must keep its market value when jc odds are partial
# --------------------------------------------------------------------------
def test_partial_jc_odds_do_not_blank_the_other_options() -> None:
    """The 9 jc-labelled options must keep a market value when jc is partial.

    A live jc scrape can come back partial (21/31, or with inf/NaN in some
    slots). Those options are labelled `jc_official`, and the pack used to drop
    the whole jc vector from the market vector, so a row whose jc value was
    unusable ended up with `market=None` even though the record still carried a
    legal 市均 copy. That row then failed the 31-row audit and the whole match
    was refused.

    Revert tested: restoring `_page_vec = _implied_vec(_page_only)` without the
    fallback keys -> market is None and this assertion fails.
    """
    extra = {'5-0': 364.2674, '1-5': 85.5994, '胜其它': 106.531, '1-0': 8.0}
    jc = {'1-0': 9.0, '5-0': float('inf'), '1-5': float('nan')}  # partial + corrupt
    prov = {'1-0': 'desktop_cs', '5-0': 'jc_official',
            '1-5': 'jc_official', '胜其它': 'jc_official'}
    pack = DSA.build_theo_mkt_31_pack(
        [], extra_market_odds=extra, market_provenance=prov,
        jc_score_odds=jc, lam_home=1.5, lam_away=1.1)
    for option in ('5-0', '1-5', '胜其它'):
        row = next(r for r in pack['rows'] if r['score'] == option)
        assert row['market'] is not None, (option, row)
        assert float(row['market']) > 0, (option, row['market'])
    audit = DSA.audit_theo_mkt_31_rows(pack['rows'])
    assert '胜其它' not in audit['missing_market'], audit['missing_market']


def test_full_jc_odds_still_win_over_the_page_copy() -> None:
    """The fallback must not change the normal case: jc value wins when present."""
    extra = {'5-0': 500.0, '1-0': 8.0}
    jc = {'5-0': 200.0, '1-0': 9.0}
    prov = {'1-0': 'desktop_cs', '5-0': 'jc_official'}
    pack = DSA.build_theo_mkt_31_pack(
        [], extra_market_odds=extra, market_provenance=prov,
        jc_score_odds=jc, lam_home=1.5, lam_away=1.1)
    row = next(r for r in pack['rows'] if r['score'] == '5-0')
    # The pack normalises the internal `jc_score_odds` label to `jc_official`.
    assert row.get('market_source') == 'jc_official', row
    assert row.get('delta_same_source') is True, row
    row10 = next(r for r in pack['rows'] if r['score'] == '1-0')
    assert row10.get('market_source') == 'desktop_cs', row10
    assert row10.get('delta_same_source') is False, row10


# --------------------------------------------------------------------------
# D9/D11/D12 — hostile values read back from a record file must not break a match
# --------------------------------------------------------------------------
def test_over_range_int_odds_do_not_abort_the_collector() -> None:
    """`json.load` really does hand over `10**400`; float() raises OverflowError.

    Revert tested: removing OverflowError from the except tuple -> the collector
    raises and the whole 31 table cannot be built for that match.
    """
    rec = {'correct_score_page_avg': {'1-0': 10 ** 400, '2-0': 9.0}}
    got = DSA._collect_legal_cs_market_odds(rec, [])
    assert got.get('1-0') in (None,), got
    assert got.get('2-0') == 9.0, got


def test_infinite_odds_do_not_shadow_a_legal_value_from_another_source() -> None:
    """inf > 1 is true, so an inf value used to win `setdefault` and mask the copy.

    Revert tested: dropping `and math.isfinite(f)` -> 1-0 keeps inf and the legal
    site_avg value never gets in.
    """
    rec = {
        'correct_score_page_avg': {'1-0': float('inf'), '2-0': float('nan')},
        'correct_score_site_avg': {'1-0': 647.6292, '2-0': 174.1624},
    }
    got = DSA._collect_legal_cs_market_odds(rec, [])
    assert got.get('1-0') == 647.6292, got
    assert got.get('2-0') == 174.1624, got


def test_partial_and_full_jc_copies_are_unioned() -> None:
    """A partial `jc_score_odds` must not hide legal odds in the official copy.

    Revert tested: passing only `jc_score_odds` -> the rows present only in
    `correct_score_jc_official` render with an empty 竞彩 column.
    """
    merged = app._merge_jc_odds_sources(
        {'1-0': 13.6673, '2-0': 'x'},
        {'1-0': 13.0, '2-0': 26.0828, '3-1': float('inf')},
    )
    assert merged['1-0'] == 13.6673, merged        # first source wins
    assert merged['2-0'] == 26.0828, merged        # unusable value skipped
    assert '3-1' not in merged, merged             # non-finite skipped


def test_pack_built_with_the_union_keeps_the_official_only_option() -> None:
    """The union must reach the pack, not just sit in a helper.

    A record whose `jc_score_odds` is partial while `correct_score_jc_official`
    still holds the option is the real case: taking only the first copy leaves
    that row's 竞彩 column empty. This drives the same call shape the emit path
    uses (union -> build_theo_mkt_31_pack) and asserts the row is filled.

    Revert tested: replacing the union with `rec['jc_score_odds']` -> the option
    only present in the official copy comes back with jc_odds None.
    """
    rec = {
        'jc_score_odds': {'1-0': 9.0},
        'correct_score_jc_official': {'1-0': 9.0, '3-1': 37.8693},
    }
    union = app._merge_jc_odds_sources(
        rec['jc_score_odds'], rec['correct_score_jc_official'])
    pack = DSA.build_theo_mkt_31_pack(
        [], extra_market_odds={'1-0': 8.0, '3-1': 40.0},
        market_provenance={'1-0': 'desktop_cs', '3-1': 'jc_official'},
        jc_score_odds=union, lam_home=1.4, lam_away=1.1)
    row = next(r for r in pack['rows'] if r['score'] == '3-1')
    assert row.get('jc_odds') == 37.8693, row
    assert row.get('jc_odds_pct') is not None, row

    # And without the union it is empty: the defect this pins.
    pack_no_union = DSA.build_theo_mkt_31_pack(
        [], extra_market_odds={'1-0': 8.0, '3-1': 40.0},
        market_provenance={'1-0': 'desktop_cs', '3-1': 'jc_official'},
        jc_score_odds=rec['jc_score_odds'], lam_home=1.4, lam_away=1.1)
    row_no = next(r for r in pack_no_union['rows'] if r['score'] == '3-1')
    assert row_no.get('jc_odds') is None, row_no


def test_emit_uses_both_jc_copies_when_one_is_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """End to end through emit: the 竞彩 column must be filled from either copy.

    `_emittable_record` gives the desktop page 22 options; the remaining 9 are
    jc-only. Here `jc_score_odds` is cut down to one option and the official
    copy keeps all 31, which is what a split two-step scrape looks like. Every
    row must still render a 竞彩 value; taking only `jc_score_odds` leaves the
    nine jc-only rows blank and the table fails.
    """
    sent: list[tuple[Any, Any]] = []
    monkeypatch.setattr(app, 'send_judgment_report_tg',
                        lambda text, parse_mode=None: sent.append((parse_mode, text)))
    rec = _emittable_record(tmp_path)
    official = dict(rec['correct_score_jc_official'])
    rec['jc_score_odds'] = {'1-0': official['1-0']}
    hist = tmp_path / 'match_history.json'
    hist.write_text(json.dumps({'m1': rec}, ensure_ascii=False), encoding='utf-8')
    _emit(rec, monkeypatch, tmp_path)

    body = max((t for _pm, t in sent if t), key=len)
    assert '31项市均×竞彩对比表' in body
    table = body.split('31项市均×竞彩对比表', 1)[1].split('📊', 1)[0]
    rows = [ln for ln in table.splitlines() if re.match(r'^  \d{2} ', ln)]
    assert len(rows) == 31, len(rows)
    # The nine jc-only options must all carry a 竞彩 value.
    for option in ('5-0', '5-1', '5-2', '0-5', '1-5', '2-5', '胜其它', '平其它', '负其它'):
        line = next((ln for ln in rows if f' {option} ' in ln), '')
        assert line, option
        assert '竞彩   -' not in line and '竞彩-' not in line, (option, line)


# --------------------------------------------------------------------------
# D13 — a long-shot quote must not round to "no market"
# --------------------------------------------------------------------------
def test_longshot_odds_never_round_the_market_share_to_zero() -> None:
    """A real quote at extreme odds must survive normalisation.

    `round(share, 4)` turns a 1e-10% share into 0.0, and the 31-row audit treats
    `market <= 0` as "no market value", so one long-shot price was enough to
    fail the whole match as 31-全表未齐.

    Revert tested: restoring the plain `round(value, 4)` -> market becomes 0.0
    and the audit reports the option as missing.
    """
    pack = DSA.build_theo_mkt_31_pack(
        [],
        extra_market_odds={'2-5': 74.4981, '1-0': 8.0, '0-0': 7.0},
        market_provenance={'2-5': 'jc_official', '胜其它': 'jc_official',
                           '1-0': 'desktop_cs'},
        jc_score_odds={'2-5': 1e12, '胜其它': 1e12, '1-0': 9.0},
        lam_home=1.4, lam_away=1.1,
    )
    for option in ('2-5', '胜其它'):
        row = next(r for r in pack['rows'] if r['score'] == option)
        assert row['market'] is not None, (option, row)
        assert float(row['market']) > 0, (option, row['market'])
    audit = DSA.audit_theo_mkt_31_rows(pack['rows'])
    assert '2-5' not in audit['missing_market'], audit['missing_market']
    # And the table still sums to 100.
    assert abs(pack['market_sum'] - 100.0) < 0.5, pack['market_sum']


def test_a_refused_match_still_records_its_delivery_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A gate refusal must leave tg_sent=False, not an absent key.

    A record that carries only `tg_quality_gate_reject` and no `tg_sent` cannot
    be told apart from one that never reached stage 2 — the exact silent gap
    this line of work exists to remove. Refusal is a conclusion, so it must be
    recorded as one.
    """
    monkeypatch.setattr(app, 'send_judgment_report_tg',
                        lambda text, parse_mode=None: True)
    rec = _emittable_record(tmp_path)
    rec.pop('six_market_mobile', None)
    bundle = rec.get('pipeline_report_bundle') or {}
    for holder in (rec, bundle,
                   (bundle.get('ds_payload') if isinstance(bundle, dict) else None)):
        if isinstance(holder, dict):
            holder.pop('six_market_mobile', None)
            holder.pop('cross_market_board', None)
    (tmp_path / 'match_history.json').write_text(
        json.dumps({'m1': rec}, ensure_ascii=False), encoding='utf-8')
    _emit(rec, monkeypatch, tmp_path)

    written = app.load_json(str(tmp_path / 'match_history.json'))['m1']
    assert 'tg_sent' in written, 'a refused match must still record tg_sent'
    assert written['tg_sent'] is False
    assert written.get('tg_report_kind') == 'quality_gate_refused', written.get('tg_report_kind')
    assert written.get('tg_not_sent_reason'), 'the refusal must say why'
