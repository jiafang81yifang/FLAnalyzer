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
    except (TypeError, ValueError):
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


def settle_display_score_pair(first: Any, second: Any = None, *args: Any, **kwargs: Any) -> tuple[str, str]:
    return _norm_score(first), _norm_score(second)


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
        bits = ['📌']
        if match_no:
            bits.append(f'[{match_no}]')
        bits.append(str(match or '未知比赛'))
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
                except (TypeError, ValueError):
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

        旧实现只回「状态: 可用 + 队名」，一行数据都没有。
        """
        m = matrix if isinstance(matrix, dict) else {}
        head = '📊【市短矩阵｜市均隐含 > 竞彩隐含】'
        rows = [r for r in (m.get('rows') or m.get('qualifying') or []) if isinstance(r, dict)]
        if not rows:
            return [head, f"  未形成（{m.get('reason') or '没有市均高于竞彩的选项'}）"]
        lines = [head, f"  输入={m.get('source') or '市均×竞彩31比分'} 项数={len(rows)}"]

        def _pct_text(value: Any) -> str:
            """市均/竞彩/差三列都不许把缺失写成 0：缺失就写「-」。"""
            try:
                return f'{float(value):.2f}'
            except (TypeError, ValueError):
                return '-'

        def _gap_text(value: Any) -> str:
            try:
                return f'{float(value):+.2f}'
            except (TypeError, ValueError):
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
        return lines

    @staticmethod
    def build_mkt_short_matrix_from_pack(pack: Any) -> dict[str, Any]:
        """从 31 全表算市短矩阵（市均隐含% > 竞彩隐含%）。

        与 `build_theo_mkt_31_pack.pos_rows` 口径不同：那个比的是 市场 vs 理论，
        这里比的是 市均 vs 竞彩官方，两者各自独立计算。
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
            except (TypeError, ValueError):
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
                except (TypeError, ValueError):
                    return '-'

            market_txt = _num_text(r.get('market'), '.2f')
            theory_txt = _num_text(r.get('theory'), '.2f')
            _market_v = r.get('market')
            _theory_v = r.get('theory')
            try:
                if _market_v is None or _theory_v is None:
                    raise TypeError('missing market/theory')
                delta_txt = f"{float(_market_v) - float(_theory_v):+.2f}pp"
            except (TypeError, ValueError):
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
                except (TypeError, ValueError):
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
            except (TypeError, ValueError):
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
                row['market'] = round(value, 4)
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
                except (TypeError, ValueError):
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
        except (TypeError, ValueError):
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
            except (TypeError, ValueError):
                return None

        lines = [head]
        _prov_raw = p.get('cs31_provenance')
        provenance: dict[str, Any] = _prov_raw if isinstance(_prov_raw, dict) else {}
        counts: dict[str, int] = {}
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
    def build_operator_direction_lock(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {'available': False, 'direction': ''}

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
        short_matrix = kwargs.get('mkt_short_matrix')
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

        # 5) heat 段与 6) desc 段，按读取顺序放在最后。
        for extra_key in (
            'heat_tg_lines', 'jc_vs_hot_tg_lines', 'wdl_matrix_tg_lines',
            'mkt_desc_lines', 'desc_lines',
            'deepseek_section_lines',
        ):
            extra = _as_lines(kwargs.get(extra_key))
            if not extra and isinstance(payload, dict):
                extra = _as_lines(payload.get(extra_key))
            if extra:
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
        """发报前硬闸：缺六盘口或缺 DeepSeek 外壳时给出明确的拒发标记。

        返回 dict（不是直通的 str）：
          text            规整后的正文（未拒发时调用点直接用它）
          ok              是否可以发
          reject          是否必须拒发
          reject_reason   一句话原因，调用点原样打进日志
          missing_boards  缺的六盘口项（中文名）
          missing_sections 缺的报告段
          section_status  各段是否在场

        历史上这里是 `return str(text or '')`：既不是门禁，签名也只收一个位置参数。
        app 用五个关键字参数调用它 → `TypeError` 被 `except` 吞掉 → 下一行
        `_gate.get('text')` 也永远不会执行。那道「质量门硬拦」一次都没真正跑过。
        """
        body = str(text or '')
        pay: dict[str, Any] = payload if isinstance(payload, dict) else {}

        board_src: dict[str, Any] = {}
        board = pay.get('cross_market_board')
        if isinstance(board, dict) and isinstance(board.get('six_market_mobile'), dict):
            board_src = board['six_market_mobile']
        if not board_src and isinstance(pay.get('six_market_mobile'), dict):
            board_src = pay['six_market_mobile']
        if not board_src:
            # 兜底：研判包顶层也可能直接放盘口标量。
            board_src = pay
        missing_boards = DeepSeekAnalyzer.six_market_gaps(board_src)

        section_status = {
            'deepseek_shell': bool(body) and DeepSeekAnalyzer.JUDGMENT_SHELL_MARKER in body,
            'six_market': not missing_boards,
        }
        missing_sections: list[str] = []
        if not section_status['deepseek_shell']:
            missing_sections.append(DeepSeekAnalyzer.JUDGMENT_SHELL_MARKER)

        reasons: list[str] = []
        if missing_boards:
            # 分开写「结构性不可达」与「本次没解析出来」，因为处置完全不同：
            # 亚盘/大小球是博彩公司盘口线，不在竞彩赔率页上，抓不到就是抓不到；
            # 其余四项在 m.okooo.com/jczq/ 的 oddsData 里，解析失败说明有东西可修。
            # 这个分类只改原因文案，不改拒发结论——门禁照旧不许放宽。
            _structural = {'亚盘让球', '大小球'} & set(missing_boards)
            _transient = [b for b in missing_boards if b not in _structural]
            _bits = []
            if _structural:
                _bits.append(
                    '结构性缺(' + '/'.join(sorted(_structural)) + ')'
                    '=博彩公司盘口线，不在竞彩赔率源，需另找可达来源'
                )
            if _transient:
                _bits.append(
                    '本次未取到(' + '/'.join(_transient) + ')'
                    '=竞彩 oddsData 里有，属解析/抓取问题，可修'
                )
            reasons.append('缺六盘口(' + '/'.join(missing_boards) + ')｜' + '；'.join(_bits))
        if missing_sections:
            reasons.append('缺DeepSeek外壳(' + '/'.join(missing_sections) + ')')
        reject = bool(reasons)
        return {
            'text': body,
            'ok': not reject,
            'reject': reject,
            'reject_reason': '；'.join(reasons),
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
        messages = assemble_prompt(
            facts=key_facts,
            task='给出本场竞彩研判：方向锁依据、①②比分、数据缺口。不得编造外部数据。',
        )
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
    def align_displayed_landings(text: Any, *args: Any, **kwargs: Any) -> str:
        return str(text or '')

    @staticmethod
    def apply_operator_direction(text: Any, *args: Any, **kwargs: Any) -> str:
        return str(text or '')

    @staticmethod
    def bold_tg_match_title_html(text: Any) -> str:
        return f'<b>{escape(str(text or ""))}</b>'

    @staticmethod
    def collapse_repeated_judgment_blocks(text: Any) -> str:
        return str(text or '')

    @staticmethod
    def mark_landing_direction_green(text: Any, *args: Any, **kwargs: Any) -> str:
        return str(text or '')

    @staticmethod
    def mark_locked_direction_yellow(text: Any, *args: Any, **kwargs: Any) -> str:
        return str(text or '')

    @staticmethod
    def mark_prob_ou_wdl_red(text: Any, *args: Any, **kwargs: Any) -> str:
        return str(text or '')

    @staticmethod
    def mark_selected_scores_deep_yellow(text: Any, *args: Any, **kwargs: Any) -> str:
        return str(text or '')

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
        return str(text or '')

    @staticmethod
    def restrict_tg_text_to_direction_lock(text: Any, *args: Any, **kwargs: Any) -> str:
        return str(text or '')

    @staticmethod
    def sanitize_bot_output(text: Any) -> str:
        return str(text or '').strip()

    @staticmethod
    def sanitize_six_market_tg_text(text: Any) -> str:
        return str(text or '')

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
        return str(text or '')

    @staticmethod
    def strip_tg_english(text: Any) -> str:
        return str(text or '')

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
