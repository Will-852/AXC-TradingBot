"""Mermaid workflow diagrams — embedded in dashboard pages.

All system workflows visualized as interactive Mermaid diagrams.
"""

from nicegui import ui

from scripts.dashboard_ng.theme import CARD_DARK, SECTION_HEADER, BG_SURFACE, BORDER

# ── Mermaid theme override for dark mode ──
MERMAID_INIT = '''%%{init: {
  "theme": "dark",
  "themeVariables": {
    "primaryColor": "#1e3a5f",
    "primaryTextColor": "#e2e8f0",
    "primaryBorderColor": "#3b82f6",
    "lineColor": "#475569",
    "secondaryColor": "#1e293b",
    "tertiaryColor": "#0f172a",
    "fontSize": "13px"
  }
}}%%'''


def _diagram_section(title: str, icon: str, mermaid_code: str, description: str = ''):
    """Render an expandable diagram section."""
    with ui.expansion(title, icon=icon).classes('w-full'):
        if description:
            ui.label(description).classes('text-xs text-slate-400 mb-2')
        ui.mermaid(MERMAID_INIT + '\n' + mermaid_code).classes('w-full')


def render_system_architecture():
    """System architecture — all services and connections."""
    _diagram_section(
        'System Architecture', 'account_tree',
        '''graph LR
    subgraph Input["📡 Data Sources"]
        BN[Binance API]
        AS[Aster FX API]
        HL[HyperLiquid API]
        NEWS[News Feeds]
        PM[Polymarket API]
    end

    subgraph Core["⚙️ Core Engine"]
        SCAN[Scanner<br/>async_scanner.py]
        IND[Indicator Engine<br/>indicator_engine.py]
        LIQ[Liq Monitor<br/>liq_monitor.py]
        WS[WS Manager<br/>ws_manager.py]
    end

    subgraph AI["🧠 AI Layer"]
        CLAUDE[Claude Sonnet<br/>Tier 1 Decision]
        HAIKU[Claude Haiku<br/>Tier 2 Scan]
        GPT[GPT Fallback<br/>Tier 3]
    end

    subgraph Execution["💰 Execution"]
        TC[Trader Cycle<br/>15min interval]
        POLY[Poly Pipeline<br/>60s interval]
    end

    subgraph Output["📊 Output"]
        TG[Telegram Bot]
        DASH[Dashboard<br/>NiceGUI :5567]
        LOG[Logs + State<br/>shared/*.json]
    end

    BN --> SCAN
    AS --> SCAN
    NEWS --> SCAN
    SCAN --> IND
    IND --> CLAUDE
    CLAUDE --> TC
    TC --> AS
    TC --> BN
    TC --> HL
    PM --> POLY
    HAIKU --> SCAN
    TC --> LOG
    POLY --> LOG
    LOG --> DASH
    TC --> TG
    POLY --> TG''',
        '9 agents + dashboard + Telegram。Claude Sonnet 做決策，Haiku 做掃描。',
    )


def render_trading_pipeline():
    """Scanner → Signal → Trader Cycle flow."""
    _diagram_section(
        'Trading Pipeline', 'swap_horiz',
        '''flowchart TD
    A[Scanner 每 3 分鐘] -->|掃描所有 symbols| B{Signal 觸發?}
    B -->|NO| A
    B -->|YES| C[寫入 SIGNAL.md<br/>pair + direction + score]

    C --> D[Trader Cycle<br/>每 15 分鐘讀取]
    D --> E{Trading<br/>Enabled?}
    E -->|NO| F[Skip — 記錄原因]
    E -->|YES| G{Risk Check}

    G -->|Circuit Breaker| F
    G -->|Cooldown| F
    G -->|Pass| H[AI Decision<br/>Claude Sonnet]

    H -->|ENTER| I[執行落單<br/>5-Step Sequence]
    H -->|SKIP| F

    I --> J[Monitor Position]
    J --> K{Exit 條件}
    K -->|SL Hit| L[止損平倉]
    K -->|TP Hit| M[止盈平倉]
    K -->|Signal Flip| N[信號反轉平倉]
    K -->|Max Hold| O[超時平倉]

    L --> P[記錄 trade_log]
    M --> P
    N --> P
    O --> P
    P --> A''',
        'Scanner 偵測 → Signal 觸發 → AI 決策 → 執行 → 監控 → 平倉。',
    )


def render_order_execution():
    """5-step order execution sequence."""
    _diagram_section(
        'Order Execution (5-Step)', 'playlist_add_check',
        '''sequenceDiagram
    participant U as Dashboard
    participant E as Exchange
    participant SL as SL Order
    participant TP as TP Order

    U->>E: ① Set Margin Mode (ISOLATED)
    E-->>U: OK (or already set)

    U->>E: ② Set Leverage
    E-->>U: Leverage confirmed

    U->>E: ③ Place Entry (Market/Limit)
    E-->>U: Fill price + qty

    U->>SL: ④ Place Stop Loss (CRITICAL)
    alt SL Success
        SL-->>U: SL order ID
        U->>TP: ⑤ Place Take Profit
        TP-->>U: TP order ID (best-effort)
    else SL Failed
        U->>E: ⚠️ Emergency Close Position
        E-->>U: Position closed
        U->>U: Return error to user
    end''',
        'SL 係 critical — 失敗會觸發 emergency close。TP 係 best-effort。',
    )


def render_polymarket_pipeline():
    """Polymarket 17-step pipeline."""
    _diagram_section(
        'Polymarket Pipeline', 'casino',
        '''flowchart TD
    S1[1. Load State] --> S2[2. Check Circuit Breakers]
    S2 -->|Tripped| STOP[❌ Abort Pipeline]
    S2 -->|OK| S3[3. Scan Gamma API<br/>Active Markets]

    S3 --> S4[4. Filter by Category<br/>crypto_15m]
    S4 --> S5[5. Fetch Order Books]
    S5 --> S6[6. Calculate Fair Value<br/>Brownian Bridge]

    S6 --> S7[7. Score Opportunities<br/>edge = fair - market]
    S7 --> S8{8. Edge > Threshold?}
    S8 -->|NO| S9[Skip — No Edge]
    S8 -->|YES| S10[9. Check Exposure Limits]

    S10 --> S11[10. Calculate Size<br/>Kelly Criterion]
    S11 --> S12[11. Plan Orders<br/>Maker-side placement]
    S12 --> S13[12. Execute via CLOB API]

    S13 --> S14[13. Monitor Fills]
    S14 --> S15[14. Update State]
    S15 --> S16[15. Check Resolutions]
    S16 --> S17[16. Update PnL]
    S17 --> S18[17. Save State + Log]

    S18 --> S1''',
        '60 秒一次。Scan → Filter → Score → Size → Execute → Monitor → Repeat。',
    )


def render_signal_flow():
    """Data flow: how signals are generated."""
    _diagram_section(
        'Signal Generation Flow', 'insights',
        '''flowchart LR
    subgraph Market["Market Data"]
        K[Klines<br/>1H/4H/1D]
        OB[Order Book<br/>Depth + OBI]
        FR[Funding Rates]
        VOL[Volume<br/>24h + ratio]
    end

    subgraph Indicators["Indicator Engine"]
        ATR[ATR<br/>Volatility]
        SR[Support /<br/>Resistance]
        HMM[HMM Regime<br/>Detection]
        BOCPD[BOCPD<br/>Change Point]
    end

    subgraph Decision["Decision Layer"]
        THRESH{Price vs<br/>Threshold}
        MODE[Market Mode<br/>TREND/RANGE]
        SCORE[Signal Score<br/>0-100]
    end

    K --> ATR
    K --> SR
    K --> HMM
    K --> BOCPD
    OB --> THRESH
    VOL --> THRESH

    ATR --> THRESH
    SR --> THRESH
    HMM --> MODE
    BOCPD --> MODE

    THRESH --> SCORE
    MODE --> SCORE
    FR --> SCORE

    SCORE -->|≥ Threshold| SIG[📡 SIGNAL.md]
    SCORE -->|< Threshold| WAIT[⏳ Continue Scanning]''',
        'Market data → Indicators → Mode detection → Score → Signal trigger。',
    )


def render_service_lifecycle():
    """LaunchAgent service lifecycle."""
    _diagram_section(
        'Service Lifecycle', 'settings_suggest',
        '''stateDiagram-v2
    [*] --> Stopped
    Stopped --> Starting: launchctl bootstrap
    Starting --> Running: Process alive
    Running --> Running: KeepAlive / StartInterval
    Running --> Crashed: Unexpected exit
    Crashed --> Starting: KeepAlive auto-restart
    Running --> Stopped: launchctl bootout
    Stopped --> [*]

    state Running {
        [*] --> Active
        Active --> Sleeping: StartInterval wait
        Sleeping --> Active: Interval elapsed
    }''',
        'LaunchAgent 管理所有服務。KeepAlive = 長駐，StartInterval = 定時。',
    )


def render_all_diagrams():
    """Render all workflow diagrams in a dedicated section."""
    ui.label('SYSTEM WORKFLOWS').classes(SECTION_HEADER)
    with ui.column().classes('w-full gap-1'):
        render_system_architecture()
        render_trading_pipeline()
        render_order_execution()
        render_polymarket_pipeline()
        render_signal_flow()
        render_service_lifecycle()
