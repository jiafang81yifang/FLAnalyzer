"""Self-check runtime support modules used by app_v84_hermes."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('OKOOO_CACHE_DIR', tempfile.mkdtemp(prefix='flanalyzer-okooo-selfcheck-'))

import blade_deepseek_pipeline as ds
import blade_ml_engine as ml
import okooo_mobile_cs as okooo
import odds_index_math as odds
import odds_fallback as fb
import sporttery_jc as jc
import convergence_arbitrator as arb
from ProductionRunnerBridge import attach_engine_clearing


def assert_dict(value, key=None):
    assert isinstance(value, dict), type(value)
    if key is not None:
        assert key in value, value
    return value


def main() -> None:
    messages = ds.assemble_prompt(facts={'score': '1-0'}, task='self-check')
    assert messages and messages[0]['role'] == 'system'
    local = ds.deepseek_json(messages)
    assert_dict(local)
    payload = ds.DeepSeekAnalyzer.build_jc_cross_only_payload({'match': 'A vs B'})
    assert_dict(payload, 'judgment_universe')

    bundle = ml.train_from_history({'1': {'actual_score': '2-1', 'lam_h': 1.5, 'lam_a': 1.0}}, save=False)
    assert_dict(bundle, 'rows')
    pred = ml.predict_match_ml({'lam_h': 1.2, 'lam_a': 0.8})
    assert_dict(pred, 'preferred_score')

    heat = okooo.scrape_okooo_mobile_cs_zuizhi('demo', html='<td>1-0</td><td>6.5</td><td>1-1</td><td>5.2</td>')
    assert_dict(heat, 'ranking')
    six = okooo.scrape_okooo_mobile_six_markets('demo', html='大小 2.5 亚盘 -0.5')
    assert_dict(six, 'boards_hit')

    inst = odds.calc_instant_probs(2.0, 3.2, 4.0)
    assert inst['valid'] and inst['home']
    rec = {'odds_correct_score': {'1-0': 6.0}}
    assert_dict(fb.apply_fallback_cs_to_rec(rec), 'ranking')
    table = jc.build_merged_market_heat_jc_table({'ranking': [{'score': '1-0', 'heat': 0.2}]}, {'1-0': 6.0})
    assert_dict(table, 'rows')
    assert arb.resolve_direction(['1-0', '2-1']) == '胜'
    target = {}
    assert_dict(attach_engine_clearing(target), 'available')
    print(json.dumps({'ok': True, 'checked': 9}, ensure_ascii=False))


if __name__ == '__main__':
    main()
