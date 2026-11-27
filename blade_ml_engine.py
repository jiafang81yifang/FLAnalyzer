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


def _safe_float(value: Any, default: float | None = None) -> float | None:
    """数值转换；缺值/非数/非有限一律回 default（默认 None = 显式缺失）。

    历史实现默认回 0.0，于是「赔率缺失」与「赔率是 0」无法区分，下游
    再把 0.0 当成真实盘口。缺就是缺，由调用方显式处理。
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _line_or_none(primary: Any, secondary: Any) -> Any:
    """盘口线取值：primary 只要 is not None 就用（0.0 = 平手盘是真实值）。

    `primary or secondary` 会把平手盘 0.0 当成缺失，于是特征退化成默认 0.0，
    和真实的平手盘再也分不开。
    """
    if primary is not None:
        return primary
    return secondary


def _bundle_value(bundle: dict[str, Any], key: str) -> float | None:
    """训练包的均值只接受有限正数；没有就返回 None，绝不回落到写死的 1.2/0.9。"""
    value = _safe_float(bundle.get(key))
    return value if value is not None and value > 0 else None


def calc_bivariate_poisson_matrix(
    lam_h: Any,
    lam_a: Any,
    lambda_shared: Any = 0.0,
    max_goals: int = 10,
) -> dict[str, float]:
    """双变量泊松矩阵。λ 缺失/非法时不编造，返回空矩阵由调用方判 aborted。"""
    lh_raw = _safe_float(lam_h)
    la_raw = _safe_float(lam_a)
    if lh_raw is None or la_raw is None or lh_raw <= 0 or la_raw <= 0:
        return {}
    lh, la = lh_raw, la_raw
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
    """只收「四项盘口都有」的样本行；缺任一项就整行跳过并在 skipped 里点名。

    历史实现给缺失的 λ/盘口写死 1.2 / 0.9 / 2.5 / 0.0，等于把「没抓到」
    训练成「主队必进 1.2 球」。缺数据的样本对模型只有污染，不参与训练。
    """
    rows = []
    skipped: list[dict[str, Any]] = []
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
        lam_h = _safe_float(_line_or_none(rec.get('lam_h'), rec.get('lambda_home')))
        lam_a = _safe_float(_line_or_none(rec.get('lam_a'), rec.get('lambda_away')))
        ou_line = _safe_float(rec.get('ou_line'))
        ah_line = _safe_float(_line_or_none(rec.get('asian_handicap'), rec.get('ah_line')))
        missing = [
            name for name, value in (
                ('lam_h', lam_h), ('lam_a', lam_a), ('ou_line', ou_line), ('ah_line', ah_line),
            ) if value is None
        ]
        if missing:
            skipped.append({'actual_score': score, 'missing': missing})
            continue
        rows.append({
            'lam_h': lam_h,
            'lam_a': lam_a,
            'ou_line': ou_line,
            'ah_line': ah_line,
            'home_goals': h,
            'away_goals': a,
        })
    _extract_training_rows.last_skipped = skipped  # type: ignore[attr-defined]
    return rows


def train_from_history(history: Any, save: bool = True) -> dict[str, Any]:
    rows = _extract_training_rows(history)
    skipped = list(getattr(_extract_training_rows, 'last_skipped', []) or [])
    bundle = {
        'trained': False,
        'rows': len(rows),
        'sklearn_ok': SKLEARN_OK,
        'model_type': 'poisson_mean',
        'created_from': 'history',
    }
    if skipped:
        bundle['skipped_rows'] = skipped
    if not rows:
        # 没有可用样本就是没有模型：不给 home_mean/away_mean 编造均值。
        bundle['reason'] = 'no_usable_completed_scores'
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


def _predict_lambdas_from_bundle(bundle: dict[str, Any], info: dict[str, Any]) -> tuple[float | None, float | None]:
    """按训练包预测 λ；输入或模型缺失时返回 (None, None)，不填 1.2/0.9/2.5。

    模型特征必须四列齐全（λh/λa/OU/AH）。任何一列缺就说明这一场的特征
    没形成，不能用写死的 2.5/0.0 冒充，交回调用方判 unavailable。
    """
    home_mean = _bundle_value(bundle, 'home_mean')
    away_mean = _bundle_value(bundle, 'away_mean')
    lam_h = _safe_float(_line_or_none(info.get('lam_h'), info.get('lambda_home')))
    lam_a = _safe_float(_line_or_none(info.get('lam_a'), info.get('lambda_away')))
    ou_line = _safe_float(info.get('ou_line'))
    ah_line = _safe_float(_line_or_none(info.get('asian_handicap'), info.get('ah_line')))
    if bundle.get('model_type') == 'gradient_boosting' and bundle.get('home_model') and bundle.get('away_model'):
        if None in (lam_h, lam_a, ou_line, ah_line):
            return None, None
        base = [lam_h, lam_a, ou_line, ah_line]
        try:
            return (
                max(0.05, float(bundle['home_model'].predict([base])[0])),
                max(0.05, float(bundle['away_model'].predict([base])[0])),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            bundle['predict_warning'] = f'{type(exc).__name__}: {str(exc)[:120]}'
    # 无梯度模型时按「记录里的 λ → 训练包均值」的顺序取；两个来源都没有就是没有。
    return (
        lam_h if lam_h is not None else home_mean,
        lam_a if lam_a is not None else away_mean,
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
    """ML 预测。λ 取不到时如实报 unavailable，绝不按 1.2/0.9 造一个矩阵。"""
    info = match_info if isinstance(match_info, dict) else {}
    bundle = load_bundle()
    if bundle:
        lam_h, lam_a = _predict_lambdas_from_bundle(bundle, info)
        source = 'trained_bundle'
    else:
        lam_h = _safe_float(_line_or_none(info.get('lam_h'), info.get('lambda_home')))
        lam_a = _safe_float(_line_or_none(info.get('lam_a'), info.get('lambda_away')))
        source = 'record_lambda'
    if lam_h is None or lam_a is None:
        return {
            'enabled': True,
            'used': False,
            'available': False,
            'reason': 'lambda_not_formed',
            'missing': [name for name, value in (('lam_h', lam_h), ('lam_a', lam_a)) if value is None],
            'preferred_score': '',
            'top_scores': [],
            'poisson_lambda': {'home': lam_h, 'away': lam_a},
            'expected_total_goals': None,
            'source': source,
        }
    matrix = calc_bivariate_poisson_matrix(lam_h, lam_a, max_goals=7)
    if not matrix:
        return {
            'enabled': True,
            'used': False,
            'available': False,
            'reason': 'poisson_matrix_empty',
            'missing': [],
            'preferred_score': '',
            'top_scores': [],
            'poisson_lambda': {'home': lam_h, 'away': lam_a},
            'expected_total_goals': None,
            'source': source,
        }
    preferred = max(matrix, key=lambda score: matrix[score])
    sorted_scores = sorted(matrix.items(), key=lambda kv: kv[1], reverse=True)[:8]
    return {
        'enabled': True,
        'used': True,
        'available': True,
        'reason': 'trained_bundle' if bundle else 'local_poisson',
        'preferred_score': preferred,
        'top_scores': sorted_scores,
        'poisson_lambda': {'home': lam_h, 'away': lam_a},
        'expected_total_goals': round(lam_h + lam_a, 4),
        'source': source,
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
    return 'deepseek-flash ML 摘要需用户配置 DeepSeek 服务；本地泊松在 λ 已形成时可用。'
