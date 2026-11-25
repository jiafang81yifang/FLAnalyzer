"""Self-check runtime support modules used by app_v84_hermes."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('OKOOO_CACHE_DIR', tempfile.mkdtemp(prefix='flanalyzer-okooo-selfcheck-'))

import blade_deepseek_pipeline as ds
import blade_ml_engine as ml
import convergence_arbitrator as arb
import odds_fallback as fb
import odds_index_math as odds
import okooo_mobile_cs as okooo
import sporttery_jc as jc
from ProductionRunnerBridge import attach_engine_clearing


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_dict(value: Any, key: str | None = None) -> dict[str, Any]:
    require(isinstance(value, dict), f'expected dict, got {type(value).__name__}')
    data = value
    if key is not None:
        require(key in data, f'missing key {key!r}: {data}')
    return data


def main() -> None:
    messages = ds.assemble_prompt(facts={'score': '1-0'}, task='self-check')
    require(bool(messages) and messages[0]['role'] == 'system', 'deepseek prompt assembly failed')
    local = ds.deepseek_json(messages)
    require_dict(local)
    payload = ds.DeepSeekAnalyzer.build_jc_cross_only_payload({'match': 'A vs B'})
    require_dict(payload, 'judgment_universe')

    bundle = ml.train_from_history({'1': {'actual_score': '2-1', 'lam_h': 1.5, 'lam_a': 1.0}}, save=False)
    require_dict(bundle, 'rows')
    pred = ml.predict_match_ml({'lam_h': 1.2, 'lam_a': 0.8})
    require_dict(pred, 'preferred_score')

    heat = okooo.scrape_okooo_mobile_cs_zuizhi('demo', html='<td>1-0</td><td>6.5</td><td>1-1</td><td>5.2</td>')
    require_dict(heat, 'ranking')
    six = okooo.scrape_okooo_mobile_six_markets('demo', html='大小 2.5 亚盘 -0.5')
    require_dict(six, 'boards_hit')

    inst = odds.calc_instant_probs(2.0, 3.2, 4.0)
    require(bool(inst['valid'] and inst['home']), 'instant probability calculation failed')
    rec = {'odds_correct_score': {'1-0': 6.0}}
    require_dict(fb.apply_fallback_cs_to_rec(rec), 'ranking')
    table = jc.build_merged_market_heat_jc_table({'ranking': [{'score': '1-0', 'heat': 0.2}]}, {'1-0': 6.0})
    require_dict(table, 'rows')
    require(arb.resolve_direction(['1-0', '2-1']) == '胜', 'arbitration direction failed')
    target: dict[str, Any] = {}
    require_dict(attach_engine_clearing(target), 'available')
    print(json.dumps({'ok': True, 'checked': 9}, ensure_ascii=False))


if __name__ == '__main__':
    main()
