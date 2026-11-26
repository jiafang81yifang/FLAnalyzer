"""幽灵节点注入与粘度锚点接线的实测。

覆盖规格（internal/2026-11-26-ghost-node-viscosity-spec.md）A 的 1-10 与 B 的诊断字段：
注入只从 31 池已有比分里挑、只注反方向、不重复、不超上限、挤出留痕、
零候选给明确原因、缺字段报错不补默认值、排序按 theory 降序 + 原始下标破平。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('BLADE_HEADLESS', '1')
os.environ.setdefault('OKOOO_ROUTE', 'direct')

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = importlib.import_module('app_v84_hermes')

CORE_SCORES = ('1-0', '2-0', '0-1', '0-2', '1-1')

HOME_POOL = [
    ('1-0', 20.0), ('2-0', 12.0), ('2-1', 9.0), ('0-0', 8.0), ('1-1', 7.0),
    ('3-0', 6.0), ('3-1', 5.0), ('4-0', 4.0), ('4-1', 3.9), ('4-2', 3.8),
    ('5-0', 3.7), ('5-1', 3.6), ('5-2', 3.5), ('3-2', 3.4), ('2-2', 3.3),
]

AWAY_TAIL = [
    ('0-1', 2.8), ('0-2', 2.4), ('1-2', 2.2), ('0-3', 1.2), ('1-3', 1.1),
    ('2-3', 1.0), ('0-4', 0.6), ('1-4', 0.5), ('2-4', 0.4), ('0-5', 0.3),
    ('1-5', 0.2), ('2-5', 0.2),
]

DRAW_EXTRA = [('2-2', 3.3), ('3-3', 0.9)]


def pool_records(pairs):
    return [
        {'score': score, 'theory': theory, 'market': theory - 0.4, 'ibi': 1.0, 'delta': -0.4}
        for score, theory in pairs
    ]


def full_records(pairs):
    return [{'score': score, 'theory': theory, 'delta': -1.2} for score, theory in pairs]


def full_31_pool():
    """31 个竞彩比分，主胜正路在 15 池内，客胜长尾只存在于全量池。"""
    return full_records(HOME_POOL + DRAW_EXTRA + AWAY_TAIL)


def inject(current_pool, full_pool, payload, max_pool_size=15):
    return app.TailEndGhostNodeInjector(max_pool_size=max_pool_size).inject_ghost_nodes(
        current_pool15=current_pool,
        full_31_pool=full_pool,
        fissure_payload=payload,
    )


def production_shape(home_lam=1.85, away_lam=0.55, tail_odds_factor=1.35):
    """按 analyze_match 的真实形状构造：(31 项池, 36 项全池)。

    与 internal/2026-11-26-ghost-node-prod-shape-repro.py 同一套构造：
      prob_dict = calc_poisson_prob(..., max_goals=8) -> 36 项（含非竞彩项）
      _pool15_enriched        = ranked[:31]   <- app L39334 真实的 [:31]
      _full_31_pool_for_ghost = ranked 全量     <- app L39703 真实的构造
    """
    prob_dict = app.calc_poisson_prob(home_lam, away_lam, max_goals=8)
    ranked = app.build_probability_rankings(prob_dict)
    odds = {s: (1.0 / max(p, 1e-6)) * 1.05 for s, p in prob_dict.items()}
    for tail_score in ('0-1', '0-2', '0-3', '1-2', '1-3', '2-3', '3-3', '2-4'):
        if tail_score in odds:
            odds[tail_score] *= tail_odds_factor
    total_inv = sum(1.0 / v for v in odds.values() if v and v > 0)

    def enrich(score, info):
        theory = info['prob'] * 100
        odd = odds.get(score)
        market = ((1.0 / float(odd)) / total_inv * 100) if odd else 0.0
        return {
            'score': score, 'theory': theory, 'market': market,
            'theory_prob': theory, 'market_prob': market,
            'ibi': (market / theory) if theory > 0.01 else 0,
            'weighted': theory, 'delta': market - theory,
        }

    pool = [enrich(s, i) for s, i in ranked[:31]]
    full = [enrich(s, i) for s, i in ranked]
    return pool, full


def production_fissure(pool):
    return app.TensorFissureGlobalReconstructor().detect_tensor_fissure('home', pool)


def scores_of(items):
    return [str(item.get('score') or '') for item in items]


def ghosts_of(items):
    return [item for item in items if item.get('tag') == 'GHOST_INJECTED_NODE']


# ── 证据 1：31 池不再白算，裂缝触发时真注入 + 日志确实打出 ──────────────────
def test_injector_really_injects_counter_direction_tail_and_logs(capsys):
    payload = {'triggered': True, 'macro_direction': 'home', 'counter_direction': 'away'}
    result = inject(pool_records(HOME_POOL), full_31_pool(), payload)

    ghosts = ghosts_of(result)
    assert ghosts, f'裂缝触发却没有任何幽灵节点注入，reason={result.reason}'
    assert result.reason == 'ghost_injected'
    assert set(result.injected_scores) <= {'0-1', '0-2', '1-2', '0-3', '1-3', '2-3',
                                           '0-4', '1-4', '2-4', '0-5', '1-5', '2-5'}
    assert '0-1' in scores_of(ghosts)

    logged = app.apply_ghost_node_injection(
        pool_records(HOME_POOL), full_31_pool(), payload,
    )
    out = capsys.readouterr().out
    assert '👻 [幽灵节点注入] 触发！' in out, out
    assert logged.injected, 'apply_ghost_node_injection 必须把注入项暴露出来，不能靠对象同一性'


def test_injected_is_populated_by_the_injector_itself():
    """`.injected` 必须由 inject_ghost_nodes 直接填好。

    否则任何绕过 apply_ghost_node_injection 的调用点都会看到恒空列表，
    `.injected` 门禁就成了死代码换了个位置。
    """
    result = inject(pool_records(HOME_POOL), full_31_pool(), {'counter_direction': 'away'})
    assert result.injected, '注入器自己没填 .injected，调用点门禁会永远为假'
    assert result.injected == ghosts_of(result)
    assert len(result.injected) == len(result.injected_scores)


def test_injected_is_empty_when_nothing_was_injected():
    result = inject(pool_records(HOME_POOL), full_31_pool(), {'triggered': True})
    assert result.reason == 'no_counter_direction_candidate'
    assert result.injected == []


# ── 证据 3：上限与保护：要注入 5 个但只剩 2 个空位 ─────────────────────────
FIVE_AWAY_CANDIDATES = [
    ('0-1', 2.8), ('0-2', 2.4), ('1-2', 2.2), ('0-3', 1.2), ('1-3', 1.1),
]


def test_injection_respects_cap_and_protects_top3_and_core_scores():
    # 13 项 => 只剩 2 个空位，31 池里正好 5 个待注入的客胜长尾：
    # 先填满 2 个空位，再从尾部挤出 3 个非保护项。
    current = pool_records(HOME_POOL[:13])
    full = full_records(HOME_POOL[:13] + DRAW_EXTRA + FIVE_AWAY_CANDIDATES)
    result = inject(current, full, {'counter_direction': 'away'})

    assert len(result) <= 15, f'超过 max_pool_size: {len(result)}'
    assert result.injected_scores == ['0-1', '0-2', '1-2', '0-3', '1-3']
    assert len(ghosts_of(result)) == 5
    assert len(result) == 15

    after = scores_of(result)
    for kept in scores_of(current[:3]):
        assert kept in after, f'前3名 {kept} 被挤掉了: {after}'
    for core in CORE_SCORES:
        assert core in after, f'核心比分 {core} 被挤掉了: {after}'
    assert [entry['score'] for entry in result.evicted] == ['5-2', '5-1', '5-0'], result.evicted
    assert all(entry['reason'] == 'tail_evicted_for_ghost_injection' for entry in result.evicted)


def test_full_pool_evicts_only_from_tail_and_records_evicted():
    current = pool_records(HOME_POOL)  # 已满 15
    result = inject(current, full_31_pool(), {'counter_direction': 'away'})

    assert len(result) <= 15
    ghosts = ghosts_of(result)
    assert ghosts, '满池时也应该挤出尾部来给反向长尾腾位'
    after = scores_of(result)
    for kept in scores_of(current[:3]):
        assert kept in after, f'前3名 {kept} 被挤掉了: {after}'
    for core in CORE_SCORES:
        assert core in after, f'核心比分 {core} 被挤掉了: {after}'

    evicted_scores = [entry['score'] for entry in result.evicted]
    assert evicted_scores, '挤出必须留痕'
    assert not set(evicted_scores) & set(CORE_SCORES)
    for ghost in ghosts:
        for entry in ghost.get('evicted') or []:
            assert entry['score'] in evicted_scores
            assert entry['reason']


# ── 证据 4：方向。away 只注客胜；counter_direction 缺失则零候选且池子不变 ────
def test_away_direction_injects_only_away_scores():
    result = inject(pool_records(HOME_POOL), full_31_pool(), {'counter_direction': 'away'})
    ghosts = ghosts_of(result)
    assert ghosts
    for ghost in ghosts:
        home, away = app.parse_score(ghost['score'])
        assert home < away, f'{ghost["score"]} 不是客胜，方向判定失效'


def test_missing_counter_direction_yields_explicit_reason_and_keeps_pool():
    current = pool_records(HOME_POOL)
    result = inject(current, full_31_pool(), {'triggered': True, 'macro_direction': 'home'})

    assert result.reason == 'no_counter_direction_candidate'
    assert scores_of(result) == scores_of(current)
    assert ghosts_of(result) == []
    assert not [x for x in result if x.get('tag') == 'GHOST_INJECTED_NODE']


def test_illegal_counter_direction_is_not_defaulted():
    result = inject(pool_records(HOME_POOL), full_31_pool(), {'counter_direction': 'sideways'})
    assert result.reason == 'no_counter_direction_candidate'
    assert ghosts_of(result) == []


# ── 证据 5：重复。池里已有 '0-1' 时不得再注入 '0:1' ────────────────────────
def test_existing_score_is_not_duplicated_across_separators():
    current = pool_records(HOME_POOL[:14] + [('0:1', 2.8)])
    result = inject(current, full_31_pool(), {'counter_direction': 'away'})

    normalized = [score.replace(':', '-') for score in scores_of(result)]
    assert normalized.count('0-1') == 1, f'0-1 被重复注入了: {normalized}'
    assert '0-1' not in [score.replace(':', '-') for score in result.injected_scores]
    for score in result.injected_scores:
        assert score.replace(':', '-') not in scores_of(current[:14])


# ── 证据 6：无新比分。注入集合 ⊆ 31 池集合 ────────────────────────────────
def test_injected_scores_are_subset_of_full_pool():
    full = full_31_pool()
    result = inject(pool_records(HOME_POOL), full, {'counter_direction': 'away'})

    full_scores = {str(item['score']) for item in full}
    assert set(result.injected_scores) <= full_scores
    assert set(result.injected_scores), '本用例应当有注入'
    for ghost in ghosts_of(result):
        assert ghost['score'] in full_scores


def test_injector_does_not_mutate_the_full_pool_records():
    full = full_31_pool()
    snapshot = [dict(item) for item in full]
    inject(pool_records(HOME_POOL), full, {'counter_direction': 'away'})
    assert full == snapshot, '注入器改写了入参 31 池'


# ── 排序：theory 降序，并列按 31 池原始下标，禁止按字符串字典序 ────────────
def test_candidates_sorted_by_theory_then_original_index():
    # 故意让 '2-5' 排在 '0-4' 前面、且 '0-4' 与 '1-4' 理论概率相同：
    # 若按字典序或按 delta 排，结果会不同。
    current = pool_records(HOME_POOL[:10])  # 剩 5 个空位
    full = full_records(
        HOME_POOL[:10]
        + [('2-5', 9.0), ('0-4', 5.0), ('1-4', 5.0), ('0-5', 7.0)]
    )
    result = inject(current, full, {'counter_direction': 'away'})

    assert result.injected_scores == ['2-5', '0-5', '0-4', '1-4'], result.injected_scores


# ── 完整性：缺字段不补默认值 ───────────────────────────────────────────────
def test_missing_theory_or_delta_is_skipped_not_defaulted():
    current = pool_records(HOME_POOL[:13])
    full = full_records(HOME_POOL[:13] + [('0-1', 9.0), ('0-2', 8.0)])
    full.append({'score': '0-3', 'theory': 30.0})          # 缺 delta
    full.append({'score': '0-4', 'delta': -1.0})           # 缺 theory
    full.append({'score': '0-5', 'theory': 1.0, 'delta': -1.0})

    result = inject(current, full, {'counter_direction': 'away'})

    skipped = {(entry['score'], entry['reason']) for entry in result.skipped}
    assert ('0-3', 'missing_delta') in skipped
    assert ('0-4', 'missing_theory') in skipped
    assert '0-3' not in result.injected_scores
    assert '0-4' not in result.injected_scores
    assert '0-5' in result.injected_scores


def test_half_pool_missing_key_fields_raises_integrity_error():
    current = pool_records(HOME_POOL[:13])
    broken = full_records([('1-0', 20.0), ('2-0', 12.0), ('0-1', 3.0)])
    broken += [{'score': '0-2'}, {'score': '1-2'}, {'score': '0-3'}, {'score': '1-3'}]

    with pytest.raises(app.GhostInjectionIntegrityError):
        inject(current, broken, {'counter_direction': 'away'})


def test_integrity_error_is_a_value_error():
    assert issubclass(app.GhostInjectionIntegrityError, ValueError)


def test_direction_lock_drops_ghost_and_restores_the_evicted_original(monkeypatch, capsys):
    """方向锁把注入的反向项丢掉时，被它挤出的原池项必须归位，不能净丢池位。"""
    monkeypatch.setattr(
        app.DeepSeekAnalyzer,
        'matrix_direction_lock',
        staticmethod(lambda *a, **k: {'available': True, 'locked': True, 'direction': '胜'}),
    )
    current = pool_records(HOME_POOL)  # 已满 15，注入必然挤出尾部项
    result = app.apply_ghost_node_injection(
        current, full_31_pool(), {'counter_direction': 'away'},
        extra_lock_payload={'direction_lock': {'locked': True, 'direction': '胜'}},
    )
    out = capsys.readouterr().out

    assert result.injected == []
    assert '🔒 [方向锁] 幽灵节点丢弃方向外' in out, out
    assert '👻 [幽灵节点注入] 触发！' not in out
    assert len(result) == len(current)
    assert scores_of(result) == scores_of(current), f'原池没有完整归位: {scores_of(result)}'


def _ghost_direction(score):
    home, away = app.parse_score(score)
    return 'home' if home > away else ('away' if home < away else 'draw')


def test_direction_lock_keeps_matching_direction_ghosts(monkeypatch):
    monkeypatch.setattr(
        app.DeepSeekAnalyzer,
        'matrix_direction_lock',
        staticmethod(lambda *a, **k: {'available': True, 'locked': True, 'direction': '负'}),
    )
    current = pool_records(HOME_POOL)
    result = app.apply_ghost_node_injection(
        current, full_31_pool(), {'counter_direction': 'away'},
        extra_lock_payload={'direction_lock': {'locked': True, 'direction': '负'}},
    )
    assert result.injected, '方向锁=负 时客胜幽灵节点应当保留'
    assert all(_ghost_direction(score) == 'away' for score in scores_of(result.injected))


# ── 空池：返回原池 + 原因，不抛错 ──────────────────────────────────────────
def test_empty_pools_return_reason_without_raising():
    empty_current = inject([], full_31_pool(), {'counter_direction': 'away'})
    assert empty_current.reason == 'empty_current_pool'
    assert list(empty_current) == []
    assert scores_of(empty_current) == []

    current = pool_records(HOME_POOL[:5])
    empty_full = inject(current, [], {'counter_direction': 'away'})
    assert empty_full.reason == 'empty_full_31_pool'
    assert scores_of(empty_full) == scores_of(current)


# ── 生产形状回归：31 项池 + 36 项全池（app L39334 / L39703 的真实形状） ──────
def test_production_shape_pool_is_never_shortened():
    """max_pool_size=15 是上限，不是目标长度；31 项池注入后必须仍是 31 项。"""
    pool, full = production_shape()
    assert len(pool) == 31, '生产形状池应为 31 项（ranked[:31]）'
    assert len(full) == 36, '生产全池应为 36 项（泊松 max_goals=8）'

    fissure = production_fissure(pool)
    assert fissure.get('triggered') is True, fissure.get('reason')

    result = app.apply_ghost_node_injection(pool, full, fissure, max_pool_size=15)

    assert len(result) == len(pool), (
        f'注入把 31 项池砍成了 {len(result)} 项；max_pool_size 只能限制增长，不能压缩下游池'
    )
    assert result.injected, '生产形状下应当真有注入'


def test_production_shape_theory_sum_does_not_collapse():
    """池子被压缩会让下游“15池理论概率总和”失真，这里钉住总和基本不变。"""
    pool, full = production_shape()
    before = sum(p['theory'] for p in pool)
    result = app.apply_ghost_node_injection(pool, full, production_fissure(pool), max_pool_size=15)
    after = sum(x['theory'] for x in result)
    assert abs(after - before) < 1.0, f'理论总和从 {before:.2f}% 掉到 {after:.2f}%'


def test_production_shape_evicted_count_equals_net_loss():
    """result.evicted 必须等于真实净丢失条数，不能只留“注入个数”条。"""
    pool, full = production_shape()
    result = app.apply_ghost_node_injection(pool, full, production_fissure(pool), max_pool_size=15)

    before = {p['score'] for p in pool}
    after = {x['score'] for x in result}
    net_lost = before - after
    assert len(result.evicted) == len(net_lost), (
        f'evicted 记了 {len(result.evicted)} 条，实际净丢失 {len(net_lost)} 项: {sorted(net_lost)}'
    )
    assert {e['score'] for e in result.evicted} == net_lost


def test_production_shape_injected_scores_stay_inside_jczq_31():
    """泊松全池含 3-4/4-4/6-3 等非竞彩项，它们不能成为注入候选。"""
    pool, full = production_shape()
    assert set(scores_of(full)) - set(app.JCZQ_ALL_OPTIONS), '构造应包含非竞彩比分'

    result = app.apply_ghost_node_injection(pool, full, production_fissure(pool), max_pool_size=15)

    allowed = set(app.JCZQ_ALL_OPTIONS)
    assert result.injected_scores, '生产形状下应当真有注入'
    assert set(result.injected_scores) <= allowed, (
        f'注入了非竞彩比分: {sorted(set(result.injected_scores) - allowed)}'
    )
    assert all(x['score'] in allowed for x in ghosts_of(result))


def test_non_jczq_scores_are_skipped_with_their_own_reason():
    pool = pool_records(HOME_POOL[:13])
    full = full_records(HOME_POOL[:13] + [('0-1', 9.0), ('3-4', 30.0), ('7-2', 25.0)])
    result = inject(pool, full, {'counter_direction': 'away'})

    non_jczq = {e['score'] for e in result.skipped if e['reason'] == 'non_jczq_score'}
    assert non_jczq == {'3-4', '7-2'}
    assert '3-4' not in result.injected_scores
    assert '7-2' not in result.injected_scores
    assert '0-1' in result.injected_scores


def test_each_ghost_node_carries_the_full_eviction_list():
    """每个注入节点都要挂完整挤出清单，不能只挂一条；否则注入器直调会丢审计。"""
    # 13 项池 + max_pool_size=13：3 个候选依次挤出 3 项，正是会丢记录的形状。
    pool = pool_records(HOME_POOL[:13])
    full = full_records(HOME_POOL[:13] + [('0-1', 9.0), ('0-2', 8.0), ('1-2', 7.0)])
    result = inject(pool, full, {'counter_direction': 'away'}, max_pool_size=13)

    assert result.injected_scores == ['0-1', '0-2', '1-2'], result.injected_scores
    assert len(result.evicted) == 3, result.evicted
    for ghost in ghosts_of(result):
        assert len(ghost['evicted']) == 3, (
            f"节点 {ghost['score']} 只挂了 {len(ghost['evicted'])} 条，实际挤出 3 条"
        )


def test_production_shape_each_ghost_node_matches_result_evicted():
    """生产形状下，节点挂的条数也必须与 result.evicted 一致（哪怕只有 1 条）。"""
    pool, full = production_shape()
    result = inject(pool, full, production_fissure(pool), max_pool_size=15)
    assert result.evicted, '生产形状应当有挤出'
    for ghost in ghosts_of(result):
        assert len(ghost['evicted']) == len(result.evicted)


# ── 上限：小池仍然受 max_pool_size 约束（不压缩，只封顶） ────────────────
def test_max_pool_size_caps_a_small_pool():
    current = pool_records(HOME_POOL[:4])
    result = inject(current, full_31_pool(), {'counter_direction': 'away'}, max_pool_size=5)
    assert len(result) == 5
    assert len(ghosts_of(result)) == 2
    assert len(result.evicted) == 1
    after = scores_of(result)
    for kept in scores_of(current[:3]):
        assert kept in after, f'前3名 {kept} 被挤掉了: {after}'


def test_pool_longer_than_max_pool_size_is_kept_intact():
    """池子已经比 max_pool_size 长时，上限不许把池子压缩。"""
    current = pool_records(HOME_POOL)  # 15 项
    result = inject(current, full_31_pool(), {'counter_direction': 'away'}, max_pool_size=5)

    assert len(result) == 15, f'max_pool_size=5 却把 15 项池压缩成 {len(result)} 项'
    assert len(result.evicted) == len({p['score'] for p in current} - {x['score'] for x in result})
    after = scores_of(result)
    for kept in scores_of(current[:3]):
        assert kept in after, f'前3名 {kept} 被挤掉了: {after}'
    for core in CORE_SCORES:
        assert core in after, f'核心比分 {core} 被挤掉了: {after}'


# ── 调用点：不许再用对象同一性判断是否注入 ─────────────────────────────────
def test_call_site_no_longer_gates_on_object_identity():
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert '_ghost_enhanced_pool is not _pool15_enriched' not in source, (
        '调用点还在用 is not 判断是否注入；is not 是对象同一性，恒为假，死代码会复活'
    )
    assert 'apply_ghost_node_injection(' in source
    assert '.injected' in source


def test_ghost_tag_appears_at_definition_and_call_site():
    source = (ROOT / 'app_v84_hermes.py').read_text(encoding='utf-8')
    assert source.count('GHOST_INJECTED_NODE') >= 2


# ── 证据 7：粘度。source == unavailable、applied 为 False 出现在真实输出结构里 ──
def test_viscosity_anchor_reports_unavailable_without_inventing_a_formula():
    multiplier, source = app.BettingStrategyExpertSystem.protocol_viscosity_anchor('HIGH', '1-0')
    assert multiplier == 1.0
    assert source == 'unavailable'


def test_viscosity_diagnostics_reach_the_expert_system_output():
    expert = app.BettingStrategyExpertSystem.analyze_pool15(pool_records(HOME_POOL[:5]))
    assert expert['viscosity_source'] == 'unavailable'
    assert expert['viscosity_applied'] is False
    assert expert['viscosity_multiplier'] == 1.0
    for row in expert['results']:
        assert row['viscosity_source'] == 'unavailable'
        assert row['viscosity_applied'] is False
        assert 'unavailable' in row['details']


def test_viscosity_diagnostics_reach_system_diagnostics():
    diag = app.build_system_diagnostics(force_tg_check=False)
    assert diag['viscosity']['source'] == 'unavailable'
    assert diag['viscosity']['applied'] is False
    assert 'Viscosity: source=unavailable applied=False' in app.format_system_diagnostics_text(
        force_tg_check=False
    )


def test_viscosity_multiplier_is_applied_literally(monkeypatch):
    # 当前乘数恒 1.0；改动乘数时效用必须精确等比变化，
    # 证明接线是“真乘上去的”，也没有 1e-9 之类的私货兜底。
    one_row = pool_records([('1-0', 20.0)])
    one_row[0]['delta_pos'] = 4

    baseline = app.BettingStrategyExpertSystem.analyze_pool15(one_row)
    base_row = baseline['results'][0]
    assert base_row['viscosity_multiplier'] == 1.0

    monkeypatch.setattr(
        app.BettingStrategyExpertSystem,
        'protocol_viscosity_weight',
        classmethod(lambda cls, level, score: {'multiplier': 2.0, 'source': 'test', 'applied': True}),
    )
    doubled = app.BettingStrategyExpertSystem.analyze_pool15(one_row)
    doubled_row = doubled['results'][0]
    assert doubled_row['utility'] == base_row['utility'] * 2.0
    assert doubled_row['viscosity_applied'] is True


@pytest.mark.skip(reason='TODO(等用户给出粘度乘数公式规格): 真乘数实现后放开；此刻不许写占位公式')
def test_viscosity_multiplier_once_formula_spec_lands():
    raise AssertionError('等粘度公式规格，见 spec 的 B 段')
