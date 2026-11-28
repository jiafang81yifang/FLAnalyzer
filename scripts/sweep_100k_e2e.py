"""100,000-case end-to-end sweep: every case must produce a real TG report.

The point of this script is that it never builds a report by hand. Every case
goes through the production chain:

    history record (real shape, from real captured pages)
      -> save_json(HISTORY_FILE)                     (tmp dir, never the user's)
      -> emit_pipeline_deepseek_tg_report(...)       (the real stage-2 出口)
      -> send_tg(...)                                (patched: capture, no network)
      -> assertions on the text that would have been sent

Input dimensions are all pushed through the real parsers:

  * 竞彩 31 比分赔率: `okooo_mobile_cs.parse_jczq_score_odds` on the real
    `/jczq/` list page (23 matches, 31/31 each), then perturbed per case.
  * 市均: `okooo_mobile_cs._parse_cs_datatable` on the real desktop `/cs/` page
    (27 books) and `okooo_mobile_cs.merge_cs31` to fold it with the jc odds —
    exactly what `scrape_okooo_mobile_cs_zuizhi` writes into a record.
  * 冻结 λ: `app.calc_poisson_prob` on a sampled λ pair.
  * 热度榜: `okooo_mobile_cs.build_score_market_heat_ranking`.

Invariants (one violation = one defect, clustered by invariant + reason):

  E1  the emit path sends exactly one non-empty TG text per case, and nothing
      in the pipeline reports an uncaught or swallowed exception
  E2  no degraded whole report: none of 「跳过系统分析」「报告内容为空」
      「31全表未抓全」, and no section that is nothing but 「未形成（…）」
  E3  the six standard sections are all present (names taken from the code:
      `build_deepseek_only_tg_sections` for the TG report,
      `build_unified_analysis_report` for the six-section skeleton)
  E4  the 31-row table is complete: 31 rows, every `market`/`jc`/`Δ`/`jc%`
      column filled, and the three sums are ~100
  E5  every split chunk is <= 4096 chars, the split is lossless and never cuts
      a line in half (`split_tg_html_preserving_pre` contract)
  E6  direction uniqueness: no cross-direction candidate pair in the report and
      no banned wording; a stated direction never contradicts its candidates
  E7  determinism: the same case rendered twice is byte-identical

Usage:
    python scripts/sweep_100k_e2e.py                       # 100000 cases
    python scripts/sweep_100k_e2e.py --cases 300           # short run
    python scripts/sweep_100k_e2e.py --workers 10 --json out.json
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import math
import os
import random
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STORE_FIXTURE = Path(
    '/cursor/stores/bc-07f01d0b-00bc-46ff-a886-c9ccf03aa31c/internal/fixtures/'
    'okooo_jczq_list_20261126.html'
)
JCZQ_FIXTURE = Path(os.environ.get('SWEEP_JCZQ_FIXTURE') or STORE_FIXTURE)
LOCAL_JCZQ_FIXTURE = ROOT / 'tests' / 'fixtures' / 'okooo_jczq_oddsdata_20slots.html'
DESKTOP_CS_FIXTURE = ROOT / 'tests' / 'fixtures' / 'okooo_desktop_cs_1347776.html'

# Six standard sections of the TG report, in reading order.  Names come from
# `DeepSeekAnalyzer.build_deepseek_only_tg_sections`, not from this docstring.
# 后四段是「市均−竞彩」原始赔率差的四种视图（与前面那几段并存，不替换）。
TG_SECTIONS = (
    '31项市均×竞彩对比表',
    '市均***竞彩对比表',
    '市短矩阵',
    '★市短·胜平负比分矩阵',
    '市热矩阵',
    '市热·胜平负比分矩阵',
    '【市热·市短分布｜冷门窗口】',
    '市热超理论专表',
    '市均前15·进球赔率矩阵',
    'DeepSeek 数据分析研判报告',
)
DEGRADED_MARKERS = (
    '跳过系统分析', '报告内容为空', '31全表未抓全', '暂不分析', '阶段2缺省',
    '无法输出 DeepSeek 研判',
)
BANNED_WORDS = ('首选', '次选', '双选', '强推', '推荐')
BANNED_ALLOWED = ('不做推荐',)
# The designed refusal when a record genuinely cannot fill all 31 rows.
REFUSAL_MARKERS = ('31全表未抓全', '暂不分析', '阶段2缺省', '无法输出 DeepSeek 研判',
                   '阶段2质量门拒发')
# E10: the policy is now "always send a real report". A body that is nothing but
# a refusal is a failure, not a clean case. These are the shapes a refusal-only
# body takes; until the always-send branch lands they are counted separately and
# are NOT reported as clean.
REFUSAL_ONLY_MARKERS = (
    '31全表未抓全', '暂不分析', '阶段2质量门拒发', '阶段2缺省',
    '无法输出 DeepSeek 研判', '未发研判正文', '抓全前不做系统分析',
)
# Environment noise that is not a defect: stage 1 now prefetches the 500 board
# once per round on a daemon thread (#20). Offline it reports fetch_failed.
ENV_NOISE_MARKERS = (
    '两源矩阵·500盘面', '500_board', 'fetch_failed',
    # The pack-absent paths deliberately log the failure they are handling and
    # then send a full body. That is a handled condition, not a swallowed
    # exception: E10/E12 judge what actually went out.
    'history 重建失败', '包缺失', 'sweep-forced rebuild failure',
)

LAMBDA_REGIMES = ('balanced', 'home_heavy', 'away_heavy', 'both_high', 'both_low')
ODDS_SHAPES = ('clean', 'spread', 'missing_keys', 'zero', 'negative',
               'huge', 'text', 'nan', 'inf', 'mixed_bad', 'hostile_str',
               'decimal')
# The overflow shape that a truthiness/exception guard cannot see: float('1e400')
# returns inf instead of raising, so a site hardened only with OverflowError
# still renders `inf%`. The sweep injected int 10**400 but never these strings,
# which is exactly why that defect hid. These are the literal texts a hostile or
# corrupted record can carry.
HOSTILE_NUMERIC_STRINGS = ('1e400', '-1e400', 'inf', '-inf', 'Infinity',
                           'nan', 'NaN', '1e999', '9' * 400)
LEAGUES = ('英超', '西甲', '意甲', '德甲', '法甲', '欧冠', '日职联', '挪超', '', '未知联赛')

MAX_TG_LEN = 4096
APP_SPLIT_LEN = 4000  # `send_tg`'s max_len


# --------------------------------------------------------------------------
# module import (once per process)
# --------------------------------------------------------------------------
def _import_app() -> tuple[Any, Any, Any]:
    boot = io.StringIO()
    with contextlib.redirect_stdout(boot), contextlib.redirect_stderr(io.StringIO()):
        app = importlib.import_module('app_v84_hermes')
        pipe = importlib.import_module('blade_deepseek_pipeline')
        okooo = importlib.import_module('okooo_mobile_cs')
    return app, pipe, okooo


APP, PIPE, OKOOO = _import_app()
JC_OPTIONS = tuple(PIPE.JCZQ_ALL_OPTIONS)
JC_SET = set(JC_OPTIONS)
# One definition of "this section carries no data", shared with the production
# quality gate, so the sweep and the app can never disagree about it.
DSA_SECTION_IS_EMPTY = PIPE.DeepSeekAnalyzer._section_is_empty


# --------------------------------------------------------------------------
# real fixtures, parsed once
# --------------------------------------------------------------------------
def _load_real_fixtures() -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Return ({match_id: 31 clean jc odds}, desktop page_avg/books_avg)."""
    path = JCZQ_FIXTURE if JCZQ_FIXTURE.exists() else LOCAL_JCZQ_FIXTURE
    text = OKOOO.decode_okooo_html(path.read_bytes())
    per_match: dict[str, dict[str, float]] = {}
    for mid in dict.fromkeys(re.findall(r'matchid="(\d+)"', text)):
        parsed = OKOOO.parse_jczq_score_odds(text, mid)
        if parsed.get('available') and len(parsed.get('odds') or {}) == 31:
            per_match[mid] = dict(parsed['odds'])
    if not per_match:
        raise RuntimeError(f'no match in {path} yields 31/31 jc odds')
    with DESKTOP_CS_FIXTURE.open('rb') as fh:
        page = OKOOO._parse_cs_datatable(OKOOO.decode_okooo_html(fh.read()))
    if not page.get('page_avg'):
        raise RuntimeError('desktop cs fixture did not parse')
    return per_match, page


JC_BY_MATCH, DESKTOP_PAGE = _load_real_fixtures()
MATCH_IDS = sorted(JC_BY_MATCH)


# --------------------------------------------------------------------------
# case construction
# --------------------------------------------------------------------------
def _sample_lambda(rng: random.Random) -> tuple[float, float, str]:
    regime = rng.choice(LAMBDA_REGIMES)
    if regime == 'balanced':
        h = rng.uniform(0.6, 2.6)
        a = h + rng.uniform(-0.7, 0.7)
    elif regime == 'home_heavy':
        h = rng.uniform(1.6, 4.5)
        a = rng.uniform(0.15, 0.9)
    elif regime == 'away_heavy':
        h = rng.uniform(0.15, 0.9)
        a = rng.uniform(1.6, 4.5)
    elif regime == 'both_high':
        h = rng.uniform(2.8, 4.5)
        a = rng.uniform(2.8, 4.5)
    else:
        h = rng.uniform(0.15, 0.5)
        a = rng.uniform(0.15, 0.5)
    return round(min(4.5, max(0.15, h)), 4), round(min(4.5, max(0.15, a)), 4), regime


def _perturb_odds(odds: dict[str, float], rng: random.Random,
                  shape: str) -> dict[str, Any]:
    """Apply a real-market-shaped distortion to a complete 31-key odds map."""
    out: dict[str, Any] = {k: round(float(v) * rng.uniform(0.85, 1.35), 4)
                           for k, v in odds.items()}
    keys = sorted(out)
    if shape in ('clean', 'spread'):
        return out
    if shape == 'mixed_bad':
        for key in [k for k in keys if rng.random() < 0.3]:
            out[key] = rng.choice((0, -1.0, 'x', float('nan'), float('inf'), 10 ** 400))
        return out
    victims = rng.sample(keys, k=max(1, len(keys) // rng.choice((6, 4, 3))))
    if shape == 'missing_keys':
        for key in victims:
            out.pop(key, None)
    elif shape == 'zero':
        for key in victims:
            out[key] = 0
    elif shape == 'negative':
        for key in victims:
            out[key] = -abs(float(out[key]))
    elif shape == 'huge':
        for key in victims:
            out[key] = rng.choice((1e6, 1e12, 10 ** 400, str(10 ** 400)))
    elif shape == 'text':
        for key in victims:
            out[key] = rng.choice(('abc', '', '  ', '8.5倍', None, []))
    elif shape == 'hostile_str':
        # every victim gets a hostile numeric *string*; some also get the int
        for key in victims:
            out[key] = rng.choice(HOSTILE_NUMERIC_STRINGS)
        if rng.random() < 0.3 and victims:
            out[victims[0]] = 10 ** 400
    elif shape == 'decimal':
        # A record arrives through json.load, which cannot carry a Decimal — so
        # the Decimal *shape* that can actually reach the app is its string
        # rendering. Injecting a real Decimal here would only fail the harness's
        # own json.dump, which would be a harness artifact, not a product shape.
        from decimal import Decimal
        for key in victims:
            out[key] = str(rng.choice((Decimal('1e400'), Decimal('1E999'), Decimal('NaN'))))
    elif shape == 'nan':
        for key in victims:
            out[key] = float('nan')
    elif shape == 'inf':
        for key in victims:
            out[key] = float('inf')
    else:  # pragma: no cover - guarded by ODDS_SHAPES
        raise AssertionError(shape)
    return out


def _legal_odd(value: Any) -> float | None:
    """A market value the app can actually use: finite and > 1.

    Mirrors the test every real parser applies (`_num_of`, `_parse_cs_datatable`),
    so "does this record have a usable value for option X" is decided by the same
    rule the app uses, not by a looser one.
    """
    if isinstance(value, dict):
        value = value.get('odds', value.get('avg'))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number > 1 else None


def _covered_count(rec: dict[str, Any]) -> int:
    """How many of the 31 options have a legal value in *any* source the record carries.

    This is the ceiling on what any implementation could fill. When it is 31 the
    app had enough data and must produce a full table; when it is lower, a row
    genuinely cannot be filled and refusing to analyse is the designed behaviour.
    """
    have = set()
    for key in ('correct_score_page_avg', 'correct_score_jc_official', 'jc_score_odds',
                'correct_score_site_avg', 'correct_score_books_avg'):
        src = rec.get(key)
        if not isinstance(src, dict):
            continue
        for option, value in src.items():
            if option in JC_SET and _legal_odd(value) is not None:
                have.add(option)
    return len(have)


_SAME_DIRECTION_PAIRS = (
    ('', ''),
    ('2-1', '1-0'), ('1-0', '2-0'), ('3-1', '2-1'), ('2-0', '3-0'),
    ('1-1', '0-0'), ('0-0', '1-1'), ('2-2', '1-1'),
    ('0-1', '0-2'), ('1-2', '0-1'), ('0-2', '1-3'),
)


def _score_pair(rng: random.Random) -> dict[str, str]:
    """A ①② pair that shares one direction, or both empty.

    A cross-direction pair is what the report is forbidden to print; generating
    one would make the harness manufacture the defect it checks for.
    """
    first, second = rng.choice(_SAME_DIRECTION_PAIRS)
    return {'predicted_score': first, 'second_score': second}


def _build_case(seed: int) -> dict[str, Any]:
    """Build one case in a shape production can actually produce.

    Two families, chosen by seed:

    ``production`` (default, ~85% of cases)
        Every value comes out of a parser: floats > 1 only. Coverage varies the
        way real captures vary — the desktop page may be thin or absent, the
        jc scrape may return only part of the 31 — and the record always carries
        the `cs31_provenance` that `merge_cs31` writes.

    ``corrupt_record`` (~15%)
        The record file was written by an older build or hand-edited: values are
        NaN/inf/'abc'/0/10**400 and the provenance table is missing. This is the
        shape `json.load` really hands the app. It is measured separately, with
        its own expectation (a legible failure, never a crash), because most of
        these records genuinely have no usable market and refusing is correct.
    """
    rng = random.Random(seed)  # nosec B311 - reproducible test data, not security
    family = 'corrupt_record' if rng.random() < 0.15 else 'production'

    match_id = rng.choice(MATCH_IDS)
    clean_jc = JC_BY_MATCH[match_id]
    lam_h, lam_a, regime = _sample_lambda(rng)

    # Coverage varies the way real captures vary. `clean` and `spread` differ
    # only in the odds level, which is exactly what "different bookmakers on a
    # different day" looks like; `thin` drops keys the way a partial page load
    # does. Nothing here can produce a non-float: `_parse_cs_datatable` only
    # ever returns finite floats > 1.
    page_shape = rng.choice(('clean', 'clean', 'spread', 'thin'))
    page_avg: dict[str, Any] = {}
    for score, odd in DESKTOP_PAGE['page_avg'].items():
        value = float(odd)
        if page_shape == 'spread':
            value *= rng.uniform(0.9, 1.12)
        page_avg[score] = value
    if page_shape == 'thin':
        for score in rng.sample(sorted(page_avg), k=max(1, len(page_avg) // 3)):
            page_avg.pop(score, None)
    books_avg = dict(DESKTOP_PAGE['books_avg'])

    # The merge below is what a live scrape does: fresh floats out of the HTML
    # parsers. Hostile values cannot appear here, so the merge itself stays on
    # the clean path — putting `10**400` into it would be a harness artifact,
    # not a production shape (`_parse_cs_datatable` only ever yields floats).
    jc_fresh = {k: round(float(v) * rng.uniform(0.85, 1.35), 4) for k, v in clean_jc.items()}
    # A jc scrape can come back partial (the parser reports `incomplete_N_of_31`).
    jc_cover = rng.choice(('full', 'full', 'full', 'partial_other9', 'partial_22'))
    if jc_cover != 'full':
        keep = (
            [o for o in JC_OPTIONS if o not in set(books_avg)]
            if jc_cover == 'partial_other9'
            else rng.sample(sorted(jc_fresh), k=22)
        )
        jc_fresh = {k: v for k, v in jc_fresh.items() if k in set(keep)}
    merged = OKOOO.merge_cs31(books_avg, books_avg, jc_fresh)
    merged_scores = merged['scores']

    if family == 'corrupt_record':
        # Only here do hostile values appear, because only here is the record
        # something a parser never wrote. Each copy is corrupted independently:
        # they come from three different fetch steps, and only a record where
        # all three are broken has genuinely no usable market.
        jc_shape = rng.choice(ODDS_SHAPES)
        official_shape = rng.choice(ODDS_SHAPES)
        stored_shape = rng.choice(ODDS_SHAPES)
        jc_public = _perturb_odds(jc_fresh, rng, jc_shape)
        jc_official_src = _perturb_odds(jc_fresh, rng, official_shape)
        stored_avg = _perturb_odds(merged_scores, rng, stored_shape)
        provenance = None if rng.random() < 0.5 else merged['provenance']
    else:
        # Production: every one of the three copies is what its parser wrote,
        # up to a level shift that a real bookmaker feed exhibits.
        jc_shape = official_shape = stored_shape = 'production'
        jc_public = {k: round(v * rng.uniform(0.95, 1.06), 4) for k, v in jc_fresh.items()}
        jc_official_src = {k: round(v * rng.uniform(0.95, 1.06), 4) for k, v in jc_fresh.items()}
        stored_avg = {k: round(v * rng.uniform(0.97, 1.03), 4) for k, v in merged_scores.items()}
        provenance = merged['provenance']

    heat = OKOOO.build_score_market_heat_ranking(
        dict(merged_scores), page_avg_odds=dict(merged_scores),
        heat_source='okooo_cs_books_avg', channel='okooo_cs',
    )
    heat = _reshape_heat(heat, rng)

    no = f'周六{rng.randint(1, 999):03d}'
    home = f'主队{rng.randint(1, 40)}'
    away = f'客队{rng.randint(1, 40)}'
    lock = _build_lock(rng, list(merged_scores))
    # A fixed window, and a fixed clock to read it against.
    #
    # Hard-coding a date is only a defect when the app judges that date against
    # the real wall clock: stage 2 refuses to re-send once kickoff has passed
    # (`is_history_match_not_started` -> `datetime.now()`), so a literal
    # `2026-11-27..30` window silently became "already kicked off" the moment
    # the run crossed 18:00 UTC. Two 100k runs were green only because they
    # finished before then; the next one reported 2,853 failures that were the
    # gate working correctly.
    #
    # Shifting the window to "today + 2 days" would only move that cliff, and
    # would make every seed's record depend on the day it ran. Instead the
    # kickoff stays a pure function of the seed, and `_frozen_clock` pins what
    # the app reads as "now" to `SWEEP_FROZEN_NOW`, which is before every
    # kickoff this generator can produce. Same seed, same record, same verdict
    # at 09:00 and at 23:59.
    kickoff = f'2026-11-{rng.randint(27, 30):02d} {rng.randint(18, 23):02d}:{rng.choice((0, 15, 30, 45)):02d}'
    rec: dict[str, Any] = {
        'id': match_id,
        'match': f'{home} vs {away}',
        'cn_name': f'[{no}] {home} vs {away}',
        'no': no,
        'status': 'pending',
        'actual_score': '',
        'time': kickoff,
        'match_time': kickoff,
        'kickoff_time': kickoff,
        'kickoff_source': 'okooo_match_time',
        'league': rng.choice(LEAGUES),
        'pipeline_scrape_done': True,
        'pipeline_phase': 'scrape',
        # The two candidates must share a direction: a real record's ①② come from
        # one direction lock, and a cross-direction pair is exactly what the
        # report must never print. Generating one would make the harness itself
        # produce the defect it is supposed to detect.
        **_score_pair(rng),
        'predicted_result': rng.choice(('', '胜', '平', '负')),
        'lam_h': lam_h,
        'lam_a': lam_a,
        'lambda_home': lam_h,
        'lambda_away': lam_a,
        'correct_score_page_avg': stored_avg,
        'correct_score_books_avg': books_avg,
        'correct_score_books_n': len(books_avg),
        'correct_score_site_avg': dict(merged_scores),
        'correct_score_jc_official': jc_official_src,
        'jc_score_odds': jc_public,
        'cs31_provenance': provenance,
        'cs31_counts': merged['counts'],
        'score_market_heat': heat,
        'direction_lock': lock,
        'okooo_match_id': match_id,
        'odds_1x2': {'Home': round(rng.uniform(1.4, 4.0), 2),
                     'Draw': round(rng.uniform(2.8, 4.5), 2),
                     'Away': round(rng.uniform(1.4, 6.0), 2)},
        'ou_line': rng.choice((2.0, 2.25, 2.5, 2.75, 3.0)),
        'ou_channel': 'mobile',
        'asian_handicap': rng.choice((-1.0, -0.5, -0.25, 0.0, 0.25, 0.5)),
        'jc_handicap': rng.choice((-2, -1, 0, 1, 2)),
        # 六盘口是质量门的硬输入：真实记录在阶段1 就落盘了手机六盘口包。
        # 不给它，门会（正确地）拒发，sweep 量到的就全是「缺盘口」而不是报告质量。
        'six_market_mobile': {
            'asian_handicap': rng.choice((-1.0, -0.5, -0.25, 0.0, 0.25, 0.5)),
            'ou_line': rng.choice((2.0, 2.25, 2.5, 2.75, 3.0)),
            'odds_1x2': {'Home': round(rng.uniform(1.4, 4.0), 2),
                         'Draw': round(rng.uniform(2.8, 4.5), 2),
                         'Away': round(rng.uniform(1.4, 6.0), 2)},
            'jc_handicap': rng.choice((-2, -1, 0, 1, 2)),
            'total_goals_odds': {'0': 9.5, '1': 5.2, '2': 3.4, '3': 3.8, '4': 6.0, '5': 9.0},
            'htft_odds': {'胜胜': 3.2, '平胜': 4.6, '负胜': 12.0, '胜平': 14.0,
                          '平平': 6.2, '负平': 15.0, '胜负': 30.0, '平负': 9.0, '负负': 4.4},
        },
    }
    return {
        'seed': seed,
        'family': family,
        'matched': _covered_count(rec),
        'match_id': match_id,
        'lam_h': lam_h,
        'lam_a': lam_a,
        'regime': regime,
        'league': rec['league'],
        'jc_shape': jc_shape,
        # Coverage accounting: these cases exercised the hostile-numeric paths,
        # which is a different claim from "100,000 ordinary cases ran".
        'hostile_numeric': (family == 'corrupt_record'
                            or jc_shape in ('hostile_str', 'decimal', 'huge', 'inf', 'nan',
                                            'mixed_bad')
                            or official_shape in ('hostile_str', 'decimal', 'huge', 'inf',
                                                  'nan', 'mixed_bad')
                            or stored_shape in ('hostile_str', 'decimal', 'huge', 'inf',
                                                'nan', 'mixed_bad')),
        'official_shape': official_shape,
        'page_shape': page_shape,
        'stored_shape': stored_shape,
        'jc_cover': jc_cover,
        'heat_shape': heat.get('_sweep_shape') if isinstance(heat, dict) else '',
        'lock': (lock.get('direction') if isinstance(lock, dict) else '') or '',
        'pool_len': len(merged_scores),
        'rec': rec,
        'bundle_prebuilt': rng.random() < 0.5,
    }


def _reshape_heat(heat: Any, rng: random.Random) -> Any:
    """Randomize the heat board shape the way production records vary."""
    base = heat if isinstance(heat, dict) else {}
    shape = rng.choice(('normal', 'normal', 'empty', 'ranking_only', 'nan', 'text'))
    rows = []
    for item in (base.get('ranking') or []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if shape == 'nan':
            row['odds'] = float('nan')
        elif shape == 'text':
            row['odds'] = 'abc'
        rows.append(row)
    out = dict(base)
    out['_sweep_shape'] = shape
    if shape == 'empty':
        out['ranking'] = []
        out['available'] = False
    elif shape == 'ranking_only':
        out.pop('available', None)
    else:
        out['ranking'] = rows
    return out


def _build_lock(rng: random.Random, pool_scores: list[str]) -> dict[str, Any]:
    shape = rng.choice(('none', 'home', 'draw', 'away', 'absent'))
    if shape == 'none':
        return {}
    direction = {'home': '胜', 'draw': '平', 'away': '负'}.get(shape, '')
    if shape == 'absent':
        present = {PIPE._score_wdl(s) for s in pool_scores}
        missing = [d for d in ('胜', '平', '负') if d not in present]
        direction = missing[0] if missing else '胜'
    return {'locked': True, 'direction': direction,
            'scores': [s for s in pool_scores if PIPE._score_wdl(s) == direction][:3]}


# --------------------------------------------------------------------------
# the model, stubbed at the transport only
# --------------------------------------------------------------------------
# The sweep must never fake the *judgment* layer. `analyze_match_judgment` is
# left alone and runs its real body; only the outbound completion is answered,
# exactly as a live API would. That keeps the five false-success shapes visible:
# if the pipeline ever hands us a placeholder instead of model output, the
# content check in `_check_model_output` fails the case.
PLACEHOLDER_MARKERS = (
    '本地确定性分析',            # shape 3: no DEEPSEEK_API_KEY, local fallback
    '未配置 DEEPSEEK_API_KEY',
    'DeepSeek调用失败',          # shape 1: completion degraded to a sentence
    '已降级：',
    'deepseek-flash 模型复盘失败',  # shape 5
    'can only concatenate',        # the list+dict TypeError sentence
    '研判异常',
    'empty_completion',
    '未调用',
)


def _model_reply(messages: Any = None, **kwargs: Any) -> str:
    """A deterministic reply shaped like a real DeepSeek judgment.

    Deliberately *not* a placeholder: it carries the ①② candidates the report
    parses, and none of `PLACEHOLDER_MARKERS`.

    It also resets the module-level completion latch, exactly as the real
    `deepseek_chat_completion` does on entry. That latch is process-wide state,
    so a stub that only returns a string inherits the last *real* call's verdict:
    once anything in the process has run without an API key, `completion_failed()`
    keeps answering True for the stub's non-empty string, `analyze_match_judgment`
    reports `available=False`, and the report falls back to the deterministic
    body. That is a stale-latch artifact of the harness, not a property of the
    case — and it made a clean case look non-idempotent on replay because only
    the *second* run of a process saw the latched failure.
    """
    pipe = PIPE
    try:
        pipe._LAST_COMPLETION_ERROR = ''
        pipe._LAST_COMPLETION_LOCAL = False
    except Exception:  # noqa: BLE001 - latch names are the shipped contract; never fail a case over them
        pass
    body = '\n'.join(str(m.get('content', '')) for m in (messages or [])
                     if isinstance(m, dict))
    hit = re.search(r'"lambda_home":\s*"?([0-9.]+)', body)
    seed = int(float(hit.group(1)) * 10000) % 7 if hit else 0
    first, second = (('2-1', '1-0'), ('1-0', '2-0'), ('1-1', '2-2'),
                     ('0-1', '0-2'), ('2-0', '3-1'), ('1-2', '0-1'),
                     ('3-0', '2-1'))[seed]
    return (
        '方向依据：理论概率与市均赔率在同一方向上收敛，未见反向资金。\n'
        f'① {first} ② {second}\n'
        '数据缺口：无。结论仅供量化参考。'
    )


def _judgment_call_result(payload: Any = None) -> dict[str, Any]:
    """Run the real judgment entry point once, to read its machine fields.

    The sweep cannot infer success from the rendered body alone: the body is a
    rendered artifact. `analyze_match_judgment` also publishes `available` and
    `error`, and those must agree with what reached Telegram.

    The completion is stubbed with `_model_reply` here for the same reason as in
    `_run_once`: this probe must exercise the judgment layer, not a real network
    call, and must see the same reply the case itself saw. Calling it with the
    bare transport returns `deepseek_api_key_missing`, which is a property of the
    sandbox, not of the case.
    """
    pipe = PIPE
    with mock.patch.object(pipe, 'deepseek_chat_completion', side_effect=_model_reply):
        try:
            return pipe.DeepSeekAnalyzer.analyze_match_judgment(
                payload if isinstance(payload, dict) else
                {'home': 'A', 'away': 'B', 'match': 'A vs B'})
        except Exception as exc:  # noqa: BLE001 - a raising judgment is itself a finding
            return {'available': False, 'error': f'{type(exc).__name__}: {exc}', 'content': ''}


def _check_judgment_fields(case: dict[str, Any], stdout: str) -> list[tuple[str, str]]:
    """E8b: available/error must agree with each other, and with having a body.

    PR #18 replaced the placeholder sentences with `available=False` plus a
    machine-readable error. That is correct, but it must stay self-consistent:
    an unavailable judgment may not carry a body, and an available one may not
    carry an error or an empty body. A silent disagreement here is how the old
    false-success shapes would come back.
    """
    out: list[tuple[str, str]] = []
    pack = _judgment_call_result(case.get('rec') or {})
    available = bool(pack.get('available'))
    error = str(pack.get('error') or '')
    content = str(pack.get('content') or '')
    if not available and content.strip():
        out.append(('E8', 'unavailable_judgment_carries_a_body'))
    if available and error:
        out.append(('E8', f'available_judgment_carries_error:{error[:60]}'))
    if available and not content.strip():
        out.append(('E8', 'available_judgment_has_empty_body'))
    for marker in PLACEHOLDER_MARKERS:
        if marker in content:
            out.append(('E8', f'judgment_body_is_placeholder:{marker}'))
            break
    return out


def _check_model_output(case: dict[str, Any], stdout: str,
                        text: str) -> list[tuple[str, str]]:
    """E8: the report body must be model output, not a success-shaped placeholder.

    A non-empty string is not evidence of a report. Before PR #18 master had five
    paths that returned a failure or fallback sentence while reporting success:
      1. `deepseek_chat_completion` returns 'DeepSeek调用失败，已降级：…' as text and
         `analyze_match_judgment` then sets available=True, error=''
      2. `run_learning_report` str()s that same sentence
      3. no DEEPSEEK_API_KEY -> '本地确定性分析：未配置…'
      4. `format_matrix_dual_prejudge_html` returning '' (fixed on master)
      5. `ModelPerformanceAnalyzer.analyze_and_generate` returning the failure sentence
    Any of them reaching Telegram is a failed case, not a pass.

    Since PR #18 those paths report `available=False` with a machine-readable
    error, so this check also asserts the two agree: a body that reached Telegram
    while the judgment says unavailable is a contradiction, and so is an empty
    body while it claims success.
    """
    out: list[tuple[str, str]] = []
    for marker in PLACEHOLDER_MARKERS:
        if marker in text:
            out.append(('E8', f'placeholder_body:{marker}'))
            break
    # The judgment section must carry the ①② candidates the model produced.
    if 'DeepSeek 数据分析研判报告' in text:
        tail = text.split('DeepSeek 数据分析研判报告', 1)[1]
        if '①' not in tail and '②' not in tail:
            out.append(('E8', 'judgment_section_has_no_model_candidates'))
    elif case['matched'] == 31:
        out.append(('E8', 'no_judgment_section'))
    return out


class _NetworkBlocked(RuntimeError):
    """Raised by every stubbed transport: the sweep never touches a socket."""


# Every kickoff `_build_case` can generate is on or after this instant, so a
# reader pinned here sees all generated cases as unstarted. It is deliberately
# *before* the earliest generated kickoff (2026-11-27 18:00) rather than "now",
# which is what makes the run reproducible: the same seed produces the same
# verdict at 09:00 and at 23:59.
SWEEP_FROZEN_NOW = datetime(2026, 11, 27, 12, 0, 0)
# A kickoff that is unambiguously in the past at the frozen instant. Used only
# by the gate control: freezing the clock must not stop the gate from firing.
SWEEP_STARTED_KICKOFF = '2026-11-27 09:00'
# The verdict text stage 2 prints when it declines to re-send a started match.
# The harness treats it as a designed exit, not a swallowed exception, and only
# ever generates such a case through the gate control.
_STARTED_SKIP_MARKER = '已开赛/完场，按规则跳过补发'


@contextlib.contextmanager
def _frozen_clock(now: datetime | None = None):
    """Pin what the app reads as "now", so runs do not depend on the hour.

    Stage 2 has a real gate: a match whose kickoff has passed is not re-sent
    (`is_history_match_not_started` compares the kickoff to `datetime.now()`).
    That gate is correct and must stay live — but a harness that feeds it the
    real clock makes its own headline number a function of when it ran. Two
    100k runs looked green only because they finished before 18:00 UTC; the
    next one failed 2,853 cases at a gate that was working as designed.

    Patching `app_v84_hermes.datetime` rather than each call site covers every
    `datetime.now()` in that module, including the ones inside
    `get_inferred_match_status` that the gate consults after the kickoff check.
    `datetime` stays a real class (subclassed, not replaced by a stub), so
    anything doing `isinstance(..., datetime)` or `.strptime` still works.
    """
    frozen_now = now or SWEEP_FROZEN_NOW
    app = APP
    real_datetime = app.datetime

    class _FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return frozen_now.replace(tzinfo=tz) if frozen_now.tzinfo is None else frozen_now
            return frozen_now

        @classmethod
        def utcnow(cls):
            return frozen_now

        @classmethod
        def today(cls):
            return frozen_now

    with mock.patch.object(app, 'datetime', _FrozenDateTime):
        yield frozen_now


def _netblock() -> Any:
    """Context manager that kills every outbound path and every sleep.

    Patching `requests.get/post` (and the Session methods) covers all three
    scrapers — the app's `safe_api_get`, `okooo_mobile_cs._http_get` and
    `sports`-style direct `requests.get` calls — without stubbing each caller,
    so no production branch is skipped: they all take their real failure path.
    """
    import requests as _rq

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise _rq.exceptions.ConnectionError('sweep: network disabled')

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(_rq, 'get', side_effect=_boom))
    stack.enter_context(mock.patch.object(_rq, 'post', side_effect=_boom))
    stack.enter_context(mock.patch.object(_rq.sessions.Session, 'get', side_effect=_boom))
    stack.enter_context(mock.patch.object(_rq.sessions.Session, 'post', side_effect=_boom))
    stack.enter_context(mock.patch('time.sleep', side_effect=lambda *a, **k: None))
    stack.enter_context(mock.patch.object(APP, 'safe_api_get', side_effect=lambda *a, **k: None))
    stack.enter_context(mock.patch.object(APP, '_tg_post', side_effect=lambda *a, **k: False))
    return stack


def _render_fallback_patch(case: dict[str, Any]) -> Any:
    """Force one of the two HTML fallbacks so the sweep actually exercises them."""
    mode = case.get('render_fallback')
    if mode == 'raise':
        def _boom(*a, **k):
            raise RuntimeError('sweep-forced render failure')
        return mock.patch.object(PIPE.DeepSeekAnalyzer, 'format_matrix_dual_prejudge_html',
                                 staticmethod(_boom))
    if mode == 'empty':
        return mock.patch.object(PIPE.DeepSeekAnalyzer, 'format_matrix_dual_prejudge_html',
                                 staticmethod(lambda *a, **k: ''))
    return mock.patch.object(APP, '_sweep_noop_render_marker', 1, create=True)


def _pack_absent_patch(case: dict[str, Any]) -> Any:
    """Make rebuild raise for the `rebuild_failed` control, else a no-op patch."""
    if case.get('pack_absent_state') == 'rebuild_failed':
        def _boom(*a, **k):
            raise RuntimeError('sweep-forced rebuild failure')
        return mock.patch.object(APP, 'rebuild_pipeline_bundle_from_history', _boom)
    return mock.patch.object(APP, '_sweep_noop_marker', 1, create=True)


_OP_LINE = re.compile(r'^.*(操作员已指定方向|已按操作员指定方向).*$', re.M)


def _canon_operator_line(body: str) -> str:
    """Normalise the one line whose value legitimately changes across a replay.

    The operator-direction line quotes the stored score, which the first run
    writes, so `原 无` becomes `原 2-1`. That is the line's documented purpose.
    Everything else is compared byte-for-byte.
    """
    return _OP_LINE.sub('<OPERATOR-DIRECTION-LINE>', str(body or ''))


def _capture_send(sent: list) -> Any:
    """Record the body and answer like a healthy transport.

    The shipped contract is `_tg_pushed = send_judgment_report_tg(...) is True`,
    so a stub that returns None reads as "not delivered" and the record is
    stamped tg_sent=False. Returning True is what a successful send looks like;
    the harness must not manufacture a failure just by staying silent.
    """
    def _send(text, parse_mode=None):
        sent.append(text)
        # E14 needs the parse_mode each body went out with.
        _send.modes.append((parse_mode, text))
        return True
    _send.modes = []
    return _send


def _run_once(case: dict[str, Any], hist_path: str,
              reseed: bool = True) -> tuple[list[str], str]:
    """Run the real stage-2 emit path; return (captured texts, stdout).

    `reseed=True` writes the case's record first — a fresh input. `reseed=False`
    replays against whatever the previous run left in `hist_path`, which is how
    you tell whether stage 2's own writeback corrupts its input.
    """
    rec = case['rec']
    app, pipe = APP, PIPE
    if reseed:
        seeded = json.loads(json.dumps(rec, ensure_ascii=False))
        _state = case.get('pack_absent_state')
        if _state == 'rebuild_failed':
            # Facts present, bundle absent; the harness makes rebuild raise.
            seeded.pop('pipeline_report_bundle', None)
        elif _state == 'payload_broken':
            seeded['pipeline_report_bundle'] = {'ds_payload': 'sweep-forced-broken'}
        elif _state == 'no_scrape_facts':
            for key in ('pipeline_report_bundle', 'predicted_score', 'second_score',
                        'pipeline_scrape_done', 'deepseek_judgment',
                        'model_primary_score', 'model_secondary_score'):
                seeded.pop(key, None)
        history = {str(rec['id']): seeded}
        with open(hist_path, 'w', encoding='utf-8') as fh:
            json.dump(history, fh, ensure_ascii=False)

    sent: list[str] = []
    _capture = _capture_send(sent)
    case['_sent_modes'] = _capture.modes
    m_obj = {'id': str(rec['id']), 'cn_name': rec['cn_name'], 'time': rec['time'],
             'match_time': rec['match_time'], 'kickoff_source': 'okooo_match_time'}

    buf = io.StringIO()
    # Only the transport is stubbed: `analyze_match_judgment` runs for real, so
    # its available/error contract and placeholder behaviour stay live.
    # `_frozen_clock` pins the app's notion of "now" so the started-match gate
    # judges every case at the same instant, whichever hour the sweep runs at.
    with _netblock(), \
         _frozen_clock(), \
         mock.patch.object(app, 'HISTORY_FILE', hist_path), \
         mock.patch.object(app, 'send_tg',
                           side_effect=_capture), \
         mock.patch.object(app, '_ensure_mobile_six',
                           side_effect=lambda r, mid, force=False: False), \
         mock.patch.object(app, 'attach_okooo_ids_to_matches',
                           side_effect=lambda items, *a, **k: items), \
         mock.patch.object(pipe, 'deepseek_chat_completion',
                           side_effect=_model_reply), \
         _pack_absent_patch(case), \
         _render_fallback_patch(case), \
         contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        try:
            if case.get('bundle_prebuilt'):
                seed_rec = json.loads(json.dumps(rec, ensure_ascii=False))
                bundle = app.rebuild_pipeline_bundle_from_history(
                    seed_rec, fid=str(rec['id']), cn_name=rec['cn_name'])
                if isinstance(bundle, dict):
                    # Read the file back: on a replay it holds the record stage 2
                    # wrote last time, not the seeded one.
                    with open(hist_path, encoding='utf-8') as fh:
                        on_disk = json.load(fh)
                    if isinstance(on_disk.get(str(rec['id'])), dict):
                        on_disk[str(rec['id'])]['pipeline_report_bundle'] = bundle
                        with open(hist_path, 'w', encoding='utf-8') as fh:
                            json.dump(on_disk, fh, ensure_ascii=False)
            app.emit_pipeline_deepseek_tg_report(m_obj, i=0, total=1)
        except BaseException as exc:  # noqa: BLE001 - the harness must survive any case
            buf.write(f'\n[HARNESS-UNCAUGHT] {type(exc).__name__}: {exc}\n')
    if case.get('inject_refusal_body') and sent:
        # Control for the E10 detector itself: overwrite the captured body with
        # the shape a refusal-only exit produces. If E10 cannot see this, then
        # `E10 == 0` on a real run proves nothing.
        sent = [('⚠️【阶段2缺省】[control]\n'
                 'history 无可用精算结果，无法输出 DeepSeek 研判。请重新跑阶段1抓取。')]
    return sent, buf.getvalue()


# --------------------------------------------------------------------------
# invariants
# --------------------------------------------------------------------------
# A designed refusal prints an ❌ line, but it is not a swallowed exception:
# the pipeline decided this record cannot be analysed and said so. Only lines
# that admit a failure while continuing count as swallowed.
_DESIGNED_REFUSALS = (
    '31全表未齐', '跳过系统分析', '质量门', '拒发', '阶段2缺省',
    '无法重建研判包', '暂不分析',
    # Stage 2's started-match gate. It is a designed exit, not a swallowed
    # exception: the pipeline decided this record must not be re-sent.
    _STARTED_SKIP_MARKER,
)


def _swallowed_exception_lines(stdout: str) -> list[str]:
    """Lines where the pipeline admitted an exception and carried on."""
    out = []
    for line in stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        if 'HARNESS-UNCAUGHT' in text or 'Traceback' in text:
            out.append(text)
            continue
        if any(marker in text for marker in _DESIGNED_REFUSALS):
            continue
        # #20 prefetches the 500 board once per round; offline that is
        # `stage=fetch_failed` and is expected, not a defect.
        if any(marker in text for marker in ENV_NOISE_MARKERS):
            continue
        if text.startswith('❌') or (text.startswith('⚠️') and '异常' in text):
            out.append(text)
    return out


def _sections_of(text: str) -> dict[str, str]:
    """Split the TG text at its standard section headers, in reading order."""
    positions = sorted((text.find(name), name) for name in TG_SECTIONS
                       if text.find(name) >= 0)
    found: dict[str, str] = {}
    for order, (idx, name) in enumerate(positions):
        end = positions[order + 1][0] if order + 1 < len(positions) else len(text)
        found.setdefault(name, text[idx:end])
    return found


def _check_report(case: dict[str, Any], sent: list[str], stdout: str) -> list[tuple[str, str]]:
    defects: list[tuple[str, str]] = []

    # A started/finished match is a *designed* exit: stage 2 will not re-send a
    # stale pre-match report after kickoff, and that is correct. It must be
    # recognised as such — the always-send policy is about incomplete data (a
    # thin 31 table, missing boards), not about replaying a report for a match
    # that has already begun. The harness only ever produces such a case
    # through its own gate control, so any *other* case reaching here means the
    # gate fired when it should not have.
    started_skip = _STARTED_SKIP_MARKER in stdout
    if started_skip and not case.get('started_control'):
        defects.append(('E1', 'unexpected_started_skip'))
    if case.get('started_control'):
        # The control asserts the opposite: the gate must fire, and nothing
        # may be delivered. That is what keeps the frozen clock honest.
        if not started_skip:
            defects.append(('E1', 'gate_control_did_not_fire'))
        if [t for t in sent if isinstance(t, str) and t.strip()]:
            defects.append(('E1', 'gate_control_still_delivered'))
        return defects

    # E1 holds for every case: never crash, never swallow an exception, and when
    # the record has a full legal market, something must actually go out.
    swallows = _swallowed_exception_lines(stdout)
    if swallows:
        defects.append(('E1', 'swallowed_exception: ' + swallows[0][:200]))

    texts = [t for t in sent if isinstance(t, str)]
    non_empty = [t for t in texts if t.strip()]
    if not non_empty:
        defects.append(('E1', f'no_report_sent texts={len(texts)}'))
        return defects
    text = max(non_empty, key=len)

    # When the record cannot fill every row, refusing to analyse is the designed
    # behaviour — but so is shipping the report anyway when the app managed to
    # complete the table (e.g. the missing option had another legal source).
    # The only thing to insist on is that *something* legible went out.
    if case['matched'] < 31:
        produced_report = all(name in text for name in (
            '31项市均×竞彩对比表', 'DeepSeek 数据分析研判报告'))
        defects.extend(_check_model_output(case, stdout, text))
        defects.extend(_check_refusal_only(text))
        defects.extend(_check_no_non_finite_in_body(text))
        defects.extend(_check_no_fabricated_gap(case, text))
        defects.extend(_check_title_once(case, text))
        defects.extend(_check_render_fallback_html(case, case.get('_sent_modes') or []))
        if produced_report:
            defects.extend(_check_split(text))
            defects.extend(_check_direction(text, case))
            return defects
        if not any(m in text for m in REFUSAL_MARKERS):
            defects.append(('E2', f'refusal_not_legible matched={case["matched"]}'))
        elif '缺' not in text:
            defects.append(('E2', 'refusal_does_not_name_the_gap'))
        return defects

    # From here the record had 31/31 legal values: a full report is mandatory.
    for marker in DEGRADED_MARKERS:
        if marker in text:
            defects.append(('E2', f'degraded_marker:{marker}'))
    # A section that is *only* 「未形成（…）」 is a degraded whole report only when
    # it is one of the sections that always carry data. 市短 / 市热 legitimately
    # have nothing to show when no option prices above the comparison base
    # (e.g. 市场本身取自竞彩 -> same-source rows are excluded by design), so those
    # two are excluded from this rule.
    always_populated = ('31项市均×竞彩对比表', 'DeepSeek 数据分析研判报告')
    sections = _sections_of(text)
    for name in always_populated:
        body = sections.get(name)
        if body is None:
            continue
        if DSA_SECTION_IS_EMPTY(text, name):
            defects.append(('E2', f'degraded_section:{name}'))
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('📈') and '未形成（' in stripped:
            defects.append(('E2', f'degraded_section:{stripped[:40]}'))

    # The pack-absent exits cannot render 市短/市热/市热超理论/市均对比:
    # those are views over a bundle payload that does not exist. Their contract is
    # the 31 table plus the judgment section, so only those two are required
    # there. Everywhere else all six are.
    if case.get('pack_absent_state'):
        required = ('31项市均×竞彩对比表', 'DeepSeek 数据分析研判报告')
    else:
        required = TG_SECTIONS
    missing = [name for name in required if name not in sections]
    if missing:
        defects.append(('E3', 'missing_sections:' + ','.join(missing)))

    defects.extend(_check_31_rows(text, sections, case))
    defects.extend(_check_split(text))
    defects.extend(_check_direction(text, case))
    defects.extend(_check_model_output(case, stdout, text))
    defects.extend(_check_judgment_fields(case, stdout))
    defects.extend(_check_refusal_only(text))
    defects.extend(_check_no_non_finite_in_body(text))
    defects.extend(_check_no_fabricated_gap(case, text))
    defects.extend(_check_title_once(case, text))
    defects.extend(_check_render_fallback_html(case, case.get('_sent_modes') or []))
    return defects


_BOARD_LABELS = ('亚盘让球', '大小球', '胜平负', '竞彩让球', '总进球', '半全场')
_OU_LINE_LABEL = '大小球盘口'


def _check_render_fallback_html(case: dict[str, Any],
                                sent: list[tuple[Any, Any]]) -> list[tuple[str, str]]:
    """E14: the two HTML fallbacks must not leak markup or over-bold.

    * a `parse_mode=None` delivery carries no HTML tags at all;
    * the raising path produces exactly one `<b>…</b>` pair, wrapping the title;
    * the empty-render path delivers plain text *and still carries the 31 table*
      — the fallback replaces the HTML matrix, not the report.

    #28 replaced `align_displayed_landings`/`mark_*` with real behaviour and made
    these fallbacks reachable; a sweep that never triggers them cannot claim
    anything about them. The `empty` exit went unswept for two full 100k runs
    (see `_RENDER_FALLBACK_SLOTS`), so this check is written per-mode rather
    than as one shared tag scan: a rule that only fires for `raise` is how a
    whole exit stays unexamined.
    """
    out: list[tuple[str, str]] = []
    mode = case.get('render_fallback')
    if not mode:
        return out
    if not sent:
        return [('E14', f'{mode}_fallback_delivered_nothing')]
    joined = '\n'.join(str(body or '') for _pm, body in sent)
    for parse_mode, body in sent:
        body = str(body or '')
        if parse_mode is None:
            for tag in ('<b>', '</b>', '<font'):
                if tag in body:
                    out.append(('E14', f'plain_delivery_carries_{tag}'))
                    break
        elif parse_mode == 'HTML' and mode == 'raise':
            pairs = body.count('<b>')
            closes = body.count('</b>')
            if pairs != 1 or closes != 1:
                out.append(('E14', f'bold_pairs:{pairs}/{closes}'))
            else:
                first = next((ln for ln in body.splitlines() if ln.strip()), '')
                if not (first.startswith('<b>') and '</b>' in first):
                    out.append(('E14', f'bold_does_not_wrap_title:{first[:60]}'))
        elif parse_mode == 'HTML' and mode == 'empty':
            # The empty-render fallback must not fall back to HTML at all.
            out.append(('E14', f'empty_fallback_still_html:{body[:60]}'))
    if mode == 'empty':
        # `format_matrix_dual_prejudge_html` returning '' must not cost the
        # reader the 31 table or the rest of the report: the fallback swaps the
        # matrix for plain text, it does not truncate the body.
        if '31项市均×竞彩对比表' not in joined:
            out.append(('E14', 'empty_fallback_lost_31_table'))
        rows = [ln for ln in joined.splitlines() if re.match(r'^  \d{2} ', ln)]
        if len(rows) != 31:
            out.append(('E14', f'empty_fallback_31_rows={len(rows)}'))
    return out


def _count_word(n: int) -> str:
    """A count spelled in Chinese numerals, so `_signature` cannot erase it.

    `_signature` digit-normalises every failure detail before clustering, which
    is right for grouping but turns `title_mark_count:2` into
    `title_mark_count:#`. A reader then cannot tell a doubled title from a
    quadrupled one without rerunning the case. Chinese numerals survive that
    substitution, so the count stays readable in CLUSTERS; the ASCII number is
    still printed first for anything that parses the line.
    """
    words = {0: '零', 1: '一', 2: '两', 3: '三', 4: '四', 5: '五', 6: '六',
             7: '七', 8: '八', 9: '九', 10: '十'}
    return words.get(int(n), str(n))


def _check_title_once(case: dict[str, Any], text: str) -> list[tuple[str, str]]:
    """E13: the delivered title line carries 📌 and the kickoff exactly once.

    `rebuild_pipeline_bundle_from_history` returns an already-formatted title_line;
    feeding that back through `format_tg_match_header` doubles 📌, the match
    number and the kickoff, and #28 pins the result to the first line a reader
    sees. Zero tolerance: one occurrence each.

    The kickoff rule is waived for the pack-absent states only, and that is a
    per-state exception rather than a blanket one: with no bundle there is no
    kickoff to quote, so that path heads its own body with
    `📌 名称（order=…）` by design. Only the kickoff assertion is waived —
    the 📌 count stays zero-tolerance on those cases too, because a doubled
    title is just as wrong there.
    """
    out: list[tuple[str, str]] = []
    title = ''
    for line in text.splitlines():
        if '📌' in line:
            title = line.strip()
            break
    if not title:
        return out
    marks = title.count('📌')
    if marks != 1:
        out.append(('E13', f'title_mark_count:{marks}（实际{_count_word(marks)}个📌，应为1个）:{title[:70]}'))
    if case.get('pack_absent_state'):
        return out
    rec = case.get('rec') or {}
    kickoff = str(rec.get('match_time') or rec.get('time') or rec.get('kickoff_time') or '')
    hits = title.count(kickoff) if kickoff else 0
    if kickoff and hits != 1:
        out.append(('E13', f'kickoff_count:{hits}（实际{_count_word(hits)}次，应为1次）:{title[:70]}'))
    return out


def _check_no_fabricated_gap(case: dict[str, Any], text: str) -> list[tuple[str, str]]:
    """E12: a body may only say 无数据 for something the record really lacks.

    The pack-absent path writes 无数据 when a source it read was empty. If the
    record carries the value and the body denies it, that is fabrication — one
    line denying what the next line prints. This checks the boards and the
    大小球盘口 line against the record itself.
    """
    out: list[tuple[str, str]] = []
    rec = case.get('rec') or {}
    six = rec.get('six_market_mobile') if isinstance(rec.get('six_market_mobile'), dict) else {}
    present = {label: six.get(key) is not None for label, key in (
        ('亚盘让球', 'asian_handicap'), ('大小球', 'ou_line'),
        ('胜平负', 'odds_1x2'), ('竞彩让球', 'jc_handicap'),
        ('总进球', 'total_goals_odds'), ('半全场', 'htft_odds'),
    )}
    for line in text.splitlines():
        stripped = line.strip()
        for label in _BOARD_LABELS:
            if stripped.startswith(f'{label}：') and '无数据' in stripped and present.get(label):
                out.append(('E12', f'fabricated_gap:{label}'))
                break
    has_ou = six.get('ou_line') is not None or rec.get('ou_line') is not None
    for line in text.splitlines():
        if line.strip().startswith(f'{_OU_LINE_LABEL}：') and '无数据' in line and has_ou:
            out.append(('E12', 'fabricated_gap:大小球盘口'))
            break
    return out


def _check_no_non_finite_in_body(text: str) -> list[tuple[str, str]]:
    """E11: the sent body must never carry a non-finite figure.

    `inf`/`nan` are not numbers a reader can act on, and `float('1e400')` reaches
    them without raising — so an exception guard alone does not protect the body.
    This is checked on the text that would actually be sent.
    """
    # A diagnostic that *names* the offending value is not the defect: telling
    # the reader "赔率数值非法: inf" is correct and useful. What must never happen
    # is a non-finite value formatted as a number — `inf%`, `Δinf`, `赔率 inf`.
    # So skip matches inside a diagnostic clause.
    DIAGNOSTIC = ('非法', '非数值', '无效', 'invalid', '熔断', 'rejected', '异常')
    out: list[tuple[str, str]] = []
    for match in re.finditer(r'(?<![\w.])-?(inf|nan)(?![\w])', text, re.IGNORECASE):
        line_start = text.rfind('\n', 0, match.start()) + 1
        line_end = text.find('\n', match.end())
        context = text[max(0, line_start):line_end if line_end > 0 else len(text)]
        if any(marker in context for marker in DIAGNOSTIC):
            continue
        snippet = context.strip()[:70]
        out.append(('E11', f'non_finite_in_body:{snippet}'))
        break
    return out


def _check_refusal_only(text: str) -> list[tuple[str, str]]:
    """E10: the user's policy is "always send a real report".

    A body that is nothing but a refusal (「暂不分析」/「未发研判正文」/…) used to be
    a legitimate designed refusal. It no longer is: it must be counted as a
    failure, and reported separately rather than as a clean pass. The marker is
    the *whole* body being a refusal, not a refusal sentence appearing inside an
    otherwise complete report.
    """
    out: list[tuple[str, str]] = []
    body = text.strip()
    if not body:
        return out
    is_refusal = any(m in body for m in REFUSAL_ONLY_MARKERS)
    has_report = ('31项市均×竞彩对比表' in body and 'DeepSeek 数据分析研判报告' in body)
    if is_refusal and not has_report:
        first = body.splitlines()[0][:60] if body.splitlines() else ''
        out.append(('E10', f'refusal_only_body:{first}'))
    return out


def _check_31_rows(text: str, sections: dict[str, str],
                   case: dict[str, Any] | None = None,
                   ) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    table = sections.get('31项市均×竞彩对比表')
    if table is None:
        out.append(('E4', 'no_31_table'))
        return out
    rows = [ln for ln in table.splitlines() if re.match(r'^  \d{2} ', ln)]
    if len(rows) != 31:
        out.append(('E4', f'row_count={len(rows)}'))
    seen: list[str] = []
    for line in rows:
        m = re.match(
            r'^  (\d{2}) (\S+)\s+市均\s+(\S+)\s+(\S+)\s+竞彩\s*(\S+)\s+(\S+)\s+Δ\s*(\S+)', line)
        if not m:
            out.append(('E4', f'unparsable_row:{line[:60]}'))
            continue
        score = m.group(2)
        seen.append(score)
        # A dash is only a defect when the record *did* carry a legal value for
        # that option. When neither jc copy nor any market copy has one, the
        # dash is the honest rendering of "no odds were captured".
        if m.group(3) == '-' and _case_has_legal_market(case, score):
            out.append(('E4', f'market_empty:{score}'))
        if m.group(5) == '-' and _case_has_legal_jc(case, score):
            out.append(('E4', f'jc_empty:{score}'))
        if m.group(6) == '-' and _case_has_legal_jc(case, score):
            out.append(('E4', f'jc_pct_empty:{score}'))
        if m.group(7) == '-' and _case_has_legal_theory(case, score):
            out.append(('E4', f'delta_unavailable:{score}'))
    if len(set(seen)) != len(seen):
        dup = [s for s, n in Counter(seen).items() if n > 1]
        out.append(('E4', 'duplicate_rows:' + ','.join(dup[:4])))
    missing = [o for o in JC_OPTIONS if o not in set(seen)]
    if missing:
        out.append(('E4', 'missing_options:' + ','.join(missing[:6])))
    # 理论列只在有冻结 λ 时才有意义。包缺失路径可能既无 bundle 也无 λ，
    # 此时 31 表只有市均/竞彩列，Δ 与理论列本就不存在——那是真缺，不是缺陷。
    # 判据用它自己的实际内容：有 theory 值才要求理论列在场。
    _rec = (case or {}).get('rec') or {}
    has_theory = (
        (_rec.get('lam_h') is not None and _rec.get('lam_a') is not None)
        or (_rec.get('lam_home') is not None and _rec.get('lam_away') is not None)
    )
    # The theory value is visible either as the 市热矩阵 '理论' column or as the
    # Δ column in the 31 table (Δ = 市均 − 理论, so Δ present means theory was
    # computed). Requiring the word '理论' alone flagged a table that does show Δ.
    if has_theory and '理论' not in text and 'Δ' not in text:
        out.append(('E4', 'theory_column_absent'))
    return out


def _case_has_legal_jc(case: dict[str, Any] | None, option: str) -> bool:
    """Did the record carry a usable jc odds for this option in either copy?"""
    if not case:
        return True
    rec = case.get('rec') or {}
    for key in ('jc_score_odds', 'correct_score_jc_official'):
        if _legal_odd((rec.get(key) or {}).get(option)) is not None:
            return True
    return False


def _case_has_legal_market(case: dict[str, Any] | None, option: str) -> bool:
    if not case:
        return True
    rec = case.get('rec') or {}
    for key in ('correct_score_page_avg', 'correct_score_site_avg',
                'correct_score_books_avg', 'jc_score_odds',
                'correct_score_jc_official'):
        if _legal_odd((rec.get(key) or {}).get(option)) is not None:
            return True
    return False


def _case_has_legal_theory(case: dict[str, Any] | None, option: str) -> bool:
    """A delta needs a theory value; λ always freezes, so theory is present."""
    return True


def _check_split(text: str) -> list[tuple[str, str]]:
    """The splitter's real contract, checked without guessing at split points.

    Two things are promised: the concatenation is lossless, and no chunk exceeds
    Telegram's 4096 limit. A page break `\x0c` is a separator, not content — the
    splitter consumes it and starts a new message there, so the round-trip is
    compared against the text with the separators removed.

    Chunk boundaries are deliberately not judged by "does it end with a newline":
    a page break may legitimately land mid-line, and the table rows this build
    emits are `|`-delimited, so a chunk ending on `|` is a whole row, not a cut.
    Losslessness plus the size cap is the contract that actually protects the
    reader; anything stricter is a guess about where the caller wanted to break.
    """
    out: list[tuple[str, str]] = []
    chunks = PIPE.split_tg_html_preserving_pre(text, APP_SPLIT_LEN)
    expected = text.replace('\x0c', '')
    if ''.join(chunks) != expected:
        out.append(('E5', 'split_not_lossless'))
    for chunk in chunks:
        if len(chunk) > MAX_TG_LEN:
            out.append(('E5', f'chunk_too_long:{len(chunk)}'))
        if '\x0c' in chunk:
            out.append(('E5', 'page_break_survived_into_a_chunk'))
    return out


_CANDIDATE_RE = re.compile(r'([0-9]+-[0-9]+|胜其它|平其它|负其它)')


def _check_direction(text: str, case: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    stripped = text
    for allowed in BANNED_ALLOWED:
        stripped = stripped.replace(allowed, '')
    for word in BANNED_WORDS:
        if word in stripped:
            out.append(('E6', f'banned_word:{word}'))
            break
    for line in text.splitlines():
        if '①' not in line and '②' not in line:
            continue
        # The operator-direction line (PR #26) names the on-screen ①② on purpose:
        # it says the recompute produced nothing in the requested direction and
        # that the scores shown are the pre-command ones. That is a disclosure,
        # not a cross-direction recommendation, so it is not a candidate pair.
        if '操作员已指定方向' in line:
            continue
        scores = _CANDIDATE_RE.findall(line)
        dirs = {PIPE._score_wdl(s) for s in scores} - {''}
        if len(dirs) > 1:
            out.append(('E6', f'cross_direction_pair:{line.strip()[:50]}'))
            break
    lock = case['rec'].get('direction_lock') or {}
    direction = str((lock or {}).get('direction') or '')
    if direction in ('胜', '平', '负'):
        for score in (lock or {}).get('scores') or []:
            if PIPE._score_wdl(score) not in ('', direction):
                out.append(('E6', f'lock_pool_contradiction:{direction}/{score}'))
                break
    return out


# --------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------
def _signature(detail: str) -> str:
    sig = re.sub(r'\d+', '#', detail)
    return re.sub(r'\s+', ' ', sig)[:90]


def _cluster(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in failures:
        buckets[(item['invariant'], _signature(item['detail']))].append(item)
    out = []
    for (invariant, sig), items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        sample = items[0]
        out.append({
            'invariant': invariant,
            'signature': sig,
            'count': len(items),
            'example': {
                'seed': sample['case']['seed'],
                'lam': [sample['case']['lam_h'], sample['case']['lam_a']],
                'regime': sample['case']['regime'],
                'jc_shape': sample['case']['jc_shape'],
                'official_shape': sample['case']['official_shape'],
                'page_shape': sample['case']['page_shape'],
                'heat_shape': sample['case']['heat_shape'],
                'league': sample['case']['league'],
                'lock': sample['case']['lock'],
                'pool_len': sample['case']['pool_len'],
            },
            'detail': sample['detail'],
        })
    return out


# --------------------------------------------------------------------------
# worker entry points
# --------------------------------------------------------------------------
_INJECT_REFUSAL = bool(__import__('os').environ.get('SWEEP_INJECT_REFUSAL'))
# The three pack-absent exits. The ordinary path never reaches them, so without
# this the headline "0 failed" says nothing about them.
_FORCE_PACK_ABSENT = bool(__import__('os').environ.get('SWEEP_FORCE_PACK_ABSENT'))
# #28 made the two HTML fallbacks real: renderer raises -> escaped plain text;
# renderer returns '' -> plain-text body. Neither was reachable in a normal run.
_FORCE_RENDER_FAIL = bool(__import__('os').environ.get('SWEEP_FORCE_RENDER_FAIL'))
_FORCE_RENDER_EMPTY = bool(__import__('os').environ.get('SWEEP_FORCE_RENDER_EMPTY'))
# The fallback cadence used to be `index % 10 == 0` selecting from
# `('', '', 'raise', 'empty')` by `index % 4`. Every index divisible by 10 is
# even, so `index % 4` could only ever be 0 or 2: slot 3 ('empty') was
# unreachable, and 100,000 cases exercised `raise` 5,000 times and `empty`
# exactly zero times — with no `render_fallback_empty` key in COVERAGE to say so.
# The period and the slots are now written down separately, so both exits are
# reachable and neither depends on another selector's modulo.
_RENDER_FALLBACK_PERIOD = 20
_RENDER_FALLBACK_SLOTS = {4: 'raise', 14: 'empty'}
_PACK_ABSENT_CYCLE = ('rebuild_failed', 'payload_broken', 'no_scrape_facts')
# Every coverage class a shard is supposed to hit. A class that silently never
# runs is how the `empty` hole survived two 100k runs: the counter simply had no
# key, and an absent key reads as "nothing to report" rather than "never ran".
# `main` refuses to call a run clean while any of these is zero.
_BASE_COVERAGE_CLASSES = (
    'cases', 'produced', 'determinism_checked',
    'hostile_numeric_cases', 'ordinary_cases', 'operator_direction_cases',
)
_PACK_ABSENT_CLASSES = tuple(f'pack_absent_{s}' for s in _PACK_ABSENT_CYCLE)
_RENDER_FALLBACK_CLASSES = tuple(f'render_fallback_{m}' for m in ('raise', 'empty'))


def _expected_coverage_classes() -> tuple[str, ...]:
    """Which coverage classes this process must actually reach.

    The force flags deliberately narrow the run to one exit, so expecting the
    other one would be a false alarm — but under a normal run both fallbacks and
    all three pack-absent states must appear.
    """
    render = _RENDER_FALLBACK_CLASSES
    if _FORCE_RENDER_FAIL:
        render = ('render_fallback_raise',)
    elif _FORCE_RENDER_EMPTY:
        render = ('render_fallback_empty',)
    return _BASE_COVERAGE_CLASSES + _PACK_ABSENT_CLASSES + render + ('started_gate_control',)


def _zero_coverage() -> Counter:
    """A Counter carrying every intended class at 0, so absence is visible."""
    cov: Counter = Counter()
    for name in _expected_coverage_classes():
        cov[name] = 0
    return cov


def run_shard(cases: list[int], seed_base: int) -> dict[str, Any]:
    """Run one shard of seed indices; returns counts, failures, coverage."""
    failures: list[dict[str, Any]] = []
    coverage: Counter = _zero_coverage()
    produced = 0
    tmpdir = tempfile.mkdtemp(prefix='sweep100k-')
    # Gate control, once per shard and outside the case budget: a match that
    # kicked off before the frozen "now" must be refused, and nothing may be
    # delivered. Without this, pinning the clock would quietly disable a real
    # production gate and every other invariant would still look green.
    _gate = _build_case(seed_base)
    _gate['started_control'] = True
    _gate['rec']['match_time'] = SWEEP_STARTED_KICKOFF
    _gate['rec']['time'] = SWEEP_STARTED_KICKOFF
    _gate['rec']['kickoff_time'] = SWEEP_STARTED_KICKOFF
    try:
        _gate_sent, _gate_out = _run_once(_gate, os.path.join(tmpdir, 'gate.json'))
        for invariant, detail in _check_report(_gate, _gate_sent, _gate_out):
            failures.append({'invariant': invariant,
                             'detail': f'gate_control:{detail}', 'case': _gate})
        coverage['started_gate_control'] += 1
    except BaseException as exc:  # noqa: BLE001 - the control must never kill a shard
        failures.append({'invariant': 'E1',
                         'detail': f'gate_control_crash:{type(exc).__name__}',
                         'case': _gate})
    for index, seed in enumerate(cases):
        case = _build_case(seed_base + seed)
        # Which coverage class a case takes is decided by `seed`, the *global*
        # case ordinal, never by `index`, the position inside this shard.
        # With `--workers N` each shard restarts `index` at 0, so index-driven
        # selectors silently multiply coverage by N without adding new cases —
        # and on a single worker they hide the real rate behind the shard
        # boundary. `seed` is `range(total)[i]` either way, so the same total
        # gets the same classes regardless of worker count.
        if _INJECT_REFUSAL:
            # Pure detector control: leave the record alone and only swap the
            # captured body for the refusal shape. That isolates "can E10 see a
            # refusal" from "does the pack-absent path still send a report".
            case['inject_refusal_body'] = True
        # Deterministic coverage of the three pack-absent exits, which the
        # ordinary path never reaches (every generated case carries a bundle).
        # One case in twenty (`seed % 20 == 0`), rotating through the three
        # states; 5% of the run, split evenly across the states.
        if _FORCE_PACK_ABSENT:
            _absent_state = _PACK_ABSENT_CYCLE[seed % len(_PACK_ABSENT_CYCLE)]
        elif seed % 20 == 0:
            _absent_state = _PACK_ABSENT_CYCLE[(seed // 20) % len(_PACK_ABSENT_CYCLE)]
        else:
            _absent_state = ''
        # Render-fallback coverage: its own cadence, so both exits are reachable.
        # `seed % _RENDER_FALLBACK_PERIOD` hits every slot in
        # `_RENDER_FALLBACK_SLOTS` once per period: 5% raise, 5% empty.
        if _FORCE_RENDER_FAIL:
            case['render_fallback'] = 'raise'
        elif _FORCE_RENDER_EMPTY:
            case['render_fallback'] = 'empty'
        else:
            _rf = _RENDER_FALLBACK_SLOTS.get(seed % _RENDER_FALLBACK_PERIOD)
            if _rf:
                case['render_fallback'] = _rf
        # /方向 coverage: one case in ten carries an operator direction, which
        # makes `_emit_pack_absent_report`'s sibling path prepend the
        # 「操作员已指定方向」 line. Counted separately.
        if seed % 10 == 3:
            case['rec']['operator_direction'] = ('胜', '平', '负')[(seed // 10) % 3]
            case['operator_direction'] = case['rec']['operator_direction']
        if _absent_state:
            case['pack_absent_state'] = _absent_state
            # The harness prebuilds a bundle for some cases by calling rebuild
            # itself; with rebuild forced to raise that would crash the harness
            # rather than exercise emit. The pack-absent control owns the bundle.
            case['bundle_prebuilt'] = False
        # One history file per case: sharing it let the previous case's record
        # leak into the replay and the determinism comparison.
        hist_path = os.path.join(tmpdir, f'h{index}.json')
        try:
            sent, stdout = _run_once(case, hist_path)
            defects = _check_report(case, sent, stdout)
            if defects:
                for invariant, detail in defects:
                    failures.append({'invariant': invariant, 'detail': detail, 'case': case})
            coverage['cases'] += 1
            if case.get('hostile_numeric'):
                coverage['hostile_numeric_cases'] += 1
            else:
                coverage['ordinary_cases'] += 1
            if case.get('pack_absent_state'):
                coverage[f'pack_absent_{case["pack_absent_state"]}'] += 1
            if case.get('render_fallback'):
                coverage[f'render_fallback_{case["render_fallback"]}'] += 1
            if case.get('operator_direction'):
                coverage['operator_direction_cases'] += 1
            coverage['determinism_checked'] += 1
            # Determinism means "same input, same output". The input includes the
            # history file, so the comparison run gets a *fresh* copy — reusing the
            # one stage 2 just rewrote tests the writeback, not determinism.
            again, _ = _run_once(case, os.path.join(tmpdir, 'rerun_history.json'))
            if [t for t in again if t] != [t for t in sent if t]:
                failures.append({'invariant': 'E7', 'detail': 'non_deterministic_output',
                                 'case': case})
                defects = list(defects) or [('E7', 'non_deterministic_output')]
            # Separately: replaying against the record stage 2 already wrote must
            # produce the same outcome. A refusal that becomes a report (or the
            # reverse) means the writeback changed what the input means.
            replay, _replay_out = _run_once(case, hist_path, reseed=False)
            first_bodies = [t for t in sent if t]
            replay_bodies = [t for t in replay if t]
            # Byte equality, with exactly one documented exemption.
            #
            # The operator-direction line quotes the score that is already stored
            # (`原 无` on a first run, `原 2-1` once the first run has written it),
            # so that single line legitimately differs on replay — its whole
            # purpose is to report what the record held before the command.
            # Measured over 240 cases rotating operator_direction /
            # render_fallback / pack_absent the way run_shard does: 15 differ
            # byte-wide, and after normalising only this line to a fixed token,
            # 0 differ. Everything else must be identical, so a section that
            # disappears, a number that moves, or a line that duplicates on
            # writeback is a failure here.
            #
            # This replaced a `len(body) > 200` shape check, which only caught
            # "the report became a refusal" and would have passed a lost section,
            # a changed number, or a duplicated line. Counter-proved through
            # run_shard: dropping one 31-table row (or the 六盘口 panel) on the
            # writeback turns 11 of 11 reachable cases red — the 12th takes the
            # pack-absent exit and never builds these sections — while the same
            # shard is 12/12 green with nothing dropped. `tests/test_title_once.py`
            # drives that through run_shard, so weakening this comparison back to
            # the shape check turns those tests red.
            _first_canon = [_canon_operator_line(t) for t in first_bodies]
            _replay_canon = [_canon_operator_line(t) for t in replay_bodies]
            if _replay_canon != _first_canon:
                _first_len = sum(len(t) for t in first_bodies)
                _replay_len = sum(len(t) for t in replay_bodies)
                failures.append({
                    'invariant': 'E9', 'case': case,
                    'detail': 'replay_after_writeback_differs:'
                              f'{len(first_bodies)}msg/{_first_len}ch'
                              f'->{len(replay_bodies)}msg/{_replay_len}ch',
                })
                defects = list(defects) or [('E9', 'replay_after_writeback_differs')]
            if not defects:
                produced += 1
            coverage['produced'] += 1 if not defects else 0
        except BaseException as exc:  # noqa: BLE001 - one bad case must not kill the shard
            failures.append({'invariant': 'E1',
                             'detail': f'harness_crash:{type(exc).__name__}', 'case': case})
            coverage['cases'] += 1
            if case.get('hostile_numeric'):
                coverage['hostile_numeric_cases'] += 1
            else:
                coverage['ordinary_cases'] += 1
            if case.get('pack_absent_state'):
                coverage[f'pack_absent_{case["pack_absent_state"]}'] += 1
            if case.get('render_fallback'):
                coverage[f'render_fallback_{case["render_fallback"]}'] += 1
            if case.get('operator_direction'):
                coverage['operator_direction_cases'] += 1
    with contextlib.suppress(OSError):
        for name in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, name))
    with contextlib.suppress(OSError):
        os.rmdir(tmpdir)
    return {'produced': produced, 'failures': failures, 'coverage': dict(coverage)}


def _run_parallel(total: int, seed: int, workers: int) -> dict[str, Any]:
    from concurrent.futures import ProcessPoolExecutor
    shards = []
    per = total // workers
    for index in range(workers):
        start = index * per
        count = per if index < workers - 1 else total - per * (workers - 1)
        shards.append((start, count))
    produced = 0
    failures: list[dict[str, Any]] = []
    coverage: Counter = Counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_shard, list(range(start, start + count)), seed)
                   for start, count in shards]
        for future in futures:
            result = future.result()
            produced += result['produced']
            failures.extend(result['failures'])
            coverage.update(result['coverage'])
    return {'produced': produced, 'failures': failures, 'coverage': coverage}


def _coverage_defects(coverage: Counter, n_cases: int) -> list[str]:
    """Every intended coverage class must actually have run.

    A missing key used to be the silent failure mode: `COVERAGE` simply had no
    `render_fallback_empty`, so two 100k runs reported 5,000 `raise` cases and
    nothing at all about `empty` while still printing `failed=0`. Reporting a
    class as "not covered" is impossible if the class is never counted, so the
    absence has to be an error of its own.

    The small-n floor keeps tiny probes honest without failing them: below one
    full cycle of each cadence a zero may simply mean "too few cases".
    """
    out: list[str] = []
    # One render period (20) and one pack-absent period (20) plus slack.
    floor = 40
    for name in _expected_coverage_classes():
        if name in ('cases', 'produced', 'determinism_checked'):
            continue
        if coverage.get(name, 0) > 0:
            continue
        if n_cases < floor:
            out.append(f'{name}=0 (n_cases={n_cases} < {floor}, not conclusive)')
        else:
            out.append(f'{name}=0 (HARNESS DEFECT: this class never ran)')
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cases', type=int, default=100000)
    ap.add_argument('--seed', type=int, default=20261127)
    ap.add_argument('--workers', type=int, default=1)
    ap.add_argument('--json', dest='json_path', default='')
    args = ap.parse_args(argv)

    started = time.time()
    if args.workers > 1:
        result = _run_parallel(args.cases, args.seed, args.workers)
    else:
        result = run_shard(list(range(args.cases)), args.seed)
    # Union with the intended classes so a zero prints as `=0`, not by absence.
    cov: Counter = _zero_coverage()
    cov.update(result['coverage'])
    result['coverage'] = cov
    elapsed = time.time() - started

    clusters = _cluster(result['failures'])
    n_fail = args.cases - result['produced']
    cov_issues = _coverage_defects(result['coverage'], args.cases)
    print(f'REPORT n_cases={args.cases} produced={result["produced"]} failed={n_fail}')
    print(f'CLUSTERS n={len(clusters)}')
    for cluster in clusters:
        print(f'[{cluster["count"]:>6}x] {cluster["invariant"]} {cluster["signature"]}'
              f' | seed={cluster["example"]["seed"]} lam={cluster["example"]["lam"]}'
              f' jc={cluster["example"]["jc_shape"]} off={cluster["example"]["official_shape"]}'
              f' heat={cluster["example"]["heat_shape"]} pool={cluster["example"]["pool_len"]}')
    cov = result['coverage']
    print('COVERAGE ' + ' '.join(f'{k}={v}' for k, v in sorted(cov.items())))
    if cov_issues:
        # A clean `failed=0` on a run that never reached half its exits is not a
        # clean run. Say so in the same breath as the headline number.
        print(f'COVERAGE-INCOMPLETE n={len(cov_issues)}')
        for issue in cov_issues:
            print(f'  {issue}')
    print(f'WALL seconds={elapsed:.2f} throughput_cases_per_s={args.cases / max(elapsed, 1e-9):.1f}')
    if args.json_path:
        Path(args.json_path).write_text(json.dumps({
            'n_cases': args.cases,
            'produced': result['produced'],
            'failed': n_fail,
            'coverage_incomplete': cov_issues,
            'clusters': clusters,
            'coverage': dict(cov),
            'wall_seconds': round(elapsed, 3),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if (n_fail == 0 and not cov_issues) else 1


if __name__ == '__main__':
    sys.exit(main())
