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
               # 闸门钉成「未开赛」：这 3 条测的是**标题行去重**（📌 只出现一次、
               # kickoff 只出现一次、场次号只出现一次），与「发不发」正交。桩掉闸门
               # 之后被测的标题去重逻辑仍完整执行，没有任何断言被削弱。
               #
               # 不打这个桩会随时间自己变红：`KICKOFF` 是写死的日期，阶段2 用
               # `datetime.now()` 判「已开赛就不补发」，于是开球时间一过，这 3 条就
               # 拿不到任何投递（`first == ''`）。把 KICKOFF 往后挪只是把炸弹延后，
               # 同一文件下方 `SWEEP_FROZEN_NOW` 那套冻结时钟就是为这个模式准备的。
               # 必须打 `app.is_history_match_not_started`（模块属性）：阶段2 是直接按
               # 模块全局调用的，不是经由实例或导入名。
               mock.patch.object(app, 'is_history_match_not_started', lambda *a, **k: True),
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

    # The writeback run and the replay must reach the *same* analyser class, so
    # pin it instead of relying on whatever the sweep happened to bind at import.
    # (This used to paper over `test_six_market_live_failure._pipe` reloading the
    # pipeline module, which left `app_v84_hermes` on the discarded class. That
    # reload is gone; the pin stays because it makes the intent explicit and the
    # test independent of import order.)
    assert S.PIPE is _pipe_mod, 'the sweep imported a different pipeline module object'
    S.APP.DeepSeekAnalyzer = _pipe_mod.DeepSeekAnalyzer

    orig = S.PIPE.DeepSeekAnalyzer.build_deepseek_only_tg_sections
    real_run_once = S._run_once
    replaying = {'on': False}
    # 当前正在跑的 case 标识 + 它在本次 shard 里的调用序号。
    #
    # 不能用 match id 当标识：`_build_case` 是 `rng.choice(MATCH_IDS)`，固定池里
    # 随机抽，12 个 case 里必然撞 id（实测 seed 20261127+4 与 +10 都抽到
    # `1347776`，两场却是不同比赛）。用 id 计数会把「两场不同比赛」当成
    # 「同一场跑了两遍」，这条反证就不再测它名字里说的那件事。
    #
    # 用「第几次 writeback 调用」+ 比赛名，两者合起来才能既识别 case、
    # 又能看出「同一次调用被重复记账」。
    current_case: dict[str, str] = {'id': ''}
    wb_counter = {'n': 0}

    def _case_id(case):
        wb_counter['n'] += 1
        name = ''
        if isinstance(case, dict):
            rec = case.get('rec') if isinstance(case.get('rec'), dict) else case
            name = str(rec.get('cn_name') or rec.get('match') or '')
        return f'call#{wb_counter["n"]} {name}'.strip()

    def _truncating(*a, **k):
        sections = orig(*a, **k)
        if not replaying['on'] or not drop:
            return sections
        if drop == 'table_row':
            out = []
            for sec in sections:
                lines = sec.splitlines()
                # 只从 **31 项对比表那一节**删行，且要求该行同时含 `市均` 和 `竞彩`。
                # 这条桩的全部意义是「写回时丢了一行 31 表数据」；判据收窄到
                # 「哪一节」+「行里到底是什么」之后，新加的段（★市短 / 市热 /
                # 进球网格…）就不会被同形状的行误命中，反证才仍然证明的是它
                # 名字里说的那件事。
                if '31项市均×竞彩对比表' not in sec:
                    out.append(sec)
                    continue
                kept = [
                    ln for ln in lines
                    if not (ln.startswith('  ') and ln[2:4].strip().isdigit()
                            and '市均' in ln and '竞彩' in ln)
                ]
                removed = len(lines) - len(kept)
                if removed and dropped is not None:
                    dropped.append(
                        f"case={current_case['id']} {removed} table row(s)"
                    )
                out.append('\n'.join(kept or lines))
            return out
        if drop == 'fact_panel':
            kept = [sec for sec in sections if '六盘口' not in sec]
            if len(kept) != len(sections) and dropped is not None:
                dropped.append(f"case={current_case['id']} 六盘口事实板")
            return kept
        return sections

    def _spy(case, hist_path, reseed=True):
        previous = current_case['id']
        current_case['id'] = _case_id(case)
        replaying['on'] = not reseed
        try:
            return real_run_once(case, hist_path, reseed=reseed)
        finally:
            replaying['on'] = False
            current_case['id'] = previous

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
    assert len(dropped) == 11, (
        f'the counter-proof removed rows from {len(dropped)} cases '
        f'(expected 11). 每条都带 case 标识，多出来的那条就是线索：\n'
        + '\n'.join(f'  [{i}] {d}' for i, d in enumerate(dropped))
    )
    # 桩删的必须**正好是 31 表的一整表行**（31 行），不是「某种形状的行」。
    # 这条防的是桩的匹配面被别的段污染：新段里若出现同形状的行而被误删，
    # 计数会变成 31 的倍数或别的数，这里立刻报出来。
    assert all(d.endswith('31 table row(s)') for d in dropped), (
        '桩删的不止 31 表整表行：\n' + '\n'.join(f'  [{i}] {d}' for i, d in enumerate(dropped))
    )
    # 每一次 writeback 调用只许记账一次：同一个 `call#N` 出现两遍，说明
    # 同一次渲染被记了两次账（或 `_truncating` 里重复 append），这条反证的
    # 分母就不可信了。
    calls = [d.split(' ')[0] for d in dropped]
    assert len(set(calls)) == len(calls), (
        '同一次 writeback 调用被记账多次：\n'
        + '\n'.join(f'  [{i}] {d}' for i, d in enumerate(dropped))
    )
    assert e9 == 11, f'a lost 31-table row on writeback went unnoticed (E9={e9})'
    assert produced == 1, produced


def test_e9_catches_a_dropped_fact_panel() -> None:
    dropped: list = []
    produced, e9 = _e9_shard(drop='fact_panel', dropped=dropped)
    # Same denominator as the table-row case: index 0 bypasses this builder.
    assert len(dropped) == 11, (
        f'the counter-proof removed the fact panel from {len(dropped)} cases '
        f'(expected 11)：\n' + '\n'.join(f'  [{i}] {d}' for i, d in enumerate(dropped))
    )
    assert all(d.endswith('六盘口事实板') for d in dropped), (
        '桩删的不止六盘口事实板：\n' + '\n'.join(f'  [{i}] {d}' for i, d in enumerate(dropped))
    )
    assert len({d.split(' ')[0] for d in dropped}) == len(dropped), (
        '同一个 case 被删了两次（说明它跑了不止一遍）：\n'
        + '\n'.join(f'  [{i}] {d}' for i, d in enumerate(dropped))
    )
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


# ---------------------------------------------------------------------------
# Coverage: every exit the harness claims to sweep must actually be swept.
#
# `render_fallback_empty` was unreachable for two full 100k runs: the fallback
# was chosen by `index % 4` inside a branch gated on `index % 10 == 0`, so the
# index was always even and slot 3 ('empty') never came up. COVERAGE simply had
# no such key, which reads as "fine" rather than "never ran".
# ---------------------------------------------------------------------------
def _harness():
    import contextlib
    import io
    import sys as _sys
    _scripts = str(ROOT / 'scripts')
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import sweep_100k_e2e as S
        import blade_deepseek_pipeline as _pipe_mod
    S.APP.DeepSeekAnalyzer = _pipe_mod.DeepSeekAnalyzer
    return S


def test_both_render_fallbacks_are_reachable() -> None:
    """Both exits must be selected by the cadence, and roughly balanced."""
    S = _harness()
    seen = {}
    for seed in range(S._RENDER_FALLBACK_PERIOD):
        mode = S._RENDER_FALLBACK_SLOTS.get(seed % S._RENDER_FALLBACK_PERIOD, '')
        if mode:
            seen[mode] = seen.get(mode, 0) + 1
    assert set(seen) == {'raise', 'empty'}, f'unreachable fallback: {seen}'
    assert seen['raise'] == seen['empty'], seen


def test_render_fallback_cadence_is_not_tied_to_a_modulo_ten_selector() -> None:
    """The regression itself: `index % 10 == 0` can never reach an odd slot.

    Reconstructs the old selection exactly — cycle indexed by `index % 4`,
    gated on `index % 10 == 0` — and asserts 'empty' is unreachable, so this
    test fails if someone re-introduces that coupling.
    """
    old_cycle = ('', '', 'raise', 'empty')
    reached = set()
    for index in range(200):
        if index % 10 == 0:
            reached.add(old_cycle[index % len(old_cycle)])
    assert 'empty' not in reached, 'the old coupling was expected to miss "empty"'

    S = _harness()
    reached_now = set()
    for seed in range(200):
        mode = S._RENDER_FALLBACK_SLOTS.get(seed % S._RENDER_FALLBACK_PERIOD, '')
        if mode:
            reached_now.add(mode)
    assert reached_now == {'raise', 'empty'}, reached_now


def test_coverage_lists_every_class_even_at_zero() -> None:
    """A class that never ran must print as `=0`, not vanish."""
    S = _harness()
    cov = S._zero_coverage()
    for name in ('render_fallback_raise', 'render_fallback_empty',
                 'pack_absent_rebuild_failed', 'pack_absent_payload_broken',
                 'pack_absent_no_scrape_facts', 'operator_direction_cases'):
        assert name in cov, f'{name} missing from the initialised Counter'


def test_clean_run_with_a_dead_coverage_class_is_not_reported_clean() -> None:
    """`failed=0` plus a never-run class must exit non-zero and say why."""
    S = _harness()
    cov = S._zero_coverage()
    for name in cov:
        cov[name] = 10
    cov['render_fallback_empty'] = 0
    issues = S._coverage_defects(cov, 100000)
    assert any('render_fallback_empty=0' in i for i in issues), issues
    assert any('HARNESS DEFECT' in i for i in issues), issues

    # And a fully covered run reports nothing.
    cov['render_fallback_empty'] = 5000
    assert S._coverage_defects(cov, 100000) == []


def test_e14_fires_on_the_empty_render_fallback() -> None:
    """E14 must assert the empty-render delivery, not just the raising one.

    Drives the sweep's own E14 with a real case forced through
    `render_fallback='empty'`, then re-checks the same captured delivery
    against a mutated copy where the 31 table is gone: that must be an E14
    failure, which is what "the fallback kept the table" is worth.
    """
    S = _harness()
    case = S._build_case(20261127)
    case['render_fallback'] = 'empty'
    import tempfile
    from pathlib import Path
    hist = str(Path(tempfile.mkdtemp()) / 'h.json')
    sent, _out = S._run_once(case, hist)
    modes = case.get('_sent_modes') or []
    assert modes, 'nothing was delivered for the empty fallback'
    assert not S._check_render_fallback_html(case, modes), (
        f'empty fallback delivery violates E14: '
        f'{S._check_render_fallback_html(case, modes)}')
    # The delivery must genuinely be plain text, not HTML.
    assert all(pm is None for pm, _b in modes), [pm for pm, _b in modes]

    # Now prove the check has teeth: strip the 31 table from the body.
    stripped = [(pm, '\n'.join(ln for ln in str(b).splitlines()
                               if not ln.startswith('  ')))
                for pm, b in modes]
    defects = S._check_render_fallback_html(case, stripped)
    assert any('31' in d for _inv, d in defects), defects


def test_generated_cases_are_unstarted_at_the_frozen_clock() -> None:
    """Every generated case must be unstarted at the instant the harness pins.

    Stage 2 refuses to re-send a match whose kickoff has passed. That judgement
    must be made against `SWEEP_FROZEN_NOW`, not the real clock — the assertion
    is deliberately *not* "unstarted right now", because "right now" is exactly
    what made the headline number depend on the hour. The separate gate control
    proves the same predicate still returns False for a genuinely started match.
    """
    S = _harness()
    started = []
    for seed in (20261127, 20265890, 20269999, 20265000, 20261234):
        rec = dict(S._build_case(seed)['rec'])
        if not S.APP.is_history_match_not_started(rec, now=S.SWEEP_FROZEN_NOW):
            started.append((seed, rec.get('match_time')))
    assert not started, (
        f'generated cases have kicked off before SWEEP_FROZEN_NOW={S.SWEEP_FROZEN_NOW}: '
        f'{started}')


# ---------------------------------------------------------------------------
# The harness must not depend on what time it runs.
#
# Stage 2 refuses to re-send a match whose kickoff has passed, judged against
# `datetime.now()`. `_build_case` generates kickoffs in a fixed
# 2026-11-27..30 window, so passing real time through made the headline number
# a property of the hour: two 100k runs were green at 17:00 UTC and the same
# cases failed 2,853 times at 18:00. `_frozen_clock` pins what the app reads.
# ---------------------------------------------------------------------------
def test_same_seed_delivers_identical_text_at_two_wall_clocks() -> None:
    """Build one seed twice on machines whose real clock reads 09:00 and 23:59.

    The axis the old harness leaked on was the *process's* clock. `_frozen_clock`
    must win over it: the app still sees one fixed instant, so the same seed
    produces the same delivery no matter when the sweep is started.

    Note this patches the real clock the app module would read, then lets the
    harness's own `_frozen_clock` override it — that ordering is the property
    under test. Patching the *frozen* instant instead would just be a different
    test (and at 23:59 the generated matches really have started).
    """
    from datetime import datetime as _dt
    import tempfile

    S = _harness()
    real_datetime = S.APP.datetime
    outputs = []
    for wall in (_dt(2026, 11, 27, 9, 0), _dt(2026, 11, 27, 23, 59)):
        class _RealClockAt(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return wall

        with mock.patch.object(S.APP, 'datetime', _RealClockAt):
            case = S._build_case(20261127)
            hist = str(Path(tempfile.mkdtemp()) / 'h.json')
            sent, _out = S._run_once(case, hist)
            outputs.append([t for t in sent if t])
    assert outputs[0] == outputs[1], (
        'delivery depends on what time the process started: '
        f'{len(outputs[0])} vs {len(outputs[1])} messages')
    assert outputs[0], 'nothing was delivered at all'


def test_frozen_clock_keeps_the_started_gate_live() -> None:
    """Freezing "now" must not disable the started-match gate.

    The gate control asserts that a kickoff before the frozen instant is
    refused and nothing is delivered. Forcing the gate open must make it fail.
    """
    S = _harness()
    import tempfile
    case = S._build_case(20261127)
    case['started_control'] = True
    for key in ('match_time', 'time', 'kickoff_time'):
        case['rec'][key] = S.SWEEP_STARTED_KICKOFF
    hist = str(Path(tempfile.mkdtemp()) / 'h.json')
    sent, out = S._run_once(case, hist)
    assert not [t for t in sent if t], 'the started match was delivered anyway'
    assert S._STARTED_SKIP_MARKER in out, 'the gate did not report its refusal'
    assert S._check_report(case, sent, out) == [], S._check_report(case, sent, out)

    # And the control has teeth: with the gate forced open it must complain.
    with mock.patch.object(S.APP, 'is_history_match_not_started',
                           lambda *a, **k: True):
        sent2, out2 = S._run_once(case, hist)
    details = [d for _i, d in S._check_report(case, sent2, out2)]
    assert 'gate_control_did_not_fire' in details, details
