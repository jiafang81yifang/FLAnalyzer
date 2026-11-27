<#
FLAnalyzer Windows install notes

Choose ONE deployment mode:

1) Single-file mode (recommended for copying to an existing Streamlit folder)
   - Copy only app_v84_hermes.py into your target folder, for example:
       C:\FLAnalyzer\app_v84_hermes.py
   - The committed app_v84_hermes.py already contains embedded runtime modules for
     blade_deepseek_pipeline, blade_ml_engine, okooo_mobile_cs, blade_monitor_panel,
     ProductionRunnerBridge, convergence_arbitrator, odds_fallback, odds_index_math,
     sporttery_jc, and unified_odds_evaluator. If you edit any sibling module in development, refresh the
     embedded block with:
       python scripts\build_single_file.py
   - Start with:
       streamlit run C:\FLAnalyzer\app_v84_hermes.py --server.headless true

2) Multi-file mode (recommended for development)
   - Put app_v84_hermes.py and these sibling files in the SAME folder:
       blade_deepseek_pipeline.py
       blade_ml_engine.py
       okooo_mobile_cs.py
       blade_monitor_panel.py
       ProductionRunnerBridge.py
       convergence_arbitrator.py
       odds_fallback.py
       odds_index_math.py
       sporttery_jc.py
       unified_odds_evaluator.py
   - Start with:
       streamlit run C:\FLAnalyzer\app_v84_hermes.py --server.headless true

Optional environment variables:
   DEEPSEEK_API_KEY       Enables real DeepSeek API calls.
   DEEPSEEK_BASE_URL      Defaults to https://api.deepseek.com/v1
   DEEPSEEK_CHAT_MODEL    Defaults to deepseek-chat.
   OKOOO_PROXY            Proxy for Okooo and Telegram HTTP(S) requests.
   HTTP_PROXY/HTTPS_PROXY Standard proxy fallback variables.
#>

Write-Host "FLAnalyzer install notes are in this file. Copy app_v84_hermes.py for single-file mode, or copy all listed .py files for multi-file mode."
