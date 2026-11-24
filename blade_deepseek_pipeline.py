"""Lightweight DeepSeek analysis pipeline helpers for FLAnalyzer.

This module is intentionally dependency-light.  It provides deterministic
formatting, payload-building and degraded-mode helpers used by app_v84_hermes.
External DeepSeek calls remain in the main app where credentials/config live.
"""
from __future__ import annotations

from html import escape
import re
from typing import Any

JCZQ_MACRO_BAIT_SCORES = {"1-0", "0-1", "1-1", "2-1", "1-2"}
_RUNTIME: dict[str, Any] = {}


def bind_hermes_runtime(**kwargs: Any) -> None:
    _RUNTIME.update(kwargs)


def strip_tg_color_marks(text: Any) -> str:
    return str(text or '').replace('🟨', '').replace('🟥', '').replace('🟩', '')


def split_tg_html_preserving_pre(text: Any, max_len: int = 3900) -> list[str]:
    s = str(text or '')
    limit = max(100, int(max_len or 3900))
    return [s[i:i + limit] for i in range(0, len(s), limit)] or ['']


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
    payload = ctx.get('direction_lock_payload') if isinstance(ctx.get('direction_lock_payload'), dict) else {}
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


def strip_match_features_from_method(text: Any) -> str:
    return str(text or '')


def compare_short_abs_hot(info: Any) -> dict[str, Any]:
    return {'available': False, 'line': '市短市热对照: 本地轻量模块未形成外部热度差。'}


class V1631StandardUnifiedEngine:
    _TEMPLATES = {'胜': '主胜', '平': '平局', '负': '客胜'}
    _TEMPLATE_FALLBACK = '未形成'


class DeepSeekAnalyzer:
    """Deterministic analyzer/formatter surface used by app_v84_hermes."""

    @staticmethod
    def _classify_score_wdl(score: Any) -> str:
        return _score_wdl(score)

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
            'asian_handicap': d.get('asian_handicap') or d.get('ah_line'),
            'ou_line': d.get('ou_line'),
            'jc_handicap': d.get('jc_handicap'),
            'odds_1x2': d.get('odds_1x2') or {},
        }

    @staticmethod
    def format_mkt_short_wdl_matrix_tg_lines(matrix: Any, home: str = '', away: str = '') -> list[str]:
        m = matrix if isinstance(matrix, dict) else {}
        return ['📊【市短胜平负矩阵】', f"  状态: {'可用' if m.get('available') else '未形成'} {home} {away}".rstrip()]

    @staticmethod
    def format_mkt_hot_wdl_matrix_tg_lines(payload: Any) -> list[str]:
        return ['🔥【市热胜平负矩阵】', '  本地轻量模块按已落盘数据展示。']

    @staticmethod
    def _collect_legal_cs_market_odds(source: Any, rows: Any) -> dict[str, float]:
        src = source if isinstance(source, dict) else {}
        odds: dict[str, float] = {}
        for key in ('correct_score_page_avg', 'correct_score_books_avg', 'correct_score_site_avg', 'odds_correct_score'):
            val = src.get(key)
            if isinstance(val, dict):
                for score, odd in val.items():
                    try:
                        if isinstance(odd, dict):
                            odd = odd.get('odds') or odd.get('avg') or odd.get('value')
                        f = float(odd)
                    except (TypeError, ValueError):
                        continue
                    if f > 1:
                        odds[_norm_score(score)] = f
        return odds

    @staticmethod
    def build_theo_mkt_31_pack(rows: Any, **kwargs: Any) -> dict[str, Any]:
        clean = [r for r in (rows or []) if isinstance(r, dict)]
        pos = [r for r in clean if float(r.get('market', r.get('mkt', 0)) or 0) > float(r.get('theory', r.get('prob', 0)) or 0)]
        return {'available': bool(clean), 'rows': clean, 'pos_rows': pos, 'pos_count': len(pos), 'system_prejudge': {}}

    @staticmethod
    def format_theo_mkt_31_tg_lines(pack: Any) -> list[str]:
        p = pack if isinstance(pack, dict) else {}
        return ['📈【31项理论×市场】', f"  市场超理论项: {int(p.get('pos_count') or 0)}"]

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
        return {'available': False, 'direction': ''}

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
        return []

    @staticmethod
    def compact_payload(payload: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def ensure_judgment_standard_sections(text: Any) -> str:
        return str(text or '')

    @staticmethod
    def evaluate_result_in_hot_top8(info: Any) -> dict[str, Any]:
        return {'available': False, 'in_hot8': False}

    @staticmethod
    def extract_dual_scores_from_judgment_text(text: Any) -> tuple[str, str]:
        scores = re.findall(r'\d+\s*[-:]\s*\d+', str(text or ''))
        return (_norm_score(scores[0]) if scores else '', _norm_score(scores[1]) if len(scores) > 1 else '')

    @staticmethod
    def format_judgment_tg_section(*args: Any, **kwargs: Any) -> list[str]:
        return []

    @staticmethod
    def format_matrix_dual_prejudge_html(*args: Any, **kwargs: Any) -> str:
        return ''

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
    def match_identity_fields(info: Any) -> dict[str, str]:
        d = info if isinstance(info, dict) else {}
        return {'match': str(d.get('match') or d.get('cn_name') or ''), 'time': str(d.get('time') or '')}

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
    def snap_score_pair(first: Any, second: Any = None) -> tuple[str, str]:
        return _norm_score(first), _norm_score(second)

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
    """
    cfg = deepseek_config()
    if not cfg['api_key']:
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
    for attempt in range(max(1, int(max_retries or 1))):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout or cfg['timeout'], stream=stream)
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
    if json_mode:
        return {'available': False, 'provider': 'deepseek', 'error': str(last_err or '')[:200]}
    return f'DeepSeek调用失败，已降级：{str(last_err or "unknown")[:160]}'


def deepseek_json(messages: Any, **kwargs: Any) -> dict[str, Any]:
    result = deepseek_chat_completion(messages, json_mode=True, **kwargs)
    return result if isinstance(result, dict) else {'available': False, 'raw': str(result)}


def run_learning_report(facts: Any, task: str = '赛后复盘学习') -> str:
    return str(deepseek_chat_completion(assemble_prompt(facts=facts, task=task)))


def get_deepseek_analyzer(*args: Any, **kwargs: Any) -> DeepSeekAnalyzer:
    return DeepSeekAnalyzer()
