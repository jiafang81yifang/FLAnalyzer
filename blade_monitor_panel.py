"""Optional Streamlit monitor panel fallbacks."""
from __future__ import annotations

from typing import Any


def render_monitor_sidebar_controls(*args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        import streamlit as st

        st.sidebar.caption('Blade monitor panel: local lightweight controls loaded')
    except Exception as exc:  # noqa: BLE001 - optional panel should never break main app
        print(f"   ⚠️ [监控面板] 侧栏渲染跳过: {type(exc).__name__}")
    return {'available': True}


def render_blade_monitor_dashboard(*args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        import streamlit as st

        st.caption('Blade monitor dashboard: local lightweight view loaded')
    except Exception as exc:  # noqa: BLE001 - optional panel should never break main app
        print(f"   ⚠️ [监控面板] 主面板渲染跳过: {type(exc).__name__}")
    return {'available': True}
