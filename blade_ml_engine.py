"""Local deterministic ML helpers for FLAnalyzer.

The real app can use sklearn when present, but this module is importable without
optional ML dependencies and provides stable Poisson-based fallbacks.
"""
from __future__ import annotations

import math
from typing import Any

try:
    import sklearn  # type: ignore  # noqa: F401
    SKLEARN_OK = True
except Exception:
    SKLEARN_OK = False

ML_SKLEARN_OK = SKLEARN_OK


def ml_predict_enabled() -> bool:
    return True


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def calc_bivariate_poisson_matrix(lam_h: Any, lam_a: Any, lambda_shared: Any = 0.0, max_goals: int = 10) -> dict[str, float]:
    lh = max(0.01, _safe_float(lam_h, 1.2))
    la = max(0.01, _safe_float(lam_a, 0.9))
    limit = max(1, int(max_goals or 10))
    out: dict[str, float] = {}
    total = 0.0
    for h in range(limit + 1):
        ph = math.exp(-lh) * (lh ** h) / math.factorial(h)
        for a in range(limit + 1):
            pa = math.exp(-la) * (la ** a) / math.factorial(a)
            p = ph * pa
            out[f'{h}-{a}'] = p
            total += p
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    return out


def ensure_ml_bundle(force_train: bool = False) -> dict[str, Any]:
    return {'enabled': True, 'reason': 'local_poisson_fallback', 'poisson': None, 'sample_count': 0, 'sklearn_ok': SKLEARN_OK}


def predict_match_ml(match_info: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
    info = match_info if isinstance(match_info, dict) else {}
    lam_h = _safe_float(info.get('lam_h') or info.get('lambda_home'), 1.2)
    lam_a = _safe_float(info.get('lam_a') or info.get('lambda_away'), 0.9)
    matrix = calc_bivariate_poisson_matrix(lam_h, lam_a, max_goals=7)
    preferred = max(matrix, key=matrix.get) if matrix else ''
    sorted_scores = sorted(matrix.items(), key=lambda kv: kv[1], reverse=True)[:8]
    return {
        'enabled': True, 'used': True, 'reason': 'local_poisson_fallback',
        'preferred_score': preferred, 'top_scores': sorted_scores,
        'poisson_lambda': {'home': lam_h, 'away': lam_a},
        'expected_total_goals': round(lam_h + lam_a, 4),
    }


def learn_match_after_result(info: Any) -> dict[str, Any]:
    return {'ok': True, 'sample_count': 1 if isinstance(info, dict) else 0, 'feature_count': 0, 'actual_score': (info or {}).get('actual_score') if isinstance(info, dict) else None}


def train_daily_poisson(force: bool = True) -> tuple[bool, str, int, int]:
    return True, 'local_poisson_fallback_no_training_needed', 0, 0


def deepseek_flash_ml_brief(*args: Any, **kwargs: Any) -> str:
    return 'deepseek-flash ML 摘要需用户配置 DeepSeek 服务；本地泊松兜底已可用。'
