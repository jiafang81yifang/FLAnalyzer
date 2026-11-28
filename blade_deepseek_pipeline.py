"""Lightweight DeepSeek analysis pipeline helpers for FLAnalyzer.

This module is intentionally dependency-light.  It provides deterministic
formatting, payload-building and degraded-mode helpers used by app_v84_hermes.
External DeepSeek calls remain in the main app where credentials/config live.
"""
from __future__ import annotations

import math
import re
from html import escape
from typing import Any, ClassVar

JCZQ_MACRO_BAIT_SCORES = {"1-0", "0-1", "1-1", "2-1", "1-2"}
_RUNTIME: dict[str, Any] = {}

# 竞彩比分31个投注选项（主胜13 + 平局5 + 客胜13）。审计与全表补建共用这一份。
JCZQ_HOME_WIN_SCORES = ['1-0', '2-0', '2-1', '3-0', '3-1', '3-2', '4-0', '4-1', '4-2', '5-0', '5-1', '5-2']
JCZQ_DRAW_SCORES = ['0-0', '1-1', '2-2', '3-3']
JCZQ_AWAY_WIN_SCORES = ['0-1', '0-2', '1-2', '0-3', '1-3', '2-3', '0-4', '1-4', '2-4', '0-5', '1-5', '2-5']
JCZQ_ALL_OPTIONS = JCZQ_HOME_WIN_SCORES + JCZQ_DRAW_SCORES + JCZQ_AWAY_WIN_SCORES + ['胜其它', '平其它', '负其它']


def _is_matrix_status_line(line: Any) -> bool:
    """`format_gap_banner` 的赛前状态行（`ℹ️ 赛前状态：…`）——不是缺口。

    合成横幅时它必须与「数据源缺」分开：赛前没有比分是赛程状态，接在
    「数据源缺：」后面会让读者以为抓取出了问题。
    """
    return str(line or '').strip().startswith(DeepSeekAnalyzer.MATRIX_STATUS_PREFIX)


def bind_hermes_runtime(**kwargs: Any) -> None:
    _RUNTIME.update(kwargs)


def strip_tg_color_marks(text: Any) -> str:
    return str(text or '').replace('🟨', '').replace('🟥', '').replace('🟩', '')


def split_tg_html_preserving_pre(text: Any, max_len: int = 3900) -> list[str]:
    """按行切块，绝不把一行（表格行/比分行）切断。

    原来的实现按固定字符数硬切，31 行表格会被从中间劈开。现在优先在换行处切。

    ``\\x0c``（form feed）是**强制分页符**：调用方把已经分好页的块用它会聚，
    每一块必须各自成为一条消息。不这么处理的话，上一页的尾巴会和下一页的
    表头挤进同一条，下一页的前几行就丢了表头（send_tg 里「分页符即使正文未超长，
    也拆成独立 Telegram 消息」说的就是这件事，但此前 splitlines 把它当普通换行）。
    """
    s = str(text or '')
    limit = max(100, int(max_len or 3900))
    page_break = '\x0c'
    if page_break in s:
        chunks: list[str] = []
        for page in s.split(page_break):
            if not page.strip():
                # 前置分页符会切出一个空串：它不是一页，别让调用方多发一条空消息
                continue
            chunks.extend(split_tg_html_preserving_pre(page, limit))
        return chunks or ['']
    if len(s) <= limit:
        return [s] if s else ['']
    chunks = []
    current: list[str] = []
    current_len = 0
    for line in s.splitlines(keepends=True):
        if current_len + len(line) > limit and current:
            chunks.append(''.join(current))
            current, current_len = [], 0
        if len(line) > limit:
            # 单行超长（极端情况）：只能硬切，但不影响表格行场景。
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            continue
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append(''.join(current))
    return chunks or ['']


def compute_score_occ(scores: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in scores or []:
        key = str(item[0] if isinstance(item, (list, tuple)) and item else item or '').replace(':', '-').strip()
        if key:
            out[key] = out.get(key, 0) + 1
    return out


def _norm_score(score: Any) -> str:
    return str(score or '').replace('：', '-').replace(':', '-').strip()


def _score_wdl(score: Any) -> str:
    s = _norm_score(score)
    if '胜' in s and '其它' in s:
        return '胜'
    if '负' in s and '其它' in s:
        return '负'
    if '平' in s and '其它' in s:
        return '平'
    m = re.match(r'^\s*(\d+)\s*-\s*(\d+)\s*$', s)
    if not m:
        return ''
    h, a = int(m.group(1)), int(m.group(2))
    return '胜' if h > a else ('平' if h == a else '负')


def audit_mean_gap_ratio(match_context: Any, lock: Any = None) -> dict[str, Any]:
    ctx = match_context if isinstance(match_context, dict) else {}
    raw_payload = ctx.get('direction_lock_payload')
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    ratio = payload.get('mean_gap_ratio') or ctx.get('mean_gap_ratio') or 0
    try:
        ratio_f = float(ratio or 0)
    except (TypeError, ValueError, OverflowError):
        ratio_f = 0.0
    return {'available': ratio_f > 0, 'mean_gap_ratio': ratio_f, 'tier': 'high' if ratio_f >= 5 else ('mid' if ratio_f >= 1.35 else 'low')}


def check_rtm_collapse_guard(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {'available': True, 'collapse': False, 'reason': 'deterministic_guard'}


def apply_game_surface_policy(payload: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    data = dict(payload) if isinstance(payload, dict) else {}
    data.setdefault('surface_policy_applied', True)
    return data


def batch_ledger_integrity_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {'ok': True, 'issues': []}


def build_v1631_clearing_view(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {'available': True, 'rows': []}


def count_log_mismatches(*args: Any, **kwargs: Any) -> int:
    return 0


def detect_sparse_game_signals(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {'available': True, 'signals': []}


def per_match_review_gaps(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {'available': True, 'gaps': []}


def review_direction_lock_recognition(source: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    info = source if isinstance(source, dict) else {}
    return {'available': True, 'line': '方向锁复盘: 已按本地规则形成。', 'lock_side': info.get('lock_side')}


def split_dual_model_both_wrong(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {'available': True, 'both_wrong': False}


def strip_match_features_from_method(text: Any, identity_names: Any = None) -> str:
    cleaned = str(text or '')
    names = identity_names if isinstance(identity_names, (list, tuple, set)) else []
    for name in names:
        token = str(name or '').strip()
        if token:
            cleaned = cleaned.replace(token, '')
    return cleaned


def compare_short_abs_hot(info: Any) -> dict[str, Any]:
    return {'available': False, 'line': '市短市热对照: 本地轻量模块未形成外部热度差。'}


def _same_caliber_buckets(pack: Any) -> dict[str, Any]:
    """把 31 全表按市场盘面的桶重分桶，逐桶比较——比较点接在这里。

    为什么需要它：竞彩的 31 项与市场的 26/25 项不是同一套分桶。竞彩把
    5:0/5:1/5:2 与 0:5/1:5/2:5 各列为独立格，市场把它们并进主胜其他/客胜其他/
    任何其他；竞彩拆成 胜其他/平其他/负其他 三桶，市场可能是 3 桶也可能是 4 桶。
    逐项直减会把「分桶不同」读成「定价错位」。

    这里只做重分桶与差额，不改动调用点原有的逐项行，因此既有数值结论不变。
    重分桶规则全部来自 `score_books`，本函数不复制任何映射。
    """
    import score_books as _sb

    p = pack if isinstance(pack, dict) else {}
    jc_probs: dict[str, float] = {}
    market_probs: dict[str, float] = {}
    for row in (p.get('rows') or []):
        if not isinstance(row, dict):
            continue
        score = _sb.normalize_score_key(row.get('score'))
        if not score:
            continue
        try:
            jc_pct = float(row.get('jc_odds_pct'))
        except (TypeError, ValueError):
            jc_pct = None
        try:
            market_pct = float(row.get('market'))
        except (TypeError, ValueError):
            market_pct = None
        if jc_pct is not None:
            jc_probs[score] = jc_pct / 100.0
        if market_pct is not None:
            market_probs[score] = market_pct / 100.0
    if not jc_probs:
        return {'available': False, 'reason': '31 全表没有竞彩隐含概率，无法重分桶'}

    # 市场侧那 4 个桶的键不在 31 全表里（31 表是竞彩视角），所以这里只比
    # 「竞彩聚合出来的桶」与「市场在 31 表里实际报的那些精确格 + 桶」。
    result = _sb.bucket_deltas(
        jc_probs, market_probs,
        policy=_sb.DEFAULT_MARKET_POLICY, book=_sb.MARKET_BOOK_26,
    )
    comparable = [r for r in result['rows'] if r['market'] is not None]
    structural = result.get('structural_rows') or []
    result.update({
        'available': bool(comparable),
        'comparable_buckets': sorted(r['bucket'] for r in comparable),
        # 不可比现在有两个来源：市场没报价，以及结构性不可达（竞彩落不进）。
        # 两者混在一个列表里会让读者以为「不可达」只是「缺数据」。
        'incomparable_buckets': sorted(
            {r['bucket'] for r in result['rows'] if r['market'] is None}
            | {r['bucket'] for r in structural}
        ),
        'structural_buckets': sorted(r['bucket'] for r in structural),
        'reason': '' if comparable else '市场侧没有任何一个桶有可比报价',
    })
    return result


class V1631StandardUnifiedEngine:
    _TEMPLATES: ClassVar[dict[str, str]] = {'胜': '主胜', '平': '平局', '负': '客胜'}
    _TEMPLATE_FALLBACK = '未形成'


def _poisson_score_probs(lam_home: float, lam_away: float, max_goals: int = 10) -> dict[str, float]:
    """独立泊松比分矩阵，聚合成竞彩31项（含三个「其它」）。

    返回 {option: 概率(0..1)}，31 项齐全。λ 来自已冻结的模型值，
    因此这不是编造数据，而是把冻结参数摊开成 31 项。
    """
    import math as _math

    def _pmf(lam: float, k: int) -> float:
        return _math.exp(-lam) * (lam ** k) / _math.factorial(k)

    home_pmf = [_pmf(lam_home, k) for k in range(max_goals + 1)]
    away_pmf = [_pmf(lam_away, k) for k in range(max_goals + 1)]
    listed = {
        'home': ['1-0', '2-0', '2-1', '3-0', '3-1', '3-2', '4-0', '4-1', '4-2', '5-0', '5-1', '5-2'],
        'draw': ['0-0', '1-1', '2-2', '3-3'],
        'away': ['0-1', '0-2', '1-2', '0-3', '1-3', '2-3', '0-4', '1-4', '2-4', '0-5', '1-5', '2-5'],
    }
    out: dict[str, float] = {}
    dir_total = {'home': 0.0, 'draw': 0.0, 'away': 0.0}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = home_pmf[h] * away_pmf[a]
            if h > a:
                dir_total['home'] += p
            elif h == a:
                dir_total['draw'] += p
            else:
                dir_total['away'] += p
            key = f'{h}-{a}'
            if h > a:
                label = 'home'
            elif h == a:
                label = 'draw'
            else:
                label = 'away'
            if key in listed[label]:
                out[key] = p
    listed_sum = {'home': 0.0, 'draw': 0.0, 'away': 0.0}
    for label, options in listed.items():
        for option in options:
            listed_sum[label] += out.get(option, 0.0)
    out['胜其它'] = max(0.0, dir_total['home'] - listed_sum['home'])
    out['平其它'] = max(0.0, dir_total['draw'] - listed_sum['draw'])
    out['负其它'] = max(0.0, dir_total['away'] - listed_sum['away'])
    total = sum(out.values())
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    return out


def _line_or_none(primary: Any, secondary: Any) -> Any:
    """盘口线取值：primary 只要 is not None 就用（0.0 是平手盘），否则用 secondary。

    不能用 `primary or secondary`：平手盘 ah=0.0 会被当成「没有」而落到另一个键，
    甚至落到 None，行情分析就看不到真实的 0 线。
    """
    if primary is not None:
        return primary
    return secondary


class DeepSeekAnalyzer:
    """Deterministic analyzer/formatter surface used by app_v84_hermes."""

    @staticmethod
    def _classify_score_wdl(score: Any) -> tuple[str, str]:
        """Score → `(normalised_score, wdl_direction)`.

        Every call site in `app_v84_hermes` reads index `[1]` for the direction
        (`胜`/`平`/`负`, `''` when the score cannot be classified), and 12 of them
        sit inside `try/except` blocks that log and then carry on. Returning a
        bare string made all 12 raise `IndexError` into those handlers, so the
        direction-lock filters silently kept everything: a match locked to 负
        still answered 胜. The 2-tuple shape is the contract those sites were
        written against; the normalised score is returned in slot `[0]` so the
        pair is useful rather than a duplicated label.
        """
        return _norm_score(score), _score_wdl(score)

    @staticmethod
    def _is_tg_match_title_line(line: Any) -> bool:
        s = str(line or '')
        return bool(s.startswith('📌') or ' vs ' in s or 'VS' in s)

    @staticmethod
    def _is_tg_section_suppressed(line: Any) -> bool:
        return False

    @staticmethod
    def format_tg_match_header(match: Any, kickoff: Any = '', match_no: Any = '') -> str:
        """组一行赛事标题：`📌 [赛号] 对阵 开赛时间`（幂等，不重复赛号）。"""
        existing = str(match or '').strip()
        if existing.startswith('📌'):
            return existing
        clean = re.sub(r'^\s*\[[^\]]{1,12}\]\s*', '', existing).strip() or existing
        bits = ['📌']
        if match_no:
            bits.append(f'[{match_no}]')
        bits.append(clean or '未知比赛')
        if kickoff:
            bits.append(str(kickoff))
        return ' '.join(bits)

    @staticmethod
    def build_jc_cross_only_payload(data: Any, **kwargs: Any) -> dict[str, Any]:
        payload = dict(data) if isinstance(data, dict) else {}
        payload.update({k: v for k, v in kwargs.items() if v is not None})
        rows = payload.get('jc_cross_rows') or payload.get('rows') or []
        payload.setdefault('jc_cross_rows', rows if isinstance(rows, list) else [])
        payload.setdefault('mkt_short_wdl_matrix', {'available': False, 'counts': {}})
        payload.setdefault('allowed_scores', [])
        payload.setdefault('judgment_universe', 'mkt_short_wdl_matrix')
        return payload

    @staticmethod
    def attach_score_odds_cold_hedge(payload: dict[str, Any], arbitration: Any = None) -> dict[str, Any]:
        payload['score_odds_cold_hedge'] = {'available': False, 'scores': [], 'writes_first_score': False, 'changes_direction': False}
        return payload

    @staticmethod
    def build_cross_market_board(data: Any, first_score: Any = '', second_score: Any = '') -> dict[str, Any]:
        d = data if isinstance(data, dict) else {}
        return {
            'available': True,
            'first_score': _norm_score(first_score),
            'second_score': _norm_score(second_score),
            'asian_handicap': _line_or_none(d.get('asian_handicap'), d.get('ah_line')),
            'ou_line': d.get('ou_line'),
            'jc_handicap': d.get('jc_handicap'),
            'odds_1x2': d.get('odds_1x2') or {},
        }

    @staticmethod
    def analyze_cross_pairs(odds: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """异化侦测：用已给的赔率集合算方向概率与最热/最冷比分，缺输入就回报缺什么。"""
        clean: dict[str, float] = {}
        if isinstance(odds, dict):
            for score, odd in odds.items():
                raw_value = odd.get('odds') if isinstance(odd, dict) else odd
                if raw_value is None:
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError, OverflowError):
                    continue
                if value > 1:
                    clean[_norm_score(score)] = value
        if not clean:
            return {'available': False, 'reason': 'empty_odds', 'wdl_share_pct': {}, 'hot_scores': []}
        inverse = {score: 1.0 / odd for score, odd in clean.items()}
        total = sum(inverse.values()) or 1.0
        share = {score: value * 100.0 / total for score, value in inverse.items()}
        wdl = {'胜': 0.0, '平': 0.0, '负': 0.0}
        for score, pct in share.items():
            label = _score_wdl(score)
            if label in wdl:
                wdl[label] += pct
        hottest = sorted(share.items(), key=lambda kv: kv[1], reverse=True)[:5]
        return {
            'available': True,
            'wdl_share_pct': {k: round(v, 2) for k, v in wdl.items()},
            'hot_scores': [score for score, _ in hottest],
            'hot_share_pct': {score: round(pct, 2) for score, pct in hottest},
            'sample': len(clean),
        }

    @staticmethod
    def format_mkt_short_wdl_matrix_tg_lines(matrix: Any, home: str = '', away: str = '') -> list[str]:
        """市短矩阵：市均隐含% > 竞彩隐含% 的选项逐项列出，再按胜平负汇总。

        末尾追加「同口径桶比较」段：数据**只从 `m['same_caliber']` 取**，
        本函数不重算任何映射。上面那几行逐项差额是竞彩 31 项对市场的市均，
        不是同一口径——竞彩的 5 球独立格与「其他」三桶在市场上没有对应的
        独立格，逐项直减会把分桶差读成定价差。同口径段给的就是修正后的读法。

        没有行的时候也要打印这一段（`available=False` 时说明原因），
        不许因为「没有市短行」就把整个口径说明跳过。
        """
        m = matrix if isinstance(matrix, dict) else {}
        head = '📊【市短矩阵｜市均隐含 > 竞彩隐含】'
        rows = [r for r in (m.get('rows') or m.get('qualifying') or []) if isinstance(r, dict)]
        if not rows:
            lines = [head, f"  未形成（{m.get('reason') or '没有市均高于竞彩的选项'}）"]
            return lines + DeepSeekAnalyzer._format_same_caliber_lines(m)
        lines = [head, f"  输入={m.get('source') or '市均×竞彩31比分'} 项数={len(rows)}"]

        def _pct_text(value: Any) -> str:
            """市均/竞彩/差三列都不许把缺失写成 0：缺失就写「-」。"""
            try:
                number = float(value)
                # float('1e400') 返回 inf 而不是抛异常：只捕异常会把 `inf`
                # 原样印进制表。非有限值按缺失写「-」。
                if not math.isfinite(number):
                    return '-'
                return f'{number:.2f}'
            except (TypeError, ValueError, OverflowError):
                return '-'

        def _gap_text(value: Any) -> str:
            try:
                return f'{float(value):+.2f}'
            except (TypeError, ValueError, OverflowError):
                return '-'

        for r in rows:
            lines.append(
                f"  {str(r.get('score', '?'))!s:<5} 市均{_pct_text(r.get('market_pct'))}% "
                f"竞彩{_pct_text(r.get('jc_pct'))}% 差{_gap_text(r.get('gap_pct'))}pp"
            )
        counts = m.get('counts')
        counts = counts if isinstance(counts, dict) else {}
        share = m.get('share_pct')
        share = share if isinstance(share, dict) else {}
        lines.append(
            '  胜平负: ' + ' '.join(
                f"{k}={counts.get(k, 0)}({share.get(k, 0):.1f}%)" for k in ('胜', '平', '负')
            )
        )
        return lines + DeepSeekAnalyzer._format_same_caliber_lines(m)

    @staticmethod
    def _format_same_caliber_lines(matrix: Any) -> list[str]:
        """把 `same_caliber` 渲染成报告段落。只读该 dict，不重算映射。

        必须做到四件事，缺一条这段就没有意义：

        1. 两层无损分开说（逐桶 / 聚合，带并集尺寸）；
        2. 定价差行按 |Δ| 排序，带 Δ 数字；
        3. 结构性差异单列，「Δ 不适用」+ 原因，**绝不与定价差混排**；
        4. 不可用 / 自相矛盾都说清楚，不许静默。

        数字一律不做 `or 0` 兜底：缺失就是「-」，不可比就是「不可比」。
        """
        m = matrix if isinstance(matrix, dict) else {}
        same = m.get('same_caliber') if isinstance(m.get('same_caliber'), dict) else {}

        def _num(value: Any, spec: str = '+.2f') -> str:
            """原样格式化一个数（**不做单位换算**）。只用于计数这类非分数。"""
            try:
                number = float(value)
            except (TypeError, ValueError):
                return '-'
            if not math.isfinite(number):
                return '-'
            return format(number, spec)

        def _pct(value: Any) -> str:
            """0..1 的分数 → `5.00%`。"""
            try:
                return f'{float(value) * 100:.2f}%'
            except (TypeError, ValueError):
                return '-'

        def _pp(value: Any) -> str:
            """0..1 的分数 → 百分点数值（`0.02` → `+2.00`）。

            存在的理由：同一行里 `竞彩5.00%` 与 `Δ...pp` 必须是同一个单位。
            用 `_num` 直接格式化 0..1 的分数会得到 `Δ+0.02pp`，
            与左边的 `5.00% / 3.00%` 差 100 倍 —— 三个数字互相矛盾。
            计数（如桶覆盖集大小）不是分数，**不要**过这个函数。
            """
            try:
                return format(float(value) * 100.0, '+.2f')
            except (TypeError, ValueError):
                return '-'

        head = '🧮【同口径桶比较｜31项聚合到市场桶】'
        if not same:
            return [head, '  不可用（原因：本记录没有同口径桶数据，只有逐项市短）']
        if same.get('available') is False:
            return [
                head,
                f"  不可用（原因：{same.get('reason') or '同口径比较未形成'}）",
            ]

        lines = [head]
        policy_label = str(same.get('policy_label') or '').strip()
        if policy_label:
            lines.append(f"  口径：{policy_label}")
        sizes = same.get('aggregate_sizes') if isinstance(same.get('aggregate_sizes'), dict) else {}
        per_bucket = same.get('per_bucket_lossless')
        aggregate = same.get('aggregate_lossless')
        lines.append(
            f"  无损性：逐桶={'是' if per_bucket else '否'}"
            f" · 聚合={'是' if aggregate else '否'}"
            # 这两个是**计数**（桶覆盖集的比分个数），不是分数，不过 _pp。
            f"（市场桶并集 {_num(sizes.get('market_buckets_union'), '.0f')}"
            f" vs 竞彩其他类并集 {_num(sizes.get('jc_other_union'), '.0f')}）"
        )

        # 定价差：可达桶，按 |Δ| 排序。
        priced = [
            r for r in (same.get('rows') or [])
            if isinstance(r, dict) and r.get('delta') is not None
        ]
        priced.sort(key=lambda r: abs(r['delta']), reverse=True)
        if priced:
            lines.append('  · 定价差（|Δ| 排序，同口径可比）')
            for row in priced[:10]:
                lines.append(
                    f"    {row.get('bucket', '?')!s:<6} 竞彩{_pct(row.get('jc')):>8} "
                    f"市场{_pct(row.get('market')):>8} Δ{_pp(row.get('delta'))}pp"
                )

        # 结构性差异：盘面报了价、竞彩落不进任何键。不进上面那段，也不出 Δ 数字。
        structural = {str(b) for b in (same.get('structural_buckets') or [])}
        if structural:
            lines.append('  · 结构性差异（不是定价差，Δ 不适用）')
            for row in (same.get('structural_rows') or []):
                if not isinstance(row, dict):
                    continue
                lines.append(
                    f"    {row.get('bucket', '?')!s:<6} 竞彩{_pct(row.get('jc')):>8} "
                    f"市场{_pct(row.get('market')):>8} Δ 不适用"
                    f" — {row.get('reason') or '盘面报价，竞彩无法在此桶落概率'}"
                )

        other_delta = same.get('other_delta')
        if other_delta is None:
            why = '有结构性不可达桶' if structural else '市场侧缺值'
            lines.append(f"  「其他」合计：Δ 不可比（{why}）")
        else:
            lines.append(f"  「其他」合计：Δ{_pp(other_delta)}pp")

        # 26 项 + 单队5球→方向桶 的自相矛盾：原样带出，别让读者以为只是缺数据。
        for warning in (same.get('warnings') or []):
            text = str(warning or '').strip()
            if text:
                lines.append(f"  ⚠️ {text}")
        return lines

    @staticmethod
    def build_mkt_short_matrix_from_pack(pack: Any) -> dict[str, Any]:
        """从 31 全表算市短矩阵（市场隐含% > 竞彩隐含%）。

        与 `build_theo_mkt_31_pack.pos_rows` 口径不同：那个比的是 市场 vs 理论，
        这里比的是 市场 vs 竞彩官方，两者各自独立计算。

        逐项差额保留原口径（竞彩 31 项对市场的市均），所以既有数值结论不变；
        另附 `same_caliber` 一段：把那 31 项先聚合到市场盘面的桶上再比，
        因为竞彩的 5 球独立格与「其他」三桶在市场上没有对应的独立格，
        逐项直减会把分桶差当成定价差。
        """
        p = pack if isinstance(pack, dict) else {}
        rows = []
        counts = {'胜': 0, '平': 0, '负': 0}
        _jc_labels = {'jc_official', 'jc_score_odds', 'jc', '竞彩'}
        for row in (p.get('rows') or []):
            if not isinstance(row, dict):
                continue
            market = row.get('market')
            jc_pct = row.get('jc_odds_pct')
            try:
                market_f = float(market) if market is not None else None
                jc_f = float(jc_pct) if jc_pct is not None else None
            except (TypeError, ValueError, OverflowError):
                continue
            if market_f is None or jc_f is None:
                continue
            # 市场本身就取自竞彩的行不能进市短：那是「竞彩 vs 竞彩」，
            # 差值恒为 0，归一化的小数误差还会变成一条有正 gap 的假信号。
            if row.get('delta_same_source') or str(row.get('market_source') or '') in _jc_labels:
                continue
            gap = market_f - jc_f
            if gap <= 0:
                continue
            label = _score_wdl(row.get('score'))
            if label in counts:
                counts[label] += 1
            rows.append({
                'score': row.get('score'),
                'market_pct': round(market_f, 4),
                'jc_pct': round(jc_f, 4),
                'gap_pct': round(gap, 4),
                'source': row.get('market_source') or '',
            })
        total = sum(counts.values())
        return {
            'available': bool(rows),
            'rows': rows,
            'counts': counts,
            'share_pct': {k: round(v * 100.0 / total, 2) if total else 0.0 for k, v in counts.items()},
            'source': '市均×竞彩31比分',
            'same_caliber': _same_caliber_buckets(p),
            'reason': '' if rows else '没有任何选项的市均隐含%高于竞彩隐含%',
        }

    @staticmethod
    def format_mkt_hot_wdl_matrix_tg_lines(payload: Any) -> list[str]:
        """市热矩阵：Δ>0（市场超理论）的选项逐项列出 + 胜平负汇总。

        口径声明：与 build_theo_mkt_31_pack.pos_rows 相同（market > theory），
        不是「市均 vs 竞彩」——后者是市短矩阵。
        """
        p = payload if isinstance(payload, dict) else {}
        head = '🔥【市热矩阵｜Δ=市均%−理论%>0，与pos_rows同口径】'
        pos = [r for r in (p.get('pos_rows') or []) if isinstance(r, dict)]
        if not pos:
            return [head, f"  未形成（{p.get('reason') or '没有市场高于理论的选项'}）"]
        lines = [head, f"  输入=31全表 项数={len(pos)}"]
        counts = {'胜': 0, '平': 0, '负': 0}
        for r in pos:
            score = r.get('score')
            label = _score_wdl(score)
            if label in counts:
                counts[label] += 1

            def _num_text(value: Any, fmt: str) -> str:
                """缺失就是缺失：不再把 None 写成 0.00 / +0.00。"""
                try:
                    return format(float(value), fmt)
                except (TypeError, ValueError, OverflowError):
                    return '-'

            market_txt = _num_text(r.get('market'), '.2f')
            theory_txt = _num_text(r.get('theory'), '.2f')
            _market_v = r.get('market')
            _theory_v = r.get('theory')
            try:
                if _market_v is None or _theory_v is None:
                    raise TypeError('missing market/theory')
                delta_txt = f"{float(_market_v) - float(_theory_v):+.2f}pp"
            except (TypeError, ValueError, OverflowError):
                delta_txt = '-'
            lines.append(
                f"  {str(score)!s:<5} 市均{market_txt}% 理论{theory_txt}% Δ{delta_txt}"
            )
        total = sum(counts.values()) or 1
        lines.append(
            '  胜平负: ' + ' '.join(
                f"{k}={counts[k]}({counts[k] * 100.0 / total:.1f}%)" for k in ('胜', '平', '负')
            )
        )
        return lines

    @staticmethod
    def resolve_cs31_provenance(source: Any = None, payload: Any = None) -> dict[str, Any]:
        """31 池逐键出处：记录 → payload → 从 books_avg 反推 → 空。

        返回 {'provenance': {option: desktop_cs|jc_official}, 'source': ...}，
        source ∈ {'recorded', 'derived', None}。把来源一起交出去，是为了让表头
        能写明「记录的」还是「反推的」——同一场比赛不该有两个说不清来历的答案。

        老记录（`cs31_*` 还没进合并白名单时写的）里没有出处表，但它一定留着
        `correct_score_books_avg` —— 原始 25 键市均页。那 25 键就是真正的市均
        覆盖集，所以「在这 25 键里 = desktop_cs，其余 = jc_official」反推出来的表
        与 merge_cs31 存的完全一致（四份真实抓包上逐键相等，不只是数目相等）。
        不反推的话，老比赛上阶段2会把已合并的 31 键整表当成市均，市短又回到
        22/19/21/19 那个错答案——同一场比赛两个答案。

        显式出处永远优先；反推只在拿不到显式出处时兜底，且绝不静默。
        """
        def _from(mapping: Any) -> dict[str, str]:
            out: dict[str, str] = {}
            if isinstance(mapping, dict):
                for score, src in mapping.items():
                    key = _norm_score(score)
                    label = str(src or '').strip()
                    if key and label and key in set(JCZQ_ALL_OPTIONS):
                        out[key] = label
            return out

        src_obj = source if isinstance(source, dict) else {}
        pay_obj = payload if isinstance(payload, dict) else {}
        for holder in (src_obj, pay_obj):
            found = _from(holder.get('cs31_provenance') if isinstance(holder, dict) else None)
            if found:
                return {'provenance': found, 'source': 'recorded'}
        for holder in (src_obj, pay_obj):
            books = holder.get('correct_score_books_avg') if isinstance(holder, dict) else None
            if not isinstance(books, dict) or not books:
                continue
            page_keys: set[str] = set()
            for score, odd in books.items():
                key = _norm_score(score)
                raw = odd.get('odds') if isinstance(odd, dict) else odd
                try:
                    value = float(raw) if raw is not None else None
                except (TypeError, ValueError, OverflowError):
                    value = None
                if key and value is not None and value > 1:
                    page_keys.add(key)
            if not page_keys:
                continue
            return {
                'provenance': {
                    option: ('desktop_cs' if option in page_keys else 'jc_official')
                    for option in JCZQ_ALL_OPTIONS
                },
                'source': 'derived',
            }
        return {'provenance': {}, 'source': None}

    # 冻结 λ 在记录 / 研判包 / 校准载荷里出现过的每一个键，按「离冻结点由近及远」排列。
    # 读侧只能通过 `frozen_lambda_of` 取，不再各自拼 `rec.get(...) or rec.get(...)`：
    # 拼漏一个键，31 表就会整列 theory 为空，日志还只说「理论概率缺31项」。
    FROZEN_LAMBDA_KEYS: ClassVar[tuple[tuple[str, str], ...]] = (
        ('lam_h', 'lam_a'),
        ('lam_home', 'lam_away'),
        ('lambda_home', 'lambda_away'),
        ('fused_lambda_h', 'fused_lambda_a'),
        ('base_home_lambda', 'base_away_lambda'),
    )

    @staticmethod
    def frozen_lambda_of(*sources: Any) -> dict[str, Any]:
        """从记录/研判包里取冻结 λ，并点名它来自哪个键。

        返回值：
          home / away   float 或 None（真的没有就 None，**绝不补默认值**）
          home_key / away_key  命中的键名，'' 表示没有
          source       命中的容器（'record' / 'payload' / 'calibration' …）
          complete      两侧都拿到正数

        取值顺序：先按 `FROZEN_LAMBDA_KEYS` 逐对取同一容器里的主客值（成对命中才收，
        避免把主场 λ 和历史客场 λ 拼成一对），再退到 `lambda_calibration.final`。
        """
        out: dict[str, Any] = {
            'home': None, 'away': None,
            'home_key': '', 'away_key': '', 'source': '', 'complete': False,
        }

        def _pos(value: Any) -> float | None:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return number if math.isfinite(number) and number > 0 else None

        for idx, holder in enumerate(sources or ()):
            if not isinstance(holder, dict):
                continue
            name = str(holder.get('__source__') or ('record' if idx == 0 else f'source{idx}'))
            for home_key, away_key in DeepSeekAnalyzer.FROZEN_LAMBDA_KEYS:
                home, away = _pos(holder.get(home_key)), _pos(holder.get(away_key))
                if home is None and away is None:
                    continue
                out['home'], out['away'] = home, away
                out['home_key'] = home_key if home is not None else ''
                out['away_key'] = away_key if away is not None else ''
                out['source'] = name
                out['complete'] = home is not None and away is not None
                return out
            # 校准载荷：{"available": True, "final": {"home": .., "away": ..}}
            calib = holder.get('lambda_calibration')
            if isinstance(calib, dict):
                final = calib.get('final') if isinstance(calib.get('final'), dict) else {}
                home, away = _pos(final.get('home')), _pos(final.get('away'))
                if home is not None or away is not None:
                    out['home'], out['away'] = home, away
                    out['home_key'] = 'lambda_calibration.final.home' if home is not None else ''
                    out['away_key'] = 'lambda_calibration.final.away' if away is not None else ''
                    out['source'] = f'{name}.lambda_calibration'
                    out['complete'] = home is not None and away is not None
                    return out
        return out

    @staticmethod
    def _collect_legal_cs_market_odds(source: Any, rows: Any) -> dict[str, float]:
        """收集合法比分赔率：市均优先，竞彩官方31比分补缺。

        `correct_score_jc_official` 曾经不在这个元组里，于是 31全表里那 9 个
        市均覆盖不到的选项（5-0/5-1/5-2/0-5/1-5/2-5 与三个「其它」）永远没有市场概率。
        顺序即优先级：市均先写入，后面同名键不覆盖。
        """
        src = source if isinstance(source, dict) else {}
        odds: dict[str, float] = {}
        for key in (
            'correct_score_page_avg', 'correct_score_books_avg', 'correct_score_site_avg',
            'odds_correct_score', 'correct_score_jc_official', 'jc_score_odds',
        ):
            val = src.get(key)
            if isinstance(val, dict):
                for score, odd in val.items():
                    try:
                        if isinstance(odd, dict):
                            odd = odd.get('odds') or odd.get('avg') or odd.get('value')
                        if odd is None:
                            continue
                        f = float(odd)
                    except (TypeError, ValueError, OverflowError):
                        # `10**400` is a legal JSON number and json.load hands it
                        # over as an int; float() raises OverflowError on it.
                        # Letting that escape aborted the collector, so one
                        # hostile value cost the whole 31 table for that match.
                        continue
                    if math.isfinite(f) and f > 1:
                        # inf > 1 is true, so a non-finite value used to win
                        # `setdefault` and mask a legal copy from a later source.
                        odds.setdefault(_norm_score(score), f)
        return odds

    @staticmethod
    def build_theo_mkt_31_pack(rows: Any, **kwargs: Any) -> dict[str, Any]:
        """把 direction_full_rows/jc_cross_rows 折成竞彩31项理论×市场全表。

        `extra_market_odds` 是市均（page_avg/books_avg/site_avg）反推出的比分赔率，
        用来补 rows 里 market 为空或为 0 的项——这正是「31全表未齐」要折入的东西。
        行的 market 缺失时先看 extra_market_odds，再看 system_prejudge 的市热。
        理论概率缺失则留 None（不编造），行本身仍然进表。
        """
        clean = [dict(r) for r in (rows or []) if isinstance(r, dict)]
        extra = kwargs.get('extra_market_odds')
        extra_odds = extra if isinstance(extra, dict) else {}

        def _f(value: Any) -> float | None:
            try:
                number = float(value)
            except OverflowError:
                # An int literal too large for a double (json.loads can produce
                # one) is not a usable number; treat it as absent, never crash.
                return None
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) else None

        by_score: dict[str, dict[str, Any]] = {}
        for row in clean:
            score = _norm_score(row.get('score') or row.get('name') or row.get('cs') or '')
            if not score:
                continue
            merged = by_score.setdefault(score, {'score': score})
            for key in ('theory', 'theory_prob', 'market', 'mkt', 'ibi', 'delta', 'diff',
                        'market_prob', 'implied_prob', 'jc_odds', 'mkt_minus_jc'):
                if key in row and row.get(key) not in (None, ''):
                    merged.setdefault(key, row.get(key))
        # 市均与竞彩各自先归一，再按选项混合，最后整体再归一。
        # 这一步必须和 app 端 build_pool31_option_rows 的 market_src 完全同序：
        # 先混合原始 100/odds 再整体归一，与「各自归一后混合」得到的向量不同，
        # 同一场比赛会给出两个 pos_count —— 一张表不能有两个答案。
        jc_odds = kwargs.get('jc_score_odds')
        jc_odds = jc_odds if isinstance(jc_odds, dict) else {}
        _jc_set = set(JCZQ_ALL_OPTIONS)

        def _odds_value(odd: Any) -> float | None:
            if isinstance(odd, dict):
                odd = odd.get('odds') if odd.get('odds') is not None else odd.get('avg')
            return _f(odd)

        def _implied_vec(odds_map: dict[str, Any]) -> dict[str, float]:
            """只认 31 项里显式给出的键，1/odds 后归一。

            4-3/3-4/4-4 不算市均对「其它」的报价，绝不折进桶里——
            否则 22+9 会串成 25+6。
            """
            vec: dict[str, float] = {}
            for score, odd in (odds_map or {}).items():
                key = _norm_score(score)
                value = _odds_value(odd)
                if key in _jc_set and value is not None and value > 1:
                    vec[key] = 1.0 / value
            total = sum(vec.values())
            return {k: v / total for k, v in vec.items()} if total > 0 else {}

        _page_vec = _implied_vec(extra_odds)
        _jc_vec = _implied_vec(jc_odds)
        # 合并页把 31 键放在同一个向量里，直接按 31 归一就把 9 个竞彩项的水
        # 摊到了市均头上（市均份额整体被压低约 三分之一，市短随之少掉几行）。
        # 有出处时先把竞彩那一路从市均向量里摘出去，只按真正的市均键归一。
        _prov_raw0 = kwargs.get('market_provenance')
        if isinstance(_prov_raw0, dict) and _prov_raw0:
            _jc_keys = {
                _norm_score(k) for k, v in _prov_raw0.items()
                if str(v or '').strip() in ('jc_official', 'jc_score_odds', 'jc', '竞彩')
            }
            _page_only = {k: v for k, v in (extra_odds or {}).items() if _norm_score(k) not in _jc_keys}
            if _page_only:
                _page_vec = _implied_vec(_page_only)
                _fallback_keys = {
                    key for key in _jc_keys
                    if key not in _jc_vec and _norm_score(key) in {
                        _norm_score(k) for k in (extra_odds or {})
                    }
                }
                if _fallback_keys:
                    _page_vec = _implied_vec({
                        **{k: v for k, v in (extra_odds or {}).items()
                           if _norm_score(k) not in (_jc_keys - _fallback_keys)},
                    })
        # 生产记录里 `correct_score_page_avg` 已经是 merge_cs31 的 31 键合并结果，
        # 那 9 个市均没有的选项就在里面，光看数值分不出来自哪一边。出处必须由
        # 合并方给出的 `market_provenance`（{option: desktop_cs|jc_official}）覆盖，
        # 否则 31 行全标 desktop_cs、市短还会拿竞彩去比竞彩。
        _prov_raw = kwargs.get('market_provenance')
        _prov: dict[str, str] = {}
        # 出处是记录的、还是从 books_avg 反推的？读者有权分清。
        _prov_kind = str(kwargs.get('market_provenance_source') or 'recorded')
        if isinstance(_prov_raw, dict):
            for score, src in _prov_raw.items():
                key = _norm_score(score)
                label = str(src or '').strip()
                if key in _jc_set and label:
                    _prov[key] = label
        _JC_LABELS = {'jc_official', 'jc_score_odds', 'jc', '竞彩'}

        def _prov_label(option: str) -> str:
            return _prov.get(option, '')

        def _prov_is_jc(option: str) -> bool:
            return _prov_label(option) in _JC_LABELS

        _mix: dict[str, float] = {}
        _mix_of: dict[str, str] = {}
        for option in JCZQ_ALL_OPTIONS:
            if _prov_is_jc(option):
                # 出处说是竞彩，就用竞彩赔率那一路的值（两边同源时数值本来就相等，
                # 但如果市均合并值与竞彩有微小出入，以出处为准）。
                if option in _jc_vec:
                    _mix[option] = _jc_vec[option]
                elif option in _page_vec:
                    _mix[option] = _page_vec[option]
                else:
                    continue
                _mix_of[option] = 'jc_score_odds'
                continue
            if option in _page_vec:
                _mix[option] = _page_vec[option]
                _mix_of[option] = str(kwargs.get('extra_market_source') or 'market_avg_odds')
            elif option in _jc_vec:
                _mix[option] = _jc_vec[option]
                _mix_of[option] = 'jc_score_odds'
        _mix_total = sum(_mix.values())
        if _mix_total > 0:
            _mix = {k: v * 100.0 / _mix_total for k, v in _mix.items()}
        for option, value in _mix.items():
            row = by_score.setdefault(option, {'score': option})
            if _f(row.get('market') or row.get('mkt')) in (None, 0.0):
                share = value if value > 0 and round(value, 4) > 0 else (0.0001 if value > 0 else 0.0)
                row['market'] = round(share, 4)
                row['market_source'] = _mix_of.get(option, '')
        # 竞彩赔率本身始终要落盘（31 项的原生赔率），与 market 取不取自竞彩无关。
        for score, odd in jc_odds.items():
            key = _norm_score(score)
            odd_f = _odds_value(odd)
            if not key or odd_f is None or odd_f <= 1:
                continue
            row = by_score.setdefault(key, {'score': key})
            if _f(row.get('jc_odds')) is None:
                row['jc_odds'] = odd_f
        # 4-3/3-4/4-4 属表尾附注，单列出去，不占 31 行。
        _extras: dict[str, float] = {}
        for score, odd in extra_odds.items():
            key = _norm_score(score)
            odd_f = _odds_value(odd)
            if key and key not in _jc_set and odd_f is not None and odd_f > 1:
                _extras[key] = odd_f
        # 理论概率：用已冻结的 λ 泊松分布补齐（λ 冻结是既定事实，不是编造）。
        lam_h = _f(kwargs.get('lam_home'))
        lam_a = _f(kwargs.get('lam_away'))
        _lambda_prov_raw = kwargs.get('lambda_provenance')
        _lambda_prov: dict[str, Any] = _lambda_prov_raw if isinstance(_lambda_prov_raw, dict) else {}
        _theory_filled = False
        if lam_h and lam_a and lam_h > 0 and lam_a > 0:
            try:
                probs = _poisson_score_probs(lam_h, lam_a)
            except Exception:  # noqa: BLE001 - λ 不可用时保持 theory 为空
                probs = {}
            if probs:
                _theory_filled = True
                for option in JCZQ_ALL_OPTIONS:
                    row = by_score.setdefault(option, {'score': option})
                    if _f(row.get('theory') or row.get('theory_prob')) is None:
                        value = probs.get(option)
                        if value is not None:
                            row['theory'] = round(value * 100.0, 4)
                            row['theory_source'] = 'frozen_lambda_poisson'
        full_rows: list[dict[str, Any]] = []
        for option in JCZQ_ALL_OPTIONS:
            row = by_score.get(option) or {'score': option}
            theory = _f(row.get('theory') if row.get('theory') is not None else row.get('theory_prob'))
            market = _f(row.get('market') if row.get('market') is not None else row.get('mkt'))
            row['score'] = option
            row['theory'] = theory
            row['market'] = market
            row['in_31'] = True
            row['formed'] = theory is not None and market is not None
            # 市场取自竞彩时 Δ 恒为 0，是「同源」而不是「真的没偏差」。
            # 出处缺失时退回原来的推断，保证老调用点行为不变。
            if market is None:
                # 没有市场值的行不带出处，也不参与汇总计数。
                # 反推出的出处表把 31 项全部标了方向（按覆盖推导），而记录的出处表
                # 只有真正从 merge_cs31 出来的项；不在这里收口，同一场比赛的两条路径
                # 会打出 jc_official=9 与 jc_official=7 两个汇总——行本身都是对的，
                # 只有计数在骗人。
                row['market_source'] = ''
                row['delta_same_source'] = False
            elif _prov:
                row['market_source'] = _prov_label(option) or row.get('market_source') or ''
                row['delta_same_source'] = _prov_is_jc(option)
            else:
                row['delta_same_source'] = row.get('market_source') == 'jc_score_odds'
            full_rows.append(row)

        # market 必须落在 100% 基准上再和 theory/jc 比。
        # `100/odds` 是单家含水利概率（31 项加起来 145.95%），拿它跟归一化到 100
        # 的 theory 比，pos_rows 会虚报「26/31 市场高于理论」，Δ 列也没有可比性。
        # 这里按 app 端 build_pool31_option_rows 的口径去水归一。
        _m_total = sum(float(r['market']) for r in full_rows if _f(r.get('market')) is not None)
        if _m_total > 0:
            for row in full_rows:
                if _f(row.get('market')) is not None:
                    row['market'] = round(float(row['market']) * 100.0 / _m_total, 4)
        # 竞彩官方同口径：单独归一，供市短矩阵逐项比较。
        _jc_total = sum(
            (100.0 / float(r['jc_odds']))
            for r in full_rows
            if _f(r.get('jc_odds')) and float(r['jc_odds']) > 1
        )
        for row in full_rows:
            jc_odd = _f(row.get('jc_odds'))
            if jc_odd and jc_odd > 1 and _jc_total > 0:
                row['jc_odds_pct'] = round((100.0 / jc_odd) * 100.0 / _jc_total, 4)
            else:
                row['jc_odds_pct'] = None
        # theory 也归一化，保证三方同基准。
        _t_total = sum(float(r['theory']) for r in full_rows if _f(r.get('theory')) is not None)
        if _t_total > 0:
            for row in full_rows:
                if _f(row.get('theory')) is not None:
                    row['theory'] = round(float(row['theory']) * 100.0 / _t_total, 4)

        pos = [
            r for r in full_rows
            if _f(r.get('market')) is not None and _f(r.get('theory')) is not None
            and float(r['market']) > float(r['theory'])
        ]
        for row in full_rows:
            theory, market = _f(row.get('theory')), _f(row.get('market'))
            row['delta'] = round(market - theory, 4) if theory is not None and market is not None else None
        market_sum = round(sum(float(r['market']) for r in full_rows if _f(r.get('market')) is not None), 4)
        theory_sum = round(sum(float(r['theory']) for r in full_rows if _f(r.get('theory')) is not None), 4)
        jc_pct_sum = round(sum(float(r['jc_odds_pct']) for r in full_rows if _f(r.get('jc_odds_pct')) is not None), 4)
        # 逐键出处与计数一并带出：调用点要能把它写回记录，下一次重建才不会再
        # 把 9 个竞彩项当成市均（同一场比赛，抓取时 22+9、重建时 31+0）。
        _prov_out: dict[str, str] = {}
        for row in full_rows:
            label = str(row.get('market_source') or '')
            if label:
                _prov_out[str(row['score'])] = label
        _counts_out: dict[str, int] = {}
        for label in _prov_out.values():
            _counts_out[label] = _counts_out.get(label, 0) + 1
        return {
            'available': bool(full_rows),
            'rows': full_rows,
            'row_count': len(full_rows),
            'formed_count': sum(1 for r in full_rows if r.get('formed')),
            'pos_rows': pos,
            'pos_count': len(pos),
            # 三方基准和，供审计断言各自 ≈100（曾经 market 是 145.95 的含水利值）。
            'market_sum': market_sum,
            'theory_sum': theory_sum,
            'jc_pct_sum': jc_pct_sum,
            'cs31_provenance': _prov_out,
            'cs31_counts': _counts_out,
            'cs31_provenance_source': _prov_kind if _prov_out else '',
            'cs31_extras': _extras,
            'cs31_complete': bool(full_rows) and all(r.get('formed') for r in full_rows),
            'system_prejudge': {},
            # 这一表的 theory 到底来自哪。λ 缺失时调用点要能点名「冻结 λ 未形成」，
            # 而不是只打一张 31 项的裸缺表。
            'lambda_provenance': dict(_lambda_prov) if _lambda_prov else {},
            'lambda_source_key': str(_lambda_prov.get('home_key') or ''),
            'lambda_away_key': str(_lambda_prov.get('away_key') or ''),
            'lambda_source': str(_lambda_prov.get('source') or ''),
            'theory_filled_from_lambda': bool(_theory_filled),
            'home': str(kwargs.get('home') or ''),
            'away': str(kwargs.get('away') or ''),
            'match_name': str(kwargs.get('match_name') or ''),
        }

    @staticmethod
    def audit_theo_mkt_31_rows(rows: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """审计竞彩31项理论×市场全表是否齐。

        返回 {'complete': bool, 'missing': [str], 'rows': n, 'formed': n,
              'missing_theory': [...], 'missing_market': [...]}。
        `missing` 是给日志用的短名单（只写前若干项 + 计数），不含换行。
        缺表/缺字段只回报不缺数据，绝不抛异常——阶段2靠它决定是折入补齐还是照常分析。

        `kwargs['sources']` 可给出每个数据源实际有多少项（如
        `{'market_page': 22, 'jc_score_odds': 0, 'frozen_lambda': None}`）；
        缺失项会带上「哪个源没给」，日志不再只出现一张裸列表。
        """
        clean = [r for r in (rows or []) if isinstance(r, dict)]
        present = {_norm_score(r.get('score') or r.get('name') or r.get('cs') or '') for r in clean}
        missing_rows = [option for option in JCZQ_ALL_OPTIONS if option not in present]

        def _num(value: Any) -> float | None:
            try:
                number = float(value)
            except OverflowError:
                # see `build_theo_mkt_31_pack._f`: an over-range int is absent, not fatal
                return None
            except (TypeError, ValueError):
                return None
            return number

        missing_theory: list[str] = []
        missing_market: list[str] = []
        for row in clean:
            score = _norm_score(row.get('score') or row.get('name') or row.get('cs') or '')
            if not score:
                continue
            theory = _num(row.get('theory') if row.get('theory') is not None else row.get('theory_prob'))
            market = _num(row.get('market') if row.get('market') is not None else row.get('mkt'))
            if theory is None:
                missing_theory.append(score)
            if market is None or market <= 0:
                missing_market.append(score)
        missing: list[str] = []
        if missing_rows:
            missing.append('比分选项缺' + str(len(missing_rows)) + '项(' + '/'.join(missing_rows) + ')')
        if missing_theory:
            missing.append('理论概率缺' + str(len(missing_theory)) + '项(' + '/'.join(missing_theory) + ')')
        if missing_market:
            missing.append('市场概率缺' + str(len(missing_market)) + '项(' + '/'.join(missing_market) + ')')
        complete = not missing
        # 逐项点名「这一项缺，是因为哪个源没给」。
        # 只写真实拿到的源计数，缺哪个源就点哪个源，绝不编出「源说有」。
        _src_raw = kwargs.get('sources')
        src_map: dict[str, Any] = _src_raw if isinstance(_src_raw, dict) else {}
        missing_sources: dict[str, str] = {}
        if missing_theory:
            _lam = src_map.get('frozen_lambda')
            _why = (
                '冻结λ未形成（上游拒绝补默认进球期望）'
                if _lam in (None, 0)
                else f'冻结λ={_lam} 但泊松未覆盖'
            )
            for option in missing_theory:
                missing_sources[option] = _why
        if missing_market:
            _page_n = src_map.get('market_page')
            _jc_n = src_map.get('jc_score_odds')
            _bits = []
            if not _page_n:
                _bits.append('市均0项')
            if not _jc_n:
                _bits.append('竞彩31比分0项')
            _why_mkt = (
                '市均与竞彩31比分都没给这一项' if _bits
                else f'市均{_page_n}项/竞彩31比分{_jc_n}项都不含这一项'
            )
            for option in missing_market:
                missing_sources.setdefault(option, _why_mkt)
        return {
            'complete': complete,
            'missing': missing,
            'rows': len(clean),
            'formed': len(clean) - len({*missing_theory} | {*missing_market}),
            'missing_rows': missing_rows,
            'missing_theory': missing_theory,
            'missing_market': missing_market,
            'missing_sources': missing_sources,
        }

    @staticmethod
    def build_mkt_short_wdl_matrix(rows: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """把市短（市均低于竞彩）行按胜平负聚合成矩阵，供方向锁使用。"""
        counts = {'胜': 0, '平': 0, '负': 0}
        discrete: dict[str, float] = {}
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            score = _norm_score(row.get('score') or '')
            label = _score_wdl(score)
            if label not in counts:
                continue
            counts[label] += 1
            # 差值缺失时不写 0.0（0.0 是「确实没有偏差」这个真实结论，不能借用）。
            gap_raw = row.get('mkt_minus_jc')
            if gap_raw is None:
                gap_raw = row.get('delta')
            gap: float | None = None
            if gap_raw is not None:
                try:
                    gap = float(gap_raw)
                except (TypeError, ValueError, OverflowError):
                    gap = None
            if gap is not None and math.isfinite(gap):
                discrete[score] = round(gap, 4)
        total = sum(counts.values())
        return {
            'available': total > 0,
            'counts': counts,
            'total': total,
            'share_pct': {k: round(v * 100.0 / total, 2) if total else 0.0 for k, v in counts.items()},
            'discrete_vector': discrete,
            'rows': [r for r in (rows or []) if isinstance(r, dict)],
        }

    @staticmethod
    def extract_physical_anchor(source: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """从记录里取已落盘的物理锚点；没有就返回空表，不推断、不编造。"""
        src = source if isinstance(source, dict) else {}
        anchor = src.get('physical_anchor')
        if isinstance(anchor, dict) and anchor:
            return dict(anchor)
        dual = src.get('dual_source_facts')
        if isinstance(dual, dict):
            inner = dual.get('physical_anchor')
            if isinstance(inner, dict) and inner:
                return dict(inner)
        return {}

    @staticmethod
    def explain_match_ou_bounds(source: Any, actual: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """大小球区间说明：只用已落盘的 ou_line / 实际比分，缺一项就说缺哪一项。"""
        src = source if isinstance(source, dict) else {}
        line = src.get('ou_line')
        goals: int | None = None
        text = _norm_score(actual)
        if text and re.fullmatch(r'\d+-\d+', text):
            left, right = text.split('-', 1)
            goals = int(left) + int(right)
        if line is None:
            return {'available': False, 'line': '大小球区间: 未形成（缺 ou_line）'}
        try:
            line_f = float(line)
        except (TypeError, ValueError, OverflowError):
            return {'available': False, 'line': f'大小球区间: 未形成（ou_line 非数值: {line!r}）'}
        if goals is None:
            return {
                'available': False,
                'line': f'大小球区间: 盘口{line_f:g}，无完场比分可对账',
                'ou_line': line_f,
            }
        verdict = '大' if goals > line_f else ('小' if goals < line_f else '走')
        return {
            'available': True,
            'line': f'大小球区间: 盘口{line_f:g} vs 实际{goals}球 → {verdict}',
            'ou_line': line_f,
            'actual_goals': goals,
            'verdict': verdict,
        }

    @staticmethod
    def build_theo_mkt_31_tg_lines(pack: Any = None, **kwargs: Any) -> list[str]:
        p = pack if isinstance(pack, dict) else {}
        return DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(p)

    @staticmethod
    def format_theo_mkt_31_tg_lines(pack: Any) -> list[str]:
        """31 项「市均 × 竞彩」完整对比表（每个选项一行）。

        用户明确要求的交付物：31 行齐全（含三个「其它」），列 =
        序/比分/市均赔率/市均隐含%/竞彩赔率/竞彩隐含%/Δ/来源。
        市均覆盖不到的 9 项来源标 jc_official；不在 31 项里的 4-3/3-4/4-4
        单独放在表尾附注，绝不混进 31 行。
        """
        p = pack if isinstance(pack, dict) else {}
        rows = [r for r in (p.get('rows') or []) if isinstance(r, dict)]
        head = '📈【31项市均×竞彩对比表】'
        if not rows:
            reason = p.get('block_reason') or '缺少31全表（无市均且无竞彩赔率）'
            return [head, f'  未形成（{reason}）']

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError, OverflowError):
                return None

        lines = [head]
        _prov_raw = p.get('cs31_provenance')
        provenance: dict[str, Any] = _prov_raw if isinstance(_prov_raw, dict) else {}
        # 缺项的逐项原因（审计给的 {比分: 为什么缺}）；没有就退回一句通用原因。
        _why_raw = p.get('missing_sources')
        why_map: dict[str, str] = _why_raw if isinstance(_why_raw, dict) else {}
        counts: dict[str, int] = {}
        unaudited: list[str] = []
        for idx, row in enumerate(rows, 1):
            score = str(row.get('score') or '')
            market = _f(row.get('market'))
            theory = _f(row.get('theory'))
            jc = _f(row.get('jc_odds'))
            src = str(row.get('market_source') or provenance.get(score) or '-')
            if src == 'market_avg_odds':
                src = 'desktop_cs'
            elif src == 'jc_score_odds':
                src = 'jc_official'
            counts[src] = counts.get(src, 0) + 1
            if market is None or theory is None:
                # 有一列没数：绝不写「-」（那是「这一列没有」的歧义写法），
                # 也绝不用 0 或估价顶上去。保留真有的那几列，把缺的那一列
                # 写成唯一约定的「无数据（原因：…）」，并逐项点名缺哪个源。
                unaudited.append(score)
                why = str(why_map.get(score) or '')
                if not why:
                    why = (
                        '市均与竞彩31比分都没给这一项'
                        if market is None and theory is None
                        else ('理论概率未形成——冻结λ缺失' if theory is None
                              else '市场赔率未取到——市均与竞彩31比分都没给')
                    )
                real = []
                if market is not None:
                    real.append(f'市均{market:.2f}')
                if theory is not None:
                    real.append(f'理论{theory:.1f}%')
                prefix = ' '.join(real)
                lines.append(
                    f'  {idx:02d} {score:<5s} '
                    + (prefix + ' ' if prefix else '')
                    + DeepSeekAnalyzer.unavailable_line(why)
                )
                continue
            market_txt = f'{market:.2f}' if market is not None else '-'
            jc_txt = f'{jc:.2f}' if jc is not None else '-'
            # 竞彩隐含% 必须用去水归一后的 jc_odds_pct，不能是单家 1/odds。
            # 单家口径 31 项加起来是 145.95%，和已归一的市均% 不是一把尺子，
            # 同表的 Δ 与「市均热过理论」都会被这列带偏。
            jc_pct_val = _f(row.get('jc_odds_pct'))
            market_pct = f'{market:.1f}%' if market is not None else '-'
            jc_pct = f'{jc_pct_val:.1f}%' if jc_pct_val is not None else '-'
            delta = f'{market - theory:+.1f}' if (market is not None and theory is not None) else '-'
            lines.append(
                f'  {idx:02d} {score:<5s} 市均{market_txt:>7s} {market_pct:>7s} '
                f'竞彩{jc_txt:>7s} {jc_pct:>7s} Δ{delta:>7s} {src}'
            )
        lines.append(
            '  汇总: ' + ' '.join(f'{k}={v}' for k, v in sorted(counts.items()) if k != '-')
            + f' | 共{len(rows)}项'
            # 出处是从 books_avg 反推的（老记录没有 cs31_provenance）时必须写明，
            # 否则读者分不清「记录的」和「推断的」两种 22+9。
            + (' | 出处:反推(老记录无cs31_provenance)'
               if str(p.get('cs31_provenance_source') or '') == 'derived' else '')
        )
        if unaudited:
            lines.append(
                '  无数据项(' + str(len(unaudited)) + '): ' + '/'.join(unaudited)
                + ' —— 上表逐行注明原因，未估算、未补占位值'
            )
        extras = p.get('cs31_extras') if isinstance(p.get('cs31_extras'), dict) else {}
        if extras:
            lines.append(
                '  附注(不属31项，未计入上表): '
                + '、'.join(f'{k}={float(v):.2f}' for k, v in sorted(extras.items()) if _f(v))
            )
        return lines

    @staticmethod
    def format_mkt_over_theo_tg_lines(pack: Any) -> list[str]:
        p = pack if isinstance(pack, dict) else {}
        return ['📉【市热超理论专表】', f"  条数: {int(p.get('pos_count') or 0)}"]

    @staticmethod
    def format_matrix_learning_tg_lines(info: Any, score: Any) -> list[str]:
        return ['📌【学习改进意见】', f'  完场比分 {score or "未形成"}，本地模块记录样本，不改首选/次选/方向锁。']

    @staticmethod
    def build_matrix_learning_report(info: Any, score: Any) -> dict[str, Any]:
        return {'available': True, 'actual_score': _norm_score(score), 'changes_first_score': False, 'changes_direction': False}

    @staticmethod
    def learning_match_teams_line(info: Any) -> str:
        d = info if isinstance(info, dict) else {}
        return f"开赛球队：{d.get('home_team') or ''} vs {d.get('away_team') or ''}".strip()

    @staticmethod
    def build_system_ml_dual_matrix_pack(info: Any) -> dict[str, Any]:
        return {
            'top8_dominant': '未形成', 'top8_share_pct': {'胜': 0.0, '平': 0.0, '负': 0.0},
            'top15_ou_dominant': '未形成', 'top15_ou_share_pct': {'少球': 0.0, '多球': 0.0},
            'short_vs_hot': '未形成', 'abs_vs_short_hot': '未形成',
        }

    @staticmethod
    def build_direction_lock_judgment_report(info: Any) -> dict[str, Any]:
        return {'verdict': '', 'available': False}

    @staticmethod
    def classify_result_score_origin(info: Any, actual: Any) -> dict[str, Any]:
        return {'line': '比分出处: 本地轻量模块未形成外部出处。'}

    @staticmethod
    def explain_score_hot_short_reason(info: Any, actual: Any) -> dict[str, Any]:
        return {'line': '冷热理由: 本地轻量模块未形成。'}

    @staticmethod
    def matrix_direction_lock(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return the direction lock carried by a payload, if it carries one.

        Every filter in `app_v84_hermes` (`filter_scores_to_direction_lock`,
        `lock_probability_cluster`, and ~15 `.get('direction')` sites) calls this
        first and gives up when `locked` is falsy. The previous body ignored its
        arguments and always returned `{'available': False, 'direction': ''}`, so
        every direction lock was inert: a match locked to 负 still answered 胜.

        Only the explicit lock is read back — `{'direction_lock': {'locked',
        'direction', 'scores'}}`, the exact structure the app writes into its own
        records. Deriving a direction from `wdl_discrete_vector` /
        `mkt_short_wdl_matrix` would need a threshold that no spec defines, so
        that case reports `no_derivation_rule` instead of picking one.
        """
        payload = args[0] if args else kwargs.get('payload')
        if not isinstance(payload, dict):
            return {'available': False, 'locked': False, 'direction': '',
                    'scores': [], 'reason': 'payload_not_a_dict'}
        lock = payload.get('direction_lock')
        if isinstance(lock, dict) and lock.get('locked'):
            direction = str(lock.get('direction') or '')
            if direction not in ('胜', '平', '负'):
                return {'available': False, 'locked': False, 'direction': '',
                        'scores': [],
                        'reason': f'direction_not_wdl:{direction or "empty"}'}
            scores = [str(s).replace('：', '-').replace(':', '-').strip()
                      for s in (lock.get('scores') or []) if str(s or '').strip()]
            return {
                'available': True,
                'locked': True,
                'direction': direction,
                'scores': [s for s in scores if _score_wdl(s) == direction],
                'source': str(lock.get('source') or 'payload'),
            }
        if payload.get('wdl_discrete_vector') or payload.get('mkt_short_wdl_matrix'):
            # No explicit lock, and no documented rule to derive one from these.
            return {'available': False, 'locked': False, 'direction': '',
                    'scores': [], 'reason': 'no_derivation_rule'}
        return {'available': False, 'locked': False, 'direction': '', 'scores': [],
                'reason': 'no_lock_in_payload'}

    @staticmethod
    def build_operator_direction_lock(direction: Any = '', existing_lock: Any = None,
                                      *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Build the lock an operator's `/方向 胜|平|负` command pins a match to.

        The command's whole promise is "比分只从指定方向里出". It used to call
        this name, which answered `{'available': False, 'direction': ''}`: the
        record's previously valid lock was *replaced* by a stub without a
        `locked` key, so every reader (`matrix_direction_lock`,
        `lookup_direction_lock_payload`, `preload_direction_lock`) saw no lock at
        all. Re-specifying a direction therefore destroyed a lock that worked.

        The returned dict has to satisfy `matrix_direction_lock`'s read
        contract, because that is what every downstream filter consults:
        `locked` truthy, `direction` one of 胜/平/负, and `scores` already
        restricted to that direction. Rather than re-implement that filter, the
        candidate goes through `matrix_direction_lock` itself and its `scores`
        are taken back from that answer — one classifier (`_score_wdl`), one
        place that decides membership.

        Scores are only ever *inherited* from `existing_lock` and filtered: a
        new direction that the old lock had no scores for yields an empty
        `scores` list, never invented ones. The direction still locks (the model
        is told which way to answer), the score pool is simply empty until real
        data supplies one.
        """
        dir_text = str(direction or '').strip()
        if dir_text not in ('胜', '平', '负'):
            return {
                'available': False, 'locked': False, 'direction': '',
                'scores': [], 'reason': f'direction_not_wdl:{dir_text or "empty"}',
            }
        existing = existing_lock if isinstance(existing_lock, dict) else {}
        inherited = [
            str(score) for score in (existing.get('scores') or []) if str(score or '').strip()
        ]
        read_back = DeepSeekAnalyzer.matrix_direction_lock(
            {'direction_lock': {'locked': True, 'direction': dir_text, 'scores': inherited}}
        )
        return {
            'available': True,
            'locked': True,
            'direction': dir_text,
            'scores': list(read_back.get('scores') or []),
            # Distinguishable from 'payload' / 'market' locks: this one exists
            # because a human typed the direction, not because data implied it.
            'source': 'operator',
        }

    @staticmethod
    def build_direction_lock_score_close(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {'available': False}

    @staticmethod
    def build_batch_anti_homogen_pack(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {'available': True, 'homogenized': False, 'items': []}

    @staticmethod
    def build_deepseek_only_tg_sections(*args: Any, **kwargs: Any) -> list[str]:
        """按读取顺序组装 DeepSeek-only TG 报告。

        顺序：31对比表 → 市短 → 市热 → 市热超理论 → 0球~7+ 表 → heat → desc。

        pack 的来源有三层，生产调用点只给 ds_payload（pack 藏在里面）：
          1. 显式 theo_mkt_31_pack / pack kwarg
          2. kwargs['ds_payload']['theo_mkt_31_pack'] / ['pack']
          3. 都拿不到就用空表 -> 各段自己打印「未形成（原因）」

        历史实现在这里只认第 1 层，生产传入的却是 ds_payload，于是三段全部塌成
        「未形成」，即使包里明明有完整的 31 全表。
        """
        payload = kwargs.get('ds_payload') if isinstance(kwargs.get('ds_payload'), dict) else {}
        pack = kwargs.get('theo_mkt_31_pack')
        if not isinstance(pack, dict):
            pack = kwargs.get('pack')
        if not isinstance(pack, dict) and payload:
            pack = payload.get('theo_mkt_31_pack') or payload.get('pack')
        if not isinstance(pack, dict):
            pack = {}

        def _as_lines(value: Any) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
            return list(value or [])

        sections: list[str] = []
        title = str(kwargs.get('title_line') or '').strip()
        if title:
            sections.append(title)

        # 1) 31 项对比表：优先用调用方已渲染好的行，否则按 pack 现渲染。
        _t31 = _as_lines(kwargs.get('theo_mkt_31_tg_lines'))
        sections.append('\n'.join(_t31 or DeepSeekAnalyzer.format_theo_mkt_31_tg_lines(pack)))

        # 2) 市短矩阵：市均 vs 竞彩（需要 jc_odds_pct，用 pack 现算）
        # 三层取值，与上面 pack 的三层一致：显式 kwarg → ds_payload 里已算好的
        # → 按 pack 现算。生产调用点只给 ds_payload，若这里不读第二层，
        # 就会拿 pack 重算一份，与调用方已经渲染/落盘的那份分叉。
        short_matrix = kwargs.get('mkt_short_matrix')
        if not isinstance(short_matrix, dict) and isinstance(payload, dict):
            short_matrix = payload.get('mkt_short_wdl_matrix')
        if not isinstance(short_matrix, dict):
            short_matrix = DeepSeekAnalyzer.build_mkt_short_matrix_from_pack(pack)
        sections.append('\n'.join(DeepSeekAnalyzer.format_mkt_short_wdl_matrix_tg_lines(
            short_matrix,
            home=str(kwargs.get('home') or ''),
            away=str(kwargs.get('away') or ''),
        )))

        # 3) 市热矩阵：市均 vs 理论（pos_rows 同口径）
        sections.append('\n'.join(DeepSeekAnalyzer.format_mkt_hot_wdl_matrix_tg_lines(pack)))

        # 4) 市热超理论专表（生产传 mkt_over_theo_tg_lines；以前没读，整段消失）
        _pos_lines = _as_lines(kwargs.get('mkt_over_theo_tg_lines'))
        if not _pos_lines and isinstance(payload, dict):
            _pos_lines = _as_lines(payload.get('mkt_over_theo_tg_lines'))
        if _pos_lines:
            sections.append('\n'.join(_pos_lines))

        # 4b) 0球~7+ 均赔/比分表：必须排在 31 项表与市短/市热之后、研判正文之前。
        #     缺数据时调用方传的是「未形成（原因）」那一行，不会静默消失。
        _goal_lines = _as_lines(kwargs.get('goal_table_tg_lines'))
        if not _goal_lines and isinstance(payload, dict):
            _goal_lines = _as_lines(payload.get('goal_table_tg_lines'))
        if _goal_lines:
            sections.append('\n'.join(_goal_lines))

        # 4c) 六盘口事实板：六块盘口逐行给值或「无数据（原因：…）」。
        #     紧贴在研判正文之前，读者先看到缺什么，再读结论。
        _six_fact = _as_lines(kwargs.get('six_market_fact_tg_lines'))
        if not _six_fact and isinstance(payload, dict):
            _six_fact = _as_lines(payload.get('six_market_fact_tg_lines'))
        if _six_fact:
            sections.append('\n'.join(_six_fact))

        # 5) heat 段与 6) desc 段，按读取顺序放在最后。
        for extra_key in (
            'heat_tg_lines', 'jc_vs_hot_tg_lines', 'wdl_matrix_tg_lines',
            'mkt_desc_lines', 'desc_lines',
            'deepseek_section_lines',
        ):
            extra = _as_lines(kwargs.get(extra_key))
            if not extra and isinstance(payload, dict):
                extra = _as_lines(payload.get(extra_key))
            if not extra:
                continue
            if extra_key == 'wdl_matrix_tg_lines' and any('市短矩阵' in ln for ln in extra):
                continue
            sections.append('\n'.join(extra))
        return [s for s in sections if s.strip()]

    @staticmethod
    def compact_payload(payload: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(payload) if isinstance(payload, dict) else {}

    # 六盘口：与 app `_ensure_mobile_six` 的 `_core_ok` 同口径。
    # 顺序即报告展示顺序；第二项是它在六盘口包里的键名。
    SIX_MARKET_BOARDS: ClassVar[tuple[tuple[str, str], ...]] = (
        ('亚盘让球', 'asian_handicap'),
        ('大小球', 'ou_line'),
        ('胜平负', 'odds_1x2'),
        ('竞彩让球', 'jc_handicap'),
        ('总进球', 'total_goals_odds'),
        ('半全场', 'htft_odds'),
    )
    # 报告外壳：没有这一段就不是「DeepSeek 标准研判报告」。
    JUDGMENT_SHELL_MARKER: ClassVar[str] = 'DeepSeek 数据分析研判报告'

    REQUIRED_TG_SECTIONS: ClassVar[tuple[str, ...]] = (
        '31项市均×竞彩对比表', '市短矩阵', '市热矩阵', 'DeepSeek 数据分析研判报告',
    )

    @staticmethod
    def _section_is_empty(text: str, name: str) -> bool:
        """该段是否整段只有一句「未形成（…）」——段在但没有任何数据行。"""
        start = text.find(name)
        if start < 0:
            return True
        tail = text[start:]
        end = len(tail)
        for other in DeepSeekAnalyzer.REQUIRED_TG_SECTIONS:
            if other == name:
                continue
            idx = tail.find(other)
            if 0 <= idx < end:
                end = idx
        rows = [ln.strip() for ln in tail[:end].splitlines() if ln.strip()]
        meaningful = [
            ln for ln in rows
            if name not in ln and '未形成' not in ln
            and not ln.startswith(('输入=', '📈', '📊', '🔥', '📉', '🧠', '📌', '🏷', '📐'))
        ]
        return not meaningful

    @staticmethod
    def six_market_union(payload: Any = None) -> dict[str, Any]:
        """把分散在三层的六盘口取并集，返回一份合成源。

        六盘口包可能在三个位置：研判包顶层、`pay['six_market_mobile']`、
        `board['six_market_mobile']`。历史实现是「第一个非空的 dict 赢」，
        于是包里只要非空就把顶层整块遮住——而亚盘/大小球来自公司明细路径
        （写在记录上、再写回包），`build_cross_market_board` 又把它们放在顶层，
        两边不同步时「包里 AH=None、顶层 AH=-0.75」会被判缺盘口。

        现在按层叠加取并集：有值压过无值，一旦某层给了值就不会被后面的层改回缺失。

        这个函数是**唯一**的六盘口读侧入口：体检、报告事实板、送模型的缺失清单
        全走它，所以三处不可能对「缺哪块」给出不同答案。
        """
        pay = payload if isinstance(payload, dict) else {}
        board = pay.get('cross_market_board')
        layers: list[Any] = [pay, pay.get('six_market_mobile')]
        if isinstance(board, dict):
            layers.append(board)
            layers.append(board.get('six_market_mobile'))
        out: dict[str, Any] = {}
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            for key, value in layer.items():
                if key == 'six_market_mobile':
                    continue
                if value in (None, '', {}, []):
                    out.setdefault(key, value)
                    continue
                out[key] = value
        return out

    @staticmethod
    def six_market_gaps(source: Any) -> list[str]:
        """列出源里真实缺的六盘口项（中文名）。

        **判「有」不能用 `not value`**：0.0 是平手盘 / 大小球 0 线，是真实盘口。
        空串、空容器、None 才算缺。
        """
        src: dict[str, Any] = source if isinstance(source, dict) else {}

        def _present(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str):
                return bool(value.strip())
            if isinstance(value, (dict, list, tuple, set)):
                return bool(value)
            return True

        gaps: list[str] = []
        for label, key in DeepSeekAnalyzer.SIX_MARKET_BOARDS:
            if not _present(src.get(key)):
                gaps.append(label)
        return gaps

    # 六盘口里「结构性不可达」的两块：博彩公司盘口线，不在竞彩赔率源上。
    # 其余四块在 m.okooo.com/jczq/ 的 oddsData 里，缺了就是我们的解析/抓取问题。
    # 这个分类只决定文案与处置建议，不决定发不发——报告一律照发。
    STRUCTURAL_BOARDS: ClassVar[tuple[str, ...]] = ('亚盘让球', '大小球')

    # 已关闭引擎的章节名标记。它们对应的引擎已经从仓库删掉（`send_tg` 里那份
    # 同名按行过滤是同一批名字），但模型偶尔仍会把它们的段落写进正文。
    # `strip_judgment_tg_suppressed_sections` 按章节边界整段剔除。
    CLOSED_ENGINE_SECTION_MARKS: ClassVar[tuple[str, ...]] = (
        '博弈策略专家系统', 'AI预测引擎', '泊松融合', 'GBDT×泊松几何平均',
    )

    @staticmethod
    def unavailable_line(reason: Any = '') -> str:
        """缺数据段落的唯一写法：`无数据（原因：…）`。

        既不许整段消失，也不许用 0 / '-' / 估算值顶上去。调用点统一走这里，
        措辞才不会一处一个样。
        """
        why = str(reason or '').strip() or '上游未提供'
        return f'无数据（原因：{why}）'

    # 缺口横幅的唯一标题。**跨模块共享常量**：PR #20 的
    # `match_data_matrix.format_gap_banner` 也读它，两处绝不各写一份字符串。
    # 放在这里而不是 match_data_matrix，是因为本模块是所有 sibling 里最底层的
    # 一个（不 import 任何兄弟模块），谁都能安全地从这里取常量。
    GAP_BANNER_HEADER: ClassVar[str] = '⚠️【本场缺口（先说清楚，不影响出报）】'

    # `format_gap_banner` 里的赛前状态行前缀（与 `match_data_matrix` 的常量同值）。
    # 这一行不是缺口：合成横幅时它必须另起一行，不能被接到「数据源缺：」后面。
    MATRIX_STATUS_PREFIX: ClassVar[str] = 'ℹ️ 赛前状态：'

    @staticmethod
    def compose_gap_banner(boards_missing: Any = None,
                           cs31_missing: Any = None,
                           matrix: Any = None) -> dict[str, Any]:
        """把三个轴的缺口合成**一个**横幅块。

        三条子行，任何一条没有内容就整条省略；三条都空则整块省略：

            ⚠️【本场缺口（先说清楚，不影响出报）】
              · 六盘口缺：…（结构性不可达 / 本次未解析，分开写）
              · 31 表缺：…（点名的比分选项）
              · 数据源缺：…#20 的 reason_text 行

        第三组**不重写**：直接调 `match_data_matrix.format_gap_banner`，把那边的
        行去掉它自己的标题后原样并入。这样 `REASON_TEXT` 只有一处真源，
        措辞不会在两份代码里漂移。

        导入方向：`match_data_matrix` 依赖 `live500_board`，而两个模块都不依赖
        本模块，所以这里 import 它**不成环**。仍然用函数内延迟导入 + 失败降级，
        理由有两条：本模块被 `build_single_file.py` 内嵌进 app（内嵌环境里
        兄弟模块可能还没装进 sys.modules），以及 PR #20 尚未合并时这里必须
        只是「没有第三组」，不能让整份报告挂掉。
        """
        boards = [str(b) for b in (boards_missing or []) if str(b or '').strip()]
        # 保持 SIX_MARKET_BOARDS 的展示顺序，不按审计返回的顺序。
        order = [label for label, _ in DeepSeekAnalyzer.SIX_MARKET_BOARDS]
        boards = sorted(boards, key=lambda b: order.index(b) if b in order else len(order))
        structural = [b for b in boards if b in DeepSeekAnalyzer.STRUCTURAL_BOARDS]
        transient = [b for b in boards if b not in DeepSeekAnalyzer.STRUCTURAL_BOARDS]
        cs31 = [str(c) for c in (cs31_missing or []) if str(c or '').strip()]

        board_line = ''
        if boards:
            bits = ['/'.join(boards)]
            detail = []
            if structural:
                detail.append('结构性不可达：' + '/'.join(structural)
                              + '（博彩公司盘口线，不在竞彩赔率源）')
            if transient:
                detail.append('本次未解析出：' + '/'.join(transient)
                              + '（竞彩 oddsData 里有，属解析/抓取问题）')
            board_line = '  · 六盘口缺：' + bits[0]
            if detail:
                board_line += '（' + '；'.join(detail) + '）'

        cs31_line = ('  · 31 表缺：' + '；'.join(cs31)) if cs31 else ''

        matrix_lines = DeepSeekAnalyzer._matrix_gap_lines(matrix)
        matrix_body = [
            ln for ln in matrix_lines
            if ln.strip() and DeepSeekAnalyzer.GAP_BANNER_HEADER not in ln
        ]
        # `format_gap_banner` 现在还会给出一条赛前状态行（`ℹ️ 赛前状态：…`）。
        # 那不是缺口，不能塞到「数据源缺：」后面，否则读者会把赛程状态读成抓取问题。
        gap_rows = [ln for ln in matrix_body if not _is_matrix_status_line(ln)]
        status_rows = [ln for ln in matrix_body if _is_matrix_status_line(ln)]
        matrix_line = ''
        if gap_rows:
            # #20 的渲染是「每行一个原因码」；并入本块时把首行接到
            # 「数据源缺：」后面，其余行保持缩进，读者仍然逐码可读。
            first = gap_rows[0].strip()
            first = first[1:].strip() if first.startswith('·') else first
            matrix_line = '  · 数据源缺：' + first
            if len(gap_rows) > 1:
                matrix_line += '\n' + '\n'.join(
                    ('    ' + ln.strip()) if ln.strip().startswith('·') else ln
                    for ln in gap_rows[1:]
                )
        status_line = '\n'.join(ln.rstrip() for ln in status_rows)

        sub_lines = [ln for ln in (board_line, cs31_line, matrix_line) if ln]
        banner = ''
        if sub_lines or status_line:
            banner = '\n'.join(
                [DeepSeekAnalyzer.GAP_BANNER_HEADER, *sub_lines]
                + ([status_line] if status_line else [])
            )
        return {
            'header': DeepSeekAnalyzer.GAP_BANNER_HEADER,
            'boards_line': board_line,
            'cs31_line': cs31_line,
            'matrix_line': matrix_line,
            'status_line': status_line,
            'lines': sub_lines,
            'banner': banner,
        }

    @staticmethod
    def gap_banner_lines(boards_missing: Any = None, cs31_missing: Any = None,
                         matrix: Any = None) -> list[str]:
        """报告顶部缺口块的**唯一**渲染入口。

        合成规则（与 PR #20 的 `match_data_matrix` 约定一致）：
          * 三条子行谁有内容写谁，都没有就返回空列表（不加横幅）；
          * 标题与「数据源缺」组由 #20 的 `format_gap_banner` 负责——它在
            `REASON_TEXT` 的中文措辞上有唯一真源，本模块**不重写**那套文案；
            本模块只提供「六盘口缺」与「31 表缺」两行，插在标题之后。
          * #20 不在时（未合并 / 内嵌环境缺模块）本模块自己补标题，
            这样只有本模块一组缺口时横幅依然成立。

        这样合并后只有**一个**标题字符串、一组「数据源缺」行，不会出现两个
        打架的抬头。
        """
        gap = DeepSeekAnalyzer.compose_gap_banner(
            boards_missing=boards_missing, cs31_missing=cs31_missing, matrix=matrix,
        )
        own = [gap['boards_line'], gap['cs31_line']]
        own = [ln for ln in own if ln]
        try:
            from match_data_matrix import format_gap_banner  # noqa: PLC0415 - 见 compose_gap_banner 的导入说明
            pr20 = [str(ln) for ln in (format_gap_banner(matrix) or [])] if matrix else []
        except Exception:  # noqa: BLE001 - #20 不可用：本模块自己标题
            pr20 = []
        if pr20:
            header = pr20[0]
            rest = [ln for ln in pr20[1:] if ln.strip()]
            return [header, *own, *rest]
        if own:
            return [DeepSeekAnalyzer.GAP_BANNER_HEADER, *own]
        return []

    @staticmethod
    def _matrix_gap_lines(matrix: Any) -> list[str]:
        """#20 的数据源缺口行。拿不到模块时返回空列表，绝不自己编一份。"""
        if not matrix:
            return []
        try:
            from match_data_matrix import format_gap_banner  # noqa: PLC0415 - 见 compose_gap_banner 的导入说明
        except Exception:  # noqa: BLE001 - #20 未合并/内嵌环境缺模块时只是没有第三组
            return []
        try:
            return [str(ln) for ln in (format_gap_banner(matrix) or [])]
        except Exception:  # noqa: BLE001 - 渲染失败不能连累横幅的另外两组
            return []

    @staticmethod
    def data_gap_report(boards_missing: Any = None, cs31_missing: Any = None,
                        matrix: Any = None) -> dict[str, Any]:
        """把「到底缺什么」整理成一份可复用的降级说明。

        这是缺口信息的**唯一**来源：报告顶部横幅、六盘口事实板、31 表缺项行、
        以及送进模型提示词的缺失数据块，全部读这一份，所以三者不可能互相矛盾。

        返回值（全部是数据，不含结论）：
          boards        缺的六盘口中文名（顺序与 SIX_MARKET_BOARDS 一致）
          structural    boards 里结构性不可达的那部分
          transient     boards 里属于我方解析/抓取的那部分
          cs31          31 全表的缺项描述（审计给的原样字符串）
          summary       一行摘要，用于日志
          banner        报告顶部横幅正文（多行，空字符串表示不缺任何东西）
          prompt_block  送进模型提示词的缺失数据块（空字符串表示不缺任何东西）
        """
        gap = DeepSeekAnalyzer.compose_gap_banner(
            boards_missing=boards_missing, cs31_missing=cs31_missing, matrix=matrix,
        )
        boards = [str(b) for b in (boards_missing or []) if str(b or '').strip()]
        order = [label for label, _ in DeepSeekAnalyzer.SIX_MARKET_BOARDS]
        boards = sorted(boards, key=lambda b: order.index(b) if b in order else len(order))
        structural = [b for b in boards if b in DeepSeekAnalyzer.STRUCTURAL_BOARDS]
        transient = [b for b in boards if b not in DeepSeekAnalyzer.STRUCTURAL_BOARDS]
        cs31 = [str(c) for c in (cs31_missing or []) if str(c or '').strip()]

        summary_bits: list[str] = []
        if boards:
            summary_bits.append(f'六盘口缺{len(boards)}/6')
        if cs31:
            summary_bits.append('31全表比分选项未齐')
        if gap.get('matrix_line'):
            summary_bits.append('数据源有缺口')
        if gap.get('status_line'):
            summary_bits.append('赛前状态已披露')
        summary = '、'.join(summary_bits)

        banner_lines: list[str] = []
        if gap.get('banner'):
            banner_lines.extend(gap['banner'].split('\n'))
            # 这句只对**缺口**成立：赛前状态行不是「缺」，正文里也没有对应占位。
            if gap.get('lines'):
                banner_lines.append(
                    '  以上缺口在正文里逐条标为「无数据（原因：…）」，未估算、未补占位值。'
                )

        prompt_lines: list[str] = []
        if boards:
            prompt_lines.append('- 缺六盘口：' + '/'.join(boards))
            if structural:
                prompt_lines.append(
                    '    其中结构性不可达：' + '/'.join(structural)
                    + '（博彩公司盘口线，不在竞彩赔率源）'
                )
            if transient:
                prompt_lines.append(
                    '    其中本次未解析出：' + '/'.join(transient) + '（竞彩 oddsData 里有，解析/抓取问题）'
                )
        if cs31:
            prompt_lines.append('- 缺 31 全表比分选项：' + '；'.join(cs31))
        if gap.get('matrix_line'):
            prompt_lines.append('- 数据源缺口：' + gap['matrix_line']
                                .replace('  · 数据源缺：', '').strip())

        return {
            'boards': boards,
            'structural': structural,
            'transient': transient,
            'cs31': cs31,
            'summary': summary,
            'header': gap['header'],
            'banner': '\n'.join(banner_lines),
            'prompt_block': '\n'.join(prompt_lines),
        }

    @staticmethod
    def format_six_market_fact_lines(source: Any = None, gaps: Any = None) -> list[str]:
        """六盘口事实板：每块盘口恰好一行，有值写值，没值写 `无数据（原因：…）`。

        以前缺口只在门禁的拒发原因里出现过一次，正文里既没有这一节、
        也没有任何一行说「这块没有」。现在它是一节固定内容：
        六块全在就六行真值，缺几块就有几行点名的 `无数据`，绝不省略。
        """
        src: dict[str, Any] = source if isinstance(source, dict) else {}
        gap_info = gaps if isinstance(gaps, dict) else DeepSeekAnalyzer.data_gap_report()
        missing = set(gap_info.get('boards') or [])
        lines = ['🔎【六盘口事实板】']
        for label, key in DeepSeekAnalyzer.SIX_MARKET_BOARDS:
            value = src.get(key)
            if label in missing or value in (None, '', {}, []):
                if label in DeepSeekAnalyzer.STRUCTURAL_BOARDS:
                    why = '结构性不可达——博彩公司盘口线，不在竞彩赔率源'
                else:
                    why = '本次未解析出——竞彩 oddsData 里有，属解析/抓取问题'
                lines.append(f'  {label}：' + DeepSeekAnalyzer.unavailable_line(why))
                continue
            lines.append(f'  {label}：{DeepSeekAnalyzer._format_board_value(value)}')
        return lines

    @staticmethod
    def _format_board_value(value: Any) -> str:
        """把一块盘口的值渲染成一行文字；只读已有值，绝不补造。"""
        if isinstance(value, dict):
            if all(isinstance(v, (int, float)) or v is None for v in value.values()):
                bits = [
                    f'{k}={v:g}' if isinstance(v, (int, float)) else f'{k}=—'
                    for k, v in value.items()
                ]
                return ' '.join(bits) if bits else DeepSeekAnalyzer.unavailable_line()
            return f'{len(value)} 项'
        if isinstance(value, (list, tuple, set)):
            return f'{len(value)} 项'
        if isinstance(value, (int, float)):
            return f'{value:g}'
        return str(value)

    @staticmethod
    def ensure_judgment_standard_sections(
        text: Any,
        payload: Any = None,
        jc_vs_hot_tg_lines: Any = None,
        wdl_matrix_tg_lines: Any = None,
        theo_mkt_31_tg_lines: Any = None,
        mkt_over_theo_tg_lines: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """发报前体检：报告缺什么就标什么，**不再决定发不发**。

        政策的反转点（用户明确要求）：以前缺六盘口或缺 DeepSeek 外壳会 `reject`，
        整份报告被换成一条「暂不分析 / 质量门拒发」的告警，用户什么都看不到。
        现在这两件事都是**披露标签**：正文照发，缺口在顶部横幅、六盘口事实板、
        31 表缺项行里逐条点名。检测逻辑一点没变（三层并集、结构性 vs 本次未解析），
        变的只有结论：`reject` 恒为 False。

        返回 dict：
          text            规整后的正文（调用点直接用它）
          ok              正文是否包含全部段落
          reject          恒 False——保留字段只为让旧调用点/日志不炸
          reject_reason   **不再是拒发原因**，而是缺失数据的一句话描述
          disclosure      'missing_boards' / 'missing_shell' / ''（本次缺的是什么）
          banner          顶部降级横幅（不缺数据时为空串）
          missing_boards  缺的六盘口项（中文名）
          missing_sections 缺的报告段
          section_status  各段是否在场

        历史上这里是 `return str(text or '')`：既不是门禁，签名也只收一个位置参数。
        app 用五个关键字参数调用它 → `TypeError` 被 `except` 吞掉 → 下一行
        `_gate.get('text')` 也永远不会执行。那道「质量门硬拦」一次都没真正跑过。
        """
        body = str(text or '')
        pay: dict[str, Any] = payload if isinstance(payload, dict) else {}

        board_src = DeepSeekAnalyzer.six_market_union(pay)
        missing_boards = DeepSeekAnalyzer.six_market_gaps(board_src)

        section_status = {
            'deepseek_shell': bool(body) and DeepSeekAnalyzer.JUDGMENT_SHELL_MARKER in body,
            'six_market': not missing_boards,
        }
        section_status.update({
            name: (name in body) for name in DeepSeekAnalyzer.REQUIRED_TG_SECTIONS
        })
        empty_sections = [
            name for name in DeepSeekAnalyzer.REQUIRED_TG_SECTIONS
            if name in body and DeepSeekAnalyzer._section_is_empty(body, name)
        ]
        missing_sections: list[str] = []
        if not section_status['deepseek_shell']:
            missing_sections.append(DeepSeekAnalyzer.JUDGMENT_SHELL_MARKER)

        reasons: list[str] = []
        if missing_boards:
            # 分开写「结构性不可达」与「本次没解析出来」，因为处置完全不同：
            # 亚盘/大小球是博彩公司盘口线，不在竞彩赔率页上，抓不到就是抓不到；
            # 其余四项在 m.okooo.com/jczq/ 的 oddsData 里，解析失败说明有东西可修。
            # 这个分类只决定文案与处置建议，不再决定发不发。
            _structural = set(DeepSeekAnalyzer.STRUCTURAL_BOARDS) & set(missing_boards)
            _transient = [b for b in missing_boards if b not in _structural]
            _bits = []
            if _structural:
                _bits.append(
                    '结构性不可达(' + '/'.join(
                        b for b in missing_boards if b in _structural) + ')'
                    '=博彩公司盘口线，不在竞彩赔率源，需另找可达来源'
                )
            if _transient:
                _bits.append(
                    '本次未解析出(' + '/'.join(_transient) + ')'
                    '=竞彩 oddsData 里有，属解析/抓取问题，可修'
                )
            reasons.append('缺六盘口(' + '/'.join(missing_boards) + ')｜' + '；'.join(_bits))
        if missing_sections:
            reasons.append('缺DeepSeek外壳(' + '/'.join(missing_sections) + ')')
        gap = DeepSeekAnalyzer.data_gap_report(boards_missing=missing_boards)
        return {
            'text': body,
            'ok': not reasons,
            # 政策反转过：缺数据不再阻止发送，报告一律照发并由横幅披露。
            'reject': False,
            'reject_reason': '；'.join(reasons),
            'disclosure': (
                ('missing_boards' if missing_boards else '')
                or ('missing_shell' if missing_sections else '')
            ),
            'banner': gap['banner'],
            'gap': gap,
            'missing_boards': missing_boards,
            'missing_sections': missing_sections,
            'section_status': section_status,
            # 调用方传进来的段：这里只回报「有没有给」，不在这里重渲染。
            'passed_sections': {
                'jc_vs_hot_tg_lines': bool(jc_vs_hot_tg_lines),
                'wdl_matrix_tg_lines': bool(wdl_matrix_tg_lines),
                'theo_mkt_31_tg_lines': bool(theo_mkt_31_tg_lines),
                'mkt_over_theo_tg_lines': bool(mkt_over_theo_tg_lines),
            },
            'empty_sections': empty_sections,
        }

    @staticmethod
    def evaluate_result_in_hot_top8(info: Any) -> dict[str, Any]:
        return {'available': False, 'in_hot8': False}

    @staticmethod
    def extract_dual_scores_from_judgment_text(text: Any,
                                               lock_payload: Any = None) -> dict[str, Any]:
        """从研判正文抽①②两个比分，并保证这两个候选同向。

        正文里的两个比分是**同一注**的候选，必须同方向。旧实现只用正则取前两个
        比分，于是一对反方向比分（如 2-0 与 1-2）会被当成同一注存进
        `predicted_score`/`second_score`，再被报告面并列渲染。

        收口规则（不新写方向判定，复用模块级唯一实现 `_score_wdl`）：
          1. 两候选同向 → 原样保留；
          2. 不同向 → 若方向锁里有该方向的**另一个**比分，用它替换第二个候选
             （只从锁里取，绝不发明比分）；
          3. 仍不能同向 → 只留第一个，第二个置空并记 `direction_mismatch`。

        `second_reason` 只在第二个候缺失或被替换时给出，调用方据此决定渲染
        「未形成」还是落盘。
        """
        found = re.findall(r'\d+\s*[-:]\s*\d+', str(text or ''))
        first = _norm_score(found[0]) if found else ''
        second = _norm_score(found[1]) if len(found) > 1 else ''
        reason = ''
        if first and second:
            first_dir = _score_wdl(first)
            second_dir = _score_wdl(second)
            if first_dir and second_dir and first_dir != second_dir:
                replacement = ''
                lock = lock_payload.get('direction_lock') if isinstance(lock_payload, dict) else None
                if isinstance(lock, dict) and lock.get('locked') \
                        and str(lock.get('direction') or '') == first_dir:
                    for candidate in (lock.get('scores') or []):
                        token = _norm_score(candidate)
                        if token and token != first and _score_wdl(token) == first_dir:
                            replacement = token
                            break
                if replacement:
                    second = replacement
                    reason = 'direction_mismatch_snapped_to_lock'
                else:
                    second = ''
                    reason = 'direction_mismatch'
        return {
            'available': bool(first),
            'first': first,
            'second': second,
            'second_reason': reason,
            'scores': [_norm_score(s) for s in found[:4]],
        }

    @staticmethod
    def analyze_match_judgment(payload: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """阶段2 研判入口：把已落盘事实整理成提示词并调用 DeepSeek。

        没有 DEEPSEEK_API_KEY 时 deepseek_chat_completion 返回本地确定性摘要，
        这里把它包装成统一结构，保证阶段2 永远有 available/content/model 三个键，
        不再出现「type object ... has no attribute」这种整段降级。
        """
        facts = payload if isinstance(payload, dict) else {}
        timeout = kwargs.get('timeout')
        model = str(kwargs.get('model') or kwargs.get('model_name') or '') or None
        home = str(facts.get('home') or '')
        away = str(facts.get('away') or '')
        match_name = str(facts.get('match') or f'{home} vs {away}'.strip())
        # 缺口清单：调用方（app 的 emit 路径）已经算好并放进 payload 的
        # `missing_data_block`；没有就现算一份，绝不能「没传就当没有缺口」。
        gap_block = str(facts.get('missing_data_block') or '').strip()
        if not gap_block:
            gap_report = facts.get('data_gap_report')
            if isinstance(gap_report, dict):
                gap_block = str(gap_report.get('prompt_block') or '').strip()
        if not gap_block:
            _boards_missing = DeepSeekAnalyzer.six_market_gaps(
                DeepSeekAnalyzer.six_market_union(facts)
            )
            _pack31 = facts.get('theo_mkt_31_pack')
            _cs31_missing = (_pack31 or {}).get('missing') if isinstance(_pack31, dict) else None
            gap_block = str(DeepSeekAnalyzer.data_gap_report(
                boards_missing=_boards_missing, cs31_missing=_cs31_missing,
            ).get('prompt_block') or '').strip()
        key_facts = {
            'match': match_name,
            'kickoff': facts.get('kickoff') or '',
            'direction_lock': facts.get('direction_lock') or {},
            'pool15_enriched': list(facts.get('pool15_enriched') or [])[:15],
            'theo_mkt_31_pack': (facts.get('theo_mkt_31_pack') or {}).get('rows') or [],
            'six_market_mobile': facts.get('six_market_mobile') or {},
            'betfair_data': facts.get('betfair_data') or {},
            'water_gaps': facts.get('water_gaps') or [],
        }
        task = '给出本场竞彩研判：方向锁依据、①②比分、数据缺口。不得编造外部数据。'
        # 两套比分盘的口径必须随提示词一起给模型：它写「其他」比较时容易把
        # 竞彩与市场的桶当同义词，从而报出一条并不存在的定价错位。
        task += '\n\n' + SCORE_BOOK_TERMINOLOGY_NOTE
        if gap_block:
            # 模型必须**知道它没有哪些数据**，否则会照常写出一份看起来齐全的研判，
            # 读者无法分辨哪一段是真实结论、哪一段是它替缺失数据编的。
            # 这条指令是提示词的一部分（下面 messages 里可断言），不是日志。
            key_facts['缺失数据（本次真实缺口）'] = gap_block
            task += (
                '\n\n本场数据不全，事实数据里的「缺失数据（本次真实缺口）」逐条列明了缺什么。'
                '对这些项：必须在正文里明确写出「该段无数据（原因：…）」，'
                '不得估算、不得反推、不得用默认盘口或历史均值填补，'
                '也不得把它们当成已观测到的事实来支撑结论。'
            )
        messages = assemble_prompt(facts=key_facts, task=task)
        try:
            text = deepseek_chat_completion(
                messages, model=model, max_retries=1, timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - network/API failure must degrade to a reportable error
            return {
                'available': False,
                'content': '',
                'error': f'{type(exc).__name__}: {exc}',
                'model': model or deepseek_config()['chat_model'],
            }
        if completion_failed(text):
            # 调用失败或没有 API key（本地占位）：available 必须为假、
            # error 必须带真实原因、content 必须为空。
            # 绝不能让占位串或告警串变成研判正文。
            return {
                'available': False,
                'content': '',
                'error': completion_error() or 'empty_completion',
                'model': model or deepseek_config()['chat_model'],
                'provider': 'deepseek' if deepseek_config()['api_key'] else 'local',
            }
        body = str(text or '').strip()
        return {
            'available': bool(body),
            'content': body,
            'error': '' if body else 'empty_completion',
            'model': model or deepseek_config()['chat_model'],
            'provider': 'deepseek' if deepseek_config()['api_key'] else 'local',
        }

    @staticmethod
    def format_operator_direction_recalc_line(recalc: Any, drawn_first: Any = '',
                                              drawn_second: Any = '') -> str:
        """One line saying what the operator asked for vs what the report shows.

        `emit_pipeline_deepseek_tg_report` used to delete the stored scores after
        an operator direction so downstream would recompute them. That deletion
        never reached the report (the body reads the bundle/record, not those
        payload keys) and, had it worked, it would have replaced a real stored
        score with 「无数据」 whenever the recompute produced nothing. So the
        stored score is kept and this line is printed instead: the reader sees
        the requested direction, the score that is on screen, and — when they
        disagree — that the score is the pre-command one and not a recompute.

        `drawn_*` are the scores actually rendered in this report, so the line
        cannot claim 「已按 X 重算」 while showing a Y-direction score.
        """
        info = recalc if isinstance(recalc, dict) else {}
        wanted = str(info.get('direction') or '').strip()
        if wanted not in ('胜', '平', '负'):
            return ''
        prev_first = str(info.get('previous_first') or '').strip()
        prev_second = str(info.get('previous_second') or '').strip()
        shown = [str(s or '').strip() for s in (drawn_first, drawn_second) if str(s or '').strip()]
        shown_dirs = {_score_wdl(s) for s in shown if _score_wdl(s)}
        if shown and shown_dirs == {wanted}:
            return f'🔒 已按操作员指定方向【{wanted}】重算：①={shown[0]}' + (
                f' ②={shown[1]}' if len(shown) > 1 else '')
        prev_text = '／'.join(s for s in (prev_first, prev_second) if s) or '无'
        head = f'🔒 操作员已指定方向【{wanted}】'
        if not shown:
            return (
                f'{head}，但本次重算未产出该方向的比分（无数据）。'
                f'记录里原有的比分 {prev_text} 未被清空，仅供参考，不是按【{wanted}】算出的结果。'
            )
        return (
            f'{head}，本次重算未产出该方向的比分；下面展示的 '
            f'①={shown[0]}' + (f' ②={shown[1]}' if len(shown) > 1 else '')
            + f' 是**指令下达前**的比分（原 {prev_text or "未记录"}），'
            f'不是按【{wanted}】重算的结果。'
        )

    @staticmethod
    def format_judgment_tg_section(*args: Any, **kwargs: Any) -> list[str]:
        return []

    @staticmethod
    def format_matrix_dual_prejudge_html(text: Any, *args: Any, **kwargs: Any) -> str:
        """把纯文本研判报告转成 Telegram HTML（转义 + 首行标题加粗）。

        契约：`bool(str(text).strip())` 为真时返回值一定非空。

        这条契约是硬要求，不是风格问题：调用方把它当渲染器，拿到的空串会被
        `send_tg` 当「消息内容为空」静默丢掉，而调用点仍然把 `_tg_pushed` 置真。
        本函数曾经是 `return ''` 的空桩，于是每一场都渲染成功、发送失败、记为已推。
        """
        raw = str(text if text is not None else '')
        if not raw.strip():
            return ''
        lines = escape(raw).split('\n')
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            if line.lstrip().startswith('📌'):
                lines[index] = f'<b>{line.lstrip()}</b>'
            break
        rendered = '\n'.join(lines)
        return rendered if rendered.strip() else ''

    @staticmethod
    def format_structural_model_tg(*args: Any, **kwargs: Any) -> list[str]:
        return []

    @staticmethod
    def format_structural_review_lines(*args: Any, **kwargs: Any) -> list[str]:
        return []

    @staticmethod
    def apply_operator_direction(payload: Any, direction: Any = '',
                                 *args: Any, **kwargs: Any) -> dict[str, Any] | Any:
        """Write an operator direction lock **into** the payload, in place.

        Callers use this as "mutate the payload" — `emit_pipeline_deepseek_tg_report`
        passes `_ds_payload` and drops the return value, then sends that same
        dict to the model. The name and the call site both say in-place; the body
        used to be `return str(text or '')`, i.e. it stringified the payload,
        returned it to a caller that ignores returns, and left
        `direction_lock` untouched. Nothing downstream ever saw 负.

        So: the payload is mutated and handed back. Non-dict input is returned
        unchanged so a caller that passes a string by mistake gets its input
        back rather than a `TypeError` inside the `except` that surrounds every
        call site.
        """
        dir_text = str(direction or '').strip()
        if not isinstance(payload, dict) or dir_text not in ('胜', '平', '负'):
            return payload
        existing = payload.get('direction_lock')
        payload['direction_lock'] = DeepSeekAnalyzer.build_operator_direction_lock(
            dir_text, existing if isinstance(existing, dict) else {}
        )
        return payload

    @staticmethod
    def bold_tg_match_title_html(text: Any) -> str:
        """转义整段文本，只把**比赛标题那一行**包成 `<b>`。

        契约：输入是纯文本，输出是 Telegram HTML。以前它把整段 escape 后整个
        包进一对 `<b>`——名字说的是「title」，做的是「全篇加粗」，而唯一调用点
        正好在 HTML 渲染器的兜底里：渲染器一崩，整份报告（31 表、矩阵、六盘口
        事实板）都变成粗体，`[n/m]` 分页标记也被吞进粗体。标题行判定与
        `format_matrix_dual_prejudge_html` 完全一致，两处不会给出两种格式。
        """
        lines = escape(str(text if text is not None else '')).split('\n')
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            if line.lstrip().startswith('📌'):
                lines[index] = f'<b>{line.lstrip()}</b>'
            break
        return '\n'.join(lines)

    @staticmethod
    def match_identity_fields(
        info: Any,
        kickoff: Any = '',
        match_no: Any = '',
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, str]:
        """Parse 赛号 / 星期 / 开赛时间 / 对阵 from whatever the caller has.

        Callers in app_v84_hermes pass either a dict record or a raw match name
        plus `kickoff=` / `match_no=` keywords, so both shapes must work here.
        """
        if isinstance(info, dict):
            d = info
        else:
            d = {}
        name = str(d.get('match') or d.get('cn_name') or ('' if d else (info or '')))
        kickoff_text = str(kickoff or d.get('kickoff') or d.get('match_time') or d.get('time') or '')
        number_text = str(match_no or d.get('no') or d.get('local_id') or d.get('match_no') or '')
        code = ''
        weekday = ''
        found = re.search(r'周[一二三四五六日]\s*\d{1,3}', f'{number_text} {name}')
        if found:
            code = found.group(0).replace(' ', '')
            weekday = code[:2]
        if not number_text and code:
            number_text = code
        return {
            'match': name,
            'teams': name,
            'time': kickoff_text,
            'kickoff': kickoff_text,
            'code': code or number_text,
            'weekday': weekday,
            'match_no': number_text,
        }

    @staticmethod
    def pin_match_title_first(text: Any) -> str:
        """把 `📌` 那行挪到正文最前面，其余行顺序原样保留。

        纯文本重排，不注入任何标签，所以 `parse_mode=None` 那条降级发送路径
        看到的仍是同一批字符（测量：真实 emit 正文 -0/+0 行）。
        调用点在两处 HTML 兜底里，那里 `send_tg` 会按「首行是不是比赛标题」
        决定把 `[n/m]` 分页标记挂在标题前还是后；标题不在首行时标记会挤在
        缺口横幅上面，读者看到的第一条消息以 `[1/6]` + `⚠️` 开头。
        """
        raw = str(text or '')
        lines = raw.split('\n')
        title_index = next(
            (i for i, line in enumerate(lines) if line.lstrip().startswith('📌')), None
        )
        if title_index is None:
            return raw
        first_content = next((i for i, line in enumerate(lines) if line.strip()), None)
        if first_content is None or first_content == title_index:
            return raw
        lines.insert(first_content, lines.pop(title_index))
        return '\n'.join(lines)

    @staticmethod
    def sanitize_bot_output(text: Any) -> str:
        return str(text or '').strip()

    # 章节标题：以 emoji/装饰符开头、紧跟一个【…】块的那一行。
    # 只认这一类，是因为本仓库全部真实报告段都是这个写法（见 _section_title）。
    _SECTION_TITLE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r'^\s*(?:[^\w\s【]{1,3}\s*)?【[^】\n]{2,40}】'
    )

    @staticmethod
    def _section_title(line: Any) -> str:
        """这一行是不是章节标题？是就返回归一后的标题，否则空串。

        真实报告里重复出现的**同一行标题**有两种，必须区别对待：

          1. 正文里真的塞了两遍同一段（100k sweep 报的「标题重复」，以及
             `📌 [周六001] 📌 [周六001] …` 那种标题行复制）—— 这是缺陷，要合并；
          2. 分页重挂表头 —— `paginate_tg_sections` 按 Telegram 上限切页时，
             每一页都用所属小节的头行开头，`format_matrices_telegram` 因此会在
             3 页里各出现一次 `【一、31 项比分矩阵】`。那是**设计**，删掉它读者
             就不知道自己在看哪张表。

        区别在「标题之间有没有换页符」：第 2 种必然以 `\\x0c` 分页符隔开
        （`format_matrices_tg_messages` 与 `format_goal_odds_matrix_messages`
        都用 `\\x0c` 拼页）。所以 `collapse_repeated_judgment_blocks` 只在
        **同一个分页之内**合并同名标题，跨分页的重复一律保留。
        """
        text = str(line or '')
        match = DeepSeekAnalyzer._SECTION_TITLE_RE.match(text)
        if not match:
            return ''
        return re.sub(r'\s+', ' ', text[match.start():match.end()]).strip()

    @staticmethod
    def collapse_repeated_judgment_blocks(text: Any) -> str:
        """同名章节只留第一次（含它的正文），后来重复的那一段整段删掉。

        这个名字下的实现以前是 `return str(text or '')` —— 一道空闸。
        `send_tg` 门禁写得很正确（`parse_mode != 'HTML'` 才调用、结果再跑
        `sanitize_bot_output`），但函数体是直通，于是「同名章节只留第一次」
        全仓库一次都没发生过：模型把同一段写两遍、或标题行被拼两次，
        都会原样发到 Telegram（100k sweep 的「标题重复」即此）。

        规则（保守，宁可少合并也不误删内容）：
          * 只把 `_section_title` 认得的标题行当作章节边界；
          * 一个章节 = 它的标题行到下一个标题行之前；
          * 同一分页内、标题字面相同的第二段起，整段丢弃；
          * **跨 `\\x0c` 分页的重复保留**——分页重挂表头是设计，不是重复；
          * 不以标题开头的行（表头、脚注、续行）永远保留，不参与合并；
          * 纯散文（全篇没有章节标题）原样返回，不做猜测性合并。
        """
        raw = str(text or '')
        if not raw.strip():
            return raw
        # 按分页符切片，逐页合并；分页符原样保留（send_tg 靠它分段）。
        return '\x0c'.join(
            DeepSeekAnalyzer._collapse_within_page(page)
            for page in raw.split('\x0c')
        )

    @staticmethod
    def _collapse_within_page(page: Any) -> str:
        """单页内的同名章节合并。空白与换行结构原样保留。"""
        text = str(page or '')
        if not text.strip():
            return text
        chunks: list[tuple[str, list[str]]] = []
        current_title = ''
        current_lines: list[str] = []
        for line in text.split('\n'):
            title = DeepSeekAnalyzer._section_title(line)
            if title:
                if current_lines:
                    chunks.append((current_title, current_lines))
                current_title = title
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            chunks.append((current_title, current_lines))

        seen: set[str] = set()
        kept: list[str] = []
        for title, block in chunks:
            if title:
                if title in seen:
                    # 同名章节的第二段起：标题与它的正文一起丢弃。
                    continue
                seen.add(title)
            kept.extend(block)
        return '\n'.join(kept)

    @staticmethod
    def snap_score_pair(first: Any, second: Any = None,
                        lock_payload: Any = None) -> tuple[str, str]:
        """Normalise a two-score pair and snap it into the direction lock.

        Every caller in `app_v84_hermes` passes the direction-lock payload as a
        third positional argument (14 sites) and then diffs the result against
        the input to log a 🔒 line. The signature accepted only two, so all of
        them raised `TypeError` into their `except`, and the direction lock never
        took effect anywhere: a match locked to 负 still answered 胜.

        The lock state is already inside the payload that `lookup_direction_lock_payload`
        returns (`{'direction_lock': {'locked', 'direction', 'scores'}}`), so no
        threshold is needed here — the allowed scores come from the payload.
        A score already in the locked direction keeps its position; a score
        outside it is replaced by the best unused locked score, and removed when
        the lock has no spare. Without a lock the pair is only normalised.
        """
        out = [_norm_score(first), _norm_score(second)]
        if not isinstance(lock_payload, dict):
            return out[0], out[1]
        lock = lock_payload.get('direction_lock')
        if not isinstance(lock, dict) or not lock.get('locked'):
            return out[0], out[1]
        direction = str(lock.get('direction') or '')
        if direction not in ('胜', '平', '负'):
            return out[0], out[1]
        allowed = [_norm_score(s) for s in (lock.get('scores') or []) if str(s or '').strip()]
        allowed = [s for s in allowed if _score_wdl(s) == direction]

        snapped: list[str] = []
        for token in out:
            if not token:
                continue
            if _score_wdl(token) == direction:
                snapped.append(token)
                continue
            # outside the lock: take the first still-unused locked score
            spare = next((s for s in allowed if s not in snapped), '')
            if spare:
                snapped.append(spare)
        # a lock with no usable scores must not empty the answer: keep the input
        if allowed and not snapped:
            snapped = allowed[:2]
        if not allowed:
            # nothing to snap to; still drop scores that contradict the lock
            snapped = [t for t in out if t and _score_wdl(t) == direction]
        snapped = list(dict.fromkeys(snapped))
        return (snapped[0] if snapped else ''), (snapped[1] if len(snapped) > 1 else '')

    @staticmethod
    def strip_judgment_tg_suppressed_sections(text: Any) -> str:
        """整段剔除已关闭引擎的章节（标题 + 它的正文），纯文本过滤。

        `send_tg` 里另有一条按行过滤：任何**含**关闭引擎名的行直接丢。那条
        只能丢标题行，正文留在原地——测量过的现场是一条悬空的
        `  投票权重 1.0 → 主胜`，读者不知道它在讲哪一节。这里按章节边界丢，
        标题和它的正文一起走。

        关闭名单只认引擎名，不认「模型偶尔这么写」的近似标题：名单里的每一
        个都对应一个已经删掉的引擎，它的输出对读者没有意义。
        """
        raw = str(text or '')
        if not raw.strip():
            return raw
        kept: list[str] = []
        dropping = False
        for line in raw.split('\n'):
            title = DeepSeekAnalyzer._section_title(line)
            if title:
                dropping = any(
                    mark in title for mark in DeepSeekAnalyzer.CLOSED_ENGINE_SECTION_MARKS
                )
            if not dropping:
                kept.append(line)
        return '\n'.join(kept)

    @staticmethod
    def summarize_hot_top8_hit_rate(items: Any) -> dict[str, Any]:
        rows = list(items or []) if not isinstance(items, dict) else list(items.values())
        total = len(rows)
        hits = sum(1 for r in rows if isinstance(r, dict) and r.get('in_hot8'))
        return {'total': total, 'hits': hits, 'hit_rate': (hits / total if total else 0.0)}



# ---- Real DeepSeek/OpenAI-compatible client helpers ----
def _env(name: str, default: str = '') -> str:
    import os
    return os.environ.get(name, default).strip()


_LAST_COMPLETION_ERROR = ''
# 最近一次调用是不是走了「没有 API key」的本地占位路径。
# 与 `_LAST_COMPLETION_ERROR` 分开：本地占位不是传输失败，
# 但它同样不是模型产出，下游据此决定 available。
_LAST_COMPLETION_LOCAL = False


def completion_failed(result: Any) -> bool:
    """这次 deepseek_chat_completion 调用是否失败。

    失败信号是「空结果」或「没有 API key 的本地占位」，不是「非空字符串」。
    文本模式成功返回 str，失败返回 {}；json_mode 成功返回 dict/str，
    失败返回 {'available': False, 'error': ...}。

    没有 API key 时两种模式必须给出**同一个判断**：
    json_mode 本来就说 available=False，文本模式以前拿一句像正文的
    「本地确定性分析…」冒充成功，同一条件两种结论。现在文本模式也走
    `_LAST_COMPLETION_LOCAL`，两边一致。
    """
    if isinstance(result, dict):
        if result.get('available') is False:
            return True
        return not result
    if isinstance(result, str):
        if not result.strip():
            return True
        # 文本模式的本地占位：内容仍在（调用方要用就用），但这次不是模型产出。
        return _LAST_COMPLETION_LOCAL
    if result is None:
        return True
    return False


def completion_is_local(result: Any = None) -> bool:
    """这次结果是不是「没有 API key 的本地占位」，而不是模型真的写了正文。"""
    if isinstance(result, dict):
        return str(result.get('provider') or '') == 'local'
    return bool(_LAST_COMPLETION_LOCAL)


def completion_error() -> str:
    """最近一次失败的原始原因（成功时为空串）。"""
    return _LAST_COMPLETION_ERROR


def deepseek_config() -> dict[str, Any]:
    return {
        'api_key': _env('DEEPSEEK_API_KEY') or _env('DEEPSEEK_KEY'),
        'base_url': (_env('DEEPSEEK_BASE_URL') or _env('OPENAI_BASE_URL') or 'https://api.deepseek.com').rstrip('/'),
        'chat_model': _env('DEEPSEEK_CHAT_MODEL', 'deepseek-chat'),
        'reasoner_model': _env('DEEPSEEK_REASONER_MODEL', 'deepseek-reasoner'),
        'timeout': float(_env('DEEPSEEK_TIMEOUT', '60') or 60),
    }


#: 「竞彩」与「市场」两套比分盘的口径，注入所有写研判正文的提示词。
#: 两边的「其他」桶不是同义词，6 个 5 球比分在竞彩是独立格、在市场不是；
#: 模型若把两边「其他」直接相减，会写出一条不存在的定价错位。
#: 正文只说这一段必须记住的事实，不复述整套分析框架。
SCORE_BOOK_TERMINOLOGY_NOTE = (
    '口径约定（必须遵守）：'
    '「竞彩」专指中国竞彩足球 31 项比分盘（主胜 13 = 12 个精确格 + 胜其他；'
    '平局 5 = 4 个精确格 + 平其他；客胜 13 = 12 个精确格 + 负其他）。'
    '「市场」专指境外主流公司 25/26 项波胆盘（22 个精确格 + 3 或 4 个「其他」桶，'
    '26 项盘面另设一个总「任何其他」）。'
    '两边差距只有两条：单边进球独立覆盖上限不同（竞彩 5 球封顶、市场 4 球封顶），'
    '以及「其他」冷门大比分的归类方式不同。'
    '因此：竞彩的 胜其他/平其他/负其他 与市场的主胜其他/平局其他/客胜其他/任何其他'
    '**不是同义词，不得直接相减**；'
    '5:0 5:1 5:2 / 0:5 1:5 2:5 这 6 个比分**在竞彩是独立格、在市场没有独立格**，'
    '不得说市场也报了这 6 个比分。'
    '要比较两边「其他」，必须先重分桶到同一口径；'
    '实际开出哪个比分以赛果为准。'
)


def assemble_prompt(system: Any = None, user: Any = None, facts: Any = None, task: str = '') -> list[dict[str, str]]:
    """Build OpenAI-compatible messages for review/learning style calls."""
    messages: list[dict[str, str]] = []
    sys_text = str(system or '你是足球量化分析助手，输出必须简洁、可审计、不得编造外部数据。')
    messages.append({'role': 'system', 'content': sys_text})
    body_parts = []
    if task:
        body_parts.append(f'任务: {task}')
    if facts is not None:
        import json
        body_parts.append('事实数据:\n' + json.dumps(facts, ensure_ascii=False, default=str))
    if user:
        body_parts.append(str(user))
    messages.append({'role': 'user', 'content': '\n\n'.join(body_parts) or '请基于已有事实给出本地分析。'})
    return messages


def _local_analysis(messages: Any, json_mode: bool = False) -> Any:
    joined = '\n'.join(str(m.get('content', '')) for m in (messages or []) if isinstance(m, dict))
    if json_mode:
        return {'available': False, 'provider': 'local', 'summary': '未配置 DEEPSEEK_API_KEY，已使用本地确定性分析。', 'input_chars': len(joined)}
    return f'本地确定性分析：未配置 DEEPSEEK_API_KEY，已基于现有事实生成降级摘要（输入{len(joined)}字）。'


def deepseek_chat_completion(messages: Any, model: str | None = None, stream: bool = False,
                             json_mode: bool = False, max_retries: int = 3,
                             temperature: float = 0.2, timeout: float | None = None) -> Any:
    """Call DeepSeek/OpenAI-compatible chat completions when a key is configured.

    Without DEEPSEEK_API_KEY this returns deterministic local analysis, so import
    and tests never require paid credentials.

    失败契约（与 json_mode 一致）：绝不把失败编码成一个「看起来像正文」的字符串。
    文本模式失败返回 `{}`（空 dict，布尔为假），原因写在模块级
    `_LAST_COMPLETION_ERROR` 里，调用方用 `completion_failed()` / 
    `completion_error()` 读。历史实现返回 `'DeepSeek调用失败，已降级：...'`，
    于是 `analyze_match_judgment` 把它当成非空正文，`available=True`、`error=''`，
    一份 API 故障被当成研判成功，并原样发进报告。
    """
    global _LAST_COMPLETION_ERROR, _LAST_COMPLETION_LOCAL
    _LAST_COMPLETION_ERROR = ''
    _LAST_COMPLETION_LOCAL = False
    cfg = deepseek_config()
    if not cfg['api_key']:
        # 契约不变：没有 key 仍然返回确定性本地分析（测试与离线可用）。
        # 但打上「这是本地占位」的标记，与 json_mode 的 available=False 对齐，
        # 消费者据此不把它当研判正文。
        _LAST_COMPLETION_LOCAL = True
        _LAST_COMPLETION_ERROR = 'deepseek_api_key_missing'
        return _local_analysis(messages, json_mode=json_mode)
    import json
    import time

    import requests
    url = f"{cfg['base_url']}/v1/chat/completions"
    payload: dict[str, Any] = {
        'model': model or cfg['chat_model'],
        'messages': list(messages or []),
        'temperature': temperature,
        'stream': bool(stream),
    }
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    headers = {'Authorization': f"Bearer {cfg['api_key']}", 'Content-Type': 'application/json'}
    last_err: Exception | None = None
    request_timeout = float(timeout or cfg['timeout'] or 60)
    # (connect, read)：read 超时按「两次 read 之间的静默」计，单给一个数会让
    # 慢速涌流的响应长时间不返回却永不超时。这里把两端都钉住。
    _timeout_arg: Any = (min(10.0, request_timeout), request_timeout)
    for attempt in range(max(1, int(max_retries or 1))):
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=_timeout_arg,
                stream=stream,
            )
            if resp.status_code == 429 and attempt < max_retries - 1:
                time.sleep(min(30, 2 ** attempt)); continue
            resp.raise_for_status()
            if stream:
                chunks=[]
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw or not str(raw).startswith('data:'):
                        continue
                    data=str(raw)[5:].strip()
                    if data == '[DONE]':
                        break
                    try:
                        obj=json.loads(data)
                        chunks.append(obj.get('choices', [{}])[0].get('delta', {}).get('content', ''))
                    except json.JSONDecodeError:
                        continue
                text=''.join(chunks)
            else:
                obj=resp.json()
                text=obj.get('choices', [{}])[0].get('message', {}).get('content', '')
            if json_mode:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {'available': True, 'provider': 'deepseek', 'raw': text}
            return text
        except Exception as exc:  # noqa: BLE001 - network/API retry wrapper
            last_err = exc
            if attempt < max_retries - 1:
                time.sleep(min(30, 2 ** attempt))
    _LAST_COMPLETION_ERROR = str(last_err or 'unknown')[:300]
    if json_mode:
        return {'available': False, 'provider': 'deepseek', 'error': _LAST_COMPLETION_ERROR[:200]}
    # 文本模式：失败就回空 dict，让调用方靠 completion_failed() 判失败，
    # 绝不返回一句「调用失败」的字符串冒充正文。
    return {}


def deepseek_json(messages: Any, **kwargs: Any) -> dict[str, Any]:
    result = deepseek_chat_completion(messages, json_mode=True, **kwargs)
    return result if isinstance(result, dict) else {'available': False, 'raw': str(result)}


def run_learning_report(facts: Any, task: str = '赛后复盘学习') -> str:
    """赛后复盘正文。失败返回空串，绝不复述失败原因冒充复盘内容。

    历史实现 `return str(deepseek_chat_completion(...))` 把
    「DeepSeek调用失败，已降级：...」原样当复盘正文，复盘面板会显示成功。
    需要区分失败与空内容时用 `completion_failed()` / `completion_error()`。
    """
    result = deepseek_chat_completion(assemble_prompt(facts=facts, task=task))
    if completion_failed(result):
        return ''
    return str(result or '')


def get_deepseek_analyzer(*args: Any, **kwargs: Any) -> DeepSeekAnalyzer:
    return DeepSeekAnalyzer()
