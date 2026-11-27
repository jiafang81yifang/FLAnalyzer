"""Bridge helpers for attaching engine clearing details."""
from __future__ import annotations

from typing import Any


def attach_engine_clearing(arbitration_result: dict[str, Any], direction_lock_payload: Any = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
    if not isinstance(arbitration_result, dict):
        return {'available': False, 'reason': 'invalid_arbitration'}
    arbitration_result['engine_clearing'] = {
        'available': True,
        'direction_lock_available': isinstance(direction_lock_payload, dict) and bool(direction_lock_payload),
    }
    return arbitration_result['engine_clearing']
