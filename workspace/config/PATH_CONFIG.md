# PATH_CONFIG.md — 全系統唯一路徑定義
# 版本: 2026-03-02（修正版）
# 規則: 所有 MD 引用此變數，禁止寫死路徑

ROOT = {HOME}/.openclaw/workspace

# ─── Root Level（OpenClaw 主讀取）───
PATH_ROOT_SOUL        = {ROOT}/SOUL.md
PATH_ROOT_AGENTS      = {ROOT}/AGENTS.md
PATH_ROOT_HEARTBEAT   = {ROOT}/HEARTBEAT.md
PATH_ROOT_IDENTITY    = {ROOT}/IDENTITY.md
PATH_ROOT_USER        = {ROOT}/USER.md
PATH_ROOT_TOOLS       = {ROOT}/TOOLS.md
PATH_CRON_PAYLOADS    = {ROOT}/CRON_PAYLOADS.md

# ─── Config ───
PATH_PATH_CONFIG      = {ROOT}/config/PATH_CONFIG.md
PATH_IDENTITY         = {ROOT}/config/IDENTITY.md
PATH_USER             = {ROOT}/config/USER.md
PATH_TOOLS            = {ROOT}/config/TOOLS.md

# ─── Routing ───
PATH_MODEL_ROUTER     = {ROOT}/routing/MODEL_ROUTER.md
PATH_COST_TRACKER     = {ROOT}/routing/COST_TRACKER.md

# ─── Protocols ───
PATH_HEARTBEAT        = {ROOT}/protocols/HEARTBEAT.md
PATH_NEWS_PROTOCOL    = {ROOT}/protocols/NEWS_PROTOCOL.md
PATH_NEWS_SOURCES     = {ROOT}/protocols/NEWS_SOURCES.md

# ─── Core ───
PATH_SOUL             = {ROOT}/core/SOUL.md
PATH_STRATEGY         = {ROOT}/core/STRATEGY.md
PATH_RISK_PROTOCOL    = {ROOT}/core/RISK_PROTOCOL.md

# ─── Agents ───
PATH_AGENTS           = {ROOT}/agents/AGENTS.md

# ─── Trader Agent ───
PATH_TRADER_SOUL      = {ROOT}/agents/trader/SOUL.md
PATH_TRADER_STRATEGY  = {ROOT}/agents/trader/STRATEGY.md
PATH_TRADE_STATE      = {ROOT}/agents/trader/TRADE_STATE.md
PATH_TRADE_LOG        = {ROOT}/agents/trader/TRADE_LOG.md
PATH_EXCHANGE_CONFIG  = {ROOT}/agents/trader/EXCHANGE_CONFIG.md
PATH_SCAN_CONFIG      = {ROOT}/agents/trader/config/SCAN_CONFIG.md
PATH_SCAN_LOG         = {ROOT}/agents/trader/logs/SCAN_LOG.md

# ─── Memory ───
PATH_MEMORY           = {ROOT}/memory/MEMORY.md
PATH_EMOTION_BIN      = {ROOT}/memory/EMOTION_BIN.md

# ─── Keys ───
PATH_API_KEYS         = {ROOT}/keys/API_KEYS.md

# ─── Skills ───
PATH_SKILL_WORKSPACE_OPS = {ROOT}/skills/WORKSPACE_OPS.md

# ─── Tools ───
PATH_TELEGRAM         = {ROOT}/tools/telegram_sender.py
PATH_INDICATOR_CALC   = {ROOT}/tools/indicator_calc.py

# ─── Knowledge Base ───
PATH_KB_TRADING_PATTERNS = {ROOT}/knowledge/TRADING_BOT_PATTERNS.md
PATH_KB_OPS_PATTERNS     = {ROOT}/knowledge/OPENCLAW_OPS_PATTERNS.md

# ─── Python Tools (trader_cycle package) ───
PATH_TRADER_CYCLE       = {ROOT}/tools/trader_cycle/
PATH_TC_MAIN            = {ROOT}/tools/trader_cycle/main.py
PATH_TC_SETTINGS        = {ROOT}/tools/trader_cycle/config/settings.py
PATH_TC_PAIRS           = {ROOT}/tools/trader_cycle/config/pairs.py
PATH_TC_CONTEXT         = {ROOT}/tools/trader_cycle/core/context.py
PATH_TC_PIPELINE        = {ROOT}/tools/trader_cycle/core/pipeline.py
PATH_TC_REGISTRY        = {ROOT}/tools/trader_cycle/core/registry.py
PATH_TC_ASTER_CLIENT    = {ROOT}/tools/trader_cycle/exchange/aster_client.py
PATH_TC_EXCEPTIONS      = {ROOT}/tools/trader_cycle/exchange/exceptions.py
PATH_TC_MARKET_DATA     = {ROOT}/tools/trader_cycle/exchange/market_data.py
PATH_TC_POSITION_SYNC   = {ROOT}/tools/trader_cycle/exchange/position_sync.py
PATH_TC_EXECUTE_TRADE   = {ROOT}/tools/trader_cycle/exchange/execute_trade.py
PATH_TC_MODE_DETECTOR   = {ROOT}/tools/trader_cycle/strategies/mode_detector.py
PATH_TC_RANGE_STRATEGY  = {ROOT}/tools/trader_cycle/strategies/range_strategy.py
PATH_TC_TREND_STRATEGY  = {ROOT}/tools/trader_cycle/strategies/trend_strategy.py
PATH_TC_EVALUATE        = {ROOT}/tools/trader_cycle/strategies/evaluate.py
PATH_TC_RISK_MANAGER    = {ROOT}/tools/trader_cycle/risk/risk_manager.py
PATH_TC_POSITION_SIZER  = {ROOT}/tools/trader_cycle/risk/position_sizer.py
PATH_TC_SCAN_CONFIG     = {ROOT}/tools/trader_cycle/state/scan_config.py
PATH_TC_TRADE_STATE     = {ROOT}/tools/trader_cycle/state/trade_state.py
PATH_TC_TRADE_LOG       = {ROOT}/tools/trader_cycle/state/trade_log.py
PATH_TC_MEMORY_KEEPER   = {ROOT}/tools/trader_cycle/state/memory_keeper.py
PATH_TC_TELEGRAM        = {ROOT}/tools/trader_cycle/notify/telegram.py
PATH_LIGHT_SCAN         = {ROOT}/tools/light_scan.py
PATH_HEARTBEAT_PY       = {ROOT}/tools/heartbeat.py
PATH_INDICATOR_CALC_PY  = {ROOT}/tools/indicator_calc.py

# ─── Reference ───
PATH_REF_LIGHTSCAN_FMT  = {ROOT}/reference/light-scan-cantonese-delivery.md
PATH_REF_RANGE_STRATEGY = {ROOT}/reference/range-strategies-btc-eth-xrp.md
PATH_REF_TV_TEST        = {ROOT}/reference/tv_test.py

# ─── Secrets ───
PATH_SECRETS_ENV        = {HOME}/.openclaw/secrets/.env

# ─── Logs ───
PATH_LOG_DIR            = {HOME}/.openclaw/logs/
PATH_CYCLE_LOG_DIR      = {HOME}/.openclaw/logs/cycles/
PATH_PAPER_GATE         = {HOME}/.openclaw/logs/paper_gate_start.txt

# ─── 注意事項 ───
# SOUL.md（根目錄）→ symlink → core/SOUL.md（Single Source of Truth）
# AGENTS.md（根目錄）→ symlink → agents/AGENTS.md（Single Source of Truth）
# agents/trader/SOUL.md → stub，指向 core/SOUL.md
# 根目錄同子目錄都有 IDENTITY/USER/TOOLS/HEARTBEAT
# 根目錄版本 = OpenClaw 主讀取，子目錄版本 = 備份/詳細參考
