"""同一字段在所有渲染器里必须同值 —— 通用守卫。

缺陷模式（上一个 PR 遇到的那一类）：同一份数据被两个渲染器各算一遍，
**只有一处算对**。CLI 把 0..1 的分数乘了 100 写成 `Δ+8.90pp`，TG 段落
忘了乘，同一份数据在同一条结论旁写出 `Δ+0.09pp`，差 100 倍。当时的修法是
逐处补测试，于是下一个渲染器再加进来时，同样的缺陷可以再犯一次。

这里改成一个模式：

  1. `FAMILIES` 把「渲染同一个量」的渲染器编成一组，每组带一份共用源数据、
     一个「把文本抠回数值」的解析器、一个由源数据算出的参照表；
  2. 遍历每组里**每一个**渲染器，把该段落里所有 `xxxpp` 数字抠出来与参照表
     逐项对账（容差按显示精度定：两位小数用 0.011）；
  3. 同一组内两个渲染器在共享字段上必须给出**同一个数**——这才是「同值」；
     源数据对账只能证明「各自都对」，出错的那一侧往往也能通过它自己的对账；
  4. `test_renderer_inventory_is_complete` 反向扫源码：任何会打印 `pp` 数字的
     函数都必须先登记进 `FAMILIES`（参与对账）或 `EXEMPT`（写明为什么不用），
     否则红。所以「新增一个渲染器但忘了换算」不可能悄悄发生——「漏登记」
     本身就是红的。

范围说明（有意收窄）：这条守卫钉的是**百分点（pp）数字**，因为那正是上一个
PR 的缺陷形态（0..1 分数该乘 100 而没乘）。百分比列（`_pct`）、赔率列
（`format_matrices_*`）也各自在 `FAMILIES` 里，但它们由显式的解析器登记，
不在自动扫描范围内——自动扫描只认 `{...}pp` 这一种字面量，宁窄不宽，
漏报比误报更容易被发现（误报会让整条守卫被 `EXEMPT` 塞满而失效）。

反证（均实测，见 PR 报告的反证表）：
  * `_format_same_caliber_lines` 的 `_pp` 不乘 100
    → `test_every_pp_number_matches_its_source[caliber_delta]` 与
      `test_renderers_agree_on_every_shared_field[caliber_delta]` 变红；
  * `score_books.format_analysis_text` 的 `_scale_pct` 不乘 100 → 同上；
  * `format_mkt_hot_wdl_matrix_tg_lines` 的 Δ 写成 0..1 分数
    → `[pack_market_minus_theory]` 变红；
  * 新增一个不登记的 pp 渲染器 → `test_renderer_inventory_is_complete` 变红。
"""
from __future__ import annotations

import ast
import importlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import blade_deepseek_pipeline as bd  # noqa: E402
import score_books as sb  # noqa: E402
import score_matrices as sm  # noqa: E402

# 两位小数显示的舍入上限。
TWO_DP = 0.011
# 一位小数显示的舍入上限。
ONE_DP = 0.051
# 四位小数显示的舍入上限。
FOUR_DP = 0.00011
# 两位小数显示的赔率。
ODDS_2DP = 0.0051


@dataclass(frozen=True)
class Family:
    """一组「渲染同一个量」的渲染器。

    name       family 名（出现在断言 id 里）
    quantity   被渲染的量，写清楚单位——报告的对照表就是它
    unit       'pp' / 'pct' / 'odds'
    tolerance  由显示精度决定的容差
    renderers  参与对账的 (模块名, 函数名)，必须解析得到真函数
    build      返回 {'render': {renderer: 无参可调用}, 'reference': {...}}
    extract    文本 -> {字段: 数值}，字段名与 reference 的键一一对应
    """

    name: str
    quantity: str
    unit: str
    tolerance: float
    renderers: tuple[tuple[str, str], ...]
    build: Callable[[], dict[str, Any]]
    extract: Callable[[str], dict[str, float]]


# --------------------------------------------------------------------------- #
# 1) tier_a 定价差 Δ：离线 CLI 与 TG 同口径段
# --------------------------------------------------------------------------- #
def _caliber_source() -> dict[str, Any]:
    """竞彩 31 项 × 市场 25 项的同一份残差分析，给 CLI 与 TG 两个渲染器共用。

    25 项盘面（而不是 26 项）是刻意的：26 项下 `other_delta` 恒为 None，
    「其他合计」那条数字分支根本走不到，两个渲染器都只会打「不可比」，
    对账就退化成对空。
    """
    market_pcts = [
        4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0, 9.0, 10.0, 12.0, 14.0, 3.9,
        3.7, 4.2, 4.8, 5.2, 6.1, 7.2, 8.4, 9.6, 11.0, 13.0, 15.0, 16.0,
    ]
    market_odds = {k: 100.0 / pct for k, pct in zip(sb.MARKET_25_KEYS, market_pcts)}
    jc_odds = {k: 20.0 for k in sb.JC_31_KEYS}
    analysis = sb.analyze_residuals(jc_odds, market_odds, book_size=25, top_n=40)
    tier_a = analysis['tier_a']
    assert tier_a['other_delta'] is not None, (
        '25 项盘面必须走到 other_delta 数字分支，否则这条对账覆盖不到它'
    )
    # TG 侧吃的是 same_caliber dict；用同一份 tier_a 原样装出来，
    # 这里不做任何单位换算——换算只许发生在渲染层。
    same_caliber = {
        'available': True,
        'policy_label': tier_a['policy_label'],
        'per_bucket_lossless': tier_a['per_bucket_lossless'],
        'aggregate_lossless': tier_a['aggregate_lossless'],
        'aggregate_sizes': dict(tier_a['aggregate_sizes']),
        'rows': [{k: r[k] for k in ('bucket', 'jc', 'market', 'delta')} for r in tier_a['rows']],
        'structural_buckets': [],
        'structural_rows': [],
        'other_delta': tier_a['other_delta'],
        'warnings': [],
    }
    reference = {
        r['bucket']: r['delta'] * 100.0 for r in tier_a['rows'] if r['delta'] is not None
    }
    reference['「其他」合计'] = tier_a['other_delta'] * 100.0
    return {
        'render': {
            ('score_books', 'format_analysis_text'):
                lambda: sb.format_analysis_text(analysis, top_n=40),
            ('blade_deepseek_pipeline', '_format_same_caliber_lines'):
                lambda: '\n'.join(
                    bd.DeepSeekAnalyzer._format_same_caliber_lines({'same_caliber': same_caliber})),
        },
        'reference': reference,
    }


_CALIBER_ROW = re.compile(r'^\s+(\S+)\s+竞彩\s+\S+\s+市场\s+\S+\s+Δ([+-]\d+\.\d{2})pp')
_CALIBER_TOTAL = re.compile(r'「其他」合计.*?Δ([+-]\d+\.\d{2})pp')


def _caliber_extract(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        if 'Δ 不适用' in line:
            continue
        total = _CALIBER_TOTAL.search(line)
        if total:
            out['「其他」合计'] = float(total.group(1))
            continue
        row = _CALIBER_ROW.match(line)
        if row:
            out[row.group(1)] = float(row.group(2))
    return out


# --------------------------------------------------------------------------- #
# 2) 市均% − 理论%：31 对比表与市热矩阵
# --------------------------------------------------------------------------- #
def _pack_source() -> dict[str, Any]:
    """一份真实的 31 全表：竞彩 31 项给全，市均只覆盖 25 项，λ 冻结。"""
    pack = _pack_source_pack()
    reference: dict[str, float] = {}
    for row in pack['rows']:
        market, theory = row.get('market'), row.get('theory')
        if market is None or theory is None:
            continue
        reference[str(row['score'])] = float(market) - float(theory)
    assert reference, '31 全表没有一行同时有 market 与 theory，对账会退化成对空'
    return {'render': _pack_source_render(), 'reference': reference}


def _pack_source_render() -> dict[str, Callable[[], str]]:
    """31 全表的三类渲染器（共用同一份 pack）。"""
    pack = _pack_source_pack()
    return {
        ('blade_deepseek_pipeline', 'format_theo_mkt_31_tg_lines'):
            lambda: '\n'.join(bd.DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(pack)),
        ('blade_deepseek_pipeline', 'format_mkt_hot_wdl_matrix_tg_lines'):
            lambda: '\n'.join(bd.DeepSeekAnalyzer.format_mkt_hot_wdl_matrix_tg_lines(pack)),
        ('blade_deepseek_pipeline', 'format_mkt_short_wdl_matrix_tg_lines'):
            lambda: '\n'.join(bd.DeepSeekAnalyzer.format_mkt_short_wdl_matrix_tg_lines(
                bd.DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(pack))),
    }


_THEO_31_ROW = re.compile(
    r'^\s+\d\d\s+(\S+)\s+市均\s*[\d.]+\s+([\d.]+)%\s+竞彩\s*[\d.]+\s+([\d.]+)%\s+Δ\s+([+-][\d.]+)'
)
_HOT_ROW = re.compile(r'^\s+(\S+)\s+市均([\d.]+)%\s+理论([\d.]+)%\s+Δ([+-][\d.]+)pp')
_SHORT_ROW = re.compile(r'^\s+(\S+)\s+市均([\d.]+)%\s+竞彩([\d.]+)%\s+差([+-][\d.]+)pp')


def _market_minus_theory_extract(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        row = _THEO_31_ROW.match(line) or _HOT_ROW.match(line)
        if row:
            out[row.group(1)] = float(row.group(4))
    return out


# --------------------------------------------------------------------------- #
# 3) 市均% / 竞彩% 两列：31 对比表与市短矩阵
# --------------------------------------------------------------------------- #
def _pack_pct_source() -> dict[str, Any]:
    """两列百分比的源值来自 pack 本身，不是从渲染文本反算。"""
    pack = _pack_source_pack()
    reference: dict[str, float] = {}
    for row in pack['rows']:
        market, jc_pct = row.get('market'), row.get('jc_odds_pct')
        if market is None:
            continue
        reference[f'{row["score"]}.market_pct'] = float(market)
        if jc_pct is not None:
            reference[f'{row["score"]}.jc_pct'] = float(jc_pct)
    assert reference, '31 全表没有任何一项带市均概率，对账会退化成对空'
    return {'render': _pack_source_render(), 'reference': reference}


def _pack_pct_extract(text: str) -> dict[str, float]:
    """把两列拍平成 `比分.列名`，与参照表逐字段对账。"""
    out: dict[str, float] = {}
    for line in text.splitlines():
        row = _THEO_31_ROW.match(line) or _SHORT_ROW.match(line)
        if row:
            out[f'{row.group(1)}.market_pct'] = float(row.group(2))
            out[f'{row.group(1)}.jc_pct'] = float(row.group(3))
    return out


def _pack_pct_reference(source: dict[str, Any] | None = None) -> dict[str, float]:
    """两列百分比的源值必须来自 pack 本身，不是从渲染文本反算。"""
    pack = _pack_source_pack()
    out: dict[str, float] = {}
    for row in pack['rows']:
        market, jc_pct = row.get('market'), row.get('jc_odds_pct')
        if market is None:
            continue
        out[f'{row["score"]}.market_pct'] = float(market)
        if jc_pct is not None:
            out[f'{row["score"]}.jc_pct'] = float(jc_pct)
    return out


_PACK_CACHE: dict[str, Any] = {}


def _pack_source_pack() -> dict[str, Any]:
    """同一次构建只算一次 pack：两个 family 用的是同一份数据。"""
    if 'pack' not in _PACK_CACHE:
        market25 = {
            '1-0': 9.0, '2-0': 15.0, '2-1': 8.0, '3-0': 30.0, '3-1': 20.0, '3-2': 30.0,
            '4-0': 70.0, '4-1': 50.0, '4-2': 80.0, '4-3': 90.0, '3-4': 95.0,
            '0-0': 12.0, '1-1': 7.0, '2-2': 16.0, '3-3': 55.0, '4-4': 120.0,
            '0-1': 12.0, '0-2': 22.0, '1-2': 15.0, '0-3': 50.0, '1-3': 37.0, '2-3': 55.0,
            '0-4': 120.0, '1-4': 90.0, '2-4': 140.0,
        }
        jc_odds = {k.replace(':', '-'): 6.0 for k in sb.JC_31_KEYS}
        market_keys = set(market25)
        _PACK_CACHE['pack'] = bd.DeepSeekAnalyzer.build_theo_mkt_31_pack(
            [], jc_score_odds=jc_odds, extra_market_odds=market25,
            lam_home=1.4, lam_away=1.1,
            market_provenance={k: ('desktop_cs' if k in market_keys else 'jc_official')
                               for k in jc_odds},
        )
    return _PACK_CACHE['pack']


# --------------------------------------------------------------------------- #
# 4) 三套比分矩阵：Markdown（桌面）与 Telegram（分页）
# --------------------------------------------------------------------------- #
def _matrix_source() -> dict[str, Any]:
    market = {sc: 10.0 for sc in sm.THIRTY_ONE_SCORES}
    market['2-1'] = 5.0
    market['1-1'] = 5.0
    jc = {sc: {'odds': 4.0, 'heat_seats': 0} for sc in sm.THIRTY_ONE_SCORES}
    jc['2-1'] = {'odds': 4.5, 'heat_seats': 3}
    jc['1-1'] = {'odds': 4.0, 'heat_seats': 2}
    bundle = sm.build_score_matrices('M20261127_PARITY', market, jc)
    reference: dict[str, float] = {}
    for row in bundle.matrix_31:
        if row.market_avg_odds is not None:
            reference[f'{row.score}.market_avg_odds'] = row.market_avg_odds
        reference[f'{row.score}.jc_odds'] = row.lottery_odds
        if row.delta is not None:
            reference[f'{row.score}.delta'] = row.delta
    for pool in bundle.matrix_tactical_pools:
        if pool.avg_delta is not None:
            reference[f'{pool.pool_id}.avg_delta'] = pool.avg_delta
    return {
        'render': {
            ('score_matrices', 'format_matrices_markdown'):
                lambda: sm.format_matrices_markdown(bundle),
            ('score_matrices', 'format_matrices_telegram'):
                lambda: '\n'.join(sm.format_matrices_telegram(bundle, max_len=100000)),
        },
        'reference': reference,
    }


_MD_SCORE_ROW = re.compile(
    r'^\|\s*(\d-\d)\s*\|\s*\S+\s*\|\s*(\S+)\s*\|\s*([\d.]+)\s*\|\s*([+-][\d.]+)\s*\|')
_TG_SCORE_ROW = re.compile(
    r'^(\d-\d)\s+\S+\s+市均\s*(\S+)\s+竞彩\s*([\d.]+)\s+Δ\s*([+-][\d.]+)')
_MD_POOL_ROW = re.compile(r'^\|\s*(P\d+_\S+)\s*\|.*?\|\s*([+-][\d.]+)\s*\|')
_TG_POOL_ROW = re.compile(r'^(P\d+_\S+)\s+均Δ\s*([+-][\d.]+)')


def _matrix_extract(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        row = _MD_SCORE_ROW.match(line) or _TG_SCORE_ROW.match(line)
        if row:
            score = row.group(1)
            if row.group(2) not in ('—', '-'):
                out[f'{score}.market_avg_odds'] = float(row.group(2))
            out[f'{score}.jc_odds'] = float(row.group(3))
            out[f'{score}.delta'] = float(row.group(4))
            continue
        row = _MD_POOL_ROW.match(line) or _TG_POOL_ROW.match(line)
        if row:
            out[f'{row.group(1)}.avg_delta'] = float(row.group(2))
    return out


# --------------------------------------------------------------------------- #
# 5) 模型 vs 市场分歧力度：三处渲染同一个百分点数
# --------------------------------------------------------------------------- #
def _divergence_source() -> dict[str, Any]:
    app = importlib.import_module('app_v84_hermes')
    info = {
        'derived_markets': {
            '1x2': {'home': 0.30, 'draw': 0.28, 'away': 0.42},
            'ou': {'line': 2.5, 'over': 0.46},
        },
        'odds_1x2': {'home': 3.40, 'draw': 3.60, 'away': 2.60},
        'tg_implied_probs': {
            f'prob_{goals}': prob
            for goals, prob in enumerate([0.10, 0.18, 0.22, 0.20, 0.14, 0.08, 0.05])
        },
    }
    items = app._ur_divergence_items(info)
    assert len(items) >= 2, f'夹具只产生 {len(items)} 条分歧项，不足以对账'
    reference = {item['topic']: float(item['magnitude_pp']) for item in items}
    # 去掉预格式化字段的同一份 items：这样两个读取方才会真正走
    # `magnitude_pp` 自己换算的那条路径，否则三条渲染里只有一条被执行，
    # 另外两条只是转抄 `magnitude_text`，单位写错了也测不出来。
    bare = [{k: v for k, v in item.items() if k != 'magnitude_text'} for item in items]
    return {
        'render': {
            # `magnitude_text` 就是 `_ur_divergence_items` 写出来的渲染结果
            ('app_v84_hermes', '_ur_divergence_items'):
                lambda: '\n'.join(f"{i['topic']} {i['magnitude_text']}" for i in items),
            ('app_v84_hermes', '_ur_magnitude_text'):
                lambda: '\n'.join(f"{i['topic']} 差 {app._ur_magnitude_text(i)}" for i in bare),
            ('app_v84_hermes', '_ur_divergence_line'):
                lambda: '\n'.join(app._ur_divergence_line(i) for i in bare),
        },
        'reference': reference,
    }


_PP_NUMBER = re.compile(r'Δ?([+-]?\d+\.\d+)pp')


def _divergence_extract(text: str) -> dict[str, float]:
    """每行取第一个 pp 数字：行首主题 → 数值。"""
    out: dict[str, float] = {}
    for line in text.splitlines():
        found = _PP_NUMBER.search(line)
        if not found:
            continue
        topic = line.split(' ', 1)[0].rstrip(':：')
        out[topic] = float(found.group(1))
    return out


FAMILIES: tuple[Family, ...] = (
    Family(
        name='caliber_delta',
        quantity='档 A 每个市场桶的定价差 Δ（0..1 分数 → 百分点）',
        unit='pp',
        tolerance=TWO_DP,
        renderers=(
            ('score_books', 'format_analysis_text'),
            ('blade_deepseek_pipeline', '_format_same_caliber_lines'),
        ),
        build=_caliber_source,
        extract=_caliber_extract,
    ),
    Family(
        name='pack_market_minus_theory',
        quantity='市均% − 理论%（pack 行上的 delta）',
        unit='pp',
        tolerance=ONE_DP,
        renderers=(
            ('blade_deepseek_pipeline', 'format_theo_mkt_31_tg_lines'),
            ('blade_deepseek_pipeline', 'format_mkt_hot_wdl_matrix_tg_lines'),
        ),
        build=_pack_source,
        extract=_market_minus_theory_extract,
    ),
    Family(
        name='pack_pct_columns',
        quantity='31 表的市均% / 竞彩% 两列',
        unit='pct',
        tolerance=ONE_DP,
        renderers=(
            ('blade_deepseek_pipeline', 'format_theo_mkt_31_tg_lines'),
            ('blade_deepseek_pipeline', 'format_mkt_short_wdl_matrix_tg_lines'),
        ),
        build=_pack_pct_source,
        extract=_pack_pct_extract,
    ),
    Family(
        name='matrix_odds_and_delta',
        quantity='每个比分的市均赔率 / 竞彩赔率 / 均差，与战术池均差',
        unit='odds',
        tolerance=ODDS_2DP,
        renderers=(
            ('score_matrices', 'format_matrices_markdown'),
            ('score_matrices', 'format_matrices_telegram'),
        ),
        build=_matrix_source,
        extract=_matrix_extract,
    ),
    Family(
        name='ur_divergence_pp',
        quantity='模型 vs 市场的分歧力度（百分点）',
        unit='pp',
        tolerance=ONE_DP,
        renderers=(
            ('app_v84_hermes', '_ur_divergence_items'),
            ('app_v84_hermes', '_ur_magnitude_text'),
            ('app_v84_hermes', '_ur_divergence_line'),
        ),
        build=_divergence_source,
        extract=_divergence_extract,
    ),
)

FAMILY_BY_NAME = {family.name: family for family in FAMILIES}

#: 渲染器 → 参与的 family。一个函数可以出现在多个 family 里
#: （31 对比表同时渲染 Δ 与两列百分比）。
RENDERER_FAMILIES: dict[tuple[str, str], tuple[str, ...]] = {}
for _family in FAMILIES:
    for _renderer in _family.renderers:
        RENDERER_FAMILIES[_renderer] = RENDERER_FAMILIES.get(_renderer, ()) + (_family.name,)

#: 会打印 `pp` 数字、但**不参与**跨渲染器对账的函数，每条必须写明理由。
#: 目前为空：扫描面里所有打印 `pp` 的函数都进了 FAMILIES。留这个机制是为了
#: 将来真有「只透传、不换算」的渲染器时不必放宽扫描规则。
EXEMPT: dict[tuple[str, str], str] = {}


# --------------------------------------------------------------------------- #
# 1. 每个渲染器的每个数字都要与源数据对上
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('family_name', sorted(FAMILY_BY_NAME))
def test_every_pp_number_matches_its_source(family_name: str) -> None:
    """遍历该 family 全部渲染器，把段落里每一个数字都和源数据对账。

    不是「钉一个数字」：渲染器输出多少个数就核多少个数。
    """
    family = FAMILY_BY_NAME[family_name]
    case = family.build()
    reference = case['reference']
    assert reference, f'{family.name} 的参照表是空的，对账没有意义'

    for renderer in family.renderers:
        text = case['render'][renderer]()
        got = family.extract(text)
        assert got, f'{family.__class__.__name__} {renderer} 一个数字都没渲染出来'

        for key, value in got.items():
            assert key in reference, (
                f'{renderer[0]}.{renderer[1]} 渲染了参照表里没有的字段 {key!r}；'
                '新字段必须先有源值才能对账'
            )
            assert abs(value - reference[key]) <= family.tolerance, (
                f'{renderer[0]}.{renderer[1]} 把 {key} 渲染成 {value}，源数据是 '
                f'{reference[key]}（差 {value - reference[key]:+.4f}，单位 {family.unit}）'
                '—— 同一字段在所有渲染器里必须同值'
            )
        # 覆盖度：渲染器给出的数字不能只核到一两个就过。
        assert len(got) >= min(3, len(reference)), (
            f'{renderer} 只核到 {len(got)} 个字段（参照表 {len(reference)} 个），'
            '守卫覆盖不足'
        )


# --------------------------------------------------------------------------- #
# 2. 同一 family 内，两个渲染器在共享字段上必须给出同一个数值
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('family_name', sorted(FAMILY_BY_NAME))
def test_renderers_agree_on_every_shared_field(family_name: str) -> None:
    """这是「同值」本身：不看源数据，只要求两个渲染器互相一致。

    缺陷模式里出错的那一侧往往也能通过「与源数据对账」（因为对账口径就是
    它自己的那一套）；只有互相比对才能证明它们说的是同一个答案。
    """
    family = FAMILY_BY_NAME[family_name]
    case = family.build()
    rendered = {renderer: family.extract(case['render'][renderer]())
                for renderer in family.renderers}
    first_renderer, first_values = next(iter(rendered.items()))
    for renderer, values in rendered.items():
        if renderer == first_renderer:
            continue
        shared = set(first_values) & set(values)
        assert shared, f'{first_renderer} 与 {renderer} 没有共享字段，比对是空的'
        for key in sorted(shared):
            assert first_values[key] == pytest.approx(values[key], abs=family.tolerance), (
                f'{first_renderer[1]} 与 {renderer[1]} 对 {key} 给出不同数值：'
                f'{first_values[key]} vs {values[key]}（容差 {family.tolerance}）'
            )


# --------------------------------------------------------------------------- #
# 3. 清单完整性：新增渲染器漏登记 → 红
# --------------------------------------------------------------------------- #
def _pp_emitters(module_path: Path) -> dict[str, list[int]]:
    """扫出一个模块里所有会打印 `pp` 数字的函数。

    只认紧跟在格式化值之后的 `pp` 字面量（`{x:.2f}pp` / `Δ{x}pp`），
    这是百分点的唯一书写形态；`Δh={x:.3f}`（球）与 `%`（百分比）不在此列，
    它们由 FAMILIES 里的显式解析器登记，见模块 docstring 的范围说明。
    """
    tree = ast.parse(module_path.read_text(encoding='utf-8'))
    found: dict[str, list[int]] = {}

    def walk(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
                continue
            if isinstance(child, ast.JoinedStr):
                values = child.values
                for index, value in enumerate(values[:-1]):
                    if not isinstance(value, ast.FormattedValue):
                        continue
                    nxt = values[index + 1]
                    if (isinstance(nxt, ast.Constant) and isinstance(nxt.value, str)
                            and nxt.value.startswith('pp')):
                        found.setdefault(owner, []).append(child.lineno)
            walk(child, owner)

    walk(tree, '<module>')
    return found


def _shipped_modules() -> list[Path]:
    """仓库根下随单文件构建一起发布的运行时模块。"""
    names = [
        'blade_deepseek_pipeline.py', 'score_books.py', 'score_matrices.py',
        'sporttery_jc.py', 'match_data_matrix.py', 'score_convergence_engine.py',
        'tg_goal_table.py', 'app_v84_hermes.py',
    ]
    return [ROOT / name for name in names if (ROOT / name).exists()]


def test_renderer_inventory_is_complete() -> None:
    """源码里每个会打印 pp 数字的函数，必须登记进 FAMILIES 或 EXEMPT。

    这是「新增一个渲染器但忘了换算」的兜底：新渲染器一出现就被扫到，
    既不在 FAMILIES（没有对账）也不在 EXEMPT（没有理由）时本断言即红。

    反方向（改名/删除）由两条更精确的断言负责：
    `test_every_registered_renderer_really_exists` 与
    `test_inventory_covers_the_known_renderer_pairs`。
    """
    discovered: dict[tuple[str, str], list[int]] = {}
    for path in _shipped_modules():
        for func, lines in _pp_emitters(path).items():
            discovered[(path.stem, func)] = lines

    registered = {*RENDERER_FAMILIES, *EXEMPT}
    missing = sorted(
        f'{module}.{func}:{lines}' for (module, func), lines in discovered.items()
        if (module, func) not in registered
    )
    assert not missing, (
        '这些函数会打印 pp 数字但没登记：新增渲染器必须进 FAMILIES（喂同一份数据对账）'
        f'或 EXEMPT（写明为什么不需要对账）：{missing}'
    )

    # 豁免必须还在：豁免掉一个已经被删掉的函数，等于把守卫的空白当成了结论。
    stale = sorted(f'{module}.{func}' for (module, func) in EXEMPT
                   if (module, func) not in discovered)
    assert not stale, f'EXEMPT 里登记了源码里不再打印 pp 的函数（该删掉豁免）：{stale}'


def test_inventory_covers_the_known_renderer_pairs() -> None:
    """把已知的渲染器对钉死：删掉任何一个 family 或它的成员，本断言就红。

    清单完整性那条是「源码 → 清单」的单向检查；这条是反方向，
    防止有人为了让上面那条变绿而把整个 family 悄悄删掉。
    """
    required = {
        ('score_books', 'format_analysis_text'),
        ('blade_deepseek_pipeline', '_format_same_caliber_lines'),
        ('blade_deepseek_pipeline', 'format_theo_mkt_31_tg_lines'),
        ('blade_deepseek_pipeline', 'format_mkt_hot_wdl_matrix_tg_lines'),
        ('blade_deepseek_pipeline', 'format_mkt_short_wdl_matrix_tg_lines'),
        ('score_matrices', 'format_matrices_markdown'),
        ('score_matrices', 'format_matrices_telegram'),
        ('app_v84_hermes', '_ur_divergence_items'),
        ('app_v84_hermes', '_ur_magnitude_text'),
        ('app_v84_hermes', '_ur_divergence_line'),
    }
    assert required <= set(RENDERER_FAMILIES), (
        f'渲染器清单漏掉了：{sorted(required - set(RENDERER_FAMILIES))}'
    )
    for family in FAMILIES:
        assert len(family.renderers) >= 2, (
            f'{family.name} 只有一个渲染器，同值比较退化成自比'
        )


def test_every_registered_renderer_really_exists() -> None:
    """登记的名字必须解析成真函数，否则对账测的是别的东西。"""
    for module_name, func_name in sorted(RENDERER_FAMILIES):
        module = importlib.import_module(module_name)
        holder = module if hasattr(module, func_name) else getattr(module, 'DeepSeekAnalyzer')
        assert callable(getattr(holder, func_name, None)), f'{module_name}.{func_name} 不存在'


def test_every_exemption_states_a_reason() -> None:
    """豁免必须写清楚理由；一句「不用」不算理由，否则这条守卫会被塞满豁免架空。"""
    for renderer, reason in EXEMPT.items():
        assert len(str(reason).strip()) >= 30, f'{renderer} 的豁免理由太短：{reason!r}'
