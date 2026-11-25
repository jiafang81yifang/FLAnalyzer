"""Local deterministic ML helpers for FLAnalyzer.

The real app can use sklearn when present, but this module is importable without
optional ML dependencies and provides stable Poisson-based fallbacks.
"""
from __future__ import annotations

import json
import math
import os

# Trusted local model-cache compatibility; load paths are constrained before use.
import pickle  # nosec B403
from pathlib import Path
from typing import Any

try:
    import sklearn  # type: ignore  # noqa: F401

    SKLEARN_OK = True
except ImportError:
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


def _line_or_none(primary: Any, secondary: Any) -> Any:
    """盘口线取值：primary 只要 is not None 就用（0.0 = 平手盘是真实值）。

    `primary or secondary` 会把平手盘 0.0 当成缺失，于是特征退化成默认 0.0，
    和真实的平手盘再也分不开。
    """
    if primary is not None:
        return primary
    return secondary


def calc_bivariate_poisson_matrix(
    lam_h: Any,
    lam_a: Any,
    lambda_shared: Any = 0.0,
    max_goals: int = 10,
) -> dict[str, float]:
    lh = max(0.01, _safe_float(lam_h, 1.2))
    la = max(0.01, _safe_float(lam_a, 0.9))
    limit = max(1, int(max_goals or 10))
    out: dict[str, float] = {}
    total = 0.0
    for h in range(limit + 1):
        ph = math.exp(-lh) * (lh**h) / math.factorial(h)
        for a in range(limit + 1):
            pa = math.exp(-la) * (la**a) / math.factorial(a)
            p = ph * pa
            out[f'{h}-{a}'] = p
            total += p
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    return out


# ---- Persistent training bundle ----
def _models_dir() -> str:
    return os.environ.get('MODELS_DIR') or os.environ.get('FLANALYZER_MODELS_DIR') or 'models'


def _models_root() -> Path:
    return Path(_models_dir()).expanduser().resolve()


def _bundle_path() -> str:
    return str(_models_root() / 'blade_ml_bundle.pkl')


def _constrained_bundle_path() -> Path:
    root = _models_root()
    path = Path(_bundle_path()).expanduser().resolve()
    if root not in path.parents and path != root:
        raise ValueError(f'model bundle path escapes MODELS_DIR: {path}')
    return path


def _extract_training_rows(history: Any) -> list[dict[str, Any]]:
    rows = []
    source = history.values() if isinstance(history, dict) else (history or [])
    for rec in source:
        if not isinstance(rec, dict):
            continue
        score = str(rec.get('actual_score') or '').replace(' (进行中)', '').replace(':', '-')
        if '-' not in score:
            continue
        left, right = [part.strip() for part in score.split('-', 1)]
        if not (left.isdigit() and right.isdigit()):
            continue
        h, a = int(left), int(right)
        rows.append({
            'lam_h': _safe_float(rec.get('lam_h') or rec.get('lambda_home'), 1.2),
            'lam_a': _safe_float(rec.get('lam_a') or rec.get('lambda_away'), 0.9),
            'ou_line': _safe_float(rec.get('ou_line'), 2.5),
            'ah_line': _safe_float(_line_or_none(rec.get('asian_handicap'), rec.get('ah_line')), 0.0),
            'home_goals': h,
            'away_goals': a,
        })
    return rows


def train_from_history(history: Any, save: bool = True) -> dict[str, Any]:
    rows = _extract_training_rows(history)
    bundle = {
        'trained': False,
        'rows': len(rows),
        'sklearn_ok': SKLEARN_OK,
        'model_type': 'poisson_mean',
        'created_from': 'history',
    }
    if not rows:
        bundle.update({'home_mean': 1.2, 'away_mean': 0.9, 'reason': 'no_completed_scores'})
    else:
        bundle['home_mean'] = sum(r['home_goals'] for r in rows) / len(rows)
        bundle['away_mean'] = sum(r['away_goals'] for r in rows) / len(rows)
        bundle['trained'] = True
        if SKLEARN_OK and len(rows) >= 5:
            try:
                from sklearn.ensemble import GradientBoostingRegressor  # type: ignore

                x_rows = [[r['lam_h'], r['lam_a'], r['ou_line'], r['ah_line']] for r in rows]
                yh = [r['home_goals'] for r in rows]
                ya = [r['away_goals'] for r in rows]
                mh = GradientBoostingRegressor(random_state=42).fit(x_rows, yh)
                ma = GradientBoostingRegressor(random_state=43).fit(x_rows, ya)
                bundle.update({'model_type': 'gradient_boosting', 'home_model': mh, 'away_model': ma})
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                bundle['train_warning'] = f'{type(exc).__name__}: {str(exc)[:120]}'
    if save:
        save_bundle(bundle)
    return bundle


def save_bundle(bundle: dict[str, Any]) -> bool:
    path = _constrained_bundle_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as fh:
        pickle.dump(bundle, fh)
    return True


def load_bundle() -> dict[str, Any] | None:
    path = _constrained_bundle_path()
    if not path.exists():
        return None
    # Trusted local model cache constrained to MODELS_DIR.
    with path.open('rb') as fh:
        bundle = pickle.load(fh)  # nosec B301
    return bundle if isinstance(bundle, dict) else None


def _predict_lambdas_from_bundle(bundle: dict[str, Any], info: dict[str, Any]) -> tuple[float, float]:
    base = [
        _safe_float(info.get('lam_h') or info.get('lambda_home'), bundle.get('home_mean', 1.2)),
        _safe_float(info.get('lam_a') or info.get('lambda_away'), bundle.get('away_mean', 0.9)),
        _safe_float(info.get('ou_line'), 2.5),
        _safe_float(_line_or_none(info.get('asian_handicap'), info.get('ah_line')), 0.0),
    ]
    if bundle.get('model_type') == 'gradient_boosting' and bundle.get('home_model') and bundle.get('away_model'):
        try:
            return (
                max(0.05, float(bundle['home_model'].predict([base])[0])),
                max(0.05, float(bundle['away_model'].predict([base])[0])),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            bundle['predict_warning'] = f'{type(exc).__name__}: {str(exc)[:120]}'
    return (
        max(0.05, _safe_float(info.get('lam_h') or info.get('lambda_home'), bundle.get('home_mean', 1.2))),
        max(0.05, _safe_float(info.get('lam_a') or info.get('lambda_away'), bundle.get('away_mean', 0.9))),
    )


def ensure_ml_bundle(force_train: bool = False) -> dict[str, Any]:
    bundle = None if force_train else load_bundle()
    if bundle is None:
        return {
            'enabled': True,
            'reason': 'no_trained_bundle',
            'poisson': None,
            'sample_count': 0,
            'sklearn_ok': SKLEARN_OK,
            'bundle_path': _bundle_path(),
        }
    return {
        'enabled': True,
        'reason': 'trained_bundle_loaded',
        'poisson': bundle,
        'sample_count': int(bundle.get('rows') or 0),
        'sklearn_ok': SKLEARN_OK,
        'bundle_path': _bundle_path(),
    }


def predict_match_ml(match_info: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
    info = match_info if isinstance(match_info, dict) else {}
    bundle = load_bundle()
    if bundle:
        lam_h, lam_a = _predict_lambdas_from_bundle(bundle, info)
    else:
        lam_h = _safe_float(info.get('lam_h') or info.get('lambda_home'), 1.2)
        lam_a = _safe_float(info.get('lam_a') or info.get('lambda_away'), 0.9)
    matrix = calc_bivariate_poisson_matrix(lam_h, lam_a, max_goals=7)
    preferred = max(matrix, key=lambda score: matrix[score]) if matrix else ''
    sorted_scores = sorted(matrix.items(), key=lambda kv: kv[1], reverse=True)[:8]
    return {
        'enabled': True,
        'used': True,
        'reason': 'trained_bundle' if bundle else 'local_poisson_fallback',
        'preferred_score': preferred,
        'top_scores': sorted_scores,
        'poisson_lambda': {'home': lam_h, 'away': lam_a},
        'expected_total_goals': round(lam_h + lam_a, 4),
    }


def learn_match_after_result(info: Any) -> dict[str, Any]:
    return {
        'ok': True,
        'sample_count': 1 if isinstance(info, dict) else 0,
        'feature_count': 0,
        'actual_score': (info or {}).get('actual_score') if isinstance(info, dict) else None,
    }


def train_daily_poisson(force: bool = True) -> tuple[bool, str, int, int]:
    hist_path = os.environ.get('HISTORY_FILE', 'match_history.json')
    history = {}
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding='utf-8') as fh:
                history = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"   ⚠️ [ML引擎] 历史样本读取失败，使用空样本: {type(exc).__name__}")
            history = {}
    bundle = train_from_history(history, save=True)
    return bool(bundle.get('trained')), str(bundle.get('model_type') or 'poisson_mean'), int(bundle.get('rows') or 0), 4


def deepseek_flash_ml_brief(*args: Any, **kwargs: Any) -> str:
    return 'deepseek-flash ML 摘要需用户配置 DeepSeek 服务；本地泊松兜底已可用。'
